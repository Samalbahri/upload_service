from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models import File
from app.repositories.files import create_file, get_file_by_hash


def test_create_file():
    # Use a dedicated test database, never the development database.
    import os

    test_url = os.environ["TEST_DATABASE_URL"]

    if not test_url.endswith("/upload_service_test"):
        raise ValueError("Tests must use upload_service_test")

    engine = create_engine(test_url)

    try:
        with engine.connect() as connection:
            transaction = connection.begin()

            try:
                with Session(bind=connection) as db:
                    unique_id = uuid4().hex

                    file = create_file(
                        db=db,
                        sha256=unique_id.ljust(64, "0"),
                        size_bytes=1024,
                        storage_key=f"test/{unique_id}",
                    )

                    assert file.id is not None

                    saved_file = db.scalar(
                        select(File).where(File.id == file.id)
                    )

                    assert saved_file is not None
                    assert saved_file.sha256 == file.sha256
                    assert saved_file.size_bytes == 1024

            finally:
                transaction.rollback()
    finally:
        engine.dispose()

def test_get_file_by_hash(db):
    # 1. Create a file in the test database
    created_file = create_file(
        db=db,
        sha256="abc123",
        size_bytes=1024,
        storage_key="test/image.jpg",
    )

    # 2. Search for that file using its hash
    found_file = get_file_by_hash(db, "abc123")

    # 3. Verify we found the correct file
    assert found_file is not None
    assert found_file.id == created_file.id