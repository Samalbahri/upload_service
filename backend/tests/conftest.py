import os
import pytest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session


@pytest.fixture
def db():
    test_url = os.environ["TEST_DATABASE_URL"]

    if not test_url.endswith("/upload_service_test"):
        raise ValueError("Tests must use upload_service_test")

    engine = create_engine(test_url)

    try:
        with engine.connect() as connection:
            transaction = connection.begin()

            try:
                with Session(bind=connection) as session:
                    yield session
            finally:
                transaction.rollback()
    finally:
        engine.dispose()