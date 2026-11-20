from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session
from app.services.thumbnails import thumbnail_path
from app.auth import AuthenticatedUser, get_current_user
from app.database import get_db
from app.repositories.files import get_owned_user_file
from app.services import storage, thumbnails
from app.services.thumbnail_cache import thumbnail_cache, thumbnail_cache_key


router = APIRouter()

def _get_owned_file(db: Session, user_id: str, reference_id: int):
    result = get_owned_user_file(db, user_id, reference_id)
    if result is None:
        raise HTTPException(404, detail="File not found")
    return result
# this checks if a file belong to user
# after authenticating it returns user-reference ID, orginal filename and size stored content type.

# descibes the file by useing it metadata
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


# authenticate the caller, checks referce ownership and resolves the phyical storage path and check path exists.
# the actual file
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

@router.get("/api/v1/files/{file_id}/thumbnail")
def get_thumbnail(
    file_id: int,
    db: Session = Depends(get_db),
    user: AuthenticatedUser = Depends(get_current_user)
):
    reference, stored_file = _get_owned_file(db, user.user_id, file_id)

    if stored_file.thumbnail_status == "failed":
        return Response(
            content=(
                '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200" '
                'viewBox="0 0 200 200"><rect width="200" height="200" fill="#eee"/>'
                '<text x="100" y="100" text-anchor="middle" font-family="sans-serif" '
                'font-size="14" fill="#555">Preview unavailable</text></svg>'
            ),
            media_type="image/svg+xml",
            headers={
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
                "X-Thumbnail-Status": "failed",
            },
        )

    if stored_file.thumbnail_status != "ready":
        return JSONResponse(
            status_code=202,
            content={"thumbnail_status": stored_file.thumbnail_status},
            headers={"Cache-Control": "private, no-store"},
        )

    key = thumbnail_cache_key(stored_file.sha256)
    data = thumbnail_cache.get(key)
    if data is None:
        storage_root = thumbnails.THUMBNAIL_DIR.resolve()
        path = thumbnail_path(stored_file.sha256).resolve()
        if storage_root not in path.parents or not path.is_file():
            raise HTTPException(404, detail="Thumbnail not found")
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            raise HTTPException(404, detail="Thumbnail not found") from None
        thumbnail_cache.put(key, data)

    return Response(
        content=data,
        media_type="image/webp",
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/api/v1/metrics/thumbnail-cache")
def get_thumbnail_cache_metrics(user: AuthenticatedUser = Depends(get_current_user)):
    # Aggregate process-local counters; no users, file IDs, or hashes are exposed.
    return JSONResponse(thumbnail_cache.stats(), headers={"Cache-Control": "private, no-store"})
