
import hashlib
import tempfile
from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile


import os

STORAGE_DIR = Path("storage/originals")


def store_new_file(staged: StagedUpload) -> str:
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)

    storage_key = staged.sha256
    destination = STORAGE_DIR / storage_key

    if destination.exists():
        staged.path.unlink(missing_ok=True)
    else:
        os.replace(staged.path, destination)

    return storage_key






CHUNK_SIZE = 1024 * 1024
MAX_FILE_SIZE = 50 * 1024 * 1024


@dataclass
class StagedUpload:
    path: Path
    sha256: str
    size_bytes: int


async def stage_upload(file: UploadFile) -> StagedUpload:
    hasher = hashlib.sha256()
    size_bytes = 0

    with tempfile.NamedTemporaryFile(
        mode="wb",
        delete=False,
    ) as temp:
        temp_path = Path(temp.name)

        try:
            while True:
                chunk = await file.read(CHUNK_SIZE)

                if not chunk:
                    break

                size_bytes += len(chunk)

                if size_bytes > MAX_FILE_SIZE:
                    raise ValueError("File exceeds 50 MiB limit")

                hasher.update(chunk)
                temp.write(chunk)

        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise

    return StagedUpload(
        path=temp_path,
        sha256=hasher.hexdigest(),
        size_bytes=size_bytes,
    )
