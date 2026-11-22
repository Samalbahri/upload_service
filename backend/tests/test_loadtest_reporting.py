from pathlib import Path
import runpy

import pytest


@pytest.fixture
def client_code():
    return runpy.run_path(str(Path(__file__).resolve().parents[1] / "loadtest" / "run_loadtest.py"))


def test_p95_uses_nearest_rank_and_handles_incomplete_run(client_code):
    percentile = client_code["percentile95"]
    assert percentile([]) is None
    assert percentile(list(range(1, 13))) == 12
    assert percentile(list(range(100, 0, -1))) == 95


def test_rejects_nonisolated_database_configuration(client_code):
    validate = client_code["validated_url"]
    schema = "loadtest_" + "a" * 32
    from sqlalchemy.engine import make_url
    url = make_url("postgresql+psycopg://localhost/upload_service_test").update_query_dict(
        {"options": f"-csearch_path={schema}"}
    )
    config = {"schema": schema, "database_url": url.render_as_string()}
    assert validate(config).database == "upload_service_test"
    for unsafe in (
        {**config, "database_url": str(url.set(database="upload_service"))},
        {**config, "database_url": str(url.update_query_dict({"options": "-csearch_path=public"}))},
        {**config, "schema": "public"},
    ):
        with pytest.raises(ValueError):
            validate(unsafe)


def test_database_totals_are_json_serializable_integers(client_code):
    from decimal import Decimal
    import json
    class Connection:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def scalar(self, statement):
            return Decimal(123)
    class Engine:
        def connect(self):
            return Connection()
    snapshot = client_code["database_snapshot"](Engine())
    assert snapshot["logical_bytes"] == 123
    assert all(type(value) is int for value in snapshot.values())
    json.dumps(snapshot)
