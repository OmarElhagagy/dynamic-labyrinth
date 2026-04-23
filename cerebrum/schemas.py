from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime

class Event(BaseModel):
    id: str
    session_id: str
    timestamp: datetime
    protocol: str
    event_type: str
    indicators: List[str] = []
    source_ip: str

class Rule(BaseModel):
    id: str
    patterns: dict
    aggregation_window_sec: int = 300
    threshold: int = 5
    skill_delta: int = 1
    level_threshold: Optional[int] = None
    asserts: Optional[dict] = None

class Decision(BaseModel):
    session_id: str
    rule_id: str
    skill_score_after: int
    action: str
    explanation: str