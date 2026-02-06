"""
Database models and session management for the Orchestrator service.
Uses SQLAlchemy async for SQLite persistence.
"""

from collections.abc import AsyncGenerator
from datetime import datetime
from typing import cast

from config import get_settings
from models import ContainerState, SessionState
from sqlalchemy import String
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    """Base class for SQLAlchemy models."""

    pass


# =============================================================================
# Database Models
# =============================================================================


class ContainerModel(Base):
    """SQLAlchemy model for containers."""

    __tablename__ = "containers"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    level: Mapped[int] = mapped_column(nullable=False, index=True)
    host: Mapped[str] = mapped_column(nullable=False)
    port: Mapped[int] = mapped_column(nullable=False)
    state: Mapped[str] = mapped_column(default=ContainerState.IDLE.value, index=True)
    assigned_session_id: Mapped[str | None] = mapped_column(nullable=True, index=True)
    last_health_check: Mapped[datetime | None] = mapped_column(nullable=True)
    healthy: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow)

    @property
    def address(self) -> str:
        return f"{self.host}:{self.port}"


class SessionModel(Base):
    """SQLAlchemy model for sessions."""

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    current_level: Mapped[int] = mapped_column(default=1, index=True)
    container_id: Mapped[str | None] = mapped_column(nullable=True, index=True)
    state: Mapped[str] = mapped_column(default=SessionState.ACTIVE.value, index=True)
    skill_score: Mapped[int] = mapped_column(default=0)
    escalation_count: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_decision_id: Mapped[str | None] = mapped_column(nullable=True)


class NginxMapEntryModel(Base):
    """SQLAlchemy model for nginx map entries."""

    __tablename__ = "nginx_map_entries"

    session_cookie: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str] = mapped_column(nullable=False, index=True)
    upstream: Mapped[str] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow)


class DecisionLogModel(Base):
    """SQLAlchemy model for decision audit log."""

    __tablename__ = "decision_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(nullable=False, index=True)
    action: Mapped[str] = mapped_column(nullable=False)
    rule_id: Mapped[str] = mapped_column(nullable=False)
    skill_score_before: Mapped[int | None] = mapped_column(nullable=True)
    skill_score_after: Mapped[int] = mapped_column(nullable=False)
    from_container: Mapped[str | None] = mapped_column(nullable=True)
    to_container: Mapped[str | None] = mapped_column(nullable=True)
    explanation: Mapped[str | None] = mapped_column(nullable=True)
    timestamp: Mapped[datetime] = mapped_column(default=datetime.utcnow, index=True)


# =============================================================================
# Database Engine & Session Factory
# =============================================================================

_engine = None
_async_session_factory = None


async def init_db(database_url: str | None = None) -> None:
    """Initialize the database engine and create tables."""
    global _engine, _async_session_factory

    url = database_url or get_settings().database_url

    # For SQLite, use StaticPool to allow async access
    if "sqlite" in url:
        _engine = create_async_engine(
            url,
            echo=get_settings().debug,
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
    else:
        _engine = create_async_engine(url, echo=get_settings().debug, pool_size=10, max_overflow=20)

    _async_session_factory = async_sessionmaker(
        _engine, class_=AsyncSession, expire_on_commit=False
    )

    # Create all tables
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Get an async database session."""
    if _async_session_factory is None:
        await init_db()

    assert _async_session_factory is not None
    async with _async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def close_db() -> None:
    """Close the database engine."""
    global _engine
    if _engine:
        await _engine.dispose()
        _engine = None


# =============================================================================
# Database Helper Functions
# =============================================================================


async def get_session_by_id(db: AsyncSession, session_id: str) -> SessionModel | None:
    """Get a session by ID."""
    from sqlalchemy import select

    result = await db.execute(select(SessionModel).where(SessionModel.id == session_id))
    return cast(SessionModel | None, result.scalar_one_or_none())


async def get_container_by_id(db: AsyncSession, container_id: str) -> ContainerModel | None:
    """Get a container by ID."""
    from sqlalchemy import select

    result = await db.execute(select(ContainerModel).where(ContainerModel.id == container_id))
    return cast(ContainerModel | None, result.scalar_one_or_none())


async def get_idle_containers_by_level(db: AsyncSession, level: int) -> list[ContainerModel]:
    """Get all idle containers for a specific level."""
    from sqlalchemy import select

    result = await db.execute(
        select(ContainerModel).where(
            ContainerModel.level == level,
            ContainerModel.state == ContainerState.IDLE.value,
            ContainerModel.healthy,
        )
    )
    return list(result.scalars().all())


async def get_all_containers(db: AsyncSession) -> list[ContainerModel]:
    """Get all containers."""
    from sqlalchemy import select

    result = await db.execute(select(ContainerModel))
    return list(result.scalars().all())


async def get_all_sessions(db: AsyncSession, state: str | None = None) -> list[SessionModel]:
    """Get all sessions, optionally filtered by state."""
    from sqlalchemy import select

    query = select(SessionModel)
    if state:
        query = query.where(SessionModel.state == state)
    result = await db.execute(query)
    return list(result.scalars().all())


async def get_all_nginx_entries(db: AsyncSession) -> list[NginxMapEntryModel]:
    """Get all nginx map entries."""
    from sqlalchemy import select

    result = await db.execute(select(NginxMapEntryModel))
    return list(result.scalars().all())
