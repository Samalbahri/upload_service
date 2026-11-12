import os
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Quota
from app.repositories.quota import get_quota


def test_get_quota():
    test_url = os.environ["TEST_DATABASE_URL"]

    if not test_url.endswith("/upload_service_test"):
        raise ValueError("Tests must use upload_service_test")

    engine = create_engine(test_url)

    try:
        with engine.connect() as connection:
            transaction = connection.begin()

            try:
                with Session(bind=connection) as db:
                    user_id = f"test-user-{uuid4().hex}"

                    quota = Quota(
                        user_id=user_id,
                        limit_bytes=10_000,
                        used_bytes=2_000,
                        reserved_bytes=500,
                    )

                    db.add(quota)
                    db.flush()

                    # Remove cached objects so the repository
                    # must retrieve the quota from PostgreSQL.
                    db.expunge_all()

                    result = get_quota(db, user_id)

                    assert result is not None
                    assert result.user_id == user_id
                    assert result.limit_bytes == 10_000
                    assert result.used_bytes == 2_000
                    assert result.reserved_bytes == 500

            finally:
                transaction.rollback()
    finally:
        engine.dispose()