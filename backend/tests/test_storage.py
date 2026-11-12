
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
