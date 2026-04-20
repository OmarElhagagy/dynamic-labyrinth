"""
HMAC authentication utilities for dynamic-labyrinth inter-service communication.

Every internal HTTP request carries two headers:
  X-DL-Timestamp : Unix epoch (seconds) at signing time
  X-DL-Signature : HMAC-SHA256(secret, "METHOD\nPATH\nTIMESTAMP\nSHA256(BODY)")

Requests older than REPLAY_WINDOW_SECONDS are rejected to prevent replay attacks.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

HMAC_SECRET: str = os.environ.get("HMAC_SECRET", "change-me-in-production")
REPLAY_WINDOW_SECONDS: int = int(os.environ.get("HMAC_REPLAY_WINDOW", "30"))

# Header names
HEADER_TIMESTAMP = "X-DL-Timestamp"
HEADER_SIGNATURE = "X-DL-Signature"


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------

def _body_hash(body: bytes) -> str:
    """Return lowercase hex SHA-256 of raw body bytes."""
    return hashlib.sha256(body).hexdigest()


def _build_signing_string(method: str, path: str, timestamp: str, body_hash: str) -> bytes:
    """Construct the canonical string that is fed into HMAC."""
    canonical = f"{method.upper()}\n{path}\n{timestamp}\n{body_hash}"
    return canonical.encode("utf-8")


def sign_request(
    method: str,
    path: str,
    body: bytes = b"",
    secret: str = HMAC_SECRET,
    timestamp: Optional[int] = None,
) -> dict[str, str]:
    """
    Produce the auth headers for an outgoing inter-service request.

    Returns a dict of HTTP headers to merge into the request.
    """
    ts = str(timestamp if timestamp is not None else int(time.time()))
    bh = _body_hash(body)
    signing_string = _build_signing_string(method, path, ts, bh)
    sig = hmac.new(secret.encode("utf-8"), signing_string, hashlib.sha256).hexdigest()
    return {
        HEADER_TIMESTAMP: ts,
        HEADER_SIGNATURE: sig,
    }


def verify_request(
    method: str,
    path: str,
    body: bytes,
    timestamp_header: Optional[str],
    signature_header: Optional[str],
    secret: str = HMAC_SECRET,
) -> tuple[bool, str]:
    """
    Verify an incoming request's HMAC headers.

    Returns (ok: bool, reason: str).
    """
    if not timestamp_header or not signature_header:
        return False, "Missing HMAC headers"

    # --- Replay check ---
    try:
        ts = int(timestamp_header)
    except ValueError:
        return False, "Invalid timestamp header"

    age = int(time.time()) - ts
    if abs(age) > REPLAY_WINDOW_SECONDS:
        return False, f"Request timestamp too old or too far in future (age={age}s)"

    # --- Signature check ---
    bh = _body_hash(body)
    signing_string = _build_signing_string(method, path, timestamp_header, bh)
    expected = hmac.new(secret.encode("utf-8"), signing_string, hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected, signature_header.lower()):
        logger.warning("HMAC mismatch on %s %s", method, path)
        return False, "Signature mismatch"

    return True, "ok"


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------

from fastapi import Header, HTTPException, Request


async def require_hmac(
    request: Request,
    x_dl_timestamp: Optional[str] = Header(None),
    x_dl_signature: Optional[str] = Header(None),
) -> None:
    """
    FastAPI dependency that enforces HMAC auth on a route.

    Usage:
        @app.post("/ingest/event", dependencies=[Depends(require_hmac)])
    """
    body = await request.body()
    ok, reason = verify_request(
        method=request.method,
        path=request.url.path,
        body=body,
        timestamp_header=x_dl_timestamp,
        signature_header=x_dl_signature,
    )
    if not ok:
        logger.warning("HMAC auth failed: %s | path=%s", reason, request.url.path)
        raise HTTPException(status_code=401, detail=f"Unauthorized: {reason}")


# ---------------------------------------------------------------------------
# Async httpx session factory with HMAC headers
# ---------------------------------------------------------------------------

import httpx


def build_signed_headers(method: str, path: str, body: bytes = b"") -> dict[str, str]:
    """Return a complete set of headers including HMAC for use with httpx."""
    auth_headers = sign_request(method=method, path=path, body=body)
    return {
        "Content-Type": "application/json",
        **auth_headers,
    }


async def signed_post(url: str, json_body: dict) -> httpx.Response:
    """
    Perform a HMAC-signed POST to another internal service.
    Raises httpx.HTTPError on network/HTTP failures.
    """
    import json

    raw_body = json.dumps(json_body).encode("utf-8")
    from urllib.parse import urlparse
    parsed = urlparse(url)
    path = parsed.path

    headers = build_signed_headers("POST", path, raw_body)
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(url, content=raw_body, headers=headers)
        resp.raise_for_status()
        return resp
