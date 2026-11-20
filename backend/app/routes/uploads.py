import logging
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from app.repositories.thumbnail_jobs import create_thumbnail_job
from app.auth import AuthenticatedUser, get_current_user
from app.database import get_db
from app.repositories.files import create_file, create_user_file, get_file_by_hash
from app.repositories.quota import charge_quota, get_quota
from app.services.storage import (
    ContentCollision, STORAGE_DIR, stage_stream, store_new_file, verify_same_content,
)

router = APIRouter()
logger = logging.getLogger(__name__)


def finish_upload(db: Session, staged, filename: str, user_id: str):
    #charge_quote uses SQL to update capacity and increase it usuage overtime
    try:
        if not charge_quota(db, user_id, staged.size_bytes):
            quota = get_quota(db, user_id)
            remaining = 0 if quota is None else quota.limit_bytes - quota.used_bytes - quota.reserved_bytes
            raise HTTPException(413, detail={"message": "Storage quota exceeded", "remaining_bytes": remaining})
    
    # checks for file if exisit get it by hash if not it creates it
        stored_file = get_file_by_hash(db, staged.sha256)
        if stored_file is None:
            storage_key = store_new_file(staged)
            try:
                # A savepoint handles identical uploads from different users:
                # the unique hash constraint chooses one shared file record.
                with db.begin_nested():
                    stored_file = create_file(
                        db,
                        staged.sha256,
                        staged.size_bytes,
                        storage_key,
                        staged.content_type,
                    )
                    create_thumbnail_job(db, stored_file.id)
            except IntegrityError:
                stored_file = get_file_by_hash(db, staged.sha256)
                if stored_file is None:
                    raise
        if stored_file.size_bytes != staged.size_bytes:
            raise ContentCollision("Stored size differs for the same hash")
        verify_same_content(staged.path, STORAGE_DIR / stored_file.storage_key)
        reference = create_user_file(db, user_id, stored_file.id, filename)
        result = {"file_id": reference.id, "size_bytes": staged.size_bytes,
              "thumbnail_status": stored_file.thumbnail_status,
              "content_type": stored_file.content_type}
        db.commit()
        return result
    except BaseException:
        db.rollback()
        raise


@router.post("/api/v1/files", status_code=201)
async def upload_image(
    request: Request,
    db: Session = Depends(get_db),
    user: AuthenticatedUser = Depends(get_current_user),
):
    staged = None
    try:
        staged = await stage_stream(request.stream())
        # write incoming bytes to temp file and update hash incrementally
        # check bytes and reject anything aobve 50mb.
        #this is suppose to return path,hash,size. content type is detected from the file extension.
        result = await run_in_threadpool(
            finish_upload,
            db,
            staged,
            request.headers.get("x-filename", "upload")[:255],
            user.user_id,
        )
        logger.info(
            "upload completed",
            extra={
                "event": "upload_completed",
                "request_id": request.state.request_id,
                "user_id": user.user_id,
                "upload_size_bytes": staged.size_bytes,
                "file_id": result["file_id"],
            },
        )
        return result
    except ContentCollision as exc:
        _log_upload_failure(request, user, staged, 409)
        raise HTTPException(409, detail=str(exc)) from exc
    except ValueError as exc:
        _log_upload_failure(request, user, staged, 413)
        raise HTTPException(413, detail=str(exc)) from exc
    except HTTPException as exc:
        _log_upload_failure(request, user, staged, exc.status_code)
        raise
    except Exception:
        _log_upload_failure(request, user, staged, 500)
        raise
    finally:
        if staged is not None:
            staged.path.unlink(missing_ok=True)


def _log_upload_failure(request: Request, user: AuthenticatedUser, staged, status_code: int) -> None:
    logger.warning(
        "upload failed",
        extra={
            "event": "upload_failed",
            "request_id": request.state.request_id,
            "user_id": user.user_id,
            "upload_size_bytes": None if staged is None else staged.size_bytes,
            "status_code": status_code,
        },
    )
