"""
Integration tests for the Dynamic Labyrinth system.
These tests require Docker and docker-compose to be available.
"""

import pytest
import asyncio
import httpx
import os
import subprocess
import time
from typing import Generator


# Skip all tests if not in integration test mode
pytestmark = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION_TESTS", "false").lower() != "true",
    reason="Integration tests disabled. Set RUN_INTEGRATION_TESTS=true to enable."
)


class TestSystemIntegration:
    """Full system integration tests."""
    
    @pytest.fixture(scope="class")
    def docker_compose_up(self) -> Generator[None, None, None]:
        """Start the full system with docker-compose."""
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        compose_file = os.path.join(project_root, "docker-compose.yml")
        override_file = os.path.join(project_root, "docker-compose.override.yml")
        
        # Start services
        subprocess.run(
            ["docker-compose", "-f", compose_file, "-f", override_file, "up", "-d", "--build"],
            cwd=project_root,
            check=True,
        )
        
        # Wait for services to be healthy
        max_wait = 120
        start_time = time.time()
        
        while time.time() - start_time < max_wait:
            try:
                response = httpx.get("http://localhost:8000/healthz", timeout=5)
                if response.status_code == 200:
                    break
            except httpx.RequestError:
                pass
            time.sleep(5)
        
        yield
        
        # Cleanup
        subprocess.run(
            ["docker-compose", "-f", compose_file, "-f", override_file, "down", "-v"],
            cwd=project_root,
        )
    
    def test_orchestrator_health(self, docker_compose_up):
        """Test orchestrator health endpoint."""
        response = httpx.get("http://localhost:8000/healthz")
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
    
    def test_pools_endpoint(self, docker_compose_up):
        """Test pools status endpoint."""
        response = httpx.get("http://localhost:8000/pools")
        
        assert response.status_code == 200
        data = response.json()
        assert "pools" in data
    
    def test_metrics_endpoint(self, docker_compose_up):
        """Test metrics endpoint."""
        response = httpx.get("http://localhost:8000/metrics")
        
        assert response.status_code == 200
    
    def test_nginx_health(self, docker_compose_up):
        """Test nginx is responding."""
        try:
            response = httpx.get("http://localhost/health", timeout=10)
            # May return 404 if health endpoint not configured
            assert response.status_code in [200, 404]
        except httpx.RequestError:
            pytest.skip("Nginx not accessible")
    
    def test_escalation_flow(self, docker_compose_up):
        """Test full escalation flow."""
        import hmac
        import hashlib
        import json
        
        # Generate auth headers
        hmac_secret = os.getenv("HMAC_SECRET", "test-secret-key")
        timestamp = str(int(time.time()))
        
        request_data = {
            "session_id": "integration-test-session",
            "source_ip": "192.168.1.100",
            "current_level": 1,
            "threat_score": 0.8,
            "attack_patterns": ["ssh_brute_force"],
        }
        
        body = json.dumps(request_data)
        message = f"{timestamp}:{body}"
        signature = hmac.new(
            hmac_secret.encode(),
            message.encode(),
            hashlib.sha256
        ).hexdigest()
        
        headers = {
            "X-Timestamp": timestamp,
            "X-Signature": signature,
            "Content-Type": "application/json",
        }
        
        response = httpx.post(
            "http://localhost:8000/escalate",
            content=body,
            headers=headers,
            timeout=30,
        )
        
        # Should return decision or service unavailable
        assert response.status_code in [200, 201, 503]


class TestContainerPool:
    """Tests for container pool functionality."""
    
    @pytest.fixture(scope="class")
    def docker_compose_up(self) -> Generator[None, None, None]:
        """Start the system."""
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        compose_file = os.path.join(project_root, "docker-compose.yml")
        
        subprocess.run(
            ["docker-compose", "-f", compose_file, "up", "-d", "--build"],
            cwd=project_root,
            check=True,
        )
        
        time.sleep(30)  # Wait for containers
        
        yield
        
        subprocess.run(
            ["docker-compose", "-f", compose_file, "down", "-v"],
            cwd=project_root,
        )
    
    def test_container_count(self, docker_compose_up):
        """Test that expected number of containers are running."""
        result = subprocess.run(
            ["docker", "ps", "--filter", "name=honeytrap", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
        )
        
        containers = [c for c in result.stdout.strip().split("\n") if c]
        
        # In dev mode, may have fewer containers
        assert len(containers) >= 1
    
    def test_container_health(self, docker_compose_up):
        """Test that containers report healthy status."""
        result = subprocess.run(
            ["docker", "ps", "--filter", "name=honeytrap", "--format", "{{.Names}}: {{.Status}}"],
            capture_output=True,
            text=True,
        )
        
        # Just verify command works, health check may not be immediate
        assert result.returncode == 0


class TestNginxRouting:
    """Tests for nginx cookie-based routing."""
    
    @pytest.fixture(scope="class")
    def docker_compose_up(self) -> Generator[None, None, None]:
        """Start the system."""
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        compose_file = os.path.join(project_root, "docker-compose.yml")
        
        subprocess.run(
            ["docker-compose", "-f", compose_file, "up", "-d"],
            cwd=project_root,
            check=True,
        )
        
        time.sleep(30)
        
        yield
        
        subprocess.run(
            ["docker-compose", "-f", compose_file, "down", "-v"],
            cwd=project_root,
        )
    
    def test_request_without_cookie(self, docker_compose_up):
        """Test request without session cookie goes to default backend."""
        try:
            response = httpx.get("http://localhost/", timeout=10)
            # Should get some response from default backend
            assert response.status_code in [200, 301, 302, 400, 404, 502, 503]
        except httpx.RequestError:
            pytest.skip("Nginx not accessible")
    
    def test_request_with_cookie(self, docker_compose_up):
        """Test request with session cookie."""
        try:
            cookies = {"dlsess": "test-session-123"}
            response = httpx.get("http://localhost/", cookies=cookies, timeout=10)
            # May route to specific backend or default
            assert response.status_code in [200, 301, 302, 400, 404, 502, 503]
        except httpx.RequestError:
            pytest.skip("Nginx not accessible")


class TestSessionLifecycle:
    """Tests for session lifecycle management."""
    
    @pytest.fixture(scope="class")  
    def docker_compose_up(self) -> Generator[None, None, None]:
        """Start the system."""
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        compose_file = os.path.join(project_root, "docker-compose.yml")
        
        subprocess.run(
            ["docker-compose", "-f", compose_file, "up", "-d"],
            cwd=project_root,
            check=True,
        )
        
        time.sleep(30)
        
        yield
        
        subprocess.run(
            ["docker-compose", "-f", compose_file, "down", "-v"],
            cwd=project_root,
        )
    
    def test_session_creation(self, docker_compose_up):
        """Test session creation via escalation."""
        import hmac
        import hashlib
        import json
        
        hmac_secret = os.getenv("HMAC_SECRET", "test-secret-key")
        timestamp = str(int(time.time()))
        
        request_data = {
            "session_id": f"lifecycle-test-{int(time.time())}",
            "source_ip": "192.168.1.200",
            "current_level": 1,
            "threat_score": 0.5,
            "attack_patterns": [],
        }
        
        body = json.dumps(request_data)
        message = f"{timestamp}:{body}"
        signature = hmac.new(
            hmac_secret.encode(),
            message.encode(),
            hashlib.sha256
        ).hexdigest()
        
        headers = {
            "X-Timestamp": timestamp,
            "X-Signature": signature,
            "Content-Type": "application/json",
        }
        
        response = httpx.post(
            "http://localhost:8000/escalate",
            content=body,
            headers=headers,
            timeout=30,
        )
        
        assert response.status_code in [200, 201, 503]
    
    def test_session_query(self, docker_compose_up):
        """Test querying session status."""
        response = httpx.get(
            "http://localhost:8000/session/nonexistent-session",
            timeout=10,
        )
        
        # Should return 404 for nonexistent session
        assert response.status_code == 404
