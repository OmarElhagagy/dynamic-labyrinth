"""
Unit tests for the HMAC authentication middleware.
Tests match actual middleware implementation in middleware/auth.py
"""

import hashlib
import hmac
from datetime import datetime, timedelta

import pytest


class TestHMACSignature:
    """Tests for HMAC signature computation and verification."""

    def compute_signature(
        self, secret: str, method: str, path: str, body: str, timestamp: str
    ) -> str:
        """
        Compute HMAC signature matching the actual middleware format.
        Format: HMAC-SHA256(secret, method + path + body + timestamp)
        """
        message = f"{method}{path}{body}{timestamp}"
        return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()

    def generate_valid_headers(self, secret: str, method: str, path: str, body: str = "") -> dict:
        """Generate valid HMAC headers matching actual middleware."""
        timestamp = datetime.utcnow().isoformat() + "Z"
        signature = self.compute_signature(secret, method, path, body, timestamp)
        return {
            "X-HMAC-Signature": signature,
            "X-HMAC-Timestamp": timestamp,
            "Content-Type": "application/json",
        }

    @pytest.mark.asyncio
    async def test_compute_hmac_signature(self):
        """Test HMAC signature computation."""
        from middleware.auth import compute_hmac_signature

        secret = "test-secret"
        method = "POST"
        path = "/escalate"
        body = b'{"test": "data"}'
        timestamp = "2026-02-06T10:00:00Z"

        signature = compute_hmac_signature(secret, method, path, body, timestamp)

        # Verify signature is a hex string
        assert len(signature) == 64  # SHA256 produces 64 hex characters
        assert all(c in "0123456789abcdef" for c in signature)

    @pytest.mark.asyncio
    async def test_signature_changes_with_body(self):
        """Test that signature changes when body changes."""
        from middleware.auth import compute_hmac_signature

        secret = "test-secret"
        method = "POST"
        path = "/escalate"
        timestamp = "2026-02-06T10:00:00Z"

        sig1 = compute_hmac_signature(secret, method, path, b'{"a": 1}', timestamp)
        sig2 = compute_hmac_signature(secret, method, path, b'{"a": 2}', timestamp)

        assert sig1 != sig2

    @pytest.mark.asyncio
    async def test_signature_changes_with_secret(self):
        """Test that signature changes when secret changes."""
        from middleware.auth import compute_hmac_signature

        method = "POST"
        path = "/escalate"
        body = b'{"test": "data"}'
        timestamp = "2026-02-06T10:00:00Z"

        sig1 = compute_hmac_signature("secret1", method, path, body, timestamp)
        sig2 = compute_hmac_signature("secret2", method, path, body, timestamp)

        assert sig1 != sig2

    @pytest.mark.asyncio
    async def test_verify_valid_signature(self):
        """Test that valid signatures are verified."""
        from middleware.auth import compute_hmac_signature, verify_hmac_signature

        secret = "test-secret"
        method = "POST"
        path = "/escalate"
        body = b'{"test": "data"}'
        timestamp = datetime.utcnow().isoformat() + "Z"

        signature = compute_hmac_signature(secret, method, path, body, timestamp)

        result = verify_hmac_signature(
            secret=secret,
            method=method,
            path=path,
            body=body,
            timestamp=timestamp,
            provided_signature=signature,
        )

        assert result is True

    @pytest.mark.asyncio
    async def test_verify_invalid_signature_rejected(self):
        """Test that invalid signatures are rejected."""
        from middleware.auth import verify_hmac_signature

        secret = "test-secret"
        method = "POST"
        path = "/escalate"
        body = b'{"test": "data"}'
        timestamp = datetime.utcnow().isoformat() + "Z"

        result = verify_hmac_signature(
            secret=secret,
            method=method,
            path=path,
            body=body,
            timestamp=timestamp,
            provided_signature="invalid-signature",
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_verify_expired_timestamp_rejected(self):
        """Test that expired timestamps are rejected."""
        from middleware.auth import compute_hmac_signature, verify_hmac_signature

        secret = "test-secret"
        method = "POST"
        path = "/escalate"
        body = b'{"test": "data"}'

        # Timestamp from 10 minutes ago (past max_age of 5 minutes)
        old_time = datetime.utcnow() - timedelta(minutes=10)
        timestamp = old_time.isoformat() + "Z"

        signature = compute_hmac_signature(secret, method, path, body, timestamp)

        result = verify_hmac_signature(
            secret=secret,
            method=method,
            path=path,
            body=body,
            timestamp=timestamp,
            provided_signature=signature,
            max_age_seconds=300,  # 5 minutes
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_generate_hmac_headers(self):
        """Test HMAC header generation."""
        from middleware.auth import generate_hmac_headers

        secret = "test-secret"
        method = "POST"
        path = "/escalate"
        body = b'{"test": "data"}'

        headers = generate_hmac_headers(secret, method, path, body)

        assert "X-HMAC-Signature" in headers
        assert "X-HMAC-Timestamp" in headers
        assert len(headers["X-HMAC-Signature"]) == 64
        assert headers["X-HMAC-Timestamp"].endswith("Z")


class TestHMACMiddleware:
    """Tests for HMAC authentication middleware."""

    @pytest.mark.asyncio
    async def test_exempt_paths_bypass_auth(self, client):
        """Test that exempt paths don't require authentication."""
        # /healthz is exempt - uses client with auth disabled but pool manager injected
        response = await client.get("/healthz")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_missing_headers_rejected(self, auth_client):
        """Test that requests without HMAC headers are rejected."""
        import json

        body = json.dumps(
            {
                "session_id": "test-123",
                "action": "escalate_to_level_2",
                "rule_id": "rule-001",
                "skill_score_after": 5,
                "explanation": "Test",
            }
        )

        response = await auth_client.post(
            "/escalate",
            content=body,
            headers={"Content-Type": "application/json"},
        )

        # Should reject - missing auth headers
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_missing_signature_rejected(self, auth_client):
        """Test that missing signature is rejected."""
        import json

        timestamp = datetime.utcnow().isoformat() + "Z"
        body = json.dumps(
            {
                "session_id": "test-123",
                "action": "escalate_to_level_2",
                "rule_id": "rule-001",
                "skill_score_after": 5,
                "explanation": "Test",
            }
        )

        response = await auth_client.post(
            "/escalate",
            content=body,
            headers={
                "X-HMAC-Timestamp": timestamp,
                "Content-Type": "application/json",
            },
        )

        # Should reject - missing signature
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_missing_timestamp_rejected(self, auth_client):
        """Test that missing timestamp is rejected."""
        import json

        body = json.dumps(
            {
                "session_id": "test-123",
                "action": "escalate_to_level_2",
                "rule_id": "rule-001",
                "skill_score_after": 5,
                "explanation": "Test",
            }
        )

        response = await auth_client.post(
            "/escalate",
            content=body,
            headers={
                "X-HMAC-Signature": "fake-signature",
                "Content-Type": "application/json",
            },
        )

        # Should reject - missing timestamp
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_valid_request_accepted(self, auth_client):
        """Test that valid HMAC request is accepted."""
        import json

        from config import get_settings
        from middleware.auth import generate_hmac_headers

        settings = get_settings()

        body_dict = {
            "session_id": "test-123",
            "action": "escalate_to_level_2",
            "rule_id": "rule-001",
            "skill_score_after": 5,
            "explanation": "Test escalation",
        }
        body = json.dumps(body_dict)

        headers = generate_hmac_headers(
            secret=settings.hmac_secret, method="POST", path="/escalate", body=body.encode()
        )
        headers["Content-Type"] = "application/json"

        response = await auth_client.post(
            "/escalate",
            content=body,
            headers=headers,
        )

        # Should accept valid signature (may fail on pool exhausted, but auth passed)
        assert response.status_code in [200, 503]

    @pytest.mark.asyncio
    async def test_invalid_signature_rejected(self, auth_client):
        """Test that invalid signature is rejected."""
        import json

        timestamp = datetime.utcnow().isoformat() + "Z"
        body = json.dumps(
            {
                "session_id": "test-123",
                "action": "escalate_to_level_2",
                "rule_id": "rule-001",
                "skill_score_after": 5,
                "explanation": "Test",
            }
        )

        response = await auth_client.post(
            "/escalate",
            content=body,
            headers={
                "X-HMAC-Signature": "invalid-signature-here",
                "X-HMAC-Timestamp": timestamp,
                "Content-Type": "application/json",
            },
        )

        # Should reject invalid signature
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_tampered_body_rejected(self, auth_client):
        """Test that tampered body is rejected."""
        import json

        from config import get_settings
        from middleware.auth import generate_hmac_headers

        settings = get_settings()

        original_body = json.dumps(
            {
                "session_id": "test-123",
                "action": "escalate_to_level_2",
                "rule_id": "rule-001",
                "skill_score_after": 5,
                "explanation": "Original",
            }
        )

        tampered_body = json.dumps(
            {
                "session_id": "test-123",
                "action": "escalate_to_level_3",  # Tampered!
                "rule_id": "rule-001",
                "skill_score_after": 5,
                "explanation": "Tampered",
            }
        )

        # Sign with original body
        headers = generate_hmac_headers(
            secret=settings.hmac_secret,
            method="POST",
            path="/escalate",
            body=original_body.encode(),
        )
        headers["Content-Type"] = "application/json"

        # Send tampered body
        response = await auth_client.post(
            "/escalate",
            content=tampered_body,
            headers=headers,
        )

        # Should reject - body doesn't match signature
        assert response.status_code == 401


class TestRateLimiting:
    """Tests for rate limiting functionality."""

    @pytest.mark.asyncio
    async def test_rate_limiter_configured(self):
        """Test that rate limiter is configured in main app."""
        from main import limiter

        assert limiter is not None

    @pytest.mark.asyncio
    async def test_rate_limit_allows_normal_requests(self, client):
        """Test that normal request rate is allowed."""
        # Multiple requests to health endpoint should be allowed
        for _ in range(5):
            response = await client.get("/healthz")
            assert response.status_code == 200
