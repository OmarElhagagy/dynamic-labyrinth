"""
HTTP webhook ingestion for Honeytrap events
"""

import logging
from typing import Dict, Any

from fastapi import APIRouter, HTTPException, Depends, Header
from pydantic import BaseModel

from security.hmac_utils import verify_hmac_header
from security.validation import validate_source_ip
from normalize import normalize_event
from queues.redis_queue import RedisQueue

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")
redis_queue = RedisQueue()

class WebhookEvent(BaseModel):
    """Webhook event payload model"""
    event_id: str
    type: str
    timestamp: str
    remote_ip: str
    service: str
    data: Dict[str, Any] = {}
    session_id: str = None
    indicators: list = []

async def verify_webhook_auth(
    authorization: str = Header(None),
    x_forwarded_for: str = Header(None)
):
    """Verify webhook authentication"""
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header required")
    
    # Validate source IP
    source_ip = x_forwarded_for.split(',')[0].strip() if x_forwarded_for else "unknown"
    if not validate_source_ip(source_ip):
        logger.warning(f"Rejected webhook from unauthorized IP: {source_ip}")
        raise HTTPException(status_code=403, detail="Unauthorized source IP")
    
    # Verify HMAC (we'll use a dummy payload for signature)
    if not verify_hmac_header(authorization, {}):
        raise HTTPException(status_code=401, detail="Invalid HMAC signature")
    
    return True

@router.post("/webhook", summary="Receive Honeytrap webhook events")
async def receive_webhook(
    event: WebhookEvent,
    authenticated: bool = Depends(verify_webhook_auth)
):
    """
    Receive events via webhook from Honeytrap instances
    """
    try:
        logger.info(f"Received webhook event: {event.event_id}")
        
        # Normalize event
        event_dict = event.dict()
        normalized_event = normalize_event(event_dict)
        
        if not normalized_event:
            logger.error(f"Failed to normalize webhook event: {event.event_id}")
            raise HTTPException(status_code=400, detail="Failed to normalize event")
        
        # Send to processing queue
        await redis_queue.publish("honeytrap_events", normalized_event)
        
        logger.info(f"Successfully processed webhook event: {normalized_event['id']}")
        return {
            "status": "success",
            "event_id": normalized_event["id"],
            "message": "Event queued for processing"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error processing webhook: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.post("/webhook/batch", summary="Receive batch webhook events")
async def receive_batch_webhook(
    events: list[WebhookEvent],
    authenticated: bool = Depends(verify_webhook_auth)
):
    """
    Receive batch events via webhook
    """
    try:
        logger.info(f"Received batch webhook with {len(events)} events")
        
        processed = 0
        failed = 0
        
        for event in events:
            try:
                normalized_event = normalize_event(event.dict())
                if normalized_event:
                    await redis_queue.publish("honeytrap_events", normalized_event)
                    processed += 1
                else:
                    failed += 1
            except Exception as e:
                logger.error(f"Error processing event in batch: {e}")
                failed += 1
                continue
        
        logger.info(f"Batch webhook processed: {processed} successful, {failed} failed")
        return {
            "status": "success",
            "processed": processed,
            "failed": failed,
            "total": len(events)
        }
        
    except Exception as e:
        logger.error(f"Error processing batch webhook: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.get("/webhook/health")
async def webhook_health():
    """Webhook health check"""
    return {
        "status": "healthy",
        "service": "webhook_ingestion",
        "redis_connected": redis_queue.connected
    }