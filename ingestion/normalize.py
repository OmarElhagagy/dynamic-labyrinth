"""
Normalization layer: converts raw Honeytrap events (file-pusher JSONL or
webhook JSON) into the canonical NormalizedEvent schema consumed by Cerebrum.

Design goals
------------
* Defensive: every field access is wrapped in try/except; bad records are
  logged and returned as None so the caller can discard them gracefully.
* Extensible: add a new adapter by registering a function in ADAPTER_MAP.
* No external I/O in this module — pure data transformation.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from schemas import (
    EventType,
    HoneytrapFileEvent,
    HoneytrapWebhookEvent,
    NormalizedEvent,
    Protocol,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------

_TIMESTAMP_FORMATS = [
    "%Y-%m-%dT%H:%M:%S.%fZ",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%d %H:%M:%S",
]


def _parse_timestamp(raw: Optional[str]) -> datetime:
    """Best-effort ISO-8601 / custom timestamp parser; returns UTC now on failure."""
    if not raw:
        return datetime.now(timezone.utc)
    for fmt in _TIMESTAMP_FORMATS:
        try:
            dt = datetime.strptime(raw, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    logger.debug("Could not parse timestamp %r, using now()", raw)
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Session ID derivation
# ---------------------------------------------------------------------------

def _derive_session_id(source_ip: str) -> str:
    """
    Build a stable session identifier from the source IP.
    Format: src_<sha256_prefix_8>  (e.g. src_a3f2c1b4)
    Using only source IP keeps the session concept simple; extend with
    (ip, dst_port, protocol) tuple if finer granularity is needed.
    """
    digest = hashlib.sha256(source_ip.encode()).hexdigest()[:8]
    return f"src_{digest}"


# ---------------------------------------------------------------------------
# Protocol & event-type classification
# ---------------------------------------------------------------------------

_PROTOCOL_MAP: Dict[str, Protocol] = {
    "ssh": Protocol.SSH,
    "http": Protocol.HTTP,
    "https": Protocol.HTTPS,
    "ftp": Protocol.FTP,
    "telnet": Protocol.TELNET,
    "mysql": Protocol.MYSQL,
    "redis": Protocol.REDIS,
}

_EVENT_TYPE_KEYWORD_MAP: List[tuple[List[str], EventType]] = [
    (["auth", "login", "authentication", "failed", "failure"], EventType.AUTHENTICATION_FAILED),
    (["auth-success", "login-success", "authentication_success"], EventType.AUTHENTICATION_SUCCESS),
    (["session-opened", "session_opened", "connection-opened"], EventType.SESSION_OPENED),
    (["session-closed", "session_closed", "connection-closed"], EventType.SESSION_CLOSED),
    (["command", "exec", "shell"], EventType.COMMAND_EXECUTED),
    (["scan", "probe", "spider"], EventType.HTTP_SCAN),
    (["exploit", "payload", "injection", "sqli", "xss", "rce", "lfi", "rfi"], EventType.HTTP_EXPLOIT_ATTEMPT),
    (["http-request", "http_request", "web-request"], EventType.HTTP_REQUEST),
    (["connect", "connection"], EventType.CONNECTION_ATTEMPT),
    (["exfil", "upload", "data-out"], EventType.DATA_EXFILTRATION),
]


def _classify_protocol(raw_proto: Optional[str]) -> Protocol:
    if not raw_proto:
        return Protocol.UNKNOWN
    return _PROTOCOL_MAP.get(raw_proto.lower().strip(), Protocol.UNKNOWN)


def _classify_event_type(type_str: Optional[str], protocol: Protocol) -> EventType:
    if not type_str:
        return EventType.UNKNOWN

    lower = type_str.lower()

    for keywords, et in _EVENT_TYPE_KEYWORD_MAP:
        if any(kw in lower for kw in keywords):
            return et

    # Protocol-specific fallbacks
    if protocol == Protocol.HTTP:
        return EventType.HTTP_REQUEST
    if protocol == Protocol.SSH:
        return EventType.CONNECTION_ATTEMPT

    return EventType.UNKNOWN


# ---------------------------------------------------------------------------
# Indicator extraction
# ---------------------------------------------------------------------------

def _extract_indicators_file(raw: HoneytrapFileEvent) -> List[str]:
    indicators: List[str] = []
    if raw.username:
        indicators.append(f"user:{raw.username[:128]}")
    if raw.password:
        # Never log the full password — just its presence and first 2 chars
        indicators.append(f"password_attempt:{raw.password[:2]}***")
    if raw.url:
        indicators.append(f"url:{raw.url[:512]}")
    if raw.method:
        indicators.append(f"method:{raw.method}")
    if raw.payload:
        payload_str = str(raw.payload)[:256]
        indicators.append(f"payload:{payload_str}")
    # Collect any extra unknown fields
    extra = raw.model_extra or {}
    for k, v in extra.items():
        if v and k not in ("type", "sensor"):
            indicators.append(f"{k}:{str(v)[:128]}")
    return indicators


def _extract_indicators_webhook(raw: HoneytrapWebhookEvent) -> List[str]:
    indicators: List[str] = []
    if raw.data:
        for k, v in raw.data.items():
            indicators.append(f"{k}:{str(v)[:256]}")
    return indicators


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------

def _normalize_file_event(record: Dict[str, Any], source: str = "file") -> Optional[NormalizedEvent]:
    """Adapter for Honeytrap file-pusher JSONL records."""
    try:
        raw = HoneytrapFileEvent.model_validate(record)

        src_ip = raw.source_ip or record.get("src-ip") or record.get("source_ip") or ""
        if not src_ip:
            logger.warning("File event missing source IP, skipping: %r", record)
            return None

        protocol = _classify_protocol(raw.protocol)
        event_type = _classify_event_type(raw.type, protocol)
        timestamp = _parse_timestamp(raw.start_time or raw.end_time)
        session_id = _derive_session_id(src_ip)
        indicators = _extract_indicators_file(raw)

        return NormalizedEvent(
            id=f"evt-{uuid.uuid4().hex[:16]}",
            session_id=session_id,
            timestamp=timestamp,
            protocol=protocol,
            event_type=event_type,
            indicators=indicators,
            source_ip=src_ip,
            destination_port=raw.destination_port,
            raw=record,
            ingestion_source=source,
        )
    except Exception as exc:
        logger.error("Failed to normalize file event: %s | record=%r", exc, record)
        return None


def _normalize_webhook_event(record: Dict[str, Any], source: str = "http") -> Optional[NormalizedEvent]:
    """Adapter for Honeytrap HTTP-pusher webhook records."""
    try:
        raw = HoneytrapWebhookEvent.model_validate(record)

        src_ip = raw.source_ip or ""
        if not src_ip:
            logger.warning("Webhook event missing source IP, skipping")
            return None

        protocol = _classify_protocol(raw.protocol)
        event_type = _classify_event_type(raw.event_type, protocol)
        timestamp = _parse_timestamp(raw.timestamp)
        session_id = _derive_session_id(src_ip)
        indicators = _extract_indicators_webhook(raw)

        return NormalizedEvent(
            id=f"evt-{uuid.uuid4().hex[:16]}",
            session_id=session_id,
            timestamp=timestamp,
            protocol=protocol,
            event_type=event_type,
            indicators=indicators,
            source_ip=src_ip,
            destination_port=raw.destination_port,
            raw=record,
            ingestion_source=source,
        )
    except Exception as exc:
        logger.error("Failed to normalize webhook event: %s | record=%r", exc, record)
        return None


def _normalize_generic(record: Dict[str, Any], source: str = "unknown") -> Optional[NormalizedEvent]:
    """
    Fallback adapter: tries to map any dict to NormalizedEvent using common
    field name heuristics. Accepts both snake_case and kebab-case variants.
    """
    try:
        def _get(*keys: str) -> Any:
            for k in keys:
                v = record.get(k)
                if v is not None:
                    return v
            return None

        src_ip = _get("source_ip", "src-ip", "src_ip", "srcip", "src") or ""
        if not src_ip:
            logger.warning("Generic event missing source IP, skipping")
            return None

        raw_proto = _get("protocol", "proto") or ""
        raw_type = _get("type", "event_type", "event-type", "action") or ""
        raw_ts = _get("timestamp", "start_time", "time", "date") or ""

        protocol = _classify_protocol(str(raw_proto))
        event_type = _classify_event_type(str(raw_type), protocol)
        timestamp = _parse_timestamp(str(raw_ts) if raw_ts else None)
        session_id = _derive_session_id(str(src_ip))

        indicators: List[str] = []
        for k in ("username", "user", "password", "url", "path", "payload", "cmd", "command"):
            v = _get(k)
            if v:
                indicators.append(f"{k}:{str(v)[:256]}")

        dst_port = _get("destination_port", "dst-port", "dst_port", "port")
        if dst_port is not None:
            try:
                dst_port = int(dst_port)
            except (TypeError, ValueError):
                dst_port = None

        return NormalizedEvent(
            id=f"evt-{uuid.uuid4().hex[:16]}",
            session_id=session_id,
            timestamp=timestamp,
            protocol=protocol,
            event_type=event_type,
            indicators=indicators,
            source_ip=str(src_ip),
            destination_port=dst_port,
            raw=record,
            ingestion_source=source,
        )
    except Exception as exc:
        logger.error("Failed to normalize generic event: %s | record=%r", exc, record)
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

ADAPTER_MAP = {
    "file": _normalize_file_event,
    "http": _normalize_webhook_event,
    "webhook": _normalize_webhook_event,
    "generic": _normalize_generic,
}


def normalize(record: Dict[str, Any], source: str = "generic") -> Optional[NormalizedEvent]:
    """
    Normalize a raw event dict into a NormalizedEvent.

    Args:
        record: Raw event dict (any supported pusher format).
        source: Origin hint — one of 'file', 'http', 'webhook', 'generic'.

    Returns:
        NormalizedEvent on success, None if the record is invalid/unrecognizable.
    """
    adapter = ADAPTER_MAP.get(source, _normalize_generic)
    result = adapter(record, source)
    if result:
        logger.debug(
            "Normalized event id=%s session=%s type=%s proto=%s",
            result.id, result.session_id, result.event_type, result.protocol,
        )
    return result


def normalize_batch(records: List[Dict[str, Any]], source: str = "generic") -> List[NormalizedEvent]:
    """Normalize a list of raw records; silently skips invalid ones."""
    results: List[NormalizedEvent] = []
    for rec in records:
        evt = normalize(rec, source=source)
        if evt:
            results.append(evt)
    logger.info("Batch normalization: %d/%d succeeded (source=%s)", len(results), len(records), source)
    return results
