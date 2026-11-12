from sqlalchemy.orm import Session
from app.models import File
from sqlalchemy import select

def get_file_by_hash(db: Session, file_hash: str) -> File | None:
    statement = select(File).where(File.sha256 == file_hash)
    return db.execute(statement).scalar_one_or_none()

from app.models import UserFile


def create_user_file(
    db: Session,
    user_id: str,
    file_id: int,
    filename: str,
) -> UserFile:
    user_file = UserFile(
        user_id=user_id,
        file_id=file_id,
        filename=filename,
    )

    db.add(user_file)
    db.flush()

    return user_file

def create_file(
    db: Session,
    sha256: str,
    size_bytes: int,
    storage_key: str,
) -> File:
    file = File(
        sha256=sha256,
        size_bytes=size_bytes,
        storage_key=storage_key,
    )

    db.add(file)
    db.flush()

    return file