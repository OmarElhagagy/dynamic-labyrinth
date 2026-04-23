import sqlite3
from datetime import datetime, timedelta
from typing import Optional

class Aggregator:
    def __init__(self, db_path: str = "cerebrum.db"):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self._create_tables()

    def _create_tables(self):
        self.conn.execute('''
            CREATE TABLE IF NOT EXISTS aggregated_counts (
                session_id TEXT,
                rule_id TEXT,
                event_time DATETIME,
                count INTEGER DEFAULT 1,
                PRIMARY KEY (session_id, rule_id, event_time)
            )
        ''')
        self.conn.execute('''
            CREATE TABLE IF NOT EXISTS session_skill (
                session_id TEXT PRIMARY KEY,
                skill_score INTEGER DEFAULT 0,
                current_level INTEGER DEFAULT 1,
                last_updated DATETIME
            )
        ''')
        self.conn.commit()

    def increment(self, session_id: str, rule_id: str, event_time: datetime):
        self.conn.execute(
            "INSERT INTO aggregated_counts (session_id, rule_id, event_time, count) VALUES (?, ?, ?, 1)",
            (session_id, rule_id, event_time)
        )
        self.conn.commit()

    def get_count_in_window(self, session_id: str, rule_id: str, window_sec: int, current_time: datetime) -> int:
        window_start = current_time - timedelta(seconds=window_sec)
        cursor = self.conn.execute(
            "SELECT SUM(count) FROM aggregated_counts WHERE session_id = ? AND rule_id = ? AND event_time >= ?",
            (session_id, rule_id, window_start)
        )
        result = cursor.fetchone()[0]
        return result if result is not None else 0

    def cleanup_old_events(self, older_than_hours: int = 24):
        cutoff = datetime.now() - timedelta(hours=older_than_hours)
        self.conn.execute("DELETE FROM aggregated_counts WHERE event_time < ?", (cutoff,))
        self.conn.commit()

    def get_skill_score(self, session_id: str) -> int:
        cursor = self.conn.execute(
            "SELECT skill_score FROM session_skill WHERE session_id = ?",
            (session_id,)
        )
        row = cursor.fetchone()
        return row[0] if row else 0

    def update_skill_score(self, session_id: str, skill_delta: int) -> int:
        current = self.get_skill_score(session_id)
        new_score = max(0, current + skill_delta)
        self.conn.execute(
            "INSERT OR REPLACE INTO session_skill (session_id, skill_score, last_updated) VALUES (?, ?, ?)",
            (session_id, new_score, datetime.now())
        )
        self.conn.commit()
        return new_score

    def get_current_level(self, session_id: str) -> int:
        cursor = self.conn.execute(
            "SELECT current_level FROM session_skill WHERE session_id = ?",
            (session_id,)
        )
        row = cursor.fetchone()
        return row[0] if row else 1

    def update_level(self, session_id: str, new_level: int):
        self.conn.execute(
            "UPDATE session_skill SET current_level = ?, last_updated = ? WHERE session_id = ?",
            (new_level, datetime.now(), session_id)
        )
        self.conn.commit()