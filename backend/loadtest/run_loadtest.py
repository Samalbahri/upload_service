import hashlib
from pathlib import Path
import asyncio
import time
import json
from datetime import datetime, timezone
from math import ceil
import platform
import re

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
import httpx

LOADTEST_DIR = Path(__file__).resolve().parent
IMAGE_DIR = LOADTEST_DIR / "compressedImages"


def prepare_workload():
    images = sorted(
        path
        for path in IMAGE_DIR.iterdir()
        if path.is_file()
        and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
    )[:12]

    if len(images) != 12:
        raise ValueError("At least 12 images are required")

    hashes = {
        hashlib.sha256(path.read_bytes()).hexdigest()
        for path in images
    }

    if len(hashes) != 12:
        raise ValueError("The selected images must have distinct bytes")

    # Twelve unique images, followed by eight repeated uploads.
    workload = images + images[:8]

    print(f"Unique images: {len(hashes)}")
    print(f"Total uploads: {len(workload)}")
    print("Expected duplicate-upload ratio: 40%")

    return workload

async def exercise_upload(client, image_path, user):
    headers = {
        "Authorization": f"Bearer {user['token']}",
        "X-Filename": image_path.name,
    }
    content = image_path.read_bytes()

    started = time.perf_counter()

    upload = await client.post(
        "/api/v1/files",
        content=content,
        headers=headers,
    )
    upload.raise_for_status()

    if upload.status_code != 201:
        raise RuntimeError("Expected upload status 201")

    upload_finished = time.perf_counter()
    file_id = upload.json()["file_id"]

    metadata = await client.get(
        f"/api/v1/files/{file_id}",
        headers=headers,
    )
    metadata.raise_for_status()

    original = await client.get(
        f"/api/v1/files/{file_id}/original",
        headers=headers,
    )
    original.raise_for_status()

    if original.content != content:
        raise RuntimeError("Downloaded original differs from upload")

    deadline = time.perf_counter() + 60

    while True:
        thumbnail = await client.get(
            f"/api/v1/files/{file_id}/thumbnail",
            headers=headers,
        )

        if thumbnail.status_code == 200:
            if thumbnail.headers.get("content-type") != "image/webp":
                raise RuntimeError("Expected WebP, possibly got a placeholder")
            break

        if thumbnail.status_code != 202:
            thumbnail.raise_for_status()
            raise RuntimeError("Unexpected thumbnail response")

        if time.perf_counter() >= deadline:
            raise TimeoutError("Thumbnail did not become ready within 60 seconds")

        await asyncio.sleep(0.1)

    ready_observed = time.perf_counter()

    # A second fetch exercises the warm cache.
    cached = await client.get(
        f"/api/v1/files/{file_id}/thumbnail",
        headers=headers,
    )
    cached.raise_for_status()

    if cached.content != thumbnail.content:
        raise RuntimeError("Repeated thumbnail fetch returned different bytes")

    return {
        "filename": image_path.name,
        "file_id": file_id,
        "upload_ms": (upload_finished - started) * 1000,
        "observed_thumbnail_lag_ms": (
            ready_observed - upload_finished
        ) * 1000,
    }
