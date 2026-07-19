import sqlite3
import sys
from typing import Optional


class MemorySchemaMixin:
    def _ensure_schema_meta_table(self):
        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )

    def _init_sqlite_schema(self):
        self._ensure_schema_meta_table()
        
        self.cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS characters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                core_traits TEXT,
                status TEXT DEFAULT 'alive',
                attributes TEXT,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            '''
        )

        self.cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS relationships (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_name TEXT NOT NULL,
                target_name TEXT NOT NULL,
                relation_type TEXT,
                details TEXT,
                UNIQUE(source_name, target_name)
            )
            '''
        )

        self.cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS world_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT,
                rule_content TEXT NOT NULL,
                strictness INTEGER DEFAULT 1,
                source_commit_id TEXT,
                version INTEGER DEFAULT 1,
                is_deleted INTEGER DEFAULT 0,
                intent_tag TEXT
            )
            '''
        )

        self.cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS timeline_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_name TEXT,
                description TEXT,
                timestamp_str TEXT,
                impact_level INTEGER,
                related_entities TEXT,
                location TEXT,
                source_commit_id TEXT,
                version INTEGER DEFAULT 1,
                is_deleted INTEGER DEFAULT 0,
                intent_tag TEXT
            )
            '''
        )

        self.cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS vector_metadata (
                faiss_id INTEGER PRIMARY KEY,
                content TEXT,
                metadata TEXT,
                source_commit_id TEXT,
                version INTEGER DEFAULT 1,
                is_deleted INTEGER DEFAULT 0,
                intent_tag TEXT,
                timestamp_created TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            '''
        )

        self.cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS fact_revisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_type TEXT NOT NULL,
                entity_key TEXT NOT NULL,
                action TEXT NOT NULL,
                before_json TEXT,
                after_json TEXT,
                source TEXT,
                chapter_num INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            '''
        )

        self.cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS conflict_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_type TEXT NOT NULL,
                entity_key TEXT NOT NULL,
                conflict_type TEXT NOT NULL,
                incoming_json TEXT,
                existing_json TEXT,
                source TEXT,
                chapter_num INTEGER,
                blocking_level TEXT DEFAULT 'BLOCKING',
                priority INTEGER DEFAULT 2,
                suggested_action TEXT DEFAULT 'manual_review',
                status TEXT DEFAULT 'PENDING',
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                resolved_at TIMESTAMP
            )
            '''
        )

        self.cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS chapter_commits (
                commit_id TEXT PRIMARY KEY,
                chapter_num INTEGER,
                source TEXT,
                payload_json TEXT,
                status TEXT DEFAULT 'STARTED',
                conflicts_count INTEGER DEFAULT 0,
                error_message TEXT,
                replay_count INTEGER DEFAULT 0,
                last_replayed_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            '''
        )

        # Create indexes
        self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_conflict_queue_status_id ON conflict_queue(status, id)")
        self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_conflict_queue_entity ON conflict_queue(entity_type, entity_key)")
        self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_timeline_event_time ON timeline_events(event_name, timestamp_str)")
        self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_world_rules_category_strictness ON world_rules(category, strictness)")
        self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_fact_revisions_entity ON fact_revisions(entity_type, entity_key, id)")
        self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_chapter_commits_chapter ON chapter_commits(chapter_num, created_at)")
        self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_conflict_queue_blocking ON conflict_queue(status, blocking_level, id)")
        self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_chapter_commits_status_created ON chapter_commits(status, created_at)")
        self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_conflict_queue_triage ON conflict_queue(status, blocking_level, priority, id)")
        self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_world_rules_active ON world_rules(is_deleted, category, strictness, id)")
        self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_timeline_active ON timeline_events(is_deleted, event_name, timestamp_str, id)")
        self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_vector_metadata_active ON vector_metadata(is_deleted, faiss_id)")

    def _init_sqlite(self):
        self.conn = sqlite3.connect(self.db_path)
        self.cursor = self.conn.cursor()
        self._init_sqlite_schema()

    def get_schema_meta(self, key: str) -> Optional[str]:
        self.cursor.execute(
            "SELECT value FROM schema_meta WHERE key = ? LIMIT 1",
            (key,),
        )
        row = self.cursor.fetchone()
        return row[0] if row else None

    def set_schema_meta(self, key: str, value: str):
        self.cursor.execute(
            """
            INSERT INTO schema_meta (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, str(value)),
        )
        if hasattr(self, "_maybe_commit"):
            self._maybe_commit()
        elif self.conn and not getattr(self, "_in_batch", False):
            self.conn.commit()
