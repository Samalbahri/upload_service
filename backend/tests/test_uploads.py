from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import logging
import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.database import Base, get_db
from app.main import app
from app.models import File, Quota, UserFile
from app.services import storage
from app.routes import uploads
from app.logging_config import JsonFormatter


DEMO_TOKEN = "demo-test-token"
OTHER_TOKEN = "other-test-token"


def _token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


@pytest.fixture
def upload_system(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False, "timeout": 15})
    Base.metadata.create_all(engine)
    original_dir = tmp_path / "originals"
    monkeypatch.setattr(storage, "STORAGE_DIR", original_dir)
    monkeypatch.setattr(uploads, "STORAGE_DIR", original_dir)
    monkeypatch.setenv(
        "UPLOAD_API_TOKEN_HASHES",
        json.dumps({
            "demo-user": _token_hash(DEMO_TOKEN),
            "other-user": _token_hash(OTHER_TOKEN),
        }),
    )
    def sessions():
        with Session(engine, expire_on_commit=False) as db:
            yield db
    app.dependency_overrides[get_db] = sessions
    with Session(engine) as db:
        db.add(Quota(user_id="demo-user", limit_bytes=10, used_bytes=0, reserved_bytes=0))
        db.commit()
    yield engine, TestClient(
        app,
        headers={"Authorization": f"Bearer {DEMO_TOKEN}"},
    ), original_dir
    app.dependency_overrides.clear()
    engine.dispose()


def test_duplicate_upload_has_two_references_one_object(upload_system):
    engine, client, directory = upload_system
    first = client.post("/api/v1/files", content=b"hello")
    second = client.post("/api/v1/files", content=b"hello")
    assert first.status_code == second.status_code == 201
    assert first.json()["file_id"] != second.json()["file_id"]
    assert "sha256" not in first.json()
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(File)) == 1
        assert db.scalar(select(func.count()).select_from(UserFile)) == 2
        assert db.get(Quota, "demo-user").used_bytes == 10
    assert len(list(directory.iterdir())) == 1


def test_concurrent_uploads_cannot_exceed_quota(upload_system):
    engine, _, directory = upload_system
    barrier = threading.Barrier(2)
    def upload():
        with TestClient(
            app,
            headers={"Authorization": f"Bearer {DEMO_TOKEN}"},
        ) as client:
            barrier.wait()
            return client.post("/api/v1/files", content=b"123456")
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: upload(), range(2)))
    assert sorted(r.status_code for r in responses) == [201, 413]
    assert next(r for r in responses if r.status_code == 413).json()["detail"]["remaining_bytes"] == 4
    with Session(engine) as db:
        assert db.get(Quota, "demo-user").used_bytes == 6
        assert db.scalar(select(func.count()).select_from(UserFile)) == 1
    assert len(list(directory.iterdir())) == 1


def test_database_failure_rolls_back_quota(upload_system, monkeypatch):
    engine, _, directory = upload_system
    def fail(*args, **kwargs):
        raise RuntimeError("database insert failed")
    monkeypatch.setattr(uploads, "create_user_file", fail)
    with TestClient(
        app,
        raise_server_exceptions=False,
        headers={"Authorization": f"Bearer {DEMO_TOKEN}"},
    ) as client:
        assert client.post("/api/v1/files", content=b"hello").status_code == 500
    with Session(engine) as db:
        assert db.get(Quota, "demo-user").used_bytes == 0
        assert db.scalar(select(func.count()).select_from(File)) == 0
    # An orphan is safe to reuse; deleting it here could race another upload.
    assert len(list(directory.iterdir())) == 1


def test_upload_requires_a_valid_bearer_token(upload_system):
    _, _, _ = upload_system
    with TestClient(app) as client:
        missing = client.post("/api/v1/files", content=b"hello")
        invalid = client.post(
            "/api/v1/files",
            content=b"hello",
            headers={"Authorization": "Bearer invalid-token"},
        )

    assert missing.status_code == invalid.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"


def test_metadata_and_original_are_owned_and_return_content_type(upload_system):
    _, client, _ = upload_system
    content = b"\xff\xd8\xffjpeg"
    uploaded = client.post(
        "/api/v1/files",
        content=content,
        headers={"X-Filename": "photo.jpg", "Content-Type": "text/plain"},
    )
    assert uploaded.status_code == 201
    file_id = uploaded.json()["file_id"]

    metadata = client.get(f"/api/v1/files/{file_id}")
    original = client.get(f"/api/v1/files/{file_id}/original")

    assert metadata.status_code == 200
    assert metadata.json()["content_type"] == "image/jpeg"
    assert metadata.json()["filename"] == "photo.jpg"
    assert metadata.headers["cache-control"] == "private, no-store"
    assert original.status_code == 200
    assert original.content == content
    assert original.headers["content-type"] == "image/jpeg"
    assert original.headers["cache-control"] == "private, no-store"
    assert original.headers["x-content-type-options"] == "nosniff"

    other_client = TestClient(
        app,
        headers={"Authorization": f"Bearer {OTHER_TOKEN}"},
    )
    assert other_client.get(f"/api/v1/files/{file_id}").status_code == 404
    assert other_client.get(f"/api/v1/files/{file_id}/original").status_code == 404


def test_request_id_and_upload_events_are_structured(upload_system, caplog):
    _, client, _ = upload_system
    with caplog.at_level(logging.INFO):
        response = client.post(
            "/api/v1/files",
            content=b"hello",
            headers={"X-Request-ID": "trace-123"},
        )

    assert response.status_code == 201
    assert response.headers["x-request-id"] == "trace-123"
    records = [
        record for record in caplog.records
        if getattr(record, "request_id", None) == "trace-123"
    ]
    request_record = next(record for record in records if record.event == "request_completed")
    upload_record = next(record for record in records if record.event == "upload_completed")
    request_json = json.loads(JsonFormatter().format(request_record))
    upload_json = json.loads(JsonFormatter().format(upload_record))
    assert request_json["status_code"] == 201
    assert upload_json["upload_size_bytes"] == 5
    assert upload_json["file_id"] == response.json()["file_id"]
