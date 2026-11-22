"""Real PostgreSQL tests: each test uses its own temporary schema."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import os
from threading import Barrier
from uuid import uuid4
from app.database import Base
import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.models import UploadRateLimit
from app.repositories.upload_rate_limits import admit_upload


@pytest.fixture
def rate_sessions():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to the PostgreSQL test database")
    parsed = make_url(url)
    if parsed.database != "upload_service_test" or parsed.get_backend_name() != "postgresql":
        pytest.fail("Use the dedicated PostgreSQL upload_service_test database")
    schema = "rate_test_" + uuid4().hex
    admin = create_engine(url)
    engine = None
    try:
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_engine(url, connect_args={"options": f"-csearch_path={schema} -clock_timeout=5000"})
        Base.metadata.create_all(engine)
        yield sessionmaker(engine)
    finally:
        if engine is not None:
            engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()


def admit(sessions, user):
    with sessions.begin() as db:
        return admit_upload(db, user)


def test_burst_and_user_isolation(rate_sessions):
    for _ in range(10):
        assert admit(rate_sessions, "alice") == 0
    assert 1 <= admit(rate_sessions, "alice") <= 60
    for _ in range(10):
        assert admit(rate_sessions, "bob") == 0
    with rate_sessions() as db:
        assert db.get(UploadRateLimit, "alice").attempts == 10
        assert db.get(UploadRateLimit, "bob").attempts == 10


def test_expired_window_resets_counter(rate_sessions):
    for _ in range(10):
        admit(rate_sessions, "alice")
    with rate_sessions.begin() as db:
        db.get(UploadRateLimit, "alice").window_start = (
            db.scalar(select(func.clock_timestamp())) - timedelta(seconds=61)
        )
    assert admit(rate_sessions, "alice") == 0
    with rate_sessions() as db:
        assert db.get(UploadRateLimit, "alice").attempts == 1


def test_two_requests_at_nine_admit_exactly_one(rate_sessions):
    for _ in range(9):
        admit(rate_sessions, "alice")
    barrier = Barrier(2)
    def request():
        barrier.wait(timeout=5)
        return admit(rate_sessions, "alice")
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(request) for _ in range(2)]
        results = [future.result(timeout=10) for future in futures]
    assert sum(result == 0 for result in results) == 1
    assert sum(1 <= result <= 60 for result in results) == 1
    with rate_sessions() as db:
        assert db.get(UploadRateLimit, "alice").attempts == 10


def test_concurrent_first_attempts_create_one_row(rate_sessions):
    barrier = Barrier(2)
    def request():
        barrier.wait(timeout=5)
        return admit(rate_sessions, "new-user")
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(request) for _ in range(2)]
        assert [future.result(timeout=10) for future in futures] == [0, 0]
    with rate_sessions() as db:
        assert db.get(UploadRateLimit, "new-user").attempts == 2

def test_quota_failure_still_counts_attempt(rate_sessions, tmp_path, monkeypatch):
    import hashlib
    import json
    from fastapi.testclient import TestClient
    from app.database import get_db
    from app.main import app
    from app.models import File, Quota, UserFile
    from app.routes import uploads
    from app.services import storage

    token = "alice-integration-token"
    monkeypatch.setenv("UPLOAD_API_TOKEN_HASHES", json.dumps({
        "alice": hashlib.sha256(token.encode()).hexdigest(),
    }))
    monkeypatch.setattr(uploads, "SessionLocal", rate_sessions)
    original_dir = tmp_path / "originals"
    monkeypatch.setattr(storage, "STORAGE_DIR", original_dir)
    monkeypatch.setattr(uploads, "STORAGE_DIR", original_dir)

    def sessions():
        with rate_sessions() as db:
            yield db

    previous_overrides = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = sessions
    try:
        with rate_sessions.begin() as db:
            db.add(Quota(user_id="alice", limit_bytes=0, used_bytes=0, reserved_bytes=0))
        for _ in range(9):
            assert admit(rate_sessions, "alice") == 0

        with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as client:
            response = client.post("/api/v1/files", content=b"hello")
            assert response.status_code == 413
            assert response.json()["detail"] == {
                "message": "Storage quota exceeded", "remaining_bytes": 0,
            }
            with rate_sessions() as db:
                assert db.get(UploadRateLimit, "alice").attempts == 10
                assert db.get(Quota, "alice").used_bytes == 0
                assert db.scalar(select(func.count()).select_from(File)) == 0
                assert db.scalar(select(func.count()).select_from(UserFile)) == 0
            assert list(original_dir.iterdir()) == []

            # The quota rollback must not undo the separate admission commit.
            response = client.post("/api/v1/files", content=b"hello")
            assert response.status_code == 429
            assert 1 <= int(response.headers["Retry-After"]) <= 60
            with rate_sessions() as db:
                assert db.get(UploadRateLimit, "alice").attempts == 10
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)
