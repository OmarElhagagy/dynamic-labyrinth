"""
Pydantic schemas for event validation and normalization.
Enforces the canonical event format used across all dynamic-labyrinth services.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator, IPvAnyAddress


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class Protocol(str, Enum):
    SSH = "ssh"
    HTTP = "http"
    HTTPS = "https"
    FTP = "ftp"
    TELNET = "telnet"
    MYSQL = "mysql"
    REDIS = "redis"
    UNKNOWN = "unknown"


class EventType(str, Enum):
    # SSH
    AUTHENTICATION_FAILED = "authentication_failed"
    AUTHENTICATION_SUCCESS = "authentication_success"
    SESSION_OPENED = "session_opened"
    SESSION_CLOSED = "session_closed"
    COMMAND_EXECUTED = "command_executed"
    # HTTP
    HTTP_REQUEST = "http_request"
    HTTP_SCAN = "http_scan"
    HTTP_EXPLOIT_ATTEMPT = "http_exploit_attempt"
    # Generic
    CONNECTION_ATTEMPT = "connection_attempt"
    DATA_EXFILTRATION = "data_exfiltration"
    PORT_SCAN = "port_scan"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Canonical (normalized) event — output of ingestion, input to Cerebrum
# ---------------------------------------------------------------------------

class NormalizedEvent(BaseModel):
    """Canonical event schema shared by all dynamic-labyrinth services."""

    id: str = Field(..., description="Unique event ID, e.g. evt-<uuid>")
    session_id: str = Field(..., description="Session identifier derived from source IP")
    timestamp: datetime = Field(..., description="ISO-8601 UTC timestamp")
    protocol: Protocol = Field(..., description="Network protocol")
    event_type: EventType = Field(..., description="Classified event type")
    indicators: List[str] = Field(default_factory=list, description="IOC strings / usernames / payloads")
    source_ip: str = Field(..., description="Attacker source IP address")
    destination_port: Optional[int] = Field(None, ge=1, le=65535)
    raw: Optional[Dict[str, Any]] = Field(None, description="Original raw record for traceability")
    ingestion_source: Optional[str] = Field(None, description="file | http | kafka | elasticsearch")

    @field_validator("source_ip")
    @classmethod
    def validate_ip(cls, v: str) -> str:
        # Allow both IPv4 and IPv6; raises ValueError on bad input
        try:
            IPvAnyAddress(v)
        except Exception:
            raise ValueError(f"Invalid IP address: {v!r}")
        return v

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        if not re.match(r"^[\w\-]{1,128}$", v):
            raise ValueError(f"Event ID contains invalid characters: {v!r}")
        return v

    @field_validator("session_id")
    @classmethod
    def validate_session_id(cls, v: str) -> str:
        if not re.match(r"^[\w\-\.]{1,256}$", v):
            raise ValueError(f"Session ID contains invalid characters: {v!r}")
        return v

    @field_validator("indicators")
    @classmethod
    def sanitize_indicators(cls, v: List[str]) -> List[str]:
        sanitized = []
        for item in v:
            # Truncate excessively long strings; strip null bytes
            clean = item.replace("\x00", "").strip()[:512]
            sanitized.append(clean)
        return sanitized

    model_config = {"json_encoders": {datetime: lambda dt: dt.isoformat()}}


# ---------------------------------------------------------------------------
# Raw Honeytrap event shapes (various pusher formats)
# ---------------------------------------------------------------------------

class HoneytrapFileEvent(BaseModel):
    """Raw event emitted by Honeytrap's file pusher (JSONL lines)."""

    type: str
    sensor: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    source_ip: Optional[str] = Field(None, alias="src-ip")
    destination_port: Optional[int] = Field(None, alias="dst-port")
    protocol: Optional[str] = None
    payload: Optional[Any] = None
    # auth-specific
    username: Optional[str] = None
    password: Optional[str] = None
    # http-specific
    method: Optional[str] = None
    url: Optional[str] = None
    headers: Optional[Dict[str, str]] = None

    model_config = {"populate_by_name": True, "extra": "allow"}


class HoneytrapWebhookEvent(BaseModel):
    """Raw event posted by Honeytrap's HTTP pusher (webhook)."""

    event_type: str
    source_ip: str
    destination_port: Optional[int] = None
    protocol: Optional[str] = None
    timestamp: Optional[str] = None
    data: Optional[Dict[str, Any]] = None

    model_config = {"extra": "allow"}


# ---------------------------------------------------------------------------
# Ingestion HTTP endpoint request/response models
# ---------------------------------------------------------------------------

class IngestRequest(BaseModel):
    """Body for POST /ingest/event."""

    event: Dict[str, Any] = Field(..., description="Raw event object (any pusher format)")
    source: str = Field("http", description="Pusher source identifier")

    @field_validator("event")
    @classmethod
    def check_non_empty(cls, v: Dict[str, Any]) -> Dict[str, Any]:
        if not v:
            raise ValueError("event payload must not be empty")
        return v


class IngestResponse(BaseModel):
    ok: bool
    event_id: Optional[str] = None
    message: Optional[str] = None


class BulkIngestRequest(BaseModel):
    """Body for POST /ingest/bulk — up to 500 events at once."""

    events: List[Dict[str, Any]] = Field(..., max_length=500)
    source: str = Field("http")


class BulkIngestResponse(BaseModel):
    ok: bool
    accepted: int
    rejected: int
    errors: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Health / status
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    status: str
    queue_size: Optional[int] = None
    redis_connected: bool = False
    cerebrum_reachable: bool = False
