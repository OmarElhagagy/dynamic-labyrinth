from typing import List, Dict, Optional
from datetime import datetime
from models.schemas import Rule, Event, Decision
from engine.aggregator import Aggregator
from kg.graph_store import KnowledgeGraph

class RuleEngine:
    def __init__(self, db_path="cerebrum.db"):
        self.db_path = db_path
        self.aggregator = Aggregator(db_path)
        self.kg = KnowledgeGraph(db_path)
        self.rules: Dict[str, Rule] = {}
        self._init_decisions_table()

    def _init_decisions_table(self):
        """Store decisions for later explanation queries."""
        self.aggregator.conn.execute('''
            CREATE TABLE IF NOT EXISTS decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                rule_id TEXT,
                skill_score_after INTEGER,
                action TEXT,
                explanation TEXT,
                timestamp DATETIME
            )
        ''')
        self.aggregator.conn.commit()

    # --- CRUD for rules ---
    def add_rule(self, rule: Rule):
        self.rules[rule.id] = rule

    def get_all_rules(self) -> List[Rule]:
        return list(self.rules.values())

    def update_rule(self, rule_id: str, rule: Rule):
        if rule_id not in self.rules:
            raise KeyError(f"Rule {rule_id} not found")
        self.rules[rule_id] = rule

    def delete_rule(self, rule_id: str):
        if rule_id in self.rules:
            del self.rules[rule_id]

    # --- Decision storage & retrieval ---
    def _store_decision(self, decision: Decision):
        self.aggregator.conn.execute(
            "INSERT INTO decisions (session_id, rule_id, skill_score_after, action, explanation, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
            (decision.session_id, decision.rule_id, decision.skill_score_after, decision.action, decision.explanation, datetime.now())
        )
        self.aggregator.conn.commit()

    def get_decisions_for_session(self, session_id: str) -> List[Decision]:
        cursor = self.aggregator.conn.execute(
            "SELECT session_id, rule_id, skill_score_after, action, explanation FROM decisions WHERE session_id = ? ORDER BY timestamp",
            (session_id,)
        )
        return [Decision(session_id=row[0], rule_id=row[1], skill_score_after=row[2], action=row[3], explanation=row[4]) for row in cursor.fetchall()]

    def get_skill_score(self, session_id: str) -> int:
        return self.aggregator.get_skill_score(session_id)

    # --- Event processing ---
    def process_event(self, event: Event) -> List[Decision]:
        decisions = []
        for rule in self.rules.values():
            if self._matches_pattern(event, rule.patterns):
                count = self.aggregator.get_count_in_window(
                    event.session_id, rule.id, rule.aggregation_window_sec, event.timestamp
                )
                if count + 1 >= rule.threshold:
                    self.aggregator.increment(event.session_id, rule.id, event.timestamp)
                    new_score = self.aggregator.update_skill_score(event.session_id, rule.skill_delta)
                    self._add_to_kg(event, rule)
                    decision = Decision(
                        session_id=event.session_id,
                        rule_id=rule.id,
                        skill_score_after=new_score,
                        action=rule.asserts.get("action", "no_op") if rule.asserts else "no_op",
                        explanation=f"Matched rule {rule.id}: {count+1} events within {rule.aggregation_window_sec}s. Evidence: {event.id}"
                    )
                    decisions.append(decision)
                    self._store_decision(decision)
                else:
                    self.aggregator.increment(event.session_id, rule.id, event.timestamp)
        return decisions

    def _matches_pattern(self, event: Event, patterns: dict) -> bool:
        for key, value in patterns.items():
            if getattr(event, key, None) != value:
                return False
        return True

    def _add_to_kg(self, event: Event, rule: Rule):
        self.kg.add_node(event.id, "event", event.model_dump_json())
        self.kg.add_edge(event.session_id, "has_event", event.id, event.id)
        rule_node_id = f"rule_{rule.id}"
        self.kg.add_node(rule_node_id, "rule", rule.model_dump_json())
        self.kg.add_edge(event.id, "matches_rule", rule_node_id, event.id)
        # Also record that the session owns this event node
        self.kg.conn.execute(
            "INSERT OR IGNORE INTO session_nodes (session_id, node_id) VALUES (?, ?)",
            (event.session_id, event.id)
        )
        self.kg.conn.commit()