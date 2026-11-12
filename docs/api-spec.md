Step 1.1 — API Specification and Upload Architecture

1. System Architecture

Client → Backend API → Database and Object Storage

The client communicates with the backend through the following endpoints:

POST /api/v1/files — Upload an image.
GET /api/v1/files/{file_id} — Retrieve image metadata.
GET /api/v1/files/{file_id}/original — Retrieve the original image.
GET /api/v1/files/{file_id}/thumbnail — Retrieve the generated thumbnail.
Database: Stores image metadata, SHA-256 hashes, storage locations, ownership relationships, and quota information.

Object Storage: Stores the actual image bytes.

Background Worker: Processes thumbnail-generation jobs asynchronously using a queue.

2. Upload Flow vs. Presigned Upload Flow

Option A: Backend-Managed Upload
Option A is best for this project
The client sends the image to the backend.
The backend streams the incoming bytes.
The backend calculates the image’s SHA-256 hash while streaming.
The backend checks for duplicate images using the hash.
The backend either reuses an existing stored image or saves a new image.
The backend creates the necessary database records.
The backend places a thumbnail-generation job into a queue.
A separate background worker retrieves the job, reads the original image, generates a smaller version, and saves the thumbnail.
Advantages:

Direct control over file validation.
Control over SHA-256 hashing and deduplication.
Centralized quota enforcement.
The backend can manage the upload and storage process.
Disadvantages:

The backend handles the incoming upload traffic.
Large uploads consume backend network bandwidth and processing resources.
Decision: Backend-managed uploads are appropriate for this project because they allow us to calculate hashes while streaming, validate files, enforce quotas, and manage deduplication within one controlled upload process.

Option B: Presigned Upload

The backend generates a temporary, authorized upload URL that allows the client to upload the image directly to object storage.

This reduces the amount of upload traffic handled by the backend but requires additional coordination to verify the uploaded object, calculate its hash, enforce quotas, and handle deduplication.

3. Endpoint 1 — Upload an Image

Request

POST /api/v1/files

Headers

Authorization: Bearer <token>
Content-Type: image/jpeg
The request body contains the raw image bytes. This specification uses a raw-body upload rather than multipart form data.

Upload Processing Flow

Authenticate the user: Validate the authorization token and identify the user.
Validate the image: Check the allowed image formats and verify the actual file contents.
Check upload size: Ensure the image does not exceed the maximum permitted size.
Reserve quota: Atomically verify that the user has sufficient available storage.
Stream and hash: Stream the image into temporary storage in chunks while calculating its SHA-256 hash.
Check global deduplication: Search the database for an existing image with the same SHA-256 hash.
Store or reuse the image:

If the image already exists, reuse its existing storage location.
Otherwise, save the image in object storage and create its database record.
Create a user-file relationship: Create a separate reference connecting the authenticated user to the stored image.
Update quota accounting: Charge the user for the logical size of the original image, even if its physical bytes are shared with another user.
Schedule thumbnail generation: Add a job to the background queue.
Return the response: Return the user’s file ID and indicate that thumbnail generation is pending.
Deduplication Strategy

Decision: Global deduplication using SHA-256.

Identical images produce the same SHA-256 hash, allowing the system to reuse an existing physical image rather than storing duplicate bytes.

Each user receives a separate record in the user_files table that references the shared image in the files table.

Privacy considerations:

Do not expose SHA-256 hashes through the public API.
Do not reveal whether an image was newly stored or reused.
Verify file ownership before allowing access to metadata, originals, or thumbnails.
Avoid response differences that reveal whether another user’s image already exists.
Quota policy: Each user is charged for the logical size of their own original images, regardless of whether the physical image is shared. Generated thumbnails are excluded from the user’s quota.

Example Successful Response

HTTP 201 Created

{
  "file_id": "user_file_123abc",
  "thumbnail_status": "pending",
  "size_bytes": 5242880,
  "content_type": "image/jpeg"
}
The upload succeeds without waiting for thumbnail generation to finish. The thumbnail is processed asynchronously by the background worker.