from fastapi import APIRouter, BackgroundTasks, HTTPException
from models.schemas import Event, Decision
from engine.rule_engine import RuleEngine   # استيراد محرك القواعد
from kg.graph_store import KnowledgeGraph
import httpx
import hmac
import hashlib
from config import settings
router = APIRouter()
rule_engine = RuleEngine()
kg = KnowledgeGraph()

async def post_decision_to_orchestrator(decision: Decision):
    # توقيع HMAC باستخدام مفتاح مشترك
    payload = decision.json()
    signature = hmac.new(settings.HMAC_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    async with httpx.AsyncClient() as client:
        await client.post(
            settings.ORCHESTRATOR_URL + "/escalate",
            content=payload,
            headers={"X-HMAC-Signature": signature}
        )

@router.post("/")
async def ingest_event(event: Event, background_tasks: BackgroundTasks):
    # 1. حفظ الحدث في KG
    kg.add_node(event.id, "event", event.json())
    kg.add_edge(event.session_id, "has_event", event.id, event.id)

    # 2. تطبيق القواعد
    decisions = rule_engine.process_event(event)

    # 3. لكل قرار، أرسله إلى Orchestrator في الخلفية
    for decision in decisions:
        background_tasks.add_task(post_decision_to_orchestrator, decision)

    return {"status": "accepted", "decisions": [d.dict() for d in decisions]}