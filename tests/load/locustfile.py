"""
Load testing script for Dynamic Labyrinth using Locust.

Usage:
    locust -f locustfile.py --host=http://localhost:8000

Or run headless:
    locust -f locustfile.py --host=http://localhost:8000 --headless -u 100 -r 10 -t 5m
"""

import hmac
import hashlib
import json
import time
import random
from locust import HttpUser, task, between, events
from locust.runners import MasterRunner


class OrchestratorUser(HttpUser):
    """Simulates an internal service calling the orchestrator."""
    
    wait_time = between(0.5, 2)
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.hmac_secret = "test-secret-key"  # Should match test env
        self.session_counter = 0
    
    def generate_auth_headers(self, body: str = "") -> dict:
        """Generate HMAC authentication headers."""
        timestamp = str(int(time.time()))
        message = f"{timestamp}:{body}"
        signature = hmac.new(
            self.hmac_secret.encode(),
            message.encode(),
            hashlib.sha256
        ).hexdigest()
        
        return {
            "X-Timestamp": timestamp,
            "X-Signature": signature,
            "Content-Type": "application/json",
        }
    
    @task(10)
    def check_health(self):
        """Check health endpoint (high frequency)."""
        self.client.get("/healthz")
    
    @task(5)
    def get_pools(self):
        """Get pool status."""
        self.client.get("/pools")
    
    @task(3)
    def get_metrics(self):
        """Get metrics."""
        self.client.get("/metrics")
    
    @task(2)
    def escalate_session(self):
        """Simulate escalation request."""
        self.session_counter += 1
        session_id = f"load-test-session-{self.session_counter}-{int(time.time())}"
        
        request_data = {
            "session_id": session_id,
            "source_ip": f"192.168.{random.randint(1, 254)}.{random.randint(1, 254)}",
            "current_level": random.choice([1, 1, 1, 2, 2, 3]),  # Weighted towards level 1
            "threat_score": random.uniform(0.3, 0.9),
            "attack_patterns": random.sample(
                ["ssh_brute_force", "port_scan", "sql_injection", "xss", "command_injection"],
                k=random.randint(1, 3)
            ),
        }
        
        body = json.dumps(request_data)
        headers = self.generate_auth_headers(body)
        
        with self.client.post(
            "/escalate",
            data=body,
            headers=headers,
            catch_response=True,
        ) as response:
            if response.status_code in [200, 201]:
                response.success()
            elif response.status_code == 503:
                # Service unavailable is expected under load
                response.success()
            else:
                response.failure(f"Unexpected status: {response.status_code}")
    
    @task(1)
    def query_session(self):
        """Query a session (will likely 404)."""
        session_id = f"query-test-{random.randint(1, 1000)}"
        
        with self.client.get(
            f"/session/{session_id}",
            catch_response=True,
        ) as response:
            if response.status_code in [200, 404]:
                response.success()
            else:
                response.failure(f"Unexpected status: {response.status_code}")


class AttackerSimulator(HttpUser):
    """Simulates external attacker traffic hitting nginx."""
    
    wait_time = between(0.1, 1)
    host = "http://localhost"  # Nginx frontend
    
    @task(10)
    def http_request(self):
        """Make HTTP request to honeypot."""
        paths = [
            "/",
            "/admin",
            "/login",
            "/wp-admin",
            "/phpmyadmin",
            "/.env",
            "/config.php",
            "/api/v1/users",
        ]
        
        self.client.get(random.choice(paths), catch_response=True)
    
    @task(3)
    def http_request_with_session(self):
        """Make HTTP request with session cookie."""
        session_id = f"attacker-session-{random.randint(1, 100)}"
        
        self.client.get(
            "/",
            cookies={"dlsess": session_id},
            catch_response=True,
        )
    
    @task(2)
    def probe_common_files(self):
        """Probe for common sensitive files."""
        files = [
            "/robots.txt",
            "/.git/config",
            "/backup.sql",
            "/database.sql.gz",
            "/wp-config.php",
            "/.htaccess",
        ]
        
        self.client.get(random.choice(files), catch_response=True)


class PoolManagerLoad(HttpUser):
    """Focused load testing for pool management."""
    
    wait_time = between(0.2, 0.5)
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.hmac_secret = "test-secret-key"
    
    def generate_auth_headers(self, body: str = "") -> dict:
        timestamp = str(int(time.time()))
        message = f"{timestamp}:{body}"
        signature = hmac.new(
            self.hmac_secret.encode(),
            message.encode(),
            hashlib.sha256
        ).hexdigest()
        
        return {
            "X-Timestamp": timestamp,
            "X-Signature": signature,
            "Content-Type": "application/json",
        }
    
    @task
    def rapid_escalate(self):
        """Rapid fire escalation requests."""
        request_data = {
            "session_id": f"rapid-{int(time.time() * 1000000)}",
            "source_ip": f"10.0.{random.randint(0, 255)}.{random.randint(1, 254)}",
            "current_level": 1,
            "threat_score": random.uniform(0.5, 1.0),
            "attack_patterns": ["rapid_test"],
        }
        
        body = json.dumps(request_data)
        headers = self.generate_auth_headers(body)
        
        self.client.post("/escalate", data=body, headers=headers)


# Event handlers for custom metrics
@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """Called when test starts."""
    print("Load test starting...")
    if isinstance(environment.runner, MasterRunner):
        print(f"Running in distributed mode with {environment.runner.worker_count} workers")


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Called when test stops."""
    print("Load test complete!")
    
    # Print summary statistics
    if environment.stats.total.num_requests > 0:
        print(f"\nTotal requests: {environment.stats.total.num_requests}")
        print(f"Total failures: {environment.stats.total.num_failures}")
        print(f"Failure rate: {environment.stats.total.fail_ratio * 100:.2f}%")
        print(f"Avg response time: {environment.stats.total.avg_response_time:.2f}ms")
        print(f"95th percentile: {environment.stats.total.get_response_time_percentile(0.95):.2f}ms")
        print(f"Requests/sec: {environment.stats.total.total_rps:.2f}")
