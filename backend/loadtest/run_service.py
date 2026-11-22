import argparse
import json
import os
from pathlib import Path
import re
import runpy
import sys

from sqlalchemy.engine import make_url


BACKEND_DIR = Path(__file__).resolve().parents[1]
LOADTEST_DIR = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("service", choices=["api", "worker"])
    args = parser.parse_args()

    config = json.loads(
        (LOADTEST_DIR / "run.json").read_text()
    )

    url = make_url(config["database_url"])
    schema = config["schema"]

    if url.database != "upload_service_test":
        raise ValueError("Refusing to use a non-test database")

    if not re.fullmatch(r"loadtest_[0-9a-f]{32}", schema):
        raise ValueError("Invalid load-test schema")

    if url.query.get("options") != f"-csearch_path={schema}":
        raise ValueError("Database schema configuration mismatch")

    # Set these before importing the application.
    os.environ["DATABASE_URL"] = config["database_url"]
    os.environ["UPLOAD_API_TOKEN_HASHES"] = json.dumps(
        config["token_hashes"]
    )

    # Existing storage paths are relative to the working directory.
    # Both processes use this separate location.
    run_directory = LOADTEST_DIR / "runs" / schema
    run_directory.mkdir(parents=True, exist_ok=True)
    os.chdir(run_directory)
    sys.path.insert(0, str(BACKEND_DIR))

    if args.service == "api":
        import uvicorn

        uvicorn.run(
            "app.main:app",
            host="127.0.0.1",
            port=8001,
        )
    else:
        runpy.run_module("app.worker", run_name="__main__")


if __name__ == "__main__":
    main()