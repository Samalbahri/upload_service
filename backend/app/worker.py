import logging
import os
import tempfile
import time
from pathlib import Path

from app.database import SessionLocal
from app.logging_config import configure_json_logging
from app.models import File
from app.repositories.thumbnail_jobs import (
    claim_pending_job, mark_thumbnail_failed, mark_thumbnail_ready, owned_claim,
)
from app.services import storage, thumbnails

logger = logging.getLogger(__name__)


def process_one_job() -> bool:
    with SessionLocal.begin() as db:
        job = claim_pending_job(db)
        if job is None:
            return False
        stored_file = db.get(File, job.file_id)
        job_id, attempt = job.id, job.attempts
        storage_key, sha256 = stored_file.storage_key, stored_file.sha256

    try:
        storage_root = storage.STORAGE_DIR.resolve()
        original_path = (storage_root / storage_key).resolve()
        if storage_root not in original_path.parents or not original_path.is_file():
            raise ValueError("Original file missing or invalid storage path")

        destination = thumbnails.thumbnail_path(sha256)
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Each attempt renders privately. A stale worker cannot overwrite output.
        with tempfile.TemporaryDirectory(dir=destination.parent, prefix=".attempt-") as attempt_dir:
            candidate = thumbnails.generate_thumbnail(
                original_path, sha256, output_path=Path(attempt_dir) / "thumbnail.webp",
            )
            with SessionLocal.begin() as db:
                if owned_claim(db, job_id, attempt) is None:
                    logger.warning("Discarding expired thumbnail attempt %s/%s", job_id, attempt)
                    return True
                # Hold the job lock only for publication and the ready transition.
                os.replace(candidate, destination)
                if not mark_thumbnail_ready(db, job_id, attempt):
                    raise RuntimeError("Thumbnail lease expired during publication")
        logger.info("Thumbnail ready for job %s", job_id)
    except Exception as exc:
        with SessionLocal.begin() as db:
            mark_thumbnail_failed(db, job_id, attempt, f"{type(exc).__name__}: {exc}")
        logger.exception("Thumbnail attempt failed for job %s", job_id)
    return True


def main() -> None:
    configure_json_logging()
    logger.info("Thumbnail worker started")
    while True:
        try:
            if not process_one_job():
                time.sleep(1)
        except Exception:
            logger.exception("Thumbnail queue unavailable")
            time.sleep(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Thumbnail worker stopped")
