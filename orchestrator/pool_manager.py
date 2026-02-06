"""
Pool Manager for the Orchestrator service.
Handles container pool management, assignment, and lifecycle.
"""

import asyncio
import hashlib
from datetime import datetime, timedelta

import structlog
from config import PoolConfig, get_pool_config, get_settings
from database import (
    ContainerModel,
    DecisionLogModel,
    SessionModel,
    get_container_by_id,
    get_idle_containers_by_level,
    get_session_by_id,
)
from models import (
    ContainerState,
    PoolStatus,
    SessionState,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger()


class PoolManager:
    """
    Manages honeytrap container pools.

    Responsibilities:
    - Track container state (idle, assigned, unhealthy)
    - Assign containers to sessions based on level
    - Handle container lifecycle (assign, release, drain)
    - Persist state to SQLite for crash recovery
    - Generate session cookies for nginx routing
    """

    def __init__(self, pool_config: PoolConfig | None = None):
        self.config = pool_config or get_pool_config()
        self.settings = get_settings()
        self._lock = asyncio.Lock()

    # =========================================================================
    # Initialization
    # =========================================================================

    async def initialize_pools(self, db: AsyncSession) -> None:
        """
        Initialize container pools from configuration.
        Creates container records in the database if they don't exist.
        """
        containers = self.config.get_all_containers()

        for container_def in containers:
            # Check if container already exists
            existing = await get_container_by_id(db, container_def["id"])

            if existing:
                log.info("Container already exists", container_id=container_def["id"])
                continue

            # Create new container record
            container = ContainerModel(
                id=container_def["id"],
                level=container_def["level"],
                host=container_def["host"],
                port=container_def["port"],
                state=ContainerState.IDLE.value,
                healthy=True,
                created_at=datetime.utcnow(),
            )
            db.add(container)
            log.info(
                "Created container", container_id=container_def["id"], level=container_def["level"]
            )

        await db.commit()
        log.info("Pool initialization complete", total_containers=len(containers))

    # =========================================================================
    # Assignment Logic
    # =========================================================================

    async def assign_container(
        self, db: AsyncSession, session_id: str, target_level: int
    ) -> ContainerModel | None:
        """
        Assign a container to a session at the specified level.

        Algorithm:
        1. Try to find an idle container at the target level
        2. If none available, try the next level up
        3. If still none, return None (pool exhausted)
        """
        async with self._lock:
            # Get or create session
            session = await get_session_by_id(db, session_id)

            if not session:
                session = SessionModel(
                    id=session_id,
                    current_level=1,
                    state=SessionState.ACTIVE.value,
                    created_at=datetime.utcnow(),
                )
                db.add(session)

            # If session already has a container at the same or higher level, return it
            if session.container_id and session.current_level >= target_level:
                container = await get_container_by_id(db, session.container_id)
                if container and container.healthy:
                    log.info(
                        "Session already has suitable container",
                        session_id=session_id,
                        container_id=container.id,
                    )
                    return container

            # Release current container if session is being escalated
            if session.container_id:
                await self._release_container(db, session.container_id)

            # Find idle container at target level
            container = await self._find_idle_container(db, target_level)

            if not container:
                # Try next level up
                for level in range(target_level + 1, 4):
                    container = await self._find_idle_container(db, level)
                    if container:
                        log.info(
                            "No container at target level, using higher level",
                            target_level=target_level,
                            actual_level=level,
                        )
                        break

            if not container:
                log.warning(
                    "No available containers", session_id=session_id, target_level=target_level
                )
                return None

            # Assign container to session
            container.state = ContainerState.ASSIGNED.value
            container.assigned_session_id = session_id

            # Update session
            session.container_id = container.id
            session.current_level = container.level
            session.updated_at = datetime.utcnow()
            session.escalation_count += 1
            session.expires_at = datetime.utcnow() + timedelta(
                seconds=self.settings.session_ttl_seconds
            )

            await db.commit()

            log.info(
                "Container assigned",
                session_id=session_id,
                container_id=container.id,
                level=container.level,
            )

            return container

    async def _find_idle_container(self, db: AsyncSession, level: int) -> ContainerModel | None:
        """Find an idle, healthy container at the specified level."""
        containers = await get_idle_containers_by_level(db, level)

        if not containers:
            return None

        # Return the first idle container
        # Could implement more sophisticated selection (e.g., least recently used)
        return containers[0]

    async def _release_container(self, db: AsyncSession, container_id: str) -> None:
        """Release a container back to the idle pool."""
        container = await get_container_by_id(db, container_id)

        if container:
            container.state = ContainerState.IDLE.value
            container.assigned_session_id = None
            log.info("Container released", container_id=container_id)

    # =========================================================================
    # Session Management
    # =========================================================================

    async def get_session(self, db: AsyncSession, session_id: str) -> SessionModel | None:
        """Get session by ID."""
        return await get_session_by_id(db, session_id)

    async def release_session(
        self, db: AsyncSession, session_id: str, reason: str = "manual"
    ) -> bool:
        """
        Release a session and its assigned container.
        """
        async with self._lock:
            session = await get_session_by_id(db, session_id)

            if not session:
                log.warning("Session not found", session_id=session_id)
                return False

            # Release container
            if session.container_id:
                await self._release_container(db, session.container_id)

            # Update session state
            session.state = SessionState.RELEASED.value
            session.container_id = None
            session.updated_at = datetime.utcnow()

            await db.commit()

            log.info("Session released", session_id=session_id, reason=reason)
            return True

    async def update_session_score(
        self, db: AsyncSession, session_id: str, skill_score: int, decision_id: str | None = None
    ) -> None:
        """Update session skill score."""
        session = await get_session_by_id(db, session_id)

        if session:
            session.skill_score = skill_score
            session.last_decision_id = decision_id
            session.updated_at = datetime.utcnow()
            await db.commit()

    async def cleanup_expired_sessions(self, db: AsyncSession) -> int:
        """
        Clean up expired sessions.
        Returns the number of sessions cleaned up.
        """
        now = datetime.utcnow()

        # Find expired sessions
        result = await db.execute(
            select(SessionModel).where(
                SessionModel.state == SessionState.ACTIVE.value, SessionModel.expires_at < now
            )
        )
        expired_sessions = list(result.scalars().all())

        count = 0
        for session in expired_sessions:
            await self.release_session(db, session.id, reason="expired")
            count += 1

        if count > 0:
            log.info("Cleaned up expired sessions", count=count)

        return count

    # =========================================================================
    # Pool Status
    # =========================================================================

    async def get_pool_status(self, db: AsyncSession) -> list[PoolStatus]:
        """Get status of all pools."""
        statuses = []

        for level in [1, 2, 3]:
            # Count containers by state
            total_result = await db.execute(
                select(func.count(ContainerModel.id)).where(ContainerModel.level == level)
            )
            total = total_result.scalar() or 0

            idle_result = await db.execute(
                select(func.count(ContainerModel.id)).where(
                    ContainerModel.level == level, ContainerModel.state == ContainerState.IDLE.value
                )
            )
            idle = idle_result.scalar() or 0

            assigned_result = await db.execute(
                select(func.count(ContainerModel.id)).where(
                    ContainerModel.level == level,
                    ContainerModel.state == ContainerState.ASSIGNED.value,
                )
            )
            assigned = assigned_result.scalar() or 0

            unhealthy_result = await db.execute(
                select(func.count(ContainerModel.id)).where(
                    ContainerModel.level == level, ContainerModel.healthy == False  # noqa: E712
                )
            )
            unhealthy = unhealthy_result.scalar() or 0

            statuses.append(
                PoolStatus(
                    level=level, total=total, idle=idle, assigned=assigned, unhealthy=unhealthy
                )
            )

        return statuses

    async def get_total_session_count(self, db: AsyncSession) -> int:
        """Get total number of active sessions."""
        result = await db.execute(
            select(func.count(SessionModel.id)).where(
                SessionModel.state == SessionState.ACTIVE.value
            )
        )
        return result.scalar() or 0

    # =========================================================================
    # Decision Logging
    # =========================================================================

    async def log_decision(
        self,
        db: AsyncSession,
        session_id: str,
        action: str,
        rule_id: str,
        skill_score_before: int | None,
        skill_score_after: int,
        from_container: str | None,
        to_container: str | None,
        explanation: str | None,
    ) -> None:
        """Log a decision for audit purposes."""
        log_entry = DecisionLogModel(
            session_id=session_id,
            action=action,
            rule_id=rule_id,
            skill_score_before=skill_score_before,
            skill_score_after=skill_score_after,
            from_container=from_container,
            to_container=to_container,
            explanation=explanation,
            timestamp=datetime.utcnow(),
        )
        db.add(log_entry)
        await db.commit()

    # =========================================================================
    # Cookie Generation
    # =========================================================================

    @staticmethod
    def generate_session_cookie(session_id: str) -> str:
        """
        Generate a unique cookie value for a session.
        Format: dlsess_<hash>
        """
        hash_input = f"{session_id}:{datetime.utcnow().isoformat()}"
        hash_value = hashlib.sha256(hash_input.encode()).hexdigest()[:16]
        return f"dlsess_{hash_value}"

    # =========================================================================
    # Health Check
    # =========================================================================

    async def mark_container_health(
        self, db: AsyncSession, container_id: str, healthy: bool
    ) -> None:
        """Update container health status."""
        container = await get_container_by_id(db, container_id)

        if container:
            container.healthy = healthy
            container.last_health_check = datetime.utcnow()

            if not healthy and container.state == ContainerState.ASSIGNED.value:
                # If unhealthy and assigned, we may need to migrate the session
                log.warning(
                    "Assigned container became unhealthy",
                    container_id=container_id,
                    session_id=container.assigned_session_id,
                )

            await db.commit()
