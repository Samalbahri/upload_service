"""Create isolated load-test tables and provision temporary API users."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
from uuid import uuid4

from dotenv import dotenv_values
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

BACKEND_DIR = Path(__file__).resolve().parents[1]
LOADTEST_DIR = Path(__file__).resolve().parent


def main():
    settings = dotenv_values(LOADTEST_DIR / ".env")
    backend_settings = dotenv_values(BACKEND_DIR / ".env")
    config_path = LOADTEST_DIR / "run.json"
    if config_path.exists():
        raise ValueError("run.json already exists; inspect the previous run first")

    database_name = settings.get("LOADTEST_DATABASE_NAME")
    if database_name != "upload_service_test":
        raise ValueError("Load tests must use upload_service_test")
    database_url = os.environ.get("DATABASE_URL") or backend_settings.get("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL is missing")
    test_url = make_url(database_url).set(database=database_name)
    if test_url.get_backend_name() != "postgresql":
        raise ValueError("Load tests require PostgreSQL")
    user_count = int(settings.get("LOADTEST_USER_COUNT", "4"))
    quota_bytes = int(settings.get("LOADTEST_QUOTA_BYTES", "104857600"))
    if user_count < 4 or quota_bytes <= 0:
        raise ValueError("Use at least four users and a positive quota")

    schema_name = "loadtest_" + uuid4().hex
    isolated_url = test_url.update_query_dict({"options": f"-csearch_path={schema_name}"})
    # Set configuration before importing application models.
    os.environ["DATABASE_URL"] = isolated_url.render_as_string(hide_password=False)
    sys.path.insert(0, str(BACKEND_DIR))
    from app.database import Base
    from app.models import Quota

    users = []
    token_hashes = {}
    for index in range(user_count):
        user_id = f"loadtest-user-{index + 1}"
        token = secrets.token_urlsafe(32)
        users.append({"user_id": user_id, "token": token})
        token_hashes[user_id] = hashlib.sha256(token.encode()).hexdigest()
    run_config = {
        "schema": schema_name,
        "database_url": isolated_url.render_as_string(hide_password=False),
        "token_hashes": token_hashes,
        "users": users,
    }

    # Reserve the configuration exclusively, with private permissions from creation.
    # Saving it before DDL preserves the schema name for investigating partial setup.
    descriptor = os.open(config_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as config_file:
        json.dump(run_config, config_file, indent=2)

    admin_engine = create_engine(test_url)
    try:
        with admin_engine.begin() as connection:
            # Identifier is generated here, never supplied by configuration.
            connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
    finally:
        admin_engine.dispose()

    isolated_engine = create_engine(isolated_url)
    try:
        # Temporary benchmark setup; migration validation is a separate check.
        Base.metadata.create_all(isolated_engine)
        with Session(isolated_engine) as db:
            for user in users:
                db.add(Quota(
                    user_id=user["user_id"], limit_bytes=quota_bytes,
                    used_bytes=0, reserved_bytes=0,
                ))
            db.commit()
    finally:
        isolated_engine.dispose()

    print(f"Created isolated schema: {schema_name}")
    print(f"Target database: {test_url.database}")
    print(f"Test users: {user_count}")
    print(f"Quota per user: {quota_bytes} bytes")
    print(f"Saved private run configuration: {config_path}")


if __name__ == "__main__":
    main()
