"""
Event queue abstraction for dynamic-labyrinth ingestion.

Supports two backends:
  1. Redis (recommended for production)  — REDIS_URL env var must be set.
  2. In-memory asyncio.Queue            — automatic fallback when Redis is unavailable.

Messages are serialised to JSON.  The worker coroutine drains the queue and
forwards events to Cerebrum with exponential-backoff retry.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

import httpx

from hmac_utils import signed_post
from schemas import NormalizedEvent

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

REDIS_URL: str = os.environ.get("REDIS_URL", "")
CEREBRUM_URL: str = os.environ.get("CEREBRUM_URL", "http://cerebrum:8001")
CEREBRUM_EVENTS_ENDPOINT: str = f"{CEREBRUM_URL}/events"

RETRY_DELAYS: List[float] = [1.0, 2.0, 4.0, 8.0, 16.0]  # seconds
QUEUE_NAME: str = "dl:ingestion:events"
MAX_MEMORY_QUEUE: int = 10_000


# ---------------------------------------------------------------------------
# In-memory fallback queue
# ---------------------------------------------------------------------------

_memory_queue: asyncio.Queue = asyncio.Queue(maxsize=MAX_MEMORY_QUEUE)


async def _enqueue_memory(event: NormalizedEvent) -> None:
    try:
        _memory_queue.put_nowait(event.model_dump(mode="json"))
        logger.debug("Enqueued event %s to memory queue (size=%d)", event.id, _memory_queue.qsize())
    except asyncio.QueueFull:
        logger.error("Memory queue full! Dropping event %s", event.id)


async def _dequeue_memory() -> Optional[Dict[str, Any]]:
    try:
        return await asyncio.wait_for(_memory_queue.get(), timeout=1.0)
    except asyncio.TimeoutError:
        return None


# ---------------------------------------------------------------------------
# Redis queue
# ---------------------------------------------------------------------------

_redis_client = None


async def _get_redis():
    global _redis_client
    if _redis_client is None and REDIS_URL:
        try:
            import redis.asyncio as aioredis  # type: ignore
            _redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)
            await _redis_client.ping()
            logger.info("Redis connected at %s", REDIS_URL)
        except Exception as exc:
            logger.warning("Redis unavailable (%s); falling back to memory queue", exc)
            _redis_client = None
    return _redis_client


async def _enqueue_redis(redis, event: NormalizedEvent) -> None:
    payload = json.dumps(event.model_dump(mode="json"))
    await redis.rpush(QUEUE_NAME, payload)
    logger.debug("Pushed event %s to Redis queue", event.id)


async def _dequeue_redis(redis) -> Optional[Dict[str, Any]]:
    result = await redis.blpop(QUEUE_NAME, timeout=1)
    if result is None:
        return None
    _queue_name, raw = result
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Public enqueue / dequeue
# ---------------------------------------------------------------------------

async def enqueue(event: NormalizedEvent) -> None:
    """Push a normalized event onto the queue (Redis if available, else memory)."""
    redis = await _get_redis()
    if redis:
        try:
            await _enqueue_redis(redis, event)
            return
        except Exception as exc:
            logger.warning("Redis push failed (%s); falling back to memory queue", exc)
    await _enqueue_memory(event)


async def dequeue() -> Optional[Dict[str, Any]]:
    """
    Pop one event from the queue.  Returns None if the queue is empty
    (non-blocking from caller perspective — caller should loop).
    """
    redis = await _get_redis()
    if redis:
        try:
            item = await _dequeue_redis(redis)
            return item
        except Exception as exc:
            logger.warning("Redis dequeue failed (%s); falling back to memory queue", exc)
    return await _dequeue_memory()


async def queue_size() -> int:
    """Return approximate queue depth."""
    redis = await _get_redis()
    if redis:
        try:
            return await redis.llen(QUEUE_NAME)
        except Exception:
            pass
    return _memory_queue.qsize()


# ---------------------------------------------------------------------------
# Retry sender
# ---------------------------------------------------------------------------

async def _send_to_cerebrum_with_retry(payload: Dict[str, Any]) -> bool:
    """
    Attempt to POST a normalized event to Cerebrum.
    Returns True on success, False after all retries exhausted.
    """
    body = [payload]  # Cerebrum /events accepts a list
    for attempt, delay in enumerate(RETRY_DELAYS, start=1):
        try:
            resp = await signed_post(CEREBRUM_EVENTS_ENDPOINT, {"events": body})
            logger.info(
                "Sent event %s to Cerebrum (attempt %d) → HTTP %d",
                payload.get("id"), attempt, resp.status_code,
            )
            return True
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "Cerebrum returned HTTP %d for event %s (attempt %d)",
                exc.response.status_code, payload.get("id"), attempt,
            )
        except httpx.RequestError as exc:
            logger.warning(
                "Network error sending event %s (attempt %d): %s",
                payload.get("id"), attempt, exc,
            )
        if attempt < len(RETRY_DELAYS):
            logger.debug("Retrying in %.1fs…", delay)
            await asyncio.sleep(delay)

    logger.error("Permanently failed to deliver event %s after %d attempts", payload.get("id"), len(RETRY_DELAYS))
    return False


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------

async def queue_worker() -> None:
    """
    Long-running coroutine — drains the queue and forwards events to Cerebrum.
    Run this as an asyncio background task:
        asyncio.create_task(queue_worker())
    """
    logger.info("Queue worker started (cerebrum=%s)", CEREBRUM_EVENTS_ENDPOINT)
    consecutive_failures = 0

    while True:
        try:
            item = await dequeue()
            if item is None:
                # Nothing in queue; brief sleep to avoid busy-loop
                await asyncio.sleep(0.1)
                continue

            success = await _send_to_cerebrum_with_retry(item)
            if success:
                consecutive_failures = 0
            else:
                consecutive_failures += 1
                if consecutive_failures >= 10:
                    logger.critical(
                        "Cerebrum unreachable for %d consecutive events — pausing worker 30s",
                        consecutive_failures,
                    )
                    await asyncio.sleep(30)
                    consecutive_failures = 0

        except asyncio.CancelledError:
            logger.info("Queue worker cancelled — shutting down")
            break
        except Exception as exc:
            logger.exception("Unexpected error in queue worker: %s", exc)
            await asyncio.sleep(1)


# ---------------------------------------------------------------------------
# Dead-letter log (last-resort persistence)
# ---------------------------------------------------------------------------

DLQ_PATH = os.environ.get("DLQ_PATH", "/tmp/dl_dead_letter.jsonl")


async def write_dead_letter(payload: Dict[str, Any]) -> None:
    """Append a failed event to the dead-letter file for manual recovery."""
    try:
        with open(DLQ_PATH, "a") as fh:
            fh.write(json.dumps(payload) + "\n")
        logger.warning("Event %s written to dead-letter file %s", payload.get("id"), DLQ_PATH)
    except OSError as exc:
        logger.error("Cannot write dead-letter file: %s", exc)
