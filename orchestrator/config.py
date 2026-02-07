"""
Configuration management for the Orchestrator service.
Loads settings from environment variables and YAML config files.
"""

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings


class OrchestratorSettings(BaseSettings):
    """
    Main configuration settings loaded from environment variables.
    """

    # Service settings
    service_name: str = "orchestrator"
    service_version: str = "1.0.0"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 8000

    # Database
    database_url: str = "sqlite+aiosqlite:///./data/orchestrator.db"

    # Pool configuration file
    pools_config_path: str = "pools.yaml"

    # Session settings
    session_ttl_seconds: int = 3600  # 1 hour default
    session_cleanup_interval: int = 300  # 5 minutes

    # Nginx settings
    nginx_map_path: str = "/etc/nginx/maps/honeytrap_upstream.map"
    nginx_reload_command: str = "nginx -s reload"
    nginx_healthcheck_url: str = "http://nginx:80/health"
    default_upstream: str = "level1_pool"

    # Security
    hmac_secret: str = Field(
        default="", description="HMAC secret for request signing. REQUIRED in production."
    )
    hmac_header_name: str = "X-HMAC-Signature"
    rate_limit: str = "100/minute"

    # CORS - comma-separated list of allowed origins
    cors_origins: str = Field(
        default="", description="Comma-separated list of allowed CORS origins. Empty = allow all in debug mode only."
    )

    @property
    def cors_origins_list(self) -> list[str]:
        """Parse CORS origins from comma-separated string."""
        if self.cors_origins:
            return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]
        return []

    def validate_production_config(self) -> list[str]:
        """Validate configuration for production readiness. Returns list of errors."""
        errors = []
        if not self.debug:
            if not self.hmac_secret or self.hmac_secret == "change-me-in-production":
                errors.append("HMAC_SECRET must be set to a secure value in production")
            if len(self.hmac_secret) < 32:
                errors.append("HMAC_SECRET must be at least 32 characters")
            if not self.cors_origins:
                errors.append("CORS_ORIGINS should be explicitly set in production")
        return errors

    # External services
    cerebrum_url: str = "http://cerebrum:8001"

    # Health checks
    health_check_interval: int = 30  # seconds
    health_check_timeout: int = 5  # seconds

    # Logging
    log_level: str = "INFO"
    log_format: str = "json"

    class Config:
        env_prefix = "ORCHESTRATOR_"
        env_file = ".env"
        case_sensitive = False


class PoolConfig:
    """
    Container pool configuration loaded from YAML.
    """

    def __init__(self, config_path: str = "pools.yaml"):
        self.config_path = Path(config_path)
        self._config: dict = {}
        self._load()

    def _load(self) -> None:
        """Load pool configuration from YAML file."""
        if self.config_path.exists():
            with open(self.config_path) as f:
                self._config = yaml.safe_load(f) or {}
        else:
            # Use default configuration
            self._config = self._default_config()

    def _default_config(self) -> dict:
        """Return default pool configuration."""
        return {
            "pools": {
                "level1": {
                    "count": 5,
                    "interaction": "low",
                    "base_port": 8081,
                    "services": ["ssh", "http", "telnet"],
                    "container_prefix": "honeytrap-level1",
                },
                "level2": {
                    "count": 3,
                    "interaction": "medium",
                    "base_port": 8091,
                    "services": ["ssh", "http", "telnet", "ftp", "smtp"],
                    "container_prefix": "honeytrap-level2",
                },
                "level3": {
                    "count": 1,
                    "interaction": "high",
                    "base_port": 8101,
                    "services": ["ssh", "http", "telnet", "ftp", "smtp", "vnc", "redis"],
                    "container_prefix": "honeytrap-level3",
                },
            },
            "network": {"subnet": "10.0.2.0/24", "gateway": "10.0.2.1"},
            "defaults": {
                "health_check_path": "/health",
                "health_check_interval": 30,
                "drain_timeout": 60,
            },
        }

    @property
    def pools(self) -> dict[str, dict]:
        """Get pool configurations."""
        result = self._config.get("pools", {})
        return result if isinstance(result, dict) else {}

    @property
    def network(self) -> dict[str, str]:
        """Get network configuration."""
        result = self._config.get("network", {})
        return result if isinstance(result, dict) else {}

    @property
    def defaults(self) -> dict[str, int]:
        """Get default settings."""
        result = self._config.get("defaults", {})
        return result if isinstance(result, dict) else {}

    def get_pool(self, level: int) -> dict | None:
        """Get configuration for a specific pool level."""
        level_key = f"level{level}"
        return self.pools.get(level_key)

    def get_container_count(self, level: int) -> int:
        """Get the number of containers for a level."""
        pool = self.get_pool(level)
        return pool.get("count", 0) if pool else 0

    def get_all_containers(self) -> list[dict]:
        """Generate list of all container definitions."""
        containers = []
        base_ip = 2  # Start from .2 (gateway is .1)

        for level in [1, 2, 3]:
            pool = self.get_pool(level)
            if not pool:
                continue

            count = pool.get("count", 0)
            base_port = pool.get("base_port", 8080 + (level * 10))
            prefix = pool.get("container_prefix", f"honeytrap-level{level}")

            for i in range(count):
                container_id = f"{prefix}-{i+1}"
                # Calculate IP in the subnet
                ip = f"10.0.2.{base_ip}"
                base_ip += 1

                containers.append(
                    {
                        "id": container_id,
                        "level": level,
                        "host": ip,
                        "port": base_port + i,
                        "services": pool.get("services", []),
                    }
                )

        return containers

    def reload(self) -> None:
        """Reload configuration from file."""
        self._load()


@lru_cache
def get_settings() -> OrchestratorSettings:
    """Get cached settings instance."""
    return OrchestratorSettings()


def get_pool_config(config_path: str | None = None) -> PoolConfig:
    """Get pool configuration instance."""
    path = config_path or get_settings().pools_config_path
    return PoolConfig(path)
