"""
Pydantic models for the Orchestrator service.
Defines request/response schemas and internal data models.
"""

from datetime import datetime
from enum import Enum
from typing import Optional, List
from pydantic import BaseModel, Field


# =============================================================================
# Enums
# =============================================================================

class ContainerLevel(int, Enum):
    """Honeytrap interaction levels."""
    LEVEL_1 = 1  # Low interaction
    LEVEL_2 = 2  # Medium interaction
    LEVEL_3 = 3  # High interaction


class ContainerState(str, Enum):
    """Container lifecycle states."""
    IDLE = "idle"
    ASSIGNED = "assigned"
    UNHEALTHY = "unhealthy"
    DRAINING = "draining"


class SessionState(str, Enum):
    """Session lifecycle states."""
    ACTIVE = "active"
    RELEASED = "released"
    EXPIRED = "expired"


class EscalationAction(str, Enum):
    """Valid escalation actions from Cerebrum."""
    ESCALATE_TO_LEVEL_2 = "escalate_to_level_2"
    ESCALATE_TO_LEVEL_3 = "escalate_to_level_3"
    MAINTAIN = "maintain"
    RELEASE = "release"


# =============================================================================
# Request Models (from Cerebrum)
# =============================================================================

class EscalationDecision(BaseModel):
    """
    Decision payload from Cerebrum requesting container escalation.
    """
    session_id: str = Field(..., description="Unique session identifier")
    action: EscalationAction = Field(..., description="Escalation action to perform")
    rule_id: str = Field(..., description="ID of the rule that triggered this decision")
    skill_score_after: int = Field(..., ge=0, le=10, description="Updated skill score (0-10)")
    explanation: str = Field(..., description="Human-readable explanation for the decision")
    timestamp: Optional[datetime] = Field(default_factory=datetime.utcnow)


class SessionReleaseRequest(BaseModel):
    """Request to release a session's container."""
    reason: Optional[str] = Field(default="manual_release", description="Reason for release")


# =============================================================================
# Response Models
# =============================================================================

class EscalationResponse(BaseModel):
    """Response to an escalation request."""
    ok: bool
    session_id: str
    container: Optional[str] = None
    target_level: Optional[int] = None
    note: Optional[str] = None


class SessionInfo(BaseModel):
    """Session state information."""
    session_id: str
    current_level: int
    container_id: Optional[str] = None
    container_address: Optional[str] = None
    state: SessionState
    skill_score: int
    created_at: datetime
    updated_at: datetime
    expires_at: Optional[datetime] = None
    escalation_count: int = 0


class ContainerInfo(BaseModel):
    """Container information."""
    container_id: str
    level: ContainerLevel
    address: str  # ip:port
    state: ContainerState
    assigned_session: Optional[str] = None
    last_health_check: Optional[datetime] = None
    healthy: bool = True


class PoolStatus(BaseModel):
    """Status of a container pool."""
    level: int
    total: int
    idle: int
    assigned: int
    unhealthy: int


class PoolsResponse(BaseModel):
    """Response for /pools endpoint."""
    pools: List[PoolStatus]
    total_containers: int
    total_sessions: int


class HealthResponse(BaseModel):
    """Health check response."""
    status: str
    version: str
    uptime_seconds: float
    pools_healthy: bool


# =============================================================================
# Internal Models (for database)
# =============================================================================

class ContainerRecord(BaseModel):
    """Internal container record."""
    id: str
    level: int
    host: str
    port: int
    state: ContainerState = ContainerState.IDLE
    assigned_session_id: Optional[str] = None
    last_health_check: Optional[datetime] = None
    healthy: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def address(self) -> str:
        return f"{self.host}:{self.port}"


class SessionRecord(BaseModel):
    """Internal session record."""
    id: str
    current_level: int = 1
    container_id: Optional[str] = None
    state: SessionState = SessionState.ACTIVE
    skill_score: int = 0
    escalation_count: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: Optional[datetime] = None
    last_decision_id: Optional[str] = None
