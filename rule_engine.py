from typing import List, Dict
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

    def add_rule(self, rule: Rule):
        self.rules[rule.id] = rule

    def process_event(self, event: Event) -> List[Decision]:
        decisions = []
        for rule in self.rules.values():
            if self._matches_pattern(event, rule.patterns):
                count = self.aggregator.get_count_in_window(
                    event.session_id, rule.id, rule.aggregation_window_sec, event.timestamp
                )
                if count + 1 >= rule.threshold:
                    # زيادة العداد أولاً
                    self.aggregator.increment(event.session_id, rule.id, event.timestamp)
                    # تحديث skill_score
                    new_score = self.aggregator.update_skill_score(event.session_id, rule.skill_delta)
                    # إضافة للـ KG
                    self._add_to_kg(event, rule)
                    # إنشاء القرار
                    decision = Decision(
                        session_id=event.session_id,
                        rule_id=rule.id,
                        skill_score_after=new_score,
                        action=rule.asserts.get("action", "no_op"),
                        explanation=f"Matched rule {rule.id}: {count+1} events within {rule.aggregation_window_sec}s. Evidence: {event.id}"
                    )
                    decisions.append(decision)
                else:
                    self.aggregator.increment(event.session_id, rule.id, event.timestamp)
        return decisions

    def _matches_pattern(self, event: Event, patterns: dict) -> bool:
        for key, value in patterns.items():
            if getattr(event, key, None) != value:
                return False
        return True

    def _add_to_kg(self, event: Event, rule: Rule):
        self.kg.add_node(event.id, "event", event.json())
        self.kg.add_edge(event.session_id, "has_event", event.id, event.id)
        rule_node_id = f"rule_{rule.id}"
        self.kg.add_node(rule_node_id, "rule", rule.json())
        self.kg.add_edge(event.id, "matches_rule", rule_node_id, event.id)