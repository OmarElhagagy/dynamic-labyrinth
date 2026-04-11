from fastapi import APIRouter, HTTPException
from engine.rule_engine import rule_engine
from models.schemas import Rule
from cerebrum.engine.rule_engine import rule_engine
router = APIRouter()

@router.get("/")
def list_rules():
    return rule_engine.get_all_rules()

@router.post("/")
def create_rule(rule: Rule):
    rule_engine.add_rule(rule)
    return {"status": "created", "id": rule.id}

@router.put("/{rule_id}")
def update_rule(rule_id: str, rule: Rule):
    rule_engine.update_rule(rule_id, rule)
    return {"status": "updated"}

@router.delete("/{rule_id}")
def delete_rule(rule_id: str):
    rule_engine.delete_rule(rule_id)
    return {"status": "deleted"}


rule_engine = rule_engine()