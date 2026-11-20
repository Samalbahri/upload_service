from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models import File, ThumbnailJob

MAX_ATTEMPTS = 3
LEASE_SECONDS = 60
BACKOFF_SECONDS = 5


def utcnow():
    return datetime.now(timezone.utc)


def create_thumbnail_job(db: Session, file_id: int) -> ThumbnailJob:
    job = ThumbnailJob(file_id=file_id, status="pending")
    db.add(job)
    db.flush()
    return job


def _terminal(db, job):
    job.status = "failed"
    job.lease_expires_at = None
    job.next_attempt_at = None
    db.get(File, job.file_id).thumbnail_status = "failed"


def claim_pending_job(db: Session, *, now=None) -> ThumbnailJob | None:
    now = now or utcnow()
    # Expired processing jobs are eligible even if their process was killed.
    while True:
        job = db.scalar(
            select(ThumbnailJob)
            .where(or_(
                and_(ThumbnailJob.status == "pending", or_(
                    ThumbnailJob.next_attempt_at.is_(None),
                    ThumbnailJob.next_attempt_at <= now,
                )),
                and_(ThumbnailJob.status == "processing", or_(
                    ThumbnailJob.lease_expires_at.is_(None),
                    ThumbnailJob.lease_expires_at <= now,
                )),
            ))
            .order_by(ThumbnailJob.created_at, ThumbnailJob.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if job is None:
            return None
        if job.status == "processing":
            job.last_error = "Worker lease expired"
        if job.attempts >= MAX_ATTEMPTS:
            _terminal(db, job)
            db.flush()
            continue
        job.status = "processing"
        job.attempts += 1
        job.next_attempt_at = None
        job.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
        db.flush()
        return job


def owned_claim(db: Session, job_id: int, attempt: int, *, now=None):
    """Lock and fence completion/publication against a reclaimed attempt."""
    now = now or utcnow()
    return db.scalar(select(ThumbnailJob).where(
        ThumbnailJob.id == job_id,
        ThumbnailJob.status == "processing",
        ThumbnailJob.attempts == attempt,
        ThumbnailJob.lease_expires_at > now,
    ).with_for_update())


def mark_thumbnail_ready(db: Session, job_id: int, attempt: int) -> bool:
    job = owned_claim(db, job_id, attempt)
    if job is None:
        return False
    job.status = "ready"
    job.lease_expires_at = None
    job.next_attempt_at = None
    job.last_error = None
    db.get(File, job.file_id).thumbnail_status = "ready"
    db.flush()
    return True


def mark_thumbnail_failed(db: Session, job_id: int, attempt: int, error: str, *, now=None) -> bool:
    now = now or utcnow()
    job = owned_claim(db, job_id, attempt, now=now)
    if job is None:
        return False
    job.last_error = error[:500]
    if job.attempts >= MAX_ATTEMPTS:
        _terminal(db, job)
    else:
        job.status = "pending"
        job.lease_expires_at = None
        job.next_attempt_at = now + timedelta(seconds=BACKOFF_SECONDS * 2 ** (job.attempts - 1))
    db.flush()
    return True
