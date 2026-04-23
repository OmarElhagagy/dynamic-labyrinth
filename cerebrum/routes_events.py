from fastapi import APIRouter, BackgroundTasks, HTTPException
from models.schemas import Event, Decision
from engine import default_engine as rule_engine
from kg.graph_store import KnowledgeGraph
import httpx
import hmac
import hashlib
from config import settings

router = APIRouter()
kg = KnowledgeGraph()  # separate KG instance (or reuse rule_engine.kg)

async def post_decision_to_orchestrator(decision: Decision):
    payload = decision.model_dump_json()
    signature = hmac.new(settings.HMAC_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    async with httpx.AsyncClient() as client:
        await client.post(
            settings.ORCHESTRATOR_URL + "/escalate",
            content=payload,
            headers={"X-HMAC-Signature": signature}
        )

@router.post("/")
async def ingest_event(event: Event, background_tasks: BackgroundTasks):
    # 1. Save event in KG
    kg.add_node(event.id, "event", event.model_dump_json())
    kg.add_edge(event.session_id, "has_event", event.id, event.id)

    # 2. Apply rules
    decisions = rule_engine.process_event(event)

    # 3. Send each decision to orchestrator in background
    for decision in decisions:
        background_tasks.add_task(post_decision_to_orchestrator, decision)

    return {"status": "accepted", "decisions": [d.model_dump() for d in decisions]}