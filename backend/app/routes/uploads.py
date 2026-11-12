
from pathlib import Path

from fastapi import APIRouter, Depends, File as UploadField
from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.repositories.files import (
    create_file,
    create_user_file,
    get_file_by_hash,
)
from app.repositories.quota import charge_quota
from app.services.storage import stage_upload, store_new_file

router = APIRouter()


@router.post("/uploads")
async def upload_image(
    file: UploadFile = UploadField(...),
    db: Session = Depends(get_db),
):
    staged = None

    try:
        staged = await stage_upload(file)

        existing_file = get_file_by_hash(db, staged.sha256)

        if not charge_quota(db, "demo-user", staged.size_bytes):
            raise HTTPException(
                status_code=413,
                detail="Storage quota exceeded",
            )

        if existing_file is not None:
            stored_file = existing_file
        else:
            storage_key = store_new_file(staged)

            stored_file = create_file(
                db=db,
                sha256=staged.sha256,
                size_bytes=staged.size_bytes,
                storage_key=storage_key,
            )

        user_file = create_user_file(
            db=db,
            user_id="demo-user",
            file_id=stored_file.id,
            filename=file.filename or "upload",
        )

        db.commit()

        return {
            "id": user_file.id,
            "file_id": stored_file.id,
            "sha256": staged.sha256,
            "size_bytes": staged.size_bytes,
        }

    except ValueError as exc:
        db.rollback()
        raise HTTPException(
            status_code=413,
            detail=str(exc),
        ) from exc

    except Exception:
        db.rollback()
        raise

    finally:
        if staged is not None:
            staged.path.unlink(missing_ok=True)

        await file.close()
