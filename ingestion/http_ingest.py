"""
http_ingest.py — Standalone HTTP ingestion service for dynamic-labyrinth.

Roadmap reference: Alaa — Security & Integration
  "http_ingest.py — alternative ingestion accepting Honeytrap webhooks"

This module can be run as a standalone FastAPI app OR imported by the main
ingestion service. It exposes:

  POST /ingest/honeytrap    Raw Honeytrap HTTP-pusher webhook (IP-allowlisted)
  POST /ingest/event        Single HMAC-authenticated event (internal services)
  POST /ingest/bulk         Bulk HMAC-authenticated events (up to 500)
  POST /ingest/replay       Replay a JSONL archive file
  GET  /health              Service health + queue depth
  GET  /metrics             Prometheus text metrics
  GET  /stats               JSON ingestion counters

Security
--------
* /ingest/honeytrap — optional IP allowlist (HONEYTRAP_ALLOWED_IPS env var)
  and optional HMAC (REQUIRE_HMAC_ON_WEBHOOK=true).
* /ingest/event and /ingest/bulk require valid HMAC headers (X-DL-Timestamp,
  X-DL-Signature). Replay attack window: 30 seconds.
* All request bodies validated with Pydantic before normalization.
* No raw SQL — no injection surface.

Environment variables
---------------------
CEREBRUM_URL             http://cerebrum:8001
HMAC_SECRET              Shared secret (CHANGE IN PRODUCTION)
REDIS_URL                redis://redis:6379/0  (empty = memory queue)
HONEYTRAP_ALLOWED_IPS    Comma-separated IP allowlist (empty = all IPs)
REQUIRE_HMAC_ON_WEBHOOK  true | false (default false)
LOG_LEVEL                INFO
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from hmac_utils import require_hmac, verify_request
from normalize import normalize, normalize_batch
from queue_manager import enqueue, queue_size, queue_worker
from schemas import (
    BulkIngestRequest,
    BulkIngestResponse,
    HealthResponse,
    IngestRequest,
    IngestResponse,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("ingestion.http")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CEREBRUM_URL: str = os.environ.get("CEREBRUM_URL", "http://cerebrum:8001")
HONEYTRAP_ALLOWED_IPS: List[str] = [
    ip.strip()
    for ip in os.environ.get("HONEYTRAP_ALLOWED_IPS", "").split(",")
    if ip.strip()
]
REQUIRE_HMAC_ON_WEBHOOK: bool = (
    os.environ.get("REQUIRE_HMAC_ON_WEBHOOK", "false").lower() == "true"
)

# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

_stats: Dict[str, int] = {
    "webhook_calls": 0,
    "events_accepted": 0,
    "events_rejected": 0,
    "bulk_batches": 0,
    "replay_lines": 0,
}

# ---------------------------------------------------------------------------
# IP allowlist helper
# ---------------------------------------------------------------------------


def _client_ip(request: Request) -> str:
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else ""


def _check_allowlist(request: Request) -> None:
    if not HONEYTRAP_ALLOWED_IPS:
        return
    ip = _client_ip(request)
    if ip not in HONEYTRAP_ALLOWED_IPS:
        logger.warning("Blocked request from disallowed IP: %s", ip)
        raise HTTPException(status_code=403, detail=f"IP {ip!r} not in allowlist")


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("HTTP Ingestion service starting (cerebrum=%s)", CEREBRUM_URL)
    worker = asyncio.create_task(queue_worker(), name="queue-worker")
    yield
    worker.cancel()
    try:
        await worker
    except asyncio.CancelledError:
        pass
    logger.info("HTTP Ingestion service stopped")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="dynamic-labyrinth HTTP Ingestion",
    version="1.0.0",
    description="Accepts Honeytrap webhook events and internal HMAC-authenticated events.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.post("/ingest/honeytrap", response_model=IngestResponse)
async def ingest_honeytrap(request: Request) -> IngestResponse:
    """
    Honeytrap HTTP-pusher webhook endpoint.

    Accepts raw JSON from Honeytrap's HTTP pusher. Optionally validates HMAC
    and/or restricts by source IP (controlled via environment variables).
    """
    _check_allowlist(request)
    _stats["webhook_calls"] += 1

    # Optional HMAC on webhook
    if REQUIRE_HMAC_ON_WEBHOOK:
        body_bytes = await request.body()
        ok, reason = verify_request(
            method=request.method,
            path=request.url.path,
            body=body_bytes,
            timestamp_header=request.headers.get("X-DL-Timestamp"),
            signature_header=request.headers.get("X-DL-Signature"),
        )
        if not ok:
            raise HTTPException(status_code=401, detail=f"HMAC auth failed: {reason}")

    try:
        raw_body = await request.body()
        record: Dict[str, Any] = json.loads(raw_body)
    except Exception as exc:
        logger.warning("Webhook invalid JSON: %s", exc)
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    event = normalize(record, source="http")
    if event is None:
        _stats["events_rejected"] += 1
        raise HTTPException(status_code=422, detail="Event could not be normalized — missing required fields")

    await enqueue(event)
    _stats["events_accepted"] += 1
    logger.info("Webhook event accepted: %s session=%s src=%s", event.id, event.session_id, event.source_ip)
    return IngestResponse(ok=True, event_id=event.id)


@app.post("/ingest/event", response_model=IngestResponse, dependencies=[Depends(require_hmac)])
async def ingest_single(body: IngestRequest) -> IngestResponse:
    """
    Accept a single raw event from an internal service (HMAC required).
    Used by other dynamic-labyrinth components to inject events directly.
    """
    event = normalize(body.event, source=body.source)
    if event is None:
        _stats["events_rejected"] += 1
        raise HTTPException(status_code=422, detail="Event normalization failed")

    await enqueue(event)
    _stats["events_accepted"] += 1
    logger.info("Single event accepted: %s", event.id)
    return IngestResponse(ok=True, event_id=event.id)


@app.post("/ingest/bulk", response_model=BulkIngestResponse, dependencies=[Depends(require_hmac)])
async def ingest_bulk(body: BulkIngestRequest) -> BulkIngestResponse:
    """
    Accept up to 500 raw events at once (HMAC required).
    Silently drops events that fail normalization and reports counts.
    """
    _stats["bulk_batches"] += 1
    events = normalize_batch(body.events, source=body.source)
    errors: List[str] = []

    for i, raw in enumerate(body.events):
        matched = any(e.raw == raw for e in events)
        if not matched:
            errors.append(f"Record[{i}]: normalization failed (likely missing source IP or invalid format)")

    for event in events:
        await enqueue(event)
        _stats["events_accepted"] += 1

    _stats["events_rejected"] += len(body.events) - len(events)
    logger.info("Bulk ingest: %d/%d accepted", len(events), len(body.events))
    return BulkIngestResponse(
        ok=True,
        accepted=len(events),
        rejected=len(body.events) - len(events),
        errors=errors[:20],
    )


class ReplayRequest(BaseModel):
    path: str
    source: str = "file"
    limit: int = 10_000


class ReplayResponse(BaseModel):
    ok: bool
    lines_processed: int
    events_enqueued: int
    errors: int


@app.post(
    "/ingest/replay",
    response_model=ReplayResponse,
    dependencies=[Depends(require_hmac)],
)
async def replay_file(body: ReplayRequest) -> ReplayResponse:
    """
    Replay a JSONL archive file through the normalization + queue pipeline.
    HMAC required. Useful for re-processing historical Honeytrap logs.
    """
    import aiofiles  # type: ignore

    lines_processed = events_enqueued = errors = 0
    try:
        async with aiofiles.open(body.path, "r") as fh:
            async for line in fh:
                if lines_processed >= body.limit:
                    break
                line = line.strip()
                if not line:
                    continue
                lines_processed += 1
                _stats["replay_lines"] += 1
                try:
                    record = json.loads(line)
                    event = normalize(record, source=body.source)
                    if event:
                        await enqueue(event)
                        events_enqueued += 1
                    else:
                        errors += 1
                except Exception as exc:
                    logger.warning("Replay line error: %s", exc)
                    errors += 1
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"Cannot open file: {exc}")

    logger.info("Replay %s: %d lines, %d enqueued, %d errors", body.path, lines_processed, events_enqueued, errors)
    return ReplayResponse(
        ok=True,
        lines_processed=lines_processed,
        events_enqueued=events_enqueued,
        errors=errors,
    )


# ---------------------------------------------------------------------------
# Health & Metrics
# ---------------------------------------------------------------------------


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    redis_ok = False
    cerebrum_ok = False

    try:
        from queue_manager import _get_redis
        r = await _get_redis()
        if r:
            await r.ping()
            redis_ok = True
    except Exception:
        pass

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{CEREBRUM_URL}/healthz")
            cerebrum_ok = resp.status_code == 200
    except Exception:
        pass

    q = await queue_size()
    return HealthResponse(
        status="ok" if cerebrum_ok else "degraded",
        queue_size=q,
        redis_connected=redis_ok,
        cerebrum_reachable=cerebrum_ok,
    )


@app.get("/metrics")
async def metrics() -> PlainTextResponse:
    q = await queue_size()
    lines = [
        "# HELP dl_http_ingest_events_accepted_total Events accepted",
        "# TYPE dl_http_ingest_events_accepted_total counter",
        f"dl_http_ingest_events_accepted_total {_stats['events_accepted']}",
        "# HELP dl_http_ingest_events_rejected_total Events rejected",
        "# TYPE dl_http_ingest_events_rejected_total counter",
        f"dl_http_ingest_events_rejected_total {_stats['events_rejected']}",
        "# HELP dl_http_ingest_webhook_calls_total Webhook POST calls",
        "# TYPE dl_http_ingest_webhook_calls_total counter",
        f"dl_http_ingest_webhook_calls_total {_stats['webhook_calls']}",
        "# HELP dl_http_ingest_queue_size Current queue depth",
        "# TYPE dl_http_ingest_queue_size gauge",
        f"dl_http_ingest_queue_size {q}",
    ]
    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")


@app.get("/stats")
async def stats() -> Dict[str, Any]:
    q = await queue_size()
    return {**_stats, "queue_size": q}


@app.get("/")
async def root():
    return {
        "service": "dynamic-labyrinth http-ingestion",
        "version": "1.0.0",
        "endpoints": ["/ingest/honeytrap", "/ingest/event", "/ingest/bulk", "/ingest/replay", "/health", "/metrics"],
    }


# ---------------------------------------------------------------------------
# Standalone entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "http_ingest:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8002")),
        log_level=os.environ.get("LOG_LEVEL", "info").lower(),
    )
