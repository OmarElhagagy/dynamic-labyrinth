from fastapi import APIRouter, HTTPException
from models.schemas import Rule
from engine import default_engine as rule_engine   # use the shared instance

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
    try:
        rule_engine.update_rule(rule_id, rule)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"status": "updated"}

@router.delete("/{rule_id}")
def delete_rule(rule_id: str):
    rule_engine.delete_rule(rule_id)
    return {"status": "deleted"}