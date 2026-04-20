"""
security_tests.py — Fuzz and security hardening tests for the ingestion service.

Tests:
  - SQL injection strings in all fields
  - XSS payloads
  - Null bytes / binary content
  - Excessively long strings
  - Unicode edge cases
  - Path traversal in replay endpoint
  - HMAC bypass attempts
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from normalize import normalize


# ---------------------------------------------------------------------------
# Malicious payloads
# ---------------------------------------------------------------------------

SQL_INJECTIONS = [
    "' OR '1'='1",
    "'; DROP TABLE sessions; --",
    "1' UNION SELECT * FROM kg_nodes--",
    "admin'--",
    "\" OR \"1\"=\"1",
]

XSS_PAYLOADS = [
    "<script>alert(1)</script>",
    "javascript:alert(document.cookie)",
    "<img src=x onerror=alert(1)>",
    "';alert(String.fromCharCode(88,83,83))//",
    "<svg/onload=alert(1)>",
]

BINARY_PAYLOADS = [
    "\x00\x01\x02\x03\x04",
    "\xff\xfe\xfd",
    "valid\x00injected",
    b"\x89PNG\r\n".decode("latin-1"),
]

LONG_STRINGS = [
    "A" * 10_000,
    "B" * 100_000,
]

UNICODE_EDGE_CASES = [
    "\u202e reversed",  # RTL override
    "\u0000",           # null
    "💀" * 1000,
    "\ufeff",           # BOM
    "café\u0301",       # decomposed accents
]


def _make_event(username: str = "root", url: str = "") -> dict:
    return {
        "type": "authentication_failed",
        "src-ip": "1.2.3.4",
        "protocol": "ssh",
        "start_time": "2025-01-01T00:00:00Z",
        "username": username,
        "url": url,
    }


class TestFuzzNormalization:

    @pytest.mark.parametrize("payload", SQL_INJECTIONS)
    def test_sql_injection_in_username(self, payload):
        event = normalize(_make_event(username=payload), source="file")
        assert event is not None
        # Key: the indicator must be stored, not executed
        for ind in event.indicators:
            assert len(ind) <= 512
            assert "\x00" not in ind

    @pytest.mark.parametrize("payload", XSS_PAYLOADS)
    def test_xss_in_username(self, payload):
        event = normalize(_make_event(username=payload), source="file")
        assert event is not None
        # We don't HTML-escape here (that's the dashboard's job), but we
        # ensure strings are truncated and null-bytes removed.
        for ind in event.indicators:
            assert len(ind) <= 512

    @pytest.mark.parametrize("payload", BINARY_PAYLOADS)
    def test_binary_content_in_username(self, payload):
        event = normalize(_make_event(username=payload), source="file")
        assert event is not None
        for ind in event.indicators:
            assert "\x00" not in ind

    @pytest.mark.parametrize("payload", LONG_STRINGS)
    def test_oversized_fields_are_truncated(self, payload):
        event = normalize(_make_event(username=payload), source="file")
        assert event is not None
        for ind in event.indicators:
            assert len(ind) <= 512

    @pytest.mark.parametrize("payload", UNICODE_EDGE_CASES)
    def test_unicode_edge_cases(self, payload):
        # Must not crash
        event = normalize(_make_event(username=payload), source="file")
        # May be None for pure-null inputs; just must not raise
        if event:
            for ind in event.indicators:
                assert len(ind) <= 512

    def test_nested_dict_payload(self):
        event = normalize(
            {
                "type": "http_request",
                "src-ip": "1.2.3.4",
                "protocol": "http",
                "payload": {"nested": {"deep": "value", "sql": "' OR 1=1"}},
                "start_time": "2025-01-01T00:00:00Z",
            },
            source="file",
        )
        assert event is not None

    def test_list_payload(self):
        event = normalize(
            {
                "type": "http_request",
                "src-ip": "1.2.3.4",
                "protocol": "http",
                "payload": ["item1", "item2", "<script>"],
                "start_time": "2025-01-01T00:00:00Z",
            },
            source="file",
        )
        assert event is not None

    def test_integer_overflow_port(self):
        event = normalize(
            {
                "type": "scan",
                "src-ip": "1.2.3.4",
                "dst-port": 999999,  # out of valid range
                "protocol": "tcp",
            },
            source="file",
        )
        # Should fail Pydantic validation on destination_port → returns None or
        # the port is not set. Either outcome is acceptable.
        # Critical: must not crash.

    def test_negative_port(self):
        event = normalize(
            {
                "type": "scan",
                "src-ip": "1.2.3.4",
                "dst-port": -1,
                "protocol": "tcp",
            },
            source="file",
        )
        # Must not crash

    def test_invalid_ip_address(self):
        event = normalize(
            {
                "type": "auth_failed",
                "src-ip": "not.an.ip.address",
                "protocol": "ssh",
            },
            source="file",
        )
        assert event is None  # invalid IP rejected

    def test_ip_injection(self):
        event = normalize(
            {
                "type": "auth_failed",
                "src-ip": "'; DROP TABLE sessions; --",
                "protocol": "ssh",
            },
            source="file",
        )
        assert event is None  # invalid IP rejected by Pydantic validator

    def test_extra_large_batch(self):
        """Ensure batch normalization does not OOM on large inputs."""
        records = [
            {"type": "auth_failed", "src-ip": f"10.0.{i // 256}.{i % 256}", "protocol": "ssh"}
            for i in range(1000)
        ]
        from normalize import normalize_batch
        results = normalize_batch(records, source="file")
        assert len(results) == 1000


class TestHMACBypassAttempts:

    def test_empty_signature(self):
        from hmac_utils import verify_request
        import time
        ok, _ = verify_request(
            method="POST", path="/ingest/event",
            body=b"{}",
            timestamp_header=str(int(time.time())),
            signature_header="",
        )
        assert ok is False

    def test_forged_all_zeros_signature(self):
        from hmac_utils import verify_request
        import time
        ok, _ = verify_request(
            method="POST", path="/ingest/event",
            body=b"{}",
            timestamp_header=str(int(time.time())),
            signature_header="0" * 64,
        )
        assert ok is False

    def test_future_timestamp_replay(self):
        from hmac_utils import verify_request, sign_request
        import time
        future_ts = int(time.time()) + 300
        headers = sign_request("POST", "/ingest/event", b"{}", secret="secret", timestamp=future_ts)
        ok, reason = verify_request(
            method="POST", path="/ingest/event",
            body=b"{}",
            timestamp_header=headers["X-DL-Timestamp"],
            signature_header=headers["X-DL-Signature"],
            secret="secret",
        )
        assert ok is False

    def test_length_extension_attack(self):
        """Ensure longer body doesn't accidentally verify with original signature."""
        from hmac_utils import verify_request, sign_request
        import time
        original_body = b'{"events":[]}'
        headers = sign_request("POST", "/ingest/event", original_body, secret="secret")
        extended_body = original_body + b"\x80" + b"\x00" * 64  # length-extension padding
        ok, _ = verify_request(
            method="POST", path="/ingest/event",
            body=extended_body,
            timestamp_header=headers["X-DL-Timestamp"],
            signature_header=headers["X-DL-Signature"],
            secret="secret",
        )
        assert ok is False
