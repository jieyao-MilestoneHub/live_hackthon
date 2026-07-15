#!/usr/bin/env python3
"""Concurrency load test for the batch-upload control plane + S3 multipart.

The 10GB bytes go browser→S3 directly, so the *architecture* bottleneck is the
control plane: create-project / upload-session (presigns ~640 URLs for a 10GB
file) / complete, all on the backend Lambda behind API Gateway. This script
hammers that path at N concurrent "users" and reports latency + throttle/error
counts so you can prove the architecture never becomes the bottleneck BEFORE the
demo. It does NOT need to move 300GB to find a throttle.

Two modes:
  control-plane-only (default): create → upload-session for each file, at the
    REAL part count for --size-gb, then stop (the dangling multipart upload is
    reaped by the raw-bucket 1-day abort-incomplete lifecycle rule). This is the
    load that reveals Lambda throttling / cold starts / DynamoDB contention.
  --full --size-mb S: also PUT real bytes to each presigned part and call
    complete. Use a SMALL S (e.g. 20–100) with a few users to validate the true
    end-to-end multipart path; not for 10GB × 30.

Auth: pass --token <CognitoIdToken> for a real deployment, or rely on the
X-Tenant-Id / X-User-Id header fallback (--tenant/--user) the backend accepts.

Examples:
  # 30 users, 1 file each, realistic 10GB part count, control-plane only:
  python scripts/loadtest_batch_upload.py --api-base https://ID.execute-api.us-east-1.amazonaws.com \
      --users 30 --files-per-user 1 --size-gb 10

  # Small real end-to-end sanity (5 users × 50MB, actually PUTs bytes):
  python scripts/loadtest_batch_upload.py --api-base ... --users 5 --full --size-mb 50
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

_PART_SIZE = 16 * 1024 * 1024  # keep in sync with backend-api/app/storage.py


@dataclass
class Metrics:
    latencies_ms: dict[str, list[float]] = field(default_factory=dict)
    status_counts: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def record(self, phase: str, ms: float) -> None:
        self.latencies_ms.setdefault(phase, []).append(ms)

    def status(self, code: int) -> None:
        self.status_counts[str(code)] = self.status_counts.get(str(code), 0) + 1


def _request(method: str, url: str, *, headers: dict, body: bytes | None = None):
    """Return (status, body_bytes, response_headers, elapsed_ms)."""
    req = urllib.request.Request(url, method=method, data=body, headers=headers)
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = resp.read()
            return resp.status, data, dict(resp.headers), (time.perf_counter() - start) * 1000
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers or {}), (time.perf_counter() - start) * 1000


def _api_headers(args) -> dict:
    headers = {"Content-Type": "application/json"}
    if args.token:
        headers["Authorization"] = f"Bearer {args.token}"
    else:
        headers["X-Tenant-Id"] = args.tenant
        headers["X-User-Id"] = args.user
    return headers


def _upload_one(args, m: Metrics, size_bytes: int, idx: int) -> bool:
    base = args.api_base.rstrip("/")
    headers = _api_headers(args)

    # 1) create project
    code, data, _, ms = _request(
        "POST", f"{base}/projects", headers=headers,
        body=json.dumps({"title": f"loadtest-{idx}", "target_duration_ms": 30000,
                         "analysis_source": "transcribe"}).encode(),
    )
    m.record("create_project", ms)
    m.status(code)
    if code >= 300:
        m.errors.append(f"create_project [{code}]: {data[:200]!r}")
        return False
    project_id = json.loads(data)["project_id"]

    # 2) upload-session (presigns REAL part count for size_bytes)
    code, data, _, ms = _request(
        "POST", f"{base}/projects/{project_id}/upload-session", headers=headers,
        body=json.dumps({"filename": "source.mp4", "content_type": "video/mp4",
                         "size_bytes": size_bytes}).encode(),
    )
    m.record("upload_session", ms)
    m.status(code)
    if code >= 300:
        m.errors.append(f"upload_session [{code}]: {data[:200]!r}")
        return False
    session = json.loads(data)
    parts = sorted(session["parts"], key=lambda p: p["part_number"])

    if not args.full:
        return True  # control-plane-only: leave the multipart dangling (lifecycle reaps it)

    # 3) PUT real bytes per part (small sizes only) and 4) complete
    part_size = -(-size_bytes // len(parts))  # ceil
    etags = []
    for i, part in enumerate(parts):
        start = i * part_size
        length = min(part_size, size_bytes - start)
        blob = b"\0" * length
        s, _, put_headers, put_ms = _request("PUT", part["url"], headers={}, body=blob)
        m.record("part_put", put_ms)
        m.status(s)
        if s >= 300:
            m.errors.append(f"part_put#{part['part_number']} [{s}]")
            return False
        etag = put_headers.get("ETag") or put_headers.get("Etag") or ""
        etags.append({"part_number": part["part_number"], "etag": etag.strip()})

    code, data, _, ms = _request(
        "POST", f"{base}/projects/{project_id}/upload-session/complete", headers=headers,
        body=json.dumps({"upload_id": session["upload_id"], "parts": etags}).encode(),
    )
    m.record("complete", ms)
    m.status(code)
    if code >= 300:
        m.errors.append(f"complete [{code}]: {data[:200]!r}")
        return False
    return True


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = min(len(s) - 1, int(round((p / 100) * (len(s) - 1))))
    return s[k]


def main() -> int:
    ap = argparse.ArgumentParser(description="Batch-upload concurrency load test")
    ap.add_argument("--api-base", default=os.environ.get("API_BASE", ""), required=False)
    ap.add_argument("--users", type=int, default=30)
    ap.add_argument("--files-per-user", type=int, default=1)
    ap.add_argument("--size-gb", type=float, default=10.0)
    ap.add_argument("--full", action="store_true", help="actually PUT bytes + complete (use small --size-mb)")
    ap.add_argument("--size-mb", type=float, default=0.0, help="override size in MB (implies smaller test)")
    ap.add_argument("--token", default=os.environ.get("ID_TOKEN"))
    ap.add_argument("--tenant", default="loadtest")
    ap.add_argument("--user", default="loadtest")
    args = ap.parse_args()

    if not args.api_base:
        ap.error("--api-base (or API_BASE env) is required")
    if args.full and args.size_mb <= 0:
        ap.error("--full requires a small --size-mb (do not PUT 10GB from this script)")

    size_bytes = int(args.size_mb * 1024 * 1024) if args.size_mb > 0 else int(args.size_gb * 1024**3)
    total = args.users * args.files_per_user
    parts_each = max(1, -(-size_bytes // _PART_SIZE))
    mode = "FULL (real PUT + complete)" if args.full else "control-plane-only"
    print(f"Load test: {args.users} users × {args.files_per_user} files = {total} uploads")
    print(f"  size/file={size_bytes / 1024**3:.2f} GB → {parts_each} parts each ({mode})")
    print(f"  target={args.api_base}\n")

    m = Metrics()
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.users) as pool:
        futures = [pool.submit(_upload_one, args, m, size_bytes, i) for i in range(total)]
        ok = sum(1 for f in as_completed(futures) if f.result())
    wall = time.perf_counter() - t0

    print(f"Done in {wall:.1f}s — {ok}/{total} uploads succeeded\n")
    print("Latency (ms)   count     p50     p95     max")
    for phase, vals in m.latencies_ms.items():
        print(f"  {phase:<14}{len(vals):>5}  {_pct(vals,50):>7.0f} {_pct(vals,95):>7.0f} {max(vals):>7.0f}")
    print("\nHTTP status counts:", dict(sorted(m.status_counts.items())))
    throttles = m.status_counts.get("429", 0)
    print(f"\n>>> 429 throttles: {throttles}  (goal: 0 — the architecture is not the bottleneck)")
    if m.errors:
        print(f"\nFirst errors ({len(m.errors)} total):")
        for e in m.errors[:10]:
            print(f"  - {e}")
    return 1 if (throttles or ok < total) else 0


if __name__ == "__main__":
    raise SystemExit(main())
