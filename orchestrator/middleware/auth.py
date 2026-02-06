"""
HMAC Authentication Middleware for the Orchestrator service.
Validates request signatures from trusted internal services.
"""

import hashlib
import hmac
import time
from collections.abc import Callable
from datetime import datetime

import structlog
from config import get_settings
from fastapi import Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

log = structlog.get_logger()


# =============================================================================
# HMAC Signature Utilities
# =============================================================================


def compute_hmac_signature(secret: str, method: str, path: str, body: bytes, timestamp: str) -> str:
    """
    Compute HMAC signature for a request.

    Format: HMAC-SHA256(secret, method + path + body + timestamp)
    """
    message = f"{method}{path}{body.decode('utf-8', errors='ignore')}{timestamp}"
    signature = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    return signature


def verify_hmac_signature(
    secret: str,
    method: str,
    path: str,
    body: bytes,
    timestamp: str,
    provided_signature: str,
    max_age_seconds: int = 300,
) -> bool:
    """
    Verify HMAC signature for a request.

    Returns True if signature is valid and timestamp is within acceptable range.
    """
    # Check timestamp freshness to prevent replay attacks
    try:
        request_time = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        now = datetime.utcnow().replace(tzinfo=request_time.tzinfo)
        age = abs((now - request_time).total_seconds())

        if age > max_age_seconds:
            log.warning("Request timestamp too old", age_seconds=age)
            return False
    except (ValueError, TypeError) as e:
        log.warning("Invalid timestamp format", timestamp=timestamp, error=str(e))
        return False

    # Compute expected signature
    expected_signature = compute_hmac_signature(secret, method, path, body, timestamp)

    # Constant-time comparison to prevent timing attacks
    return hmac.compare_digest(expected_signature, provided_signature)


def generate_hmac_headers(secret: str, method: str, path: str, body: bytes) -> dict:
    """
    Generate HMAC headers for making authenticated requests.

    Returns headers dict with signature and timestamp.
    """
    timestamp = datetime.utcnow().isoformat() + "Z"
    signature = compute_hmac_signature(secret, method, path, body, timestamp)

    return {"X-HMAC-Signature": signature, "X-HMAC-Timestamp": timestamp}


# =============================================================================
# HMAC Auth Middleware
# =============================================================================


class HMACAuthMiddleware(BaseHTTPMiddleware):
    """
    Middleware to validate HMAC signatures on incoming requests.

    Protected endpoints require:
    - X-HMAC-Signature: HMAC-SHA256 signature
    - X-HMAC-Timestamp: ISO timestamp of request
    """

    # Endpoints that don't require authentication
    EXEMPT_PATHS: list[str] = [
        "/",
        "/healthz",
        "/health",
        "/metrics",
        "/docs",
        "/openapi.json",
        "/redoc",
    ]

    # Endpoints that require authentication
    PROTECTED_PREFIXES: list[str] = ["/escalate", "/session", "/pools", "/admin"]

    def __init__(self, app, enforce: bool = True):
        super().__init__(app)
        self.settings = get_settings()
        self.enforce = enforce

    async def dispatch(self, request: Request, call_next: Callable):
        """Process the request and validate HMAC if required."""
        path = request.url.path

        # Skip auth for exempt paths
        if self._is_exempt(path):
            return await call_next(request)

        # Skip auth for non-protected paths if not enforcing globally
        if not self._is_protected(path) and not self.enforce:
            return await call_next(request)

        # Get HMAC headers
        signature = request.headers.get(self.settings.hmac_header_name)
        timestamp = request.headers.get("X-HMAC-Timestamp")

        if not signature or not timestamp:
            if self.settings.debug:
                # In debug mode, allow unauthenticated requests with warning
                log.warning("Missing HMAC headers (debug mode)", path=path)
                return await call_next(request)

            log.warning("Missing HMAC headers", path=path)
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Missing authentication headers"},
            )

        # Read and cache body for signature verification
        body = await request.body()

        # Verify signature
        if not verify_hmac_signature(
            secret=self.settings.hmac_secret,
            method=request.method,
            path=path,
            body=body,
            timestamp=timestamp,
            provided_signature=signature,
        ):
            log.warning("Invalid HMAC signature", path=path)
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED, content={"detail": "Invalid signature"}
            )

        # Signature valid, proceed
        log.debug("HMAC validation passed", path=path)
        return await call_next(request)

    def _is_exempt(self, path: str) -> bool:
        """Check if path is exempt from authentication."""
        return path in self.EXEMPT_PATHS

    def _is_protected(self, path: str) -> bool:
        """Check if path requires authentication."""
        return any(path.startswith(prefix) for prefix in self.PROTECTED_PREFIXES)


# =============================================================================
# Rate Limiting (using slowapi)
# =============================================================================


def get_rate_limit_key(request: Request) -> str:
    """
    Get the key for rate limiting.
    Uses X-Forwarded-For header if behind proxy, otherwise client IP.
    """
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        # Take the first IP in the chain
        return forwarded_for.split(",")[0].strip()

    return request.client.host if request.client else "unknown"


# =============================================================================
# Request Logging Middleware
# =============================================================================


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware to log all incoming requests."""

    async def dispatch(self, request: Request, call_next: Callable):
        start_time = time.time()

        # Process request
        response = await call_next(request)

        # Calculate duration
        duration_ms = (time.time() - start_time) * 1000

        # Log request
        log.info(
            "request",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=round(duration_ms, 2),
            client_ip=request.client.host if request.client else None,
        )

        return response