def percentile95(values):
    """Nearest-rank p95; small samples are reported with their sample count."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[ceil(0.95 * len(ordered)) - 1]


def database_snapshot(engine):
    with engine.connect() as connection:
        snapshot = {
            "references": connection.scalar(text("SELECT count(*) FROM user_files")),
            "shared_files": connection.scalar(text("SELECT count(*) FROM files")),
            "jobs": connection.scalar(text("SELECT count(*) FROM thumbnail_jobs")),
            "ready_jobs": connection.scalar(text(
                "SELECT count(*) FROM thumbnail_jobs WHERE status = 'ready'"
            )),
            "ready_files": connection.scalar(text(
                "SELECT count(*) FROM files WHERE thumbnail_status = 'ready'"
            )),
            "rate_limit_rows": connection.scalar(text("SELECT count(*) FROM upload_rate_limits")),
            "logical_bytes": connection.scalar(text("SELECT coalesce(sum(used_bytes), 0) FROM quota")),
            "physical_original_bytes": connection.scalar(text("SELECT coalesce(sum(size_bytes), 0) FROM files")),
        }
        # PostgreSQL SUM(bigint) returns Decimal; counts and byte totals are integers.
        return {key: int(value) for key, value in snapshot.items()}


def validated_url(config):
    url = make_url(config["database_url"])
    schema = config["schema"]
    if url.get_backend_name() != "postgresql" or url.database != "upload_service_test":
        raise ValueError("Refusing to use a non-test PostgreSQL database")
    if not re.fullmatch(r"loadtest_[0-9a-f]{32}", schema):
        raise ValueError("Invalid load-test schema")
    if url.query.get("options") != f"-csearch_path={schema}":
        raise ValueError("Database schema configuration mismatch")
    return url


async def main():
    workload = prepare_workload()
    config = json.loads((LOADTEST_DIR / "run.json").read_text())
    users = config["users"]
    if len(users) < 4 or len({user["user_id"] for user in users[:4]}) != 4:
        raise ValueError("Four distinct test users are required")
    url = validated_url(config)
    engine = create_engine(url)
    output_path = LOADTEST_DIR / "results.json"
    report_path = LOADTEST_DIR / "report.json"
    # Never silently overwrite evidence or spend another 20 attempts on old data.
    if output_path.exists() or report_path.exists():
        engine.dispose()
        raise ValueError("Results already exist; use a fresh isolated run")

    request_records = []
    async def request_started(request):
        request.extensions["loadtest_started"] = time.perf_counter()

    async def response_received(response):
        await response.aread()  # Include response-body transfer in latency.
        request = response.request
        request_records.append({
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "duration_ms": (time.perf_counter() - request.extensions["loadtest_started"]) * 1000,
        })

    semaphore = asyncio.Semaphore(4)
    async def run_one(index, image_path, client):
        async with semaphore:
            try:
                result = await exercise_upload(client, image_path, users[index % 4])
                result["upload_index"] = index
                result["is_seeded_duplicate"] = index >= 12
                return result
            except Exception as exc:
                # Avoid exception strings that could contain connection credentials.
                return {"upload_index": index, "filename": image_path.name,
                        "error_type": type(exc).__name__}

    try:
        before = database_snapshot(engine)
        if any(before.values()):
            raise ValueError("Test schema is not empty; create a fresh isolated run")
        run_dir = LOADTEST_DIR / "runs" / config["schema"]
        if any(path.is_file() for path in (run_dir / "storage").rglob("*")):
            raise ValueError("Run storage contains files; use a fresh isolated run")
        headers = {"Authorization": f"Bearer {users[0]['token']}"}
        async with httpx.AsyncClient(
            base_url="http://127.0.0.1:8001", timeout=30,
            event_hooks={"request": [request_started], "response": [response_received]},
        ) as client:
            metrics = await client.get("/api/v1/metrics/thumbnail-cache", headers=headers)
            metrics.raise_for_status()
            cache_before = metrics.json()
            if any(cache_before.values()):
                raise ValueError("Restart the isolated API to begin with an empty cache")
            started_at = datetime.now(timezone.utc).isoformat()
            started = time.perf_counter()
            results = await asyncio.gather(*(
                run_one(index, path, client) for index, path in enumerate(workload)
            ))
            duration = time.perf_counter() - started
            metrics = await client.get("/api/v1/metrics/thumbnail-cache", headers=headers)
            metrics.raise_for_status()
            cache_after = metrics.json()
        after = database_snapshot(engine)
    finally:
        engine.dispose()

    successes = [result for result in results if "error_type" not in result]
    failures = [result for result in results if "error_type" in result]
    lags = [result["observed_thumbnail_lag_ms"] for result in successes
            if not result["is_seeded_duplicate"]]
    references = after["references"]
    actual_ratio = ((references - after["shared_files"]) / references) if references else None
    cache_delta = {key: cache_after[key] - cache_before[key] for key in ("hits", "misses")}
    expected_bytes = sum(path.stat().st_size for path in workload)
    stored_originals = [path for path in (run_dir / "storage" / "originals").glob("*") if path.is_file()]
    stored_thumbnails = list((run_dir / "storage" / "thumbnails").glob("*.webp"))
    checks = {
        "all_flows_completed": len(successes) == 20 and not failures,
        "twenty_references": references == 20,
        "twelve_shared_files": after["shared_files"] == 12,
        "twelve_ready_jobs": after["jobs"] == after["ready_jobs"] == 12,
        "twelve_ready_files": after["ready_files"] == 12,
        "dedup_ratio_matches_seed": actual_ratio is not None and abs(actual_ratio - 0.4) < 1e-9,
        "quota_matches_logical_bytes": after["logical_bytes"] == expected_bytes,
        "twelve_lag_samples": len(lags) == 12,
        "cache_hits_observed": cache_delta["hits"] >= 20,
        "physical_object_counts": len(stored_originals) == len(stored_thumbnails) == 12,
    }
    latencies = {}
    for method, group in (("POST", "uploads"), ("GET", "fetches")):
        samples = [r["duration_ms"] for r in request_records
                   if r["method"] == method and "/metrics/" not in r["path"]]
        latencies[group] = {"samples": len(samples), "p95_ms": percentile95(samples)}
    report = {
        "started_at_utc": started_at,
        "environment": {"platform": platform.platform(), "python": platform.python_version(),
                        "api_processes": 1, "worker_processes": 1, "flow_concurrency": 4,
                        "users": 4, "rate_limit_enabled": True},
        "images": [{"filename": path.name, "bytes": path.stat().st_size,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in workload[:12]],
        "duration_seconds": duration,
        "completed_flows": len(successes), "failed_flows": len(failures),
        "uploads_per_second": references / duration,
        "database_before": before, "database_after": after,
        "expected_dedup_ratio": 0.4, "measured_dedup_ratio": actual_ratio,
        "thumbnail_lag": {"p95_ms": percentile95(lags), "samples": len(lags),
            "definition": "Client-observed from upload response to first ready thumbnail response; first 12 unique flows only. Includes metadata/original fetch and 100ms polling delays; not exact worker processing time."},
        "latency": latencies, "cache_before": cache_before,
        "cache_after": cache_after, "cache_delta": cache_delta,
        "checks": checks,
        "limitations": "20 small-image uploads are a functional mixed-workload benchmark, not a capacity or large-file stress test. One API and one worker must be run with run_service.py and no other test traffic.",
    }
    output_path.write_text(json.dumps({"flows": results, "requests": request_records,
        "context": {"started_at_utc": started_at, "duration_seconds": duration,
                    "database_before": before, "database_after": after,
                    "cache_before": cache_before, "cache_after": cache_after}}, indent=2))
    report_path.write_text(json.dumps(report, indent=2))
    print(f"Completed flows: {len(successes)}; failures: {len(failures)}")
    print(f"Measured duplicate-upload ratio: {actual_ratio}")
    print(f"Observed thumbnail p95 lag: {percentile95(lags)} ms ({len(lags)} samples)")
    print(f"Cache counter changes: {cache_delta}")
    for name, passed in checks.items():
        print(f"{'PASS' if passed else 'FAIL'}: {name}")
    print(f"Report saved to {report_path}")
    if not all(checks.values()):
        raise SystemExit("Load-test acceptance checks failed; inspect results.json and report.json")


if __name__ == "__main__":
    asyncio.run(main())
