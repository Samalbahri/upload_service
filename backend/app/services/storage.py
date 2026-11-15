import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterable

from fastapi import UploadFile

STORAGE_DIR = Path("storage/originals")
CHUNK_SIZE = 1024 * 1024
MAX_FILE_SIZE = 50 * 1024 * 1024


@dataclass
class StagedUpload:
    path: Path
    sha256: str
    size_bytes: int
    content_type: str = "application/octet-stream"


class ContentCollision(ValueError):
    pass


def detect_content_type(path: Path) -> str:
    with path.open("rb") as staged_file:
        header = staged_file.read(12)

    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if header.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


def verify_same_content(left: Path, right: Path) -> None:
    # Compare bytes as well as hashes: never silently reuse different content.
    with left.open("rb") as first, right.open("rb") as second:
        while True:
            chunk = first.read(CHUNK_SIZE)
            if chunk != second.read(CHUNK_SIZE):
                raise ContentCollision("Content hash collision or corrupt stored object")
            if not chunk:
                return


def store_new_file(staged: StagedUpload) -> str:
    destination = STORAGE_DIR / staged.sha256
    try:
        # Staging on the same filesystem makes publication atomic. Unlike replace,
        # link cannot overwrite a concurrent upload's existing object.
        os.link(staged.path, destination)
    except FileExistsError:
        verify_same_content(staged.path, destination)
    return staged.sha256


async def stage_stream(chunks: AsyncIterable[bytes]) -> StagedUpload:
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    hasher = hashlib.sha256()
    size_bytes = 0
    with tempfile.NamedTemporaryFile(mode="wb", dir=STORAGE_DIR, prefix=".upload-", delete=False) as temp:
        temp_path = Path(temp.name)
        try:
            async for incoming in chunks:
                size_bytes += len(incoming)
                if size_bytes > MAX_FILE_SIZE:
                    raise ValueError("File exceeds 50 MiB limit")
                # A view avoids copying a potentially large incoming chunk.
                view = memoryview(incoming)
                for offset in range(0, len(view), CHUNK_SIZE):
                    chunk = view[offset:offset + CHUNK_SIZE]
                    hasher.update(chunk)
                    temp.write(chunk)
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise
    return StagedUpload(
        temp_path,
        hasher.hexdigest(),
        size_bytes,
        detect_content_type(temp_path),
    )


async def stage_upload(file: UploadFile) -> StagedUpload:
    async def chunks():
        while chunk := await file.read(CHUNK_SIZE):
            yield chunk
    return await stage_stream(chunks())
