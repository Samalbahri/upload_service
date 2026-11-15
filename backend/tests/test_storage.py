
import hashlib
from io import BytesIO

import pytest
from fastapi import UploadFile

from app.services.storage import stage_upload


@pytest.mark.anyio
async def test_stage_upload():
    content = b"hello upload service"

    upload = UploadFile(
        file=BytesIO(content),
        filename="test.txt",
    )

    staged = await stage_upload(upload)

    try:
        assert staged.size_bytes == len(content)
        assert staged.sha256 == hashlib.sha256(content).hexdigest()
        assert staged.path.read_bytes() == content
    finally:
        staged.path.unlink(missing_ok=True)
        await upload.close()


@pytest.mark.anyio
async def test_stage_stream_detects_content_type_from_bytes(tmp_path, monkeypatch):
    from app.services import storage

    monkeypatch.setattr(storage, "STORAGE_DIR", tmp_path)

    async def chunks():
        yield b"\x89PNG\r\n\x1a\nimage-data"

    staged = await storage.stage_stream(chunks())
    try:
        assert staged.content_type == "image/png"
    finally:
        staged.path.unlink(missing_ok=True)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_50_mib_stream_has_bounded_python_memory(tmp_path, monkeypatch):
    import tracemalloc
    from app.services import storage
    monkeypatch.setattr(storage, "STORAGE_DIR", tmp_path)
    async def chunks():
        for _ in range(50):
            yield b"x" * storage.CHUNK_SIZE
    tracemalloc.start()
    try:
        staged = await storage.stage_stream(chunks())
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    try:
        assert staged.size_bytes == 50 * 1024 * 1024
        assert peak < 4 * 1024 * 1024
        print(f"50 MiB staging peak Python allocation: {peak} bytes")
    finally:
        staged.path.unlink()


@pytest.mark.anyio
async def test_oversized_stream_cleans_temporary_file(tmp_path, monkeypatch):
    from app.services import storage
    monkeypatch.setattr(storage, "STORAGE_DIR", tmp_path)
    monkeypatch.setattr(storage, "MAX_FILE_SIZE", 3)
    async def chunks():
        yield b"1234"
    with pytest.raises(ValueError, match="limit"):
        await storage.stage_stream(chunks())
    assert list(tmp_path.iterdir()) == []


def test_collision_does_not_overwrite_original(tmp_path, monkeypatch):
    from app.services import storage
    monkeypatch.setattr(storage, "STORAGE_DIR", tmp_path)
    original = tmp_path / "same-hash"
    original.write_bytes(b"first")
    candidate = tmp_path / "candidate"
    candidate.write_bytes(b"other")
    staged = storage.StagedUpload(candidate, "same-hash", 5)
    with pytest.raises(storage.ContentCollision):
        storage.store_new_file(staged)
    assert original.read_bytes() == b"first"
