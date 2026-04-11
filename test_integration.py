import pytest
import tempfile
import os
from datetime import datetime
from models.schemas import Event, Rule, Decision
from cerebrum.engine.rule_engine import RuleEngine
from kg.graph_store import KnowledgeGraph
from engine.aggregator import Aggregator
from datetime import datetime, timedelta
@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    os.unlink(path)

@pytest.fixture
def engine(temp_db):
    return RuleEngine(db_path=temp_db)

def test_simple_rule_trigger(engine):
    rule = Rule(
        id="test_ssh_brute",
        patterns={"protocol": "ssh", "event_type": "authentication_failed"},
        aggregation_window_sec=60,
        threshold=3,
        skill_delta=2,
        asserts={"action": "escalate_to_level_2"}
    )
    engine.add_rule(rule)

    session_id = "session1"
    now = datetime.now()

    for i in range(3):
        event = Event(
            id=f"evt{i}",
            session_id=session_id,
            timestamp=now,
            protocol="ssh",
            event_type="authentication_failed",
            indicators=["root"],
            source_ip="1.2.3.4"
        )
        decisions = engine.process_event(event)

    assert len(decisions) == 1
    assert decisions[0].rule_id == "test_ssh_brute"
    assert decisions[0].skill_score_after == 2
    assert decisions[0].action == "escalate_to_level_2"

def test_multiple_rules(engine):
    rule1 = Rule(
        id="rule_ssh",
        patterns={"protocol": "ssh", "event_type": "authentication_failed"},
        aggregation_window_sec=60,
        threshold=2,
        skill_delta=1,
        asserts={"action": "escalate_to_level_2"}
    )
    rule2 = Rule(
        id="rule_http",
        patterns={"protocol": "http", "event_type": "sql_injection"},
        aggregation_window_sec=60,
        threshold=1,
        skill_delta=1,
        asserts={"action": "escalate_to_level_2"}
    )
    engine.add_rule(rule1)
    engine.add_rule(rule2)

    session_id = "session_multi"
    now = datetime.now()

    for i in range(2):
        event = Event(
            id=f"ssh_{i}",
            session_id=session_id,
            timestamp=now,
            protocol="ssh",
            event_type="authentication_failed",
            indicators=[],
            source_ip="1.2.3.4"
        )
        engine.process_event(event)

    event_http = Event(
        id="http_1",
        session_id=session_id,
        timestamp=now,
        protocol="http",
        event_type="sql_injection",
        indicators=["' OR 1=1"],
        source_ip="1.2.3.4"
    )
    decisions = engine.process_event(event_http)

    assert len(decisions) == 2
    rule_ids = {d.rule_id for d in decisions}
    assert rule_ids == {"rule_ssh", "rule_http"}

def test_kg_creation(engine):
    """اختبار: عند تطابق قاعدة، يجب إضافة عقد وحواف في الـ KG"""
    rule = Rule(
        id="kg_test",
        patterns={"event_type": "authentication_failed"},
        aggregation_window_sec=60,
        threshold=1,
        skill_delta=1,
        asserts={}
    )
    engine.add_rule(rule)

    event = Event(
        id="evt_kg",
        session_id="session_kg",
        timestamp=datetime.now(),
        protocol="ssh",
        event_type="authentication_failed",
        indicators=["admin"],
        source_ip="10.0.0.1"
    )
    engine.process_event(event)

    kg = engine.kg  
    cursor = kg.conn.execute("SELECT id FROM kg_nodes WHERE id = ?", ("evt_kg",))
    assert cursor.fetchone() is not None
    cursor = kg.conn.execute("SELECT * FROM kg_edges WHERE src = ? AND rel = ?", ("session_kg", "has_event"))
    assert cursor.fetchone() is not None

def test_no_rule_match(engine):
    rule = Rule(
        id="only_ssh",
        patterns={"protocol": "ssh"},
        aggregation_window_sec=60,
        threshold=1,
        skill_delta=1,
        asserts={}
    )
    engine.add_rule(rule)

    event = Event(
        id="http_event",
        session_id="s1",
        timestamp=datetime.now(),
        protocol="http",
        event_type="get",
        indicators=[],
        source_ip="1.1.1.1"
    )
    decisions = engine.process_event(event)
    assert len(decisions) == 0

def test_aggregation_window(engine):
    rule = Rule(
        id="time_test",
        patterns={"protocol": "ssh"},
        aggregation_window_sec=10,  # 10 ثواني فقط
        threshold=2,
        skill_delta=1,
        asserts={}
    )
    engine.add_rule(rule)

    session_id = "session_time"
    t1 = datetime.now()
    t2 = t1 + timedelta(seconds=5)
    t3 = t1 + timedelta(seconds=20)  

    event1 = Event(id="e1", session_id=session_id, timestamp=t1, protocol="ssh", event_type="auth", indicators=[], source_ip="1.1.1.1")
    event2 = Event(id="e2", session_id=session_id, timestamp=t2, protocol="ssh", event_type="auth", indicators=[], source_ip="1.1.1.1")
    engine.process_event(event1)
    decisions = engine.process_event(event2)
    assert len(decisions) == 1

    event3 = Event(id="e3", session_id=session_id, timestamp=t3, protocol="ssh", event_type="auth", indicators=[], source_ip="1.1.1.1")
    decisions = engine.process_event(event3)
    assert len(decisions) == 0

def test_skill_score_update(engine):
    rule = Rule(
        id="skill_rule",
        patterns={"event_type": "test"},
        aggregation_window_sec=60,
        threshold=1,
        skill_delta=3,
        asserts={}
    )
    engine.add_rule(rule)

    event = Event(
        id="skill_evt",
        session_id="session_skill",
        timestamp=datetime.now(),
        protocol="test",
        event_type="test",
        indicators=[],
        source_ip="1.1.1.1"
    )
    decisions = engine.process_event(event)
    assert len(decisions) == 1
    assert decisions[0].skill_score_after == 3

    event2 = Event(
        id="skill_evt2",
        session_id="session_skill",
        timestamp=datetime.now(),
        protocol="test",
        event_type="test",
        indicators=[],
        source_ip="1.1.1.1"
    )
    decisions = engine.process_event(event2)
    assert decisions[0].skill_score_after == 6