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

# Set test environment BEFORE importing app modules
os.environ["ENVIRONMENT"] = "test"
os.environ["DEBUG"] = "true"  # Disable HMAC enforcement
os.environ["HMAC_SECRET"] = "test-secret-key-for-testing-only"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"

# Clear the settings cache to ensure our env vars are picked up
# This must be done before any imports that use get_settings()
import config  # noqa: E402

config.get_settings.cache_clear()

from config import get_settings  # noqa: E402
from database import Base  # noqa: E402
from main import app, get_db, get_pool_manager  # noqa: E402
from models import PoolStatus  # noqa: E402
from pool_manager import PoolManager  # noqa: E402

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
def test_pool_manager() -> PoolManager:
    """Create a real PoolManager instance for testing."""
    from config import get_pool_config

    return PoolManager(get_pool_config())


def _get_hmac_middleware():
    """Get the HMAC middleware from the app's middleware stack."""
    from middleware.auth import HMACAuthMiddleware

    # Walk the middleware stack to find HMAC middleware
    middleware_stack = app.middleware_stack
    while middleware_stack is not None:
        if isinstance(middleware_stack, HMACAuthMiddleware):
            return middleware_stack
        middleware_stack = getattr(middleware_stack, "app", None)
    return None


@pytest.fixture(scope="function")
async def client(
    db_session: AsyncSession, test_pool_manager: PoolManager
) -> AsyncGenerator[AsyncClient, None]:
    """
    Create async HTTP client for testing API endpoints.
    HMAC authentication is DISABLED for this client.
    """

    async def override_get_db():
        yield db_session

    def override_get_pool_manager() -> PoolManager:
        return test_pool_manager

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_pool_manager] = override_get_pool_manager

    async with AsyncClient(app=app, base_url="http://test") as ac:
        # After first request, middleware stack is built - now we can patch it
        # Make a dummy request to build the stack
        await ac.get("/")

        # Now disable HMAC enforcement
        hmac_mw = _get_hmac_middleware()
        original_enforce = None
        if hmac_mw:
            original_enforce = hmac_mw.enforce
            hmac_mw.enforce = False
            hmac_mw.settings = settings  # Ensure debug=True for fallback

        yield ac

        # Restore original enforce value
        if hmac_mw and original_enforce is not None:
            hmac_mw.enforce = original_enforce

    app.dependency_overrides.clear()


@pytest.fixture(scope="function")
async def auth_client(
    db_session: AsyncSession, test_pool_manager: PoolManager
) -> AsyncGenerator[AsyncClient, None]:
    """
    Create async HTTP client for testing auth behavior.
    HMAC authentication is ENABLED for this client.
    """

    async def override_get_db():
        yield db_session

    def override_get_pool_manager() -> PoolManager:
        return test_pool_manager

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_pool_manager] = override_get_pool_manager

    async with AsyncClient(app=app, base_url="http://test") as ac:
        # Make a dummy request to build middleware stack
        await ac.get("/")

        # Ensure HMAC enforcement is ON
        hmac_mw = _get_hmac_middleware()
        original_enforce = None
        if hmac_mw:
            original_enforce = hmac_mw.enforce
            hmac_mw.enforce = True

        yield ac

        # Restore original enforce value
        if hmac_mw and original_enforce is not None:
            hmac_mw.enforce = original_enforce

    app.dependency_overrides.clear()


@pytest.fixture
def mock_pool_manager():
    """Create a mock pool manager with proper return types."""
    manager = MagicMock(spec=PoolManager)
    manager.assign_container = AsyncMock(return_value=None)
    manager.release_session = AsyncMock(return_value=True)
    manager.get_session = AsyncMock(return_value=None)
    manager.update_session_score = AsyncMock(return_value=None)
    manager.log_decision = AsyncMock(return_value=None)
    manager.cleanup_expired_sessions = AsyncMock(return_value=0)
    manager.get_total_session_count = AsyncMock(return_value=0)
    manager.generate_session_cookie = MagicMock(return_value="dlsess_test123")

    # Return proper PoolStatus objects
    manager.get_pool_status = AsyncMock(
        return_value=[
            PoolStatus(level=1, total=5, idle=5, assigned=0, unhealthy=0),
            PoolStatus(level=2, total=3, idle=3, assigned=0, unhealthy=0),
            PoolStatus(level=3, total=1, idle=1, assigned=0, unhealthy=0),
        ]
    )
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
