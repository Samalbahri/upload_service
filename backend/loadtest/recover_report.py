"""Recover measurements from a completed workload without resending uploads."""
import json
from datetime import datetime, timezone

import httpx
from sqlalchemy import create_engine

from run_loadtest import LOADTEST_DIR, database_snapshot, percentile95, validated_url


def main():
    report_path = LOADTEST_DIR / "report.json"
    if report_path.exists():
        raise ValueError("report.json already exists; refusing to overwrite")
    saved = json.loads((LOADTEST_DIR / "results.json").read_text())
    config = json.loads((LOADTEST_DIR / "run.json").read_text())
    engine = create_engine(validated_url(config))
    try:
        database = database_snapshot(engine)
    finally:
        engine.dispose()
    flows = saved["flows"]
    successful = [flow for flow in flows if "error_type" not in flow]
    lags = [flow["observed_thumbnail_lag_ms"] for flow in successful
            if not flow["is_seeded_duplicate"]]
    ratio = ((database["references"] - database["shared_files"]) / database["references"]
             if database["references"] else None)
    # These are current counters, not a recovered end-of-run snapshot.
    cache = None
    try:
        response = httpx.get("http://127.0.0.1:8001/api/v1/metrics/thumbnail-cache",
                            headers={"Authorization": f"Bearer {config['users'][0]['token']}"}, timeout=5)
        response.raise_for_status()
        cache = response.json()
    except httpx.HTTPError:
        pass
    latencies = {}
    for method in ("POST", "GET"):
        samples = [item["duration_ms"] for item in saved["requests"]
                   if item["method"] == method and "/metrics/" not in item["path"]]
        latencies[method] = {"samples": len(samples), "p95_ms": percentile95(samples)}
    run_dir = LOADTEST_DIR / "runs" / config["schema"] / "storage"
    originals = list((run_dir / "originals").glob("*"))
    previews = list((run_dir / "thumbnails").glob("*.webp"))
    checks = {
        "twenty_completed_flows": len(successful) == len(flows) == 20,
        "twenty_references": database["references"] == 20,
        "twelve_shared_files": database["shared_files"] == 12,
        "twelve_ready_jobs": database["jobs"] == database["ready_jobs"] == 12,
        "forty_percent_duplicates": ratio is not None and abs(ratio - 0.4) < 1e-9,
        "twelve_unique_lag_samples": len(lags) == 12,
        "physical_object_counts": len(originals) == len(previews) == 12,
    }
    report = {
        "recovered_at_utc": datetime.now(timezone.utc).isoformat(),
        "recovery_note": "No uploads resent. Flow and request timings come from saved results; database and cache counts were read during recovery. Original total duration and cache deltas were not saved and cannot be reconstructed accurately.",
        "completed_flows": len(successful), "failed_flows": len(flows) - len(successful),
        "measured_dedup_ratio": ratio, "expected_dedup_ratio": 0.4,
        "thumbnail_lag": {"p95_ms": percentile95(lags), "samples": len(lags),
            "definition": "Client-observed upload-response to ready-thumbnail response; unique flows only, including intervening fetches and polling delay."},
        "latency": latencies, "database_at_recovery": database,
        "cache_at_recovery": cache, "duration_seconds": None, "checks": checks,
        "limitations": "20 small-image functional mixed-workload run; not a capacity benchmark. Recovery assumes no intervening uploads to this isolated schema.",
    }
    report_path.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    if not all(checks.values()):
        raise SystemExit("Some acceptance checks failed")


if __name__ == "__main__":
    main()
