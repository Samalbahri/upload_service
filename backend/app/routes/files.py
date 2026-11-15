from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.auth import AuthenticatedUser, get_current_user
from app.database import get_db
from app.repositories.files import get_owned_user_file
from app.services import storage


router = APIRouter()


def _get_owned_file(db: Session, user_id: str, reference_id: int):
    result = get_owned_user_file(db, user_id, reference_id)
    if result is None:
        raise HTTPException(404, detail="File not found")
    return result


@router.get("/api/v1/files/{file_id}")
def get_file_metadata(
    file_id: int,
    response: Response,
    db: Session = Depends(get_db),
    user: AuthenticatedUser = Depends(get_current_user),
):
    reference, stored_file = _get_owned_file(db, user.user_id, file_id)
    response.headers["Cache-Control"] = "private, no-store"
    return {
        "file_id": reference.id,
        "filename": reference.filename,
        "size_bytes": stored_file.size_bytes,
        "content_type": stored_file.content_type,
        "thumbnail_status": stored_file.thumbnail_status,
        "created_at": reference.created_at,
    }


@router.get("/api/v1/files/{file_id}/original")
def get_original_file(
    file_id: int,
    db: Session = Depends(get_db),
    user: AuthenticatedUser = Depends(get_current_user),
):
    reference, stored_file = _get_owned_file(db, user.user_id, file_id)
    storage_root = storage.STORAGE_DIR.resolve()
    original_path = (storage_root / stored_file.storage_key).resolve()
    if storage_root not in original_path.parents or not original_path.is_file():
        raise HTTPException(404, detail="File not found")

    return FileResponse(
        original_path,
        media_type=stored_file.content_type,
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )