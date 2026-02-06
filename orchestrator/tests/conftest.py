"""
Pytest configuration and fixtures for orchestrator tests.
"""

import asyncio
import os
from collections.abc import AsyncGenerator, Generator
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Set test environment
os.environ["ENVIRONMENT"] = "test"
os.environ["HMAC_SECRET"] = "test-secret-key-for-testing-only"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"

from config import get_settings
from database import Base
from main import app, get_db

settings = get_settings()


@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="function")
async def async_engine():
    """Create async engine for testing with in-memory SQLite."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()


@pytest.fixture(scope="function")
async def db_session(async_engine) -> AsyncGenerator[AsyncSession, None]:
    """Create a test database session."""
    async_session_maker = sessionmaker(
        async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with async_session_maker() as session:
        yield session
        await session.rollback()


@pytest.fixture(scope="function")
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """Create async HTTP client for testing API endpoints."""

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(app=app, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest.fixture
def mock_pool_manager():
    """Create a mock pool manager."""
    manager = MagicMock()
    manager.assign_container = AsyncMock(return_value="honeytrap-level1-1")
    manager.release_container = AsyncMock(return_value=True)
    manager.get_pool_status = AsyncMock(
        return_value={
            "level1": {"available": 5, "in_use": 0, "unhealthy": 0, "status": "healthy"},
            "level2": {"available": 3, "in_use": 0, "unhealthy": 0, "status": "healthy"},
            "level3": {"available": 1, "in_use": 0, "unhealthy": 0, "status": "healthy"},
        }
    )
    manager.health_check = AsyncMock(return_value=True)
    return manager


@pytest.fixture
def mock_nginx_writer():
    """Create a mock nginx writer."""
    writer = MagicMock()
    writer.add_session_mapping = AsyncMock(return_value=True)
    writer.remove_session_mapping = AsyncMock(return_value=True)
    writer.reload_nginx = AsyncMock(return_value=True)
    writer.write_map_file = AsyncMock(return_value=True)
    return writer


@pytest.fixture
def sample_escalation_request():
    """Sample escalation request data matching EscalationDecision model."""
    return {
        "session_id": "test-session-123",
        "action": "escalate_to_level_2",
        "rule_id": "rule-001",
        "skill_score_after": 5,
        "explanation": "Detected SSH brute force attack pattern",
    }


@pytest.fixture
def sample_session_data():
    """Sample session data."""
    return {
        "session_id": "test-session-456",
        "container_id": "honeytrap-level1-2",
        "level": 1,
        "source_ip": "10.0.0.50",
        "created_at": "2026-02-06T10:00:00Z",
        "last_activity": "2026-02-06T10:30:00Z",
    }


@pytest.fixture
def auth_headers():
    """Generate valid HMAC authentication headers for GET request to /pools."""
    from middleware.auth import generate_hmac_headers

    headers = generate_hmac_headers(
        secret=settings.hmac_secret, method="GET", path="/pools", body=b""
    )
    return headers


def generate_auth_headers(body: str = "", method: str = "POST", path: str = "/escalate") -> dict:
    """
    Helper function to generate auth headers with custom body.

    Args:
        body: Request body as string (default: empty)
        method: HTTP method (default: POST)
        path: Request path (default: /escalate)

    Returns:
        Dict with X-HMAC-Signature and X-HMAC-Timestamp headers
    """
    from middleware.auth import generate_hmac_headers

    body_bytes = body.encode() if isinstance(body, str) else body
    headers = generate_hmac_headers(
        secret=settings.hmac_secret, method=method, path=path, body=body_bytes
    )
    return headers
