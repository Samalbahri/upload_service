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
    from app.routes import files as file_routes
    from app.services.thumbnail_cache import ThumbnailCache
    monkeypatch.setattr(file_routes, "thumbnail_cache", ThumbnailCache())
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


def test_thumbnail_pending_ready_and_owned(upload_system, tmp_path, monkeypatch):
    from io import BytesIO
    from PIL import Image
    from app.services import thumbnails

    engine, client, _ = upload_system
    monkeypatch.setattr(thumbnails, "THUMBNAIL_DIR", tmp_path / "thumbnails")
    uploaded = client.post("/api/v1/files", content=b"hello")
    assert uploaded.status_code == 201
    reference_id = uploaded.json()["file_id"]
    url = f"/api/v1/files/{reference_id}/thumbnail"

    pending = client.get(url)
    assert pending.status_code == 202
    assert pending.json() == {"thumbnail_status": "pending"}
    assert pending.headers["cache-control"] == "private, no-store"
    other_headers = {"Authorization": f"Bearer {OTHER_TOKEN}"}
    assert client.get(url, headers=other_headers).status_code == 404
    with TestClient(app) as anonymous:
        assert anonymous.get(url).status_code == 401

    original = tmp_path / "source.png"
    with Image.new("RGB", (400, 300), "red") as image:
        image.save(original)
    with Session(engine) as db:
        stored_file = db.get(File, db.get(UserFile, reference_id).file_id)
        stored_file_id = stored_file.id
        path = thumbnails.generate_thumbnail(original, stored_file.sha256)
        stored_file.thumbnail_status = "ready"
        db.commit()

    ready = client.get(url)
    assert ready.status_code == 200
    assert ready.content == path.read_bytes()
    assert ready.headers["content-type"] == "image/webp"
    assert ready.headers["cache-control"] == "private, no-store"
    assert ready.headers["x-content-type-options"] == "nosniff"
    with Image.open(BytesIO(ready.content)) as image:
        assert image.format == "WEBP"
        assert image.size == (200, 150)
    assert client.get(url, headers=other_headers).status_code == 404

    from app.routes import files as file_routes
    stats = client.get("/api/v1/metrics/thumbnail-cache")
    assert stats.json()["misses"] == 1
    assert stats.json()["hits"] == 0
    # No disk object: the second fetch must come from cache.
    path.unlink()
    cached = client.get(url)
    assert cached.status_code == 200
    assert cached.content == ready.content
    assert client.get("/api/v1/metrics/thumbnail-cache").json()["hits"] == 1
    assert client.get(url, headers=other_headers).status_code == 404
    assert client.get("/api/v1/metrics/thumbnail-cache").json()["hits"] == 1
    # Simulate deleting this user's reference: cached bytes give no access.
    with Session(engine) as db:
        reference = db.get(UserFile, reference_id)
        db.delete(reference)
        db.commit()
    assert client.get(url).status_code == 404
    from app.services.thumbnail_cache import ThumbnailCache
    file_routes.thumbnail_cache = ThumbnailCache()
    # Restore the reference to exercise a true storage miss.
    with Session(engine) as db:
        db.add(UserFile(id=reference_id, user_id="demo-user", file_id=stored_file_id, filename="upload"))
        db.commit()
    missing = client.get(url)
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Thumbnail not found"


def test_failed_thumbnail_serves_placeholder_and_queryable_state(upload_system):
    engine, client, _ = upload_system
    uploaded = client.post("/api/v1/files", content=b"hello")
    reference_id = uploaded.json()["file_id"]
    with Session(engine) as db:
        stored_file = db.get(File, db.get(UserFile, reference_id).file_id)
        stored_file.thumbnail_status = "failed"
        db.commit()
    response = client.get(f"/api/v1/files/{reference_id}/thumbnail")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/svg+xml"
    assert response.headers["x-thumbnail-status"] == "failed"
    assert b"Preview unavailable" in response.content
    assert client.get(f"/api/v1/files/{reference_id}").json()["thumbnail_status"] == "failed"
    assert client.get(f"/api/v1/files/{reference_id}/thumbnail", headers={
        "Authorization": f"Bearer {OTHER_TOKEN}",
    }).status_code == 404


def test_cache_metrics_require_authentication_and_exclude_pending_and_failed(upload_system):
    engine, client, _ = upload_system
    reference_id = client.post("/api/v1/files", content=b"hello").json()["file_id"]
    url = f"/api/v1/files/{reference_id}/thumbnail"
    assert client.get(url).status_code == 202
    with Session(engine) as db:
        db.get(File, db.get(UserFile, reference_id).file_id).thumbnail_status = "failed"
        db.commit()
    assert client.get(url).headers["x-thumbnail-status"] == "failed"
    assert client.get("/api/v1/metrics/thumbnail-cache").json() == {
        "hits": 0, "misses": 0, "size_bytes": 0, "entries": 0,
    }
    with TestClient(app) as anonymous:
        assert anonymous.get("/api/v1/metrics/thumbnail-cache").status_code == 401
