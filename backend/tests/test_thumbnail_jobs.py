from datetime import timedelta
from pathlib import Path
import os
import subprocess

import pytest
from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import File, ThumbnailJob
from app.repositories import thumbnail_jobs as jobs
from app import worker
from app.services import storage, thumbnails


@pytest.fixture
def queue(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'jobs.db'}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(worker, "SessionLocal", sessions)
    monkeypatch.setattr(storage, "STORAGE_DIR", tmp_path / "originals")
    monkeypatch.setattr(thumbnails, "THUMBNAIL_DIR", tmp_path / "thumbnails")
    storage.STORAGE_DIR.mkdir()
    digest = "a" * 64
    with Image.new("RGB", (400, 300), "blue") as image:
        image.save(storage.STORAGE_DIR / digest, format="PNG")
    with sessions.begin() as db:
        file = File(sha256=digest, size_bytes=10, storage_key=digest)
        db.add(file)
        db.flush()
        job_id = jobs.create_thumbnail_job(db, file.id).id
    yield sessions, job_id, digest, url
    engine.dispose()


def test_retries_backoff_then_terminal_failure(queue):
    sessions, job_id, _, _ = queue
    now = jobs.utcnow()
    for attempt in range(1, 4):
        with sessions.begin() as db:
            job = jobs.claim_pending_job(db, now=now)
            assert job.attempts == attempt
            assert jobs.mark_thumbnail_failed(db, job_id, attempt, "bad image", now=now)
        if attempt < 3:
            delay = jobs.BACKOFF_SECONDS * 2 ** (attempt - 1)
            with sessions.begin() as db:
                assert jobs.claim_pending_job(db, now=now + timedelta(seconds=delay - 1)) is None
            now += timedelta(seconds=delay)
    with sessions.begin() as db:
        job = db.get(ThumbnailJob, job_id)
        assert job.status == "failed"
        assert job.last_error == "bad image"
        assert job.next_attempt_at is None
        assert db.get(File, job.file_id).thumbnail_status == "failed"
        assert jobs.claim_pending_job(db, now=now + timedelta(days=1)) is None


def test_expired_claim_fences_old_worker(queue):
    sessions, job_id, _, _ = queue
    now = jobs.utcnow()
    with sessions.begin() as db:
        assert jobs.claim_pending_job(db, now=now).attempts == 1
    with sessions.begin() as db:
        assert jobs.claim_pending_job(db, now=now + timedelta(seconds=1)) is None
    later = now + timedelta(seconds=jobs.LEASE_SECONDS + 1)
    with sessions.begin() as db:
        assert jobs.claim_pending_job(db, now=later).attempts == 2
        assert not jobs.mark_thumbnail_ready(db, job_id, 1)
        assert not jobs.mark_thumbnail_failed(db, job_id, 1, "old failure", now=later)
        assert db.get(ThumbnailJob, job_id).attempts == 2


def test_repeated_worker_crashes_stop_at_attempt_limit(queue):
    sessions, job_id, _, _ = queue
    now = jobs.utcnow()
    for attempt in range(1, 4):
        with sessions.begin() as db:
            assert jobs.claim_pending_job(db, now=now).attempts == attempt
        now += timedelta(seconds=jobs.LEASE_SECONDS + 1)
    with sessions.begin() as db:
        assert jobs.claim_pending_job(db, now=now) is None
        job = db.get(ThumbnailJob, job_id)
        assert job.status == "failed"
        assert db.get(File, job.file_id).thumbnail_status == "failed"


def test_worker_success_and_failure(queue, monkeypatch):
    sessions, job_id, digest, _ = queue
    original = storage.STORAGE_DIR / digest
    original.write_bytes(b"not an image")
    assert worker.process_one_job()
    with sessions.begin() as db:
        job = db.get(ThumbnailJob, job_id)
        assert job.status == "pending"
        assert job.attempts == 1
        assert job.last_error
        job.next_attempt_at = jobs.utcnow() - timedelta(seconds=1)
    with Image.new("RGB", (400, 300)) as image:
        image.save(original, format="PNG")
    assert worker.process_one_job()
    with sessions.begin() as db:
        assert db.get(ThumbnailJob, job_id).status == "ready"
    with Image.open(thumbnails.thumbnail_path(digest)) as image:
        assert image.format == "WEBP"
        assert image.size == (200, 150)
    assert not worker.process_one_job()


def test_stale_worker_cannot_publish(queue, monkeypatch):
    sessions, job_id, digest, _ = queue
    def lose_lease(*args, **kwargs):
        path = kwargs["output_path"]
        path.write_bytes(b"stale output")
        with sessions.begin() as db:
            db.get(ThumbnailJob, job_id).lease_expires_at = jobs.utcnow() - timedelta(seconds=1)
        with sessions.begin() as db:
            jobs.claim_pending_job(db)
        return path
    monkeypatch.setattr(thumbnails, "generate_thumbnail", lose_lease)
    assert worker.process_one_job()
    assert not thumbnails.thumbnail_path(digest).exists()
    with sessions.begin() as db:
        assert db.get(ThumbnailJob, job_id).attempts == 2
        assert db.get(ThumbnailJob, job_id).status == "processing"


def test_process_death_mid_render_recovers(queue):
    sessions, job_id, digest, url = queue
    # Exit without finally blocks during rendering, after the durable claim.
    script = '''
import os, sys
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app import worker
from app.services import storage, thumbnails
worker.SessionLocal = sessionmaker(create_engine(sys.argv[1]))
storage.STORAGE_DIR = Path(sys.argv[2])
thumbnails.THUMBNAIL_DIR = Path(sys.argv[3])
def die(*args, **kwargs):
    kwargs["output_path"].write_bytes(b"partial image")
    os._exit(9)
thumbnails.generate_thumbnail = die
worker.process_one_job()
'''
    result = subprocess.run(
        [os.sys.executable, "-c", script, url, str(storage.STORAGE_DIR), str(thumbnails.THUMBNAIL_DIR)],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=15,
    )
    assert result.returncode == 9, result.stderr.decode()
    assert not thumbnails.thumbnail_path(digest).exists()
    with sessions.begin() as db:
        job = db.get(ThumbnailJob, job_id)
        assert job.status == "processing"
        job.lease_expires_at = jobs.utcnow() - timedelta(seconds=1)
    assert worker.process_one_job()
    with sessions.begin() as db:
        assert db.get(ThumbnailJob, job_id).status == "ready"
        assert db.get(ThumbnailJob, job_id).attempts == 2
    with Image.open(thumbnails.thumbnail_path(digest)) as image:
        assert image.format == "WEBP"


def test_postgres_skip_locked_claims_are_exclusive():
    from uuid import uuid4
    from sqlalchemy import text
    from sqlalchemy.engine import make_url
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL required for PostgreSQL locking test")
    parsed = make_url(url)
    if parsed.database != "upload_service_test" or parsed.get_backend_name() != "postgresql":
        pytest.fail("Use the dedicated PostgreSQL upload_service_test database")
    schema = "thumbnail_test_" + uuid4().hex
    admin = create_engine(url)
    engine = None
    try:
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
        Base.metadata.create_all(engine)
        sessions = sessionmaker(engine)
        with sessions.begin() as db:
            file = File(sha256="b" * 64, size_bytes=1, storage_key="b" * 64)
            db.add(file)
            db.flush()
            job_id = jobs.create_thumbnail_job(db, file.id).id
        with sessions() as first, sessions() as second:
            claimed = jobs.claim_pending_job(first)
            assert claimed.id == job_id
            assert jobs.claim_pending_job(second) is None
            second.rollback()
            first.commit()
            assert jobs.claim_pending_job(second) is None
            second.rollback()
        with sessions.begin() as db:
            db.get(ThumbnailJob, job_id).lease_expires_at = jobs.utcnow() - timedelta(seconds=1)
        with sessions.begin() as db:
            assert jobs.claim_pending_job(db).attempts == 2
        with sessions.begin() as db:
            assert not jobs.mark_thumbnail_ready(db, job_id, 1)
            assert jobs.mark_thumbnail_ready(db, job_id, 2)
    finally:
        if engine is not None:
            engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()
