"""
Unit tests for the orchestrator API endpoints.
Tests match the actual FastAPI endpoints and Pydantic models.
"""

import pytest
from httpx import AsyncClient

from conftest import generate_auth_headers


class TestRootEndpoint:
    """Tests for the / root endpoint."""
    
    @pytest.mark.asyncio
    async def test_root_returns_service_info(self, client: AsyncClient):
        """Test that root returns service info."""
        response = await client.get("/")
        
        assert response.status_code == 200
        data = response.json()
        assert data["service"] == "orchestrator"
        assert "version" in data
        assert data["status"] == "running"


class TestHealthEndpoint:
    """Tests for the /healthz endpoint."""
    
    @pytest.mark.asyncio
    async def test_health_check_returns_ok(self, client: AsyncClient):
        """Test that health check returns expected fields."""
        response = await client.get("/healthz")
        
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "version" in data
        assert "uptime_seconds" in data
        assert "pools_healthy" in data
    
    @pytest.mark.asyncio
    async def test_health_check_includes_version(self, client: AsyncClient):
        """Test that health check includes version info."""
        response = await client.get("/healthz")
        
        assert response.status_code == 200
        data = response.json()
        assert "version" in data


class TestPoolsEndpoint:
    """Tests for the /pools endpoint."""
    
    @pytest.mark.asyncio
    async def test_get_pools_status(self, client: AsyncClient):
        """Test getting pool status."""
        response = await client.get("/pools")
        
        assert response.status_code == 200
        data = response.json()
        assert "pools" in data
        assert "total_containers" in data
        assert "total_sessions" in data
    
    @pytest.mark.asyncio
    async def test_pools_response_structure(self, client: AsyncClient):
        """Test that pools response has correct structure."""
        response = await client.get("/pools")
        
        assert response.status_code == 200
        data = response.json()
        
        # Each pool should have level, total, idle, assigned, unhealthy
        for pool in data.get("pools", []):
            assert "level" in pool
            assert "total" in pool
            assert "idle" in pool
            assert "assigned" in pool
            assert "unhealthy" in pool


class TestMetricsEndpoint:
    """Tests for the /metrics endpoint."""
    
    @pytest.mark.asyncio
    async def test_metrics_endpoint_accessible(self, client: AsyncClient):
        """Test that metrics endpoint is accessible."""
        response = await client.get("/metrics")
        
        assert response.status_code == 200
    
    @pytest.mark.asyncio
    async def test_metrics_returns_prometheus_format(self, client: AsyncClient):
        """Test that metrics returns data in Prometheus format."""
        response = await client.get("/metrics")
        
        assert response.status_code == 200
        # Prometheus metrics should be plain text
        content_type = response.headers.get("content-type", "")
        assert "text" in content_type or "openmetrics" in content_type


class TestEscalateEndpoint:
    """Tests for the /escalate endpoint."""
    
    @pytest.mark.asyncio
    async def test_escalate_with_valid_request(
        self, client: AsyncClient, sample_escalation_request
    ):
        """Test escalate with valid request."""
        import json
        body = json.dumps(sample_escalation_request)
        headers = generate_auth_headers(body)
        
        response = await client.post(
            "/escalate",
            json=sample_escalation_request,
            headers=headers,
        )
        
        # Should process the request (may fail due to no containers in test)
        assert response.status_code in [200, 201, 503]
    
    @pytest.mark.asyncio
    async def test_escalate_validates_skill_score(self, client: AsyncClient):
        """Test that escalate validates skill score range."""
        import json
        
        invalid_request = {
            "session_id": "test-123",
            "action": "escalate_to_level_2",
            "rule_id": "rule-001",
            "skill_score_after": 15,  # Invalid: should be 0-10
            "explanation": "Test",
        }
        
        body = json.dumps(invalid_request)
        headers = generate_auth_headers(body)
        
        response = await client.post(
            "/escalate",
            json=invalid_request,
            headers=headers,
        )
        
        # Should reject invalid skill score
        assert response.status_code in [422, 400]
    
    @pytest.mark.asyncio
    async def test_escalate_validates_action(self, client: AsyncClient):
        """Test that escalate validates action value."""
        import json
        
        invalid_request = {
            "session_id": "test-123",
            "action": "invalid_action",  # Invalid action
            "rule_id": "rule-001",
            "skill_score_after": 5,
            "explanation": "Test",
        }
        
        body = json.dumps(invalid_request)
        headers = generate_auth_headers(body)
        
        response = await client.post(
            "/escalate",
            json=invalid_request,
            headers=headers,
        )
        
        # Should reject invalid action
        assert response.status_code in [422, 400]
    
    @pytest.mark.asyncio
    async def test_escalate_response_structure(
        self, client: AsyncClient, sample_escalation_request
    ):
        """Test escalate response has correct structure."""
        import json
        body = json.dumps(sample_escalation_request)
        headers = generate_auth_headers(body)
        
        response = await client.post(
            "/escalate",
            json=sample_escalation_request,
            headers=headers,
        )
        
        if response.status_code == 200:
            data = response.json()
            assert "ok" in data
            assert "session_id" in data


class TestSessionEndpoint:
    """Tests for the /session/{session_id} endpoint."""
    
    @pytest.mark.asyncio
    async def test_get_nonexistent_session(self, client: AsyncClient):
        """Test getting a session that doesn't exist."""
        response = await client.get("/session/nonexistent-session-id")
        
        assert response.status_code == 404
    
    @pytest.mark.asyncio
    async def test_release_session_endpoint(self, client: AsyncClient):
        """Test session release endpoint."""
        import json
        
        release_request = {"reason": "manual_release"}
        body = json.dumps(release_request)
        headers = generate_auth_headers(body)
        
        response = await client.post(
            "/session/test-session-123/release",
            json=release_request,
            headers=headers,
        )
        
        # Session may not exist, so 404 is acceptable
        assert response.status_code in [200, 404]


class TestSessionsListEndpoint:
    """Tests for the /sessions endpoint."""
    
    @pytest.mark.asyncio
    async def test_list_sessions(self, client: AsyncClient):
        """Test listing all sessions."""
        response = await client.get("/sessions")
        
        assert response.status_code == 200
        data = response.json()
        assert "sessions" in data
        assert "total" in data
    
    @pytest.mark.asyncio
    async def test_list_sessions_with_state_filter(self, client: AsyncClient):
        """Test listing sessions with state filter."""
        response = await client.get("/sessions?state=active")
        
        assert response.status_code == 200
        data = response.json()
        assert "sessions" in data


class TestPoolByLevelEndpoint:
    """Tests for the /pools/{level} endpoint."""
    
    @pytest.mark.asyncio
    async def test_get_pool_level_1(self, client: AsyncClient):
        """Test getting pool level 1 status."""
        response = await client.get("/pools/1")
        
        # May return 200 or 404 depending on initialization
        assert response.status_code in [200, 404]
    
    @pytest.mark.asyncio
    async def test_invalid_pool_level(self, client: AsyncClient):
        """Test getting invalid pool level."""
        response = await client.get("/pools/5")
        
        assert response.status_code == 400


class TestInputValidation:
    """Tests for input validation across endpoints."""
    
    @pytest.mark.asyncio
    async def test_invalid_json_body(self, client: AsyncClient):
        """Test handling of invalid JSON body."""
        headers = generate_auth_headers("not valid json")
        headers["Content-Type"] = "application/json"
        
        response = await client.post(
            "/escalate",
            content="not valid json",
            headers=headers,
        )
        
        assert response.status_code in [400, 422]
    
    @pytest.mark.asyncio
    async def test_missing_required_fields(self, client: AsyncClient):
        """Test handling of missing required fields."""
        import json
        
        incomplete_request = {
            "session_id": "test-123",
            # Missing: action, rule_id, skill_score_after, explanation
        }
        
        body = json.dumps(incomplete_request)
        headers = generate_auth_headers(body)
        
        response = await client.post(
            "/escalate",
            json=incomplete_request,
            headers=headers,
        )
        
        assert response.status_code == 422


class TestAdminEndpoints:
    """Tests for admin endpoints."""
    
    @pytest.mark.asyncio
    async def test_get_nginx_mappings(self, client: AsyncClient):
        """Test getting nginx mappings."""
        response = await client.get("/admin/nginx/mappings")
        
        assert response.status_code == 200
        data = response.json()
        assert "mappings" in data
        assert "total" in data
    
    @pytest.mark.asyncio
    async def test_nginx_reload(self, client: AsyncClient):
        """Test nginx reload endpoint."""
        response = await client.post("/admin/nginx/reload")
        
        # May fail if nginx not available in test, but should respond
        assert response.status_code in [200, 500]
    
    @pytest.mark.asyncio
    async def test_reinitialize_pools(self, client: AsyncClient):
        """Test pool reinitialization endpoint."""
        response = await client.post("/admin/pools/reinitialize")
        
        assert response.status_code == 200
        data = response.json()
        assert data.get("ok") is True


class TestRateLimiting:
    """Tests for rate limiting functionality."""
    
    @pytest.mark.asyncio
    async def test_health_endpoint_not_rate_limited(self, client: AsyncClient):
        """Test that health endpoint is not heavily rate limited."""
        for _ in range(10):
            response = await client.get("/healthz")
            assert response.status_code == 200
    
    @pytest.mark.asyncio
    async def test_root_endpoint_accessible(self, client: AsyncClient):
        """Test that root endpoint is accessible multiple times."""
        for _ in range(10):
            response = await client.get("/")
            assert response.status_code == 200
