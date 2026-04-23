import sqlite3
import json

class KnowledgeGraph:
    def __init__(self, db_path="cerebrum.db"):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self._create_tables()

    def _create_tables(self):
        self.conn.execute('''
            CREATE TABLE IF NOT EXISTS kg_nodes (
                id TEXT PRIMARY KEY,
                type TEXT,
                data TEXT
            )
        ''')
        self.conn.execute('''
            CREATE TABLE IF NOT EXISTS kg_edges (
                src TEXT,
                rel TEXT,
                dst TEXT,
                evidence_event_id TEXT,
                FOREIGN KEY(src) REFERENCES kg_nodes(id),
                FOREIGN KEY(dst) REFERENCES kg_nodes(id)
            )
        ''')
        self.conn.execute('''
            CREATE TABLE IF NOT EXISTS session_nodes (
                session_id TEXT,
                node_id TEXT
            )
        ''')
        self.conn.commit()

    def add_node(self, node_id, type, data):
        self.conn.execute(
            "INSERT OR IGNORE INTO kg_nodes (id, type, data) VALUES (?, ?, ?)",
            (node_id, type, data)
        )
        self.conn.commit()

    def add_edge(self, src, rel, dst, evidence_event_id):
        self.conn.execute(
            "INSERT INTO kg_edges (src, rel, dst, evidence_event_id) VALUES (?, ?, ?, ?)",
            (src, rel, dst, evidence_event_id)
        )
        self.conn.commit()

    def get_triples_for_session(self, session_id: str):
        """Return all triples (src, rel, dst) related to a session."""
        cursor = self.conn.execute('''
            SELECT e.src, e.rel, e.dst
            FROM kg_edges e
            JOIN session_nodes sn ON sn.node_id = e.src OR sn.node_id = e.dst
            WHERE sn.session_id = ?
        ''', (session_id,))
        return cursor.fetchall()