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
## Thumbnail cache (task 2.3)

Ready thumbnail fetches authenticate the caller and check ownership and current
thumbnail status before accessing a process-local LRU cache. Pending responses
and failure placeholders are not cached. Internal immutable keys include the
original SHA-256, bounding dimensions, WebP format, and recipe version:
`<sha256>:200x200:webp:v1`. A recipe change must change both the cache key and the
thumbnail storage filename/version in `services/thumbnails.py`.

The cache holds at most 16 MiB of payload bytes (Python overhead and in-flight
responses are additional). Oversized values are served without caching. Entries
are evicted in least-recently-used order. Each API process has independent data
and counters, which reset on restart; this is not a distributed cache. Concurrent
cold requests may each read storage before one populates the cache.

`GET /api/v1/metrics/thumbnail-cache` requires a valid bearer token and returns
aggregate `hits`, `misses`, `size_bytes`, and `entries` for the serving process.
It exposes no file keys or user identifiers. A missing ready object counts as a
cache miss. To demonstrate a hit, fetch the same ready thumbnail twice on one
fresh API process and compare counters before and after: one miss then one hit.
HTTP responses retain `Cache-Control: private, no-store`; this controls client
and intermediary caching independently of the internal cache.

Deletion policy: removing a user reference immediately removes that reference's
access, including on cache hits. Shared originals and thumbnails remain while
other user references exist. Once the last reference is removed, both physical
objects become eligible for coordinated orphan/garbage collection with a grace
period and publisher synchronization. Cached immutable bytes may remain until
LRU eviction or process restart, but cannot be fetched through deleted references.
There is no delete endpoint or garbage collector implemented yet; this is the
policy they must follow. Missing backing files do not invalidate a warm cache.

## Upload rate limiting and quota responses (task 2.4)

Each authenticated user may make 10 admitted upload attempts per 60-second fixed
window, beginning with their first admitted attempt. A window resets on the next
attempt after expiry; this is not a rolling-window limit and can allow bursts
across a boundary. PostgreSQL stores the per-user counter. Row locking and
conflict-safe insertion enforce the same allowance across API processes.

Admission is checked before application-level body streaming and committed in a
separate short transaction. An admitted attempt counts even if upload processing
later fails, including quota rejection or an oversized body. Authentication
failures do not count. Rate-limit rejections do not increment or extend the
window. This limits attempts, not concurrent connections or network buffering.

Excess attempts receive HTTP 429 with `detail: "Upload rate limit exceeded"` and
an integer `Retry-After` header in seconds (rounded up, minimum 1). After waiting,
clients may retry; intervening requests may consume the new allowance.

Quota is checked separately and atomically. Insufficient quota returns HTTP 413
with `detail: {"message": "Storage quota exceeded", "remaining_bytes": N}`.
Remaining bytes subtract both used and reserved bytes. A missing quota row also
reports zero remaining; users must be provisioned with a quota row. Rejected
uploads create no user reference or quota charge, but retain their admitted
rate-limit attempt. Oversized uploads also return 413 with a size-limit message.

Integration tests in `tests/test_upload_rate_limits.py` require
`TEST_DATABASE_URL` pointing to PostgreSQL's dedicated `upload_service_test`
database. They create and remove isolated schemas, covering bursts, user
isolation, expiry, concurrent admission, first-row races, and an actual HTTP
quota failure followed by rate-limit rejection.
