"""
main.py — FastAPI HTTP ingestion service for dynamic-labyrinth.

Endpoints
---------
POST /ingest/event          Accept a single raw event (HMAC-authenticated)
POST /ingest/bulk           Accept up to 500 raw events at once
POST /ingest/honeytrap      Honeytrap webhook endpoint (unauthenticated internally,
                            validated via HMAC + IP allowlist)
GET  /health                Health check (queue size, Redis, Cerebrum reachability)
GET  /metrics               Prometheus-compatible text metrics
GET  /stats                 JSON ingestion statistics

Security
--------
* /ingest/event and /ingest/bulk require HMAC signature (X-DL-Timestamp + X-DL-Signature).
* /ingest/honeytrap optionally enforces an IP allowlist (HONEYTRAP_ALLOWED_IPS env var).
* All inputs validated with Pydantic.
* No SQL is performed in this service — pure ingestion/normalization.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from file_ingest import get_file_stats, start_file_watchers, stop_file_watchers
from hmac_utils import require_hmac
from normalize import normalize, normalize_batch
from queue_manager import enqueue, queue_size, queue_worker
from schemas import (
    BulkIngestRequest,
    BulkIngestResponse,
    HealthResponse,
    IngestRequest,
    IngestResponse,
    NormalizedEvent,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("ingestion")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CEREBRUM_URL: str = os.environ.get("CEREBRUM_URL", "http://cerebrum:8001")
HONEYTRAP_ALLOWED_IPS: List[str] = [
    ip.strip()
    for ip in os.environ.get("HONEYTRAP_ALLOWED_IPS", "").split(",")
    if ip.strip()
]
ENABLE_FILE_INGEST: bool = os.environ.get("ENABLE_FILE_INGEST", "true").lower() == "true"
REQUIRE_HMAC_ON_WEBHOOK: bool = os.environ.get("REQUIRE_HMAC_ON_WEBHOOK", "false").lower() == "true"

# ---------------------------------------------------------------------------
# Ingestion counters
# ---------------------------------------------------------------------------

_counters: Dict[str, int] = {
    "events_accepted": 0,
    "events_rejected": 0,
    "bulk_batches": 0,
    "webhook_calls": 0,
}

# ---------------------------------------------------------------------------
# Lifespan (startup / shutdown)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Ingestion service starting up")

    # Start queue worker
    worker_task = asyncio.create_task(queue_worker(), name="queue-worker")

    # Start file watchers (if enabled)
    if ENABLE_FILE_INGEST:
        await start_file_watchers()

    yield  # ← application is running

    logger.info("Ingestion service shutting down")
    if ENABLE_FILE_INGEST:
        await stop_file_watchers()
    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

app = FastAPI(
    title="dynamic-labyrinth Ingestion Service",
    version="1.0.0",
    description="Normalizes and queues Honeytrap events for Cerebrum processing.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# IP allowlist middleware helper
# ---------------------------------------------------------------------------

def _client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    if request.client:
        return request.client.host
    return ""


def _check_ip_allowlist(request: Request) -> None:
    if not HONEYTRAP_ALLOWED_IPS:
        return  # allowlist disabled → open access (rely on HMAC)
    ip = _client_ip(request)
    if ip not in HONEYTRAP_ALLOWED_IPS:
        logger.warning("Rejected request from disallowed IP: %s", ip)
        raise HTTPException(status_code=403, detail=f"IP {ip!r} not in allowlist")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.post("/ingest/event", response_model=IngestResponse, dependencies=[Depends(require_hmac)])
async def ingest_single(body: IngestRequest) -> IngestResponse:
    """Accept a single raw event (HMAC-required)."""
    event = normalize(body.event, source=body.source)
    if event is None:
        _counters["events_rejected"] += 1
        logger.warning("Event normalization failed: %r", body.event)
        raise HTTPException(status_code=422, detail="Event could not be normalized")

    await enqueue(event)
    _counters["events_accepted"] += 1
    logger.info("Ingested event %s (session=%s, type=%s)", event.id, event.session_id, event.event_type)
    return IngestResponse(ok=True, event_id=event.id)


@app.post("/ingest/bulk", response_model=BulkIngestResponse, dependencies=[Depends(require_hmac)])
async def ingest_bulk(body: BulkIngestRequest) -> BulkIngestResponse:
    """Accept up to 500 raw events in one request (HMAC-required)."""
    _counters["bulk_batches"] += 1
    events = normalize_batch(body.events, source=body.source)

    errors: List[str] = []
    for i, raw in enumerate(body.events):
        if not any(e.raw == raw for e in events):
            errors.append(f"Record {i}: normalization failed")

    for event in events:
        await enqueue(event)
        _counters["events_accepted"] += 1

    _counters["events_rejected"] += len(body.events) - len(events)
    logger.info("Bulk ingest: %d/%d accepted", len(events), len(body.events))
    return BulkIngestResponse(
        ok=True,
        accepted=len(events),
        rejected=len(body.events) - len(events),
        errors=errors[:20],  # cap error list size
    )


@app.post("/ingest/honeytrap", response_model=IngestResponse)
async def ingest_honeytrap_webhook(request: Request) -> IngestResponse:
    """
    Honeytrap HTTP-pusher webhook endpoint.

    Optionally validates HMAC (REQUIRE_HMAC_ON_WEBHOOK=true) and/or
    restricts to an IP allowlist (HONEYTRAP_ALLOWED_IPS).
    """
    _check_ip_allowlist(request)
    _counters["webhook_calls"] += 1

    if REQUIRE_HMAC_ON_WEBHOOK:
        body_bytes = await request.body()
        ts = request.headers.get("X-DL-Timestamp")
        sig = request.headers.get("X-DL-Signature")
        from hmac_utils import verify_request
        ok, reason = verify_request(
            method=request.method,
            path=request.url.path,
            body=body_bytes,
            timestamp_header=ts,
            signature_header=sig,
        )
        if not ok:
            raise HTTPException(status_code=401, detail=f"HMAC auth failed: {reason}")

    try:
        raw_body = await request.body()
        record: Dict[str, Any] = json.loads(raw_body)
    except (json.JSONDecodeError, Exception) as exc:
        logger.warning("Honeytrap webhook: invalid JSON body: %s", exc)
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    event = normalize(record, source="http")
    if event is None:
        _counters["events_rejected"] += 1
        raise HTTPException(status_code=422, detail="Event could not be normalized")

    await enqueue(event)
    _counters["events_accepted"] += 1
    logger.info("Webhook event accepted: %s (session=%s)", event.id, event.session_id)
    return IngestResponse(ok=True, event_id=event.id)


# ---------------------------------------------------------------------------
# JSONL file replay endpoint (admin use)
# ---------------------------------------------------------------------------

class ReplayRequest(BaseModel):
    path: str
    source: str = "file"
    limit: int = 10_000


class ReplayResponse(BaseModel):
    ok: bool
    lines_processed: int
    events_enqueued: int
    errors: int


@app.post("/ingest/replay", response_model=ReplayResponse, dependencies=[Depends(require_hmac)])
async def replay_jsonl_file(body: ReplayRequest) -> ReplayResponse:
    """
    Replay a JSONL file into the ingestion pipeline (HMAC-required).
    Useful for re-processing archived Honeytrap logs.
    """
    import aiofiles  # type: ignore

    lines_processed = 0
    events_enqueued = 0
    errors = 0

    try:
        async with aiofiles.open(body.path, "r") as fh:
            async for line in fh:
                if lines_processed >= body.limit:
                    break
                line = line.strip()
                if not line:
                    continue
                lines_processed += 1
                try:
                    record = json.loads(line)
                    event = normalize(record, source=body.source)
                    if event:
                        await enqueue(event)
                        events_enqueued += 1
                    else:
                        errors += 1
                except Exception as exc:
                    logger.warning("Replay parse error: %s", exc)
                    errors += 1
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"Cannot open file: {exc}")

    logger.info("Replay %s: %d lines, %d events, %d errors", body.path, lines_processed, events_enqueued, errors)
    return ReplayResponse(ok=True, lines_processed=lines_processed, events_enqueued=events_enqueued, errors=errors)


# ---------------------------------------------------------------------------
# Health & metrics
# ---------------------------------------------------------------------------

@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    redis_ok = False
    cerebrum_ok = False

    # Check Redis
    try:
        from queue_manager import _get_redis
        r = await _get_redis()
        if r:
            await r.ping()
            redis_ok = True
    except Exception:
        pass

    # Check Cerebrum
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{CEREBRUM_URL}/healthz")
            cerebrum_ok = resp.status_code == 200
    except Exception:
        pass

    q_size = await queue_size()
    status = "ok" if cerebrum_ok else "degraded"
    return HealthResponse(
        status=status,
        queue_size=q_size,
        redis_connected=redis_ok,
        cerebrum_reachable=cerebrum_ok,
    )


@app.get("/metrics")
async def metrics(response: Response) -> Response:
    """Prometheus text format metrics."""
    file_stats = get_file_stats()
    q_size = await queue_size()

    lines = [
        "# HELP dl_ingestion_events_accepted_total Total events accepted",
        "# TYPE dl_ingestion_events_accepted_total counter",
        f"dl_ingestion_events_accepted_total {_counters['events_accepted']}",
        "# HELP dl_ingestion_events_rejected_total Total events rejected",
        "# TYPE dl_ingestion_events_rejected_total counter",
        f"dl_ingestion_events_rejected_total {_counters['events_rejected']}",
        "# HELP dl_ingestion_queue_size Current queue depth",
        "# TYPE dl_ingestion_queue_size gauge",
        f"dl_ingestion_queue_size {q_size}",
        "# HELP dl_ingestion_file_lines_read_total Lines read from log files",
        "# TYPE dl_ingestion_file_lines_read_total counter",
        f"dl_ingestion_file_lines_read_total {file_stats.get('lines_read', 0)}",
        "# HELP dl_ingestion_file_parse_errors_total Parse errors in log files",
        "# TYPE dl_ingestion_file_parse_errors_total counter",
        f"dl_ingestion_file_parse_errors_total {file_stats.get('parse_errors', 0)}",
    ]

    response = Response(content="\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")
    return response


@app.get("/stats")
async def stats() -> Dict[str, Any]:
    """JSON ingestion statistics."""
    file_stats = get_file_stats()
    q_size = await queue_size()
    return {
        "counters": _counters,
        "file_ingest": file_stats,
        "queue_size": q_size,
        "uptime_note": "counters reset on restart",
    }


# ---------------------------------------------------------------------------
# Root
# ---------------------------------------------------------------------------

@app.get("/")
async def root():
    return {
        "service": "dynamic-labyrinth ingestion",
        "version": "1.0.0",
        "docs": "/docs",
    }
