# Project 1C — Image Upload Service with Async Thumbnails

**Duration:** 2 weeks · **Effort:** 12 hours/week · **Reviews:** 2 per week

---

## The Brief

We are building the upload backend for a small photo-sharing app. Users upload images, get back a URL, and see a thumbnail in their feed.

Requirements from the product side:

- Uploads can be large and slow, so the upload endpoint must stay responsive and thumbnail generation must happen in the background.
- People upload the same file many times. Do not store the same bytes ten times.
- Each user has a storage quota so one person cannot fill the disk.
- The feed must load fast even when the original image is large.

Decisions you are expected to make and defend:

- What counts against the quota: the original, the thumbnail, or both?
- If thumbnail generation fails permanently, does the upload fail, or do you serve a placeholder?
- Is deduplication per user or global? If global, does that leak the existence of another user's file?

---

## What You Will Practice

| Area | What it means here |
| :--- | :--- |
| API design | Streaming uploads, metadata fetch, origin fetch, thumbnail fetch |
| ID / encoding | Content-addressed storage: hashing bytes as the identity |
| Data modeling | Files, per-user references, and quota as separate normalized tables |
| Caching | Immutable content-hash keys and a thumbnail cache |
| Rate limiting | Uploads per user per minute, plus a storage quota |
| Async side effect | Thumbnail generation with retries and a terminal failure state |
| Resource management | Peak memory bounds during large uploads; orphaned file cleanup |
| Observability | Upload throughput, thumbnail lag, quota utilization, dedup ratio |

---

## Week 1 — Streaming Upload and Content Addressing

| # | Task | Est. | Acceptance Criteria |
| :-- | :--- | :--- | :--- |
| 1.1 | Write the API spec: upload flow (direct or presigned), metadata fetch, origin fetch, thumbnail fetch. State the quota semantics and the deduplication scope with a justification. | 2h | Spec committed before code. It explicitly answers whether global deduplication leaks file existence. |
| 1.2 | Write an ADR comparing content-addressed storage against random IDs, and streaming uploads against buffering. | 1h | ADR names the trade-offs, especially memory usage for large files. |
| 1.3 | Data layer and migrations: `files`, `user_files`, `quota`. | 2h | Migrations clean; repository tests for insert and quota read. |
| 1.4 | Implement the upload endpoint: stream to storage, compute the hash on the fly, deduplicate, and update the quota atomically. | 3.5h | A 50MB upload does not exceed a stated memory bound. Uploading the same file twice stores the bytes once and creates two user references (proven by test). Concurrent uploads from one user cannot exceed the quota (proven by test). |
| 1.5 | Implement metadata and origin-fetch endpoints. | 1.5h | Correct content type returned; cache headers are correct and you can justify them. |
| 1.6 | Structured logging with correlation ID and upload size. | 1h | Structured JSON logs; a single upload is traceable end to end. |

**Week 1 demo:** a 50MB upload with a memory reading, the deduplication test, and the concurrent quota test.

---

## Week 2 — The Async Thumbnail and the Fast Feed

| # | Task | Est. | Acceptance Criteria |
| :-- | :--- | :--- | :--- |
| 2.1 | Thumbnail job: a queue plus a worker that reads the original, generates a thumbnail, and stores it. | 2.5h | Blocking the worker does not slow the upload. The thumbnail appears once the worker catches up. |
| 2.2 | Job failure handling: retries with backoff, a placeholder thumbnail after N failures, and a queryable state so a user or operator can see "processing failed". | 2h | Killing the worker mid-job recovers without a corrupt thumbnail. A permanently failing job ends in the placeholder state, and that state is queryable. |
| 2.3 | Thumbnail cache using immutable content-hash keys, with a cache-hit metric. | 1.5h | The second fetch of the same thumbnail is served from cache, shown by the metric. Decide and defend what happens to a thumbnail when the original is deleted. |
| 2.4 | Per-user upload rate limit plus a quota-exceeded response. | 1.5h | Burst test enforced. Quota-exceeded returns a clean response including the remaining quota. |
| 2.5 | Load test: mixed upload, fetch, and thumbnail workload. Report deduplication ratio and thumbnail lag. | 1.5h | Numbers recorded; dedup ratio matches the seeded duplicates; thumbnail p95 lag reported. |
| 2.6 | Runbook: worker down, disk full, hash collision, quota race, orphaned files. | 2h | Each has a mitigation. The orphan case (file written, database insert failed) is addressed explicitly. |

**Week 2 demo:** the placeholder path, the deduplication ratio, and the orphaned-file conversation.

---

## Deliverables

1. API specification (committed before implementation).
2. ADR covering content addressing and the streaming decision.
3. Working service with tests, including the dedup and quota concurrency tests.
4. Async thumbnail pipeline with retry and a terminal failure state.
5. Immutable-key cache with a hit metric.
6. An orphaned-file reconciliation design.
7. Runbook with five failure modes.
8. A half-page retrospective: what content addressing bought you, and what it cost in the deduplication and privacy conversation.

---

## Definition of Done

- [ ] Streaming upload with a proven memory bound.
- [ ] Deduplication implemented with a stated scope and a collision policy.
- [ ] Quota enforced atomically under concurrent uploads.
- [ ] Async thumbnails with retry and a placeholder terminal state.
- [ ] Thumbnail cache with immutable keys and a hit metric.
- [ ] Orphaned-file reconciliation designed.
- [ ] Runbook with five failure modes.
- [ ] Retrospective written.

---

## Self-Check Before Each Review

Be ready to answer these without notes:

1. Two different files hash to the same value. Walk through what happens. What is your collision policy?
2. If deduplication is global, user B can be served from user A's bytes. Is that a privacy leak? What does user B see if user A deletes their file?
3. Show me where the upload streams. For a 2GB file, what is your peak memory and where do the bytes live while hashing?
4. Show me the race between two concurrent uploads from the same user against the quota. How do you enforce the limit without locking the whole table?
5. Thumbnail generation fails permanently. What does the user see at each step, and what query finds stuck thumbnails for an operator?
6. Your cache key is a content hash. What is your invalidation story, and how does it differ from a shortener's?
7. An upload writes bytes but the database insert fails. How do you find and clean orphaned files? Design the reconciliation job.