#!/usr/bin/env python3
"""
Main ingestion service - FastAPI application
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from config.settings import settings
from security.hmac_utils import verify_hmac_header
from security.validation import validate_source_ip, sanitize_string
from queues.redis_queue import RedisQueue
from models.events import HoneytrapEvent, NormalizedEvent
from normalize import normalize_event

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

security = HTTPBearer()
redis_queue = RedisQueue()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle"""
    # Startup
    logger.info("Starting Ingestion Service...")
    await redis_queue.connect()
    logger.info("Ingestion Service started successfully")
    
    yield
    
    # Shutdown
    logger.info("Shutting down Ingestion Service...")
    await redis_queue.disconnect()
    logger.info("Ingestion Service stopped")

app = FastAPI(
    title="Event Ingestion Service",
    description="Secure event ingestion and normalization for Honeytrap",
    version="1.0.0",
    lifespan=lifespan
)

async def verify_authentication(
    authorization: str = Header(None),
    x_forwarded_for: str = Header(None)
):
    """Verify HMAC authentication and source IP"""
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header required")
    
    # Extract source IP
    source_ip = x_forwarded_for.split(',')[0].strip() if x_forwarded_for else "unknown"
    
    if not validate_source_ip(source_ip):
        logger.warning(f"Rejected request from unauthorized IP: {source_ip}")
        raise HTTPException(status_code=403, detail="Unauthorized source IP")
    
    if not verify_hmac_header(authorization, {}):
        raise HTTPException(status_code=401, detail="Invalid HMAC signature")
    
    return True

@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "service": "Event Ingestion",
        "version": "1.0.0",
        "status": "operational"
    }

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    redis_status = "connected" if redis_queue.connected else "disconnected"
    return {
        "status": "healthy",
        "service": "ingestion",
        "redis": redis_status,
        "timestamp": asyncio.get_event_loop().time()
    }

@app.post("/ingest/file")
async def ingest_file_events(
    events: list,
    authenticated: bool = Depends(verify_authentication)
):
    """Ingest batch events from file pusher"""
    try:
        processed_count = 0
        failed_count = 0
        
        for raw_event in events:
            try:
                # Validate and sanitize input
                sanitized_event = sanitize_event_data(raw_event)
                normalized_event = normalize_event(sanitized_event)
                
                if normalized_event:
                    await redis_queue.publish("honeytrap_events", normalized_event)
                    processed_count += 1
                    logger.info(f"Processed event: {normalized_event['id']}")
                else:
                    failed_count += 1
                    logger.warning(f"Failed to normalize event: {raw_event.get('event_id', 'unknown')}")
                    
            except Exception as e:
                failed_count += 1
                logger.error(f"Error processing event: {e}")
                continue
        
        logger.info(f"Batch processing complete: {processed_count} successful, {failed_count} failed")
        return {
            "status": "success",
            "processed": processed_count,
            "failed": failed_count,
            "total": len(events)
        }
    
    except Exception as e:
        logger.error(f"Error processing batch events: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/ingest/webhook")
async def ingest_webhook_event(
    event: HoneytrapEvent,
    authenticated: bool = Depends(verify_authentication)
):
    """Ingest single event from webhook"""
    try:
        # Normalize event
        normalized_event = normalize_event(event.dict())
        
        if not normalized_event:
            raise HTTPException(status_code=400, detail="Failed to normalize event")
        
        # Send to queue
        await redis_queue.publish("honeytrap_events", normalized_event)
        
        logger.info(f"Webhook event processed: {normalized_event['id']}")
        return {
            "status": "success",
            "event_id": normalized_event["id"],
            "message": "Event processed successfully"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing webhook event: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/metrics")
async def get_metrics():
    """Get service metrics"""
    return {
        "service": "ingestion",
        "redis_connected": redis_queue.connected,
        "uptime": "TODO",  # Implement uptime tracking
        "processed_events": "TODO"  # Implement counter
    }

def sanitize_event_data(event_data: dict) -> dict:
    """Sanitize event data to prevent injection attacks"""
    sanitized = {}
    for key, value in event_data.items():
        if isinstance(value, str):
            sanitized[key] = sanitize_string(value)
        elif isinstance(value, dict):
            sanitized[key] = sanitize_event_data(value)
        elif isinstance(value, list):
            sanitized[key] = [sanitize_string(str(item)) if isinstance(item, str) else item for item in value]
        else:
            sanitized[key] = value
    return sanitized

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host=settings.INGESTION_HOST,
        port=settings.INGESTION_PORT,
        log_level="info"
    )