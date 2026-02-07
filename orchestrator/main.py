"""
Dynamic Labyrinth - Orchestrator Service

Main FastAPI application that manages honeytrap container pools,
session routing, and nginx configuration.

Endpoints:
- POST /escalate - Receive escalation decisions from Cerebrum
- GET /session/{session_id} - Get session state
- POST /session/{session_id}/release - Release session container
- GET /pools - Get pool status
- GET /healthz - Health check
- GET /metrics - Prometheus metrics
"""

import asyncio
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from config import get_pool_config, get_settings
from database import close_db, get_db_session, init_db
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from middleware.auth import HMACAuthMiddleware, RequestLoggingMiddleware, get_rate_limit_key
from models import (
    EscalationAction,
    EscalationDecision,
    EscalationResponse,
    HealthResponse,
    PoolsResponse,
    SessionInfo,
    SessionReleaseRequest,
    SessionState,
)
from nginx_writer import get_nginx_writer
from pool_manager import PoolManager
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from sqlalchemy.ext.asyncio import AsyncSession

# =============================================================================
# Logging Setup
# =============================================================================

structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

log = structlog.get_logger()

# =============================================================================
# Prometheus Metrics
# =============================================================================

REQUEST_COUNT = Counter(
    "orchestrator_requests_total", "Total request count", ["method", "endpoint", "status"]
)

REQUEST_LATENCY = Histogram(
    "orchestrator_request_latency_seconds", "Request latency in seconds", ["method", "endpoint"]
)

ACTIVE_SESSIONS = Gauge("orchestrator_active_sessions", "Number of active sessions")

POOL_CONTAINERS = Gauge(
    "orchestrator_pool_containers", "Number of containers in pool", ["level", "state"]
)

ESCALATIONS = Counter(
    "orchestrator_escalations_total", "Total escalation count", ["from_level", "to_level"]
)

# =============================================================================
# Application Setup
# =============================================================================

settings = get_settings()
pool_config = get_pool_config()

# Rate limiter
limiter = Limiter(key_func=get_rate_limit_key)

# Global state
_start_time: float = 0
_pool_manager: PoolManager | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    global _start_time, _pool_manager

    _start_time = time.time()
    log.info("Starting Orchestrator service", version=settings.service_version)

    # Validate production configuration
    config_errors = settings.validate_production_config()
    if config_errors:
        for error in config_errors:
            log.error("Configuration error", error=error)
        if not settings.debug:
            raise RuntimeError(f"Production configuration invalid: {'; '.join(config_errors)}")
        else:
            log.warning("Running in debug mode with invalid production config")

    # Verify nginx map file path is writable
    from pathlib import Path

    nginx_map_dir = Path(settings.nginx_map_path).parent
    if nginx_map_dir.exists():
        test_file = nginx_map_dir / ".write_test"
        try:
            test_file.touch()
            test_file.unlink()
            log.info("Nginx map directory is writable", path=str(nginx_map_dir))
        except (OSError, PermissionError) as e:
            log.error(
                "Nginx map directory is NOT writable",
                path=str(nginx_map_dir),
                error=str(e),
            )
            if not settings.debug:
                raise RuntimeError(f"Nginx map path not writable: {nginx_map_dir}") from e
    else:
        log.warning("Nginx map directory does not exist yet", path=str(nginx_map_dir))
        try:
            nginx_map_dir.mkdir(parents=True, exist_ok=True)
            log.info("Created nginx map directory", path=str(nginx_map_dir))
        except (OSError, PermissionError) as e:
            log.error(
                "Cannot create nginx map directory",
                path=str(nginx_map_dir),
                error=str(e),
            )
            if not settings.debug:
                raise RuntimeError(f"Cannot create nginx map directory: {nginx_map_dir}") from e

    # Initialize database
    await init_db()
    log.info("Database initialized")

    # Initialize pool manager
    _pool_manager = PoolManager(pool_config)

    # Initialize pools from config
    async for db in get_db_session():
        await _pool_manager.initialize_pools(db)

    log.info("Pool manager initialized")

    # Start background tasks
    cleanup_task = asyncio.create_task(session_cleanup_loop())

    yield

    # Shutdown
    log.info("Shutting down Orchestrator service")
    cleanup_task.cancel()
    await close_db()


app = FastAPI(
    title="Dynamic Labyrinth Orchestrator",
    description="Manages honeytrap container pools and session routing",
    version=settings.service_version,
    lifespan=lifespan,
)

# =============================================================================
# Middleware
# =============================================================================

# CORS - Use configured origins in production, allow all only in debug mode
if settings.cors_origins_list:
    cors_origins = settings.cors_origins_list
elif settings.debug:
    cors_origins = ["*"]
else:
    cors_origins = []

if not cors_origins and not settings.debug:
    log.warning("CORS origins not configured for production - defaulting to internal networks only")
    cors_origins = ["http://localhost:3000", "http://dashboard:3000", "http://10.0.3.0/24"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)

# Request logging
app.add_middleware(RequestLoggingMiddleware)

# HMAC Authentication (only enforce in production)
app.add_middleware(HMACAuthMiddleware, enforce=not settings.debug)

# Rate limiting
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS, content={"detail": "Rate limit exceeded"}
    )


# =============================================================================
# Dependencies
# =============================================================================


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency to get database session."""
    async for session in get_db_session():
        yield session


def get_pool_manager() -> PoolManager:
    """Dependency to get pool manager."""
    if _pool_manager is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Service not initialized"
        )
    return _pool_manager


# =============================================================================
# Background Tasks
# =============================================================================


async def session_cleanup_loop():
    """Background task to clean up expired sessions."""
    while True:
        try:
            await asyncio.sleep(settings.session_cleanup_interval)

            async for db in get_db_session():
                if _pool_manager:
                    count = await _pool_manager.cleanup_expired_sessions(db)
                    if count > 0:
                        # Update nginx map after cleanup
                        nginx_writer = get_nginx_writer()
                        await nginx_writer.write_map_file(db)

        except asyncio.CancelledError:
            break
        except Exception as e:
            log.error("Session cleanup error", error=str(e))


# =============================================================================
# Health & Metrics Endpoints
# =============================================================================


@app.get("/", tags=["Health"])
async def root():
    """Root endpoint."""
    return {"service": "orchestrator", "version": settings.service_version, "status": "running"}


@app.get("/healthz", response_model=HealthResponse, tags=["Health"])
async def health_check(
    db: AsyncSession = Depends(get_db), pm: PoolManager = Depends(get_pool_manager)
):
    """
    Health check endpoint.

    Returns service status and pool health.
    """
    try:
        # Check pool status
        pool_status = await pm.get_pool_status(db)
        pools_healthy = all(p.unhealthy == 0 for p in pool_status)

        return HealthResponse(
            status="healthy" if pools_healthy else "degraded",
            version=settings.service_version,
            uptime_seconds=time.time() - _start_time,
            pools_healthy=pools_healthy,
        )
    except Exception as e:
        log.error("Health check failed", error=str(e))
        return HealthResponse(
            status="unhealthy",
            version=settings.service_version,
            uptime_seconds=time.time() - _start_time,
            pools_healthy=False,
        )


@app.get("/metrics", tags=["Health"])
async def metrics():
    """Prometheus metrics endpoint."""
    return PlainTextResponse(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


# =============================================================================
# Escalation Endpoints
# =============================================================================


@app.post("/escalate", response_model=EscalationResponse, tags=["Escalation"])
@limiter.limit(settings.rate_limit)
async def escalate(
    request: Request,
    decision: EscalationDecision,
    db: AsyncSession = Depends(get_db),
    pm: PoolManager = Depends(get_pool_manager),
):
    """
    Receive escalation decision from Cerebrum.

    Assigns a container at the appropriate level and updates nginx routing.
    """
    log.info(
        "Received escalation decision",
        session_id=decision.session_id,
        action=decision.action.value,
        rule_id=decision.rule_id,
    )

    # Get current session state for logging
    current_session = await pm.get_session(db, decision.session_id)
    current_level = current_session.current_level if current_session else 1
    current_container = current_session.container_id if current_session else None
    skill_before = current_session.skill_score if current_session else 0

    # Determine target level based on action
    if decision.action == EscalationAction.ESCALATE_TO_LEVEL_2:
        target_level = 2
    elif decision.action == EscalationAction.ESCALATE_TO_LEVEL_3:
        target_level = 3
    elif decision.action == EscalationAction.RELEASE:
        # Release session
        if current_session:
            await pm.release_session(db, decision.session_id, reason="cerebrum_decision")
            nginx_writer = get_nginx_writer()
            await nginx_writer.remove_session_mapping(db, decision.session_id)

        return EscalationResponse(ok=True, session_id=decision.session_id, note="Session released")
    else:
        # MAINTAIN - no action needed
        return EscalationResponse(
            ok=True, session_id=decision.session_id, note="No escalation needed"
        )

    # Skip if already at target level or higher
    if current_level >= target_level:
        return EscalationResponse(
            ok=True,
            session_id=decision.session_id,
            container=current_container,
            target_level=current_level,
            note=f"Already at level {current_level}",
        )

    # Assign container
    container = await pm.assign_container(db, decision.session_id, target_level)

    if not container:
        log.error("No containers available", target_level=target_level)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"No containers available at level {target_level}",
        )

    # Update skill score
    await pm.update_session_score(db, decision.session_id, decision.skill_score_after)

    # Update nginx map
    nginx_writer = get_nginx_writer()
    session_cookie = pm.generate_session_cookie(decision.session_id)
    await nginx_writer.add_session_mapping(
        db, decision.session_id, session_cookie, container.address
    )

    # Reload nginx
    await nginx_writer.reload_nginx()

    # Log decision
    await pm.log_decision(
        db,
        session_id=decision.session_id,
        action=decision.action.value,
        rule_id=decision.rule_id,
        skill_score_before=skill_before,
        skill_score_after=decision.skill_score_after,
        from_container=current_container,
        to_container=container.id,
        explanation=decision.explanation,
    )

    # Update metrics
    ESCALATIONS.labels(from_level=current_level, to_level=target_level).inc()

    log.info(
        "Escalation complete",
        session_id=decision.session_id,
        from_level=current_level,
        to_level=target_level,
        container=container.id,
    )

    return EscalationResponse(
        ok=True,
        session_id=decision.session_id,
        container=container.id,
        target_level=container.level,
    )


# =============================================================================
# Session Endpoints
# =============================================================================


@app.get("/session/{session_id}", response_model=SessionInfo, tags=["Sessions"])
async def get_session(
    session_id: str, db: AsyncSession = Depends(get_db), pm: PoolManager = Depends(get_pool_manager)
):
    """
    Get session state.

    Returns current level, assigned container, and session metadata.
    """
    session = await pm.get_session(db, session_id)

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found"
        )

    # Get container info if assigned
    container_address = None
    if session.container_id:
        from database import get_container_by_id

        container = await get_container_by_id(db, session.container_id)
        if container:
            container_address = container.address

    return SessionInfo(
        session_id=session.id,
        current_level=session.current_level,
        container_id=session.container_id,
        container_address=container_address,
        state=SessionState(session.state),
        skill_score=session.skill_score,
        created_at=session.created_at,
        updated_at=session.updated_at,
        expires_at=session.expires_at,
        escalation_count=session.escalation_count,
    )


@app.post("/session/{session_id}/release", tags=["Sessions"])
async def release_session(
    session_id: str,
    release_request: SessionReleaseRequest | None = None,
    db: AsyncSession = Depends(get_db),
    pm: PoolManager = Depends(get_pool_manager),
):
    """
    Release a session's container.

    Returns the container to the idle pool and removes nginx mapping.
    """
    reason = (
        release_request.reason if release_request and release_request.reason else "manual_release"
    )

    # Release session
    success = await pm.release_session(db, session_id, reason)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found"
        )

    # Remove nginx mapping
    nginx_writer = get_nginx_writer()
    await nginx_writer.remove_session_mapping(db, session_id)
    await nginx_writer.reload_nginx()

    return {"ok": True, "session_id": session_id, "released": True}


@app.get("/sessions", tags=["Sessions"])
async def list_sessions(
    state: str | None = None, limit: int = 100, db: AsyncSession = Depends(get_db)
):
    """
    List all sessions.

    Optionally filter by state (active, released, expired).
    """
    from database import get_all_sessions

    sessions = await get_all_sessions(db, state)

    return {
        "sessions": [
            {
                "id": s.id,
                "level": s.current_level,
                "container_id": s.container_id,
                "state": s.state,
                "skill_score": s.skill_score,
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in sessions[:limit]
        ],
        "total": len(sessions),
    }


# =============================================================================
# Pool Endpoints
# =============================================================================


@app.get("/pools", response_model=PoolsResponse, tags=["Pools"])
async def get_pools(
    db: AsyncSession = Depends(get_db), pm: PoolManager = Depends(get_pool_manager)
):
    """
    Get pool status.

    Returns container counts by level and state.
    """
    pool_status = await pm.get_pool_status(db)
    total_sessions = await pm.get_total_session_count(db)
    total_containers = sum(p.total for p in pool_status)

    # Update metrics
    ACTIVE_SESSIONS.set(total_sessions)
    for p in pool_status:
        POOL_CONTAINERS.labels(level=p.level, state="idle").set(p.idle)
        POOL_CONTAINERS.labels(level=p.level, state="assigned").set(p.assigned)
        POOL_CONTAINERS.labels(level=p.level, state="unhealthy").set(p.unhealthy)

    return PoolsResponse(
        pools=pool_status, total_containers=total_containers, total_sessions=total_sessions
    )


@app.get("/pools/{level}", tags=["Pools"])
async def get_pool_by_level(
    level: int, db: AsyncSession = Depends(get_db), pm: PoolManager = Depends(get_pool_manager)
):
    """Get status for a specific pool level."""
    if level not in [1, 2, 3]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Level must be 1, 2, or 3"
        )

    pool_status = await pm.get_pool_status(db)
    level_status = next((p for p in pool_status if p.level == level), None)

    if not level_status:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Pool level {level} not found"
        )

    return level_status


# =============================================================================
# Admin Endpoints
# =============================================================================


@app.get("/admin/nginx/mappings", tags=["Admin"])
async def get_nginx_mappings(db: AsyncSession = Depends(get_db)):
    """Get current nginx session mappings."""
    nginx_writer = get_nginx_writer()
    mappings = await nginx_writer.get_current_mappings(db)
    return {"mappings": mappings, "total": len(mappings)}


@app.post("/admin/nginx/reload", tags=["Admin"])
async def reload_nginx():
    """Force nginx reload."""
    nginx_writer = get_nginx_writer()
    success = await nginx_writer.reload_nginx()

    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to reload nginx"
        )

    return {"ok": True, "message": "Nginx reloaded"}


@app.post("/admin/pools/reinitialize", tags=["Admin"])
async def reinitialize_pools(
    db: AsyncSession = Depends(get_db), pm: PoolManager = Depends(get_pool_manager)
):
    """Reinitialize container pools from configuration."""
    await pm.initialize_pools(db)
    return {"ok": True, "message": "Pools reinitialized"}


# =============================================================================
# Entry Point
# =============================================================================

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        log_level=settings.log_level.lower(),
    )
