ADR 001: Storage Identification and Upload Processing Strategy


Project: Image Upload Service with Async Thumbnails

1. Context

The image upload service must support large image uploads, prevent duplicate physical storage, and maintain reasonable memory usage under concurrent uploads.

Two architectural decisions need to be made:

Whether to identify stored images using content-addressed storage or randomly generated IDs.
Whether to process incoming uploads through streaming or buffering.
These decisions affect storage efficiency, privacy, memory consumption, implementation complexity, and scalability.

2. Decision A: Content-Addressed Storage vs. Random IDs

Option 1: Content-Addressed Storage

Use SHA-256 to calculate a unique content fingerprint for each uploaded image. The resulting hash serves as the internal storage key.

Advantages:

Identical image contents produce the same hash, enabling deduplication.
Reduces physical storage consumption by reusing existing images.
Helps verify file integrity.
Provides deterministic identification of image contents.
Disadvantages:

Requires hashing the image contents.
Identical content produces identical hashes, which can create privacy concerns if hashes are exposed.
Hash collisions are theoretically possible, although extremely unlikely with SHA-256.
Concurrent uploads of identical files require atomic handling to prevent duplicate storage.
Option 2: Random IDs

Generate a random UUID for every uploaded image and use it as the storage identifier.

Advantages:

Simple to implement.
IDs do not reveal the image’s contents.
Separates image identity from its contents.
Avoids deterministic identifiers.
Disadvantages:

Identical uploads receive different identifiers.
Does not naturally support deduplication.
Requires additional logic to identify duplicate images if storage optimization is needed.
Decision

Use SHA-256 content-addressed storage for internal physical image identification and UUIDs for public user-file references.

The files table stores the image and its SHA-256 hash. The user_files table stores user-specific references to that image.

This allows multiple users to reference the same physical image without exposing its internal hash through the API.

Collision and Privacy Considerations

Although SHA-256 collisions are extremely unlikely, the system should not assume that matching hashes automatically prove matching contents. If a hash already exists, verify the existing object’s size and, where appropriate, its contents before reusing it.

The API must not expose internal hashes or reveal whether an image was newly stored or reused. Authorization must be checked against each user’s file reference.

3. Decision B: Streaming vs. Buffering Uploads

Option 1: Buffering

Read the entire uploaded image into memory before processing or storing it.

Example:

image_bytes = await file.read()
Advantages:

Simple implementation.
Easier to process the complete file in memory.
Straightforward error handling.
Disadvantages:

Memory consumption grows with the size of each uploaded image.
Large uploads can consume substantial memory.
Concurrent uploads increase memory pressure.
May cause performance problems or out-of-memory failures.
Option 2: Streaming

Read the incoming image in smaller chunks, calculate the SHA-256 hash incrementally, and write the chunks to temporary storage.

Example:

while chunk := await file.read(1024 * 1024):
    hasher.update(chunk)
    await storage.write(chunk)
Advantages:

Memory usage is approximately proportional to the chunk size rather than the total image size.
Better suited to large image uploads.
Supports incremental hash calculation.
Reduces memory pressure under concurrent uploads.
Disadvantages:

More complex implementation.
Requires handling partial uploads and cleanup after failures.
Requires careful management of storage writes and resource limits.
Actual memory usage still depends on framework and storage-client buffering.
Decision

Use streaming uploads with an initial chunk size of 1 MB.

The backend will calculate the SHA-256 hash while receiving the image and write the incoming chunks to temporary storage.

After the upload completes and validation succeeds, the backend will check for an existing image using the calculated hash. It will reuse the existing physical object or persist a new one.

The chunk size can be benchmarked and adjusted during performance testing.

4. Consequences of These Decisions

Positive consequences:

Duplicate image contents can share one physical storage object.
Public file references remain separate from internal storage identifiers.
Large uploads do not need to be loaded entirely into application memory.
Hash calculation occurs during upload rather than requiring a separate full-file read.
Trade-offs:

Streaming requires more careful error handling and cleanup.
Global deduplication introduces privacy considerations.
Concurrent uploads require atomic deduplication and quota operations.
Additional logic is needed to keep object storage and database records consistent.
5. Final Architecture

Internal image identity: SHA-256 hash.
Public user-file identity: Random UUID.
Upload strategy: Streaming.
Initial chunk size: 1 MB.
Physical storage: Object storage.
Metadata and relationships: Relational database.
Deduplication: Global, based on verified content hashes.