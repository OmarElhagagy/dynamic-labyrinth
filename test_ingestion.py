"""
Tests for the ingestion service.

Covers:
  - Event normalization (file and webhook adapters, generic fallback)
  - HMAC signing and verification
  - Queue enqueue/dequeue (memory backend)
  - FastAPI HTTP endpoints (single, bulk, webhook, health)
  - Edge cases: malformed JSON, missing IP, oversized fields, replay attacks
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from httpx import AsyncClient

# Make sure the ingestion package is importable
sys.path.insert(0, str(Path(__file__).parent.parent / "ingestion"))

from hmac_utils import HEADER_SIGNATURE, HEADER_TIMESTAMP, sign_request, verify_request
from normalize import normalize, normalize_batch, _derive_session_id, _parse_timestamp
from schemas import EventType, NormalizedEvent, Protocol


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture
def sample_file_event() -> Dict[str, Any]:
    return {
        "type": "authentication_failed",
        "sensor": "ssh-honeypot-1",
        "start_time": "2025-10-16T19:00:00Z",
        "src-ip": "1.2.3.4",
        "dst-port": 22,
        "protocol": "ssh",
        "username": "root",
        "password": "password123",
    }


@pytest.fixture
def sample_webhook_event() -> Dict[str, Any]:
    return {
        "event_type": "http_request",
        "source_ip": "5.6.7.8",
        "destination_port": 80,
        "protocol": "http",
        "timestamp": "2025-10-16T20:00:00Z",
        "data": {"method": "GET", "url": "/admin/login"},
    }


@pytest.fixture
def sample_generic_event() -> Dict[str, Any]:
    return {
        "type": "port_scan",
        "source_ip": "10.0.0.1",
        "protocol": "tcp",
        "timestamp": "2025-10-17T08:00:00Z",
        "port": 3306,
    }


# ===========================================================================
# Normalization tests
# ===========================================================================

class TestNormalize:

    def test_file_event_ssh_auth_failed(self, sample_file_event):
        event = normalize(sample_file_event, source="file")
        assert event is not None
        assert isinstance(event, NormalizedEvent)
        assert event.protocol == Protocol.SSH
        assert event.event_type == EventType.AUTHENTICATION_FAILED
        assert event.source_ip == "1.2.3.4"
        assert event.session_id.startswith("src_")
        assert event.destination_port == 22
        assert any("user:root" in ind for ind in event.indicators)
        assert event.ingestion_source == "file"

    def test_webhook_event_http(self, sample_webhook_event):
        event = normalize(sample_webhook_event, source="http")
        assert event is not None
        assert event.protocol == Protocol.HTTP
        assert event.source_ip == "5.6.7.8"
        assert event.destination_port == 80

    def test_generic_event_fallback(self, sample_generic_event):
        event = normalize(sample_generic_event, source="generic")
        assert event is not None
        assert event.source_ip == "10.0.0.1"

    def test_missing_source_ip_returns_none(self):
        bad_event = {"type": "authentication_failed", "protocol": "ssh"}
        result = normalize(bad_event, source="file")
        assert result is None

    def test_empty_dict_returns_none(self):
        result = normalize({}, source="file")
        assert result is None

    def test_indicator_truncation(self, sample_file_event):
        sample_file_event["password"] = "x" * 1000
        event = normalize(sample_file_event, source="file")
        assert event is not None
        for ind in event.indicators:
            assert len(ind) <= 512

    def test_null_bytes_stripped(self, sample_file_event):
        sample_file_event["username"] = "root\x00\x00admin"
        event = normalize(sample_file_event, source="file")
        assert event is not None
        for ind in event.indicators:
            assert "\x00" not in ind

    def test_normalize_batch(self, sample_file_event, sample_webhook_event):
        batch = [sample_file_event, sample_webhook_event, {"bad": "record"}]
        results = normalize_batch(batch, source="file")
        # Two good, one bad (missing IP)
        assert len(results) == 2

    def test_event_id_format(self, sample_file_event):
        event = normalize(sample_file_event, source="file")
        assert event.id.startswith("evt-")
        assert len(event.id) > 4

    def test_session_id_stability(self):
        sid1 = _derive_session_id("1.2.3.4")
        sid2 = _derive_session_id("1.2.3.4")
        assert sid1 == sid2

    def test_session_id_different_ips(self):
        sid1 = _derive_session_id("1.2.3.4")
        sid2 = _derive_session_id("5.6.7.8")
        assert sid1 != sid2

    def test_timestamp_fallback_to_now(self, sample_file_event):
        sample_file_event.pop("start_time", None)
        event = normalize(sample_file_event, source="file")
        assert event is not None
        assert event.timestamp is not None

    def test_invalid_timestamp_format(self):
        ts = _parse_timestamp("not-a-date")
        assert ts is not None  # returns now()

    def test_unknown_protocol(self, sample_file_event):
        sample_file_event["protocol"] = "quic"
        event = normalize(sample_file_event, source="file")
        assert event is not None
        assert event.protocol == Protocol.UNKNOWN

    def test_http_scan_classification(self):
        event = normalize({
            "type": "scan",
            "src-ip": "9.9.9.9",
            "protocol": "http",
            "start_time": "2025-01-01T00:00:00Z",
        }, source="file")
        assert event is not None
        assert event.event_type == EventType.HTTP_SCAN


# ===========================================================================
# HMAC tests
# ===========================================================================

class TestHMAC:

    def test_sign_and_verify_roundtrip(self):
        body = b'{"test": 1}'
        headers = sign_request("POST", "/ingest/event", body, secret="test-secret")
        ok, reason = verify_request(
            method="POST",
            path="/ingest/event",
            body=body,
            timestamp_header=headers[HEADER_TIMESTAMP],
            signature_header=headers[HEADER_SIGNATURE],
            secret="test-secret",
        )
        assert ok is True
        assert reason == "ok"

    def test_wrong_secret_fails(self):
        body = b'{"test": 1}'
        headers = sign_request("POST", "/ingest/event", body, secret="correct-secret")
        ok, reason = verify_request(
            method="POST",
            path="/ingest/event",
            body=body,
            timestamp_header=headers[HEADER_TIMESTAMP],
            signature_header=headers[HEADER_SIGNATURE],
            secret="wrong-secret",
        )
        assert ok is False
        assert "mismatch" in reason.lower()

    def test_modified_body_fails(self):
        body = b'{"test": 1}'
        headers = sign_request("POST", "/ingest/event", body, secret="secret")
        ok, _ = verify_request(
            method="POST",
            path="/ingest/event",
            body=b'{"test": 2}',  # tampered
            timestamp_header=headers[HEADER_TIMESTAMP],
            signature_header=headers[HEADER_SIGNATURE],
            secret="secret",
        )
        assert ok is False

    def test_replay_attack_old_timestamp(self):
        body = b"{}"
        old_ts = int(time.time()) - 300  # 5 minutes ago
        headers = sign_request("POST", "/ingest/event", body, secret="secret", timestamp=old_ts)
        ok, reason = verify_request(
            method="POST",
            path="/ingest/event",
            body=body,
            timestamp_header=headers[HEADER_TIMESTAMP],
            signature_header=headers[HEADER_SIGNATURE],
            secret="secret",
        )
        assert ok is False
        assert "old" in reason.lower() or "future" in reason.lower()

    def test_missing_headers_fails(self):
        ok, reason = verify_request(
            method="POST",
            path="/ingest/event",
            body=b"{}",
            timestamp_header=None,
            signature_header=None,
        )
        assert ok is False
        assert "Missing" in reason

    def test_invalid_timestamp_header(self):
        ok, reason = verify_request(
            method="POST",
            path="/ingest/event",
            body=b"{}",
            timestamp_header="not-a-number",
            signature_header="aabbcc",
        )
        assert ok is False
        assert "Invalid" in reason

    def test_different_paths_fail(self):
        body = b"{}"
        headers = sign_request("POST", "/ingest/event", body, secret="secret")
        ok, _ = verify_request(
            method="POST",
            path="/ingest/other",  # different path
            body=body,
            timestamp_header=headers[HEADER_TIMESTAMP],
            signature_header=headers[HEADER_SIGNATURE],
            secret="secret",
        )
        assert ok is False


# ===========================================================================
# Queue tests (in-memory)
# ===========================================================================

class TestQueue:

    @pytest.mark.asyncio
    async def test_enqueue_and_dequeue(self, sample_file_event):
        from queue_manager import _memory_queue, enqueue, dequeue
        # Clear queue first
        while not _memory_queue.empty():
            try:
                _memory_queue.get_nowait()
            except Exception:
                break

        event = normalize(sample_file_event, source="file")
        assert event is not None

        with patch("queue_manager._get_redis", new=AsyncMock(return_value=None)):
            await enqueue(event)
            item = await dequeue()

        assert item is not None
        assert item["id"] == event.id
        assert item["source_ip"] == "1.2.3.4"

    @pytest.mark.asyncio
    async def test_queue_size(self):
        from queue_manager import queue_size, _memory_queue
        while not _memory_queue.empty():
            try:
                _memory_queue.get_nowait()
            except Exception:
                break

        with patch("queue_manager._get_redis", new=AsyncMock(return_value=None)):
            size = await queue_size()
        assert size == 0


# ===========================================================================
# HTTP endpoint tests
# ===========================================================================

@pytest.fixture
def client():
    """FastAPI TestClient with HMAC mocked out."""
    with patch("main.require_hmac", return_value=None):
        from main import app
        with TestClient(app) as c:
            yield c


class TestEndpoints:

    def test_root(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert resp.json()["service"] == "dynamic-labyrinth ingestion"

    def test_health(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
        assert "queue_size" in data

    def test_stats(self, client):
        resp = client.get("/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "counters" in data

    def test_metrics(self, client):
        resp = client.get("/metrics")
        assert resp.status_code == 200
        assert "dl_ingestion_events_accepted_total" in resp.text

    def test_ingest_single_valid_event(self, client, sample_file_event):
        with patch("main.enqueue", new=AsyncMock()):
            resp = client.post(
                "/ingest/event",
                json={"event": sample_file_event, "source": "file"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["event_id"].startswith("evt-")

    def test_ingest_single_missing_ip(self, client):
        bad_event = {"type": "auth_failed", "protocol": "ssh"}
        with patch("main.enqueue", new=AsyncMock()):
            resp = client.post(
                "/ingest/event",
                json={"event": bad_event, "source": "file"},
            )
        assert resp.status_code == 422

    def test_ingest_empty_event_body(self, client):
        with patch("main.enqueue", new=AsyncMock()):
            resp = client.post(
                "/ingest/event",
                json={"event": {}, "source": "file"},
            )
        assert resp.status_code == 422

    def test_ingest_bulk_valid(self, client, sample_file_event, sample_webhook_event):
        with patch("main.enqueue", new=AsyncMock()):
            resp = client.post(
                "/ingest/bulk",
                json={
                    "events": [sample_file_event, sample_webhook_event],
                    "source": "file",
                },
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["accepted"] >= 1

    def test_ingest_bulk_too_many(self, client):
        events = [{"x": i} for i in range(501)]
        resp = client.post("/ingest/bulk", json={"events": events, "source": "file"})
        assert resp.status_code == 422

    def test_honeytrap_webhook_valid(self, client, sample_webhook_event):
        with patch("main.enqueue", new=AsyncMock()):
            resp = client.post("/ingest/honeytrap", json=sample_webhook_event)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_honeytrap_webhook_invalid_json(self, client):
        resp = client.post(
            "/ingest/honeytrap",
            content=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400

    def test_honeytrap_webhook_missing_ip(self, client):
        with patch("main.enqueue", new=AsyncMock()):
            resp = client.post(
                "/ingest/honeytrap",
                json={"event_type": "scan", "protocol": "http"},
            )
        assert resp.status_code == 422


# ===========================================================================
# Integration test: full pipeline simulation
# ===========================================================================

class TestIntegration:
    """
    Simulates the full path: raw event → normalize → enqueue → (mock) Cerebrum.
    """

    @pytest.mark.asyncio
    async def test_full_pipeline_ssh_brute_force(self, sample_file_event):
        """
        Simulate 6 SSH auth failures from the same IP being normalized
        and enqueued, then delivered to a mock Cerebrum.
        """
        delivered: list = []

        async def mock_send(url: str, json_body: dict):
            delivered.extend(json_body.get("events", []))
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.raise_for_status = MagicMock()
            return mock_resp

        from queue_manager import _memory_queue

        # Clear queue
        while not _memory_queue.empty():
            try:
                _memory_queue.get_nowait()
            except Exception:
                break

        # Enqueue 6 auth-failure events
        with patch("queue_manager._get_redis", new=AsyncMock(return_value=None)):
            for i in range(6):
                event = normalize(sample_file_event, source="file")
                assert event is not None
                await asyncio.get_event_loop().run_in_executor(None, _memory_queue.put_nowait, event.model_dump(mode="json"))

        # Drain queue with mock sender
        from queue_manager import dequeue, _send_to_cerebrum_with_retry
        with patch("queue_manager._get_redis", new=AsyncMock(return_value=None)):
            with patch("queue_manager.signed_post", side_effect=mock_send):
                for _ in range(6):
                    item = await dequeue()
                    if item:
                        await _send_to_cerebrum_with_retry(item)

        assert len(delivered) == 6
        for evt in delivered:
            assert evt["event_type"] == "authentication_failed"
            assert evt["source_ip"] == "1.2.3.4"

    @pytest.mark.asyncio
    async def test_normalization_preserves_raw(self, sample_file_event):
        event = normalize(sample_file_event, source="file")
        assert event is not None
        assert event.raw == sample_file_event
