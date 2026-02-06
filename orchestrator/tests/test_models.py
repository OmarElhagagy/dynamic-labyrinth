"""
Unit tests for the models module.
Tests for actual Pydantic models used in the orchestrator.
"""

import pytest
from pydantic import ValidationError
from datetime import datetime


class TestEscalationDecision:
    """Tests for EscalationDecision model."""
    
    def test_valid_escalation_decision(self):
        """Test creating a valid escalation decision."""
        from models import EscalationDecision, EscalationAction
        
        decision = EscalationDecision(
            session_id="test-session-123",
            action=EscalationAction.ESCALATE_TO_LEVEL_2,
            rule_id="rule-001",
            skill_score_after=5,
            explanation="Detected SSH brute force attack pattern",
        )
        
        assert decision.session_id == "test-session-123"
        assert decision.action == EscalationAction.ESCALATE_TO_LEVEL_2
        assert decision.skill_score_after == 5
    
    def test_skill_score_bounds(self):
        """Test that skill score must be between 0 and 10."""
        from models import EscalationDecision, EscalationAction
        
        # Valid score
        decision = EscalationDecision(
            session_id="test",
            action=EscalationAction.MAINTAIN,
            rule_id="rule-001",
            skill_score_after=5,
            explanation="Test",
        )
        assert decision.skill_score_after == 5
        
        # Test boundaries
        decision_zero = EscalationDecision(
            session_id="test",
            action=EscalationAction.MAINTAIN,
            rule_id="rule-001",
            skill_score_after=0,
            explanation="Test",
        )
        assert decision_zero.skill_score_after == 0
        
        decision_ten = EscalationDecision(
            session_id="test",
            action=EscalationAction.MAINTAIN,
            rule_id="rule-001",
            skill_score_after=10,
            explanation="Test",
        )
        assert decision_ten.skill_score_after == 10
    
    def test_invalid_skill_score(self):
        """Test that invalid skill score raises error."""
        from models import EscalationDecision, EscalationAction
        
        with pytest.raises(ValidationError):
            EscalationDecision(
                session_id="test",
                action=EscalationAction.MAINTAIN,
                rule_id="rule-001",
                skill_score_after=15,  # Invalid: should be 0-10
                explanation="Test",
            )
    
    def test_all_escalation_actions(self):
        """Test all valid escalation actions."""
        from models import EscalationDecision, EscalationAction
        
        actions = [
            EscalationAction.ESCALATE_TO_LEVEL_2,
            EscalationAction.ESCALATE_TO_LEVEL_3,
            EscalationAction.MAINTAIN,
            EscalationAction.RELEASE,
        ]
        
        for action in actions:
            decision = EscalationDecision(
                session_id="test",
                action=action,
                rule_id="rule-001",
                skill_score_after=5,
                explanation="Test",
            )
            assert decision.action == action
    
    def test_timestamp_default(self):
        """Test that timestamp defaults to current time."""
        from models import EscalationDecision, EscalationAction
        
        decision = EscalationDecision(
            session_id="test",
            action=EscalationAction.MAINTAIN,
            rule_id="rule-001",
            skill_score_after=5,
            explanation="Test",
        )
        
        assert decision.timestamp is not None


class TestEscalationResponse:
    """Tests for EscalationResponse model."""
    
    def test_successful_response(self):
        """Test successful escalation response."""
        from models import EscalationResponse
        
        response = EscalationResponse(
            ok=True,
            session_id="test-session-123",
            container="honeytrap-level2-1",
            target_level=2,
        )
        
        assert response.ok is True
        assert response.container == "honeytrap-level2-1"
        assert response.target_level == 2
    
    def test_failed_response(self):
        """Test failed escalation response."""
        from models import EscalationResponse
        
        response = EscalationResponse(
            ok=False,
            session_id="test-session-123",
            note="No containers available",
        )
        
        assert response.ok is False
        assert response.container is None


class TestSessionInfo:
    """Tests for SessionInfo model."""
    
    def test_valid_session_info(self):
        """Test creating valid session info."""
        from models import SessionInfo, SessionState
        
        session = SessionInfo(
            session_id="session-123",
            current_level=1,
            container_id="honeytrap-level1-1",
            container_address="10.0.2.11:8080",
            state=SessionState.ACTIVE,
            skill_score=5,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            escalation_count=1,
        )
        
        assert session.session_id == "session-123"
        assert session.current_level == 1
        assert session.state == SessionState.ACTIVE
    
    def test_session_states(self):
        """Test all valid session states."""
        from models import SessionState
        
        assert SessionState.ACTIVE == "active"
        assert SessionState.RELEASED == "released"
        assert SessionState.EXPIRED == "expired"


class TestPoolStatus:
    """Tests for PoolStatus model."""
    
    def test_valid_pool_status(self):
        """Test creating valid pool status."""
        from models import PoolStatus
        
        status = PoolStatus(
            level=1,
            total=5,
            idle=3,
            assigned=2,
            unhealthy=0,
        )
        
        assert status.level == 1
        assert status.total == 5
        assert status.idle == 3
    
    def test_pool_counts(self):
        """Test pool count calculations."""
        from models import PoolStatus
        
        status = PoolStatus(
            level=2,
            total=3,
            idle=1,
            assigned=2,
            unhealthy=0,
        )
        
        # Idle + assigned + unhealthy should equal total
        assert status.idle + status.assigned + status.unhealthy == status.total


class TestPoolsResponse:
    """Tests for PoolsResponse model."""
    
    def test_valid_pools_response(self):
        """Test creating valid pools response."""
        from models import PoolsResponse, PoolStatus
        
        pools = [
            PoolStatus(level=1, total=5, idle=5, assigned=0, unhealthy=0),
            PoolStatus(level=2, total=3, idle=3, assigned=0, unhealthy=0),
            PoolStatus(level=3, total=1, idle=1, assigned=0, unhealthy=0),
        ]
        
        response = PoolsResponse(
            pools=pools,
            total_containers=9,
            total_sessions=0,
        )
        
        assert len(response.pools) == 3
        assert response.total_containers == 9


class TestHealthResponse:
    """Tests for HealthResponse model."""
    
    def test_healthy_response(self):
        """Test healthy status response."""
        from models import HealthResponse
        
        health = HealthResponse(
            status="healthy",
            version="1.0.0",
            uptime_seconds=3600.0,
            pools_healthy=True,
        )
        
        assert health.status == "healthy"
        assert health.pools_healthy is True
    
    def test_degraded_response(self):
        """Test degraded status response."""
        from models import HealthResponse
        
        health = HealthResponse(
            status="degraded",
            version="1.0.0",
            uptime_seconds=3600.0,
            pools_healthy=False,
        )
        
        assert health.status == "degraded"
        assert health.pools_healthy is False


class TestContainerInfo:
    """Tests for ContainerInfo model."""
    
    def test_valid_container_info(self):
        """Test creating valid container info."""
        from models import ContainerInfo, ContainerLevel, ContainerState
        
        container = ContainerInfo(
            container_id="honeytrap-level1-1",
            level=ContainerLevel.LEVEL_1,
            address="10.0.2.11:8080",
            state=ContainerState.IDLE,
            healthy=True,
        )
        
        assert container.container_id == "honeytrap-level1-1"
        assert container.level == ContainerLevel.LEVEL_1
        assert container.state == ContainerState.IDLE


class TestContainerState:
    """Tests for ContainerState enum."""
    
    def test_all_container_states(self):
        """Test all valid container states."""
        from models import ContainerState
        
        assert ContainerState.IDLE == "idle"
        assert ContainerState.ASSIGNED == "assigned"
        assert ContainerState.UNHEALTHY == "unhealthy"
        assert ContainerState.DRAINING == "draining"


class TestContainerLevel:
    """Tests for ContainerLevel enum."""
    
    def test_all_container_levels(self):
        """Test all valid container levels."""
        from models import ContainerLevel
        
        assert ContainerLevel.LEVEL_1 == 1
        assert ContainerLevel.LEVEL_2 == 2
        assert ContainerLevel.LEVEL_3 == 3


class TestContainerRecord:
    """Tests for ContainerRecord internal model."""
    
    def test_container_address_property(self):
        """Test container address property."""
        from models import ContainerRecord
        
        container = ContainerRecord(
            id="honeytrap-level1-1",
            level=1,
            host="10.0.2.11",
            port=8080,
        )
        
        assert container.address == "10.0.2.11:8080"


class TestSessionReleaseRequest:
    """Tests for SessionReleaseRequest model."""
    
    def test_default_reason(self):
        """Test default release reason."""
        from models import SessionReleaseRequest
        
        request = SessionReleaseRequest()
        
        assert request.reason == "manual_release"
    
    def test_custom_reason(self):
        """Test custom release reason."""
        from models import SessionReleaseRequest
        
        request = SessionReleaseRequest(reason="expired")
        
        assert request.reason == "expired"
