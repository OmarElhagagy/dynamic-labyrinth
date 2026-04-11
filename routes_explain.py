from fastapi import APIRouter, HTTPException
from kg.graph_store import kg
from cerebrum.engine.rule_engine import rule_engine

router = APIRouter()

@router.get("/{session_id}")
def explain_session(session_id: str):
    # جلب سجل القرارات الخاصة بالجلسة من قاعدة البيانات (افترض وجود جدول decisions)
    decisions = rule_engine.get_decisions_for_session(session_id)
    kg_triples = kg.get_triples_for_session(session_id)

    text = []
    text.append(f"Session {session_id}:")
    for d in decisions:
        text.append(f"  - {d.explanation}")
    text.append("Knowledge Graph:")
    for triple in kg_triples:
        text.append(f"    {triple}")

    return {
        "session_id": session_id,
        "skill_score": rule_engine.get_skill_score(session_id),
        "decisions": decisions,
        "kg_triples": kg_triples,
        "explanation_text": "\n".join(text)
    }