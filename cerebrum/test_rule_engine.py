import pytest
from datetime import datetime
from engine.rule_engine import RuleEngine
from models.schemas import Event, Rule

def test_rule_matching():
    engine = RuleEngine(db_path=":memory:")  # use in-memory DB for tests
    rule = Rule(
        id="test",
        patterns={"event_type": "authentication_failed", "protocol": "ssh"},
        threshold=3,
        aggregation_window_sec=60
    )
    engine.add_rule(rule)
    event = Event(
        id="evt1",
        session_id="s1",
        timestamp=datetime.now(),
        protocol="ssh",
        event_type="authentication_failed",
        indicators=["root"],
        source_ip="1.2.3.4"
    )
    # First event: no decision
    decisions = engine.process_event(event)
    assert len(decisions) == 0

    # Second event
    event2 = Event(
        id="evt2",
        session_id="s1",
        timestamp=datetime.now(),
        protocol="ssh",
        event_type="authentication_failed",
        indicators=[],
        source_ip="1.2.3.4"
    )
    decisions = engine.process_event(event2)
    assert len(decisions) == 0

    # Third event
    event3 = Event(
        id="evt3",
        session_id="s1",
        timestamp=datetime.now(),
        protocol="ssh",
        event_type="authentication_failed",
        indicators=[],
        source_ip="1.2.3.4"
    )
    decisions = engine.process_event(event3)
    assert len(decisions) == 1
    assert decisions[0].rule_id == "test"