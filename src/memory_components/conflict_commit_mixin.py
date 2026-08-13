import json
import uuid
from typing import Any, Dict, List, Optional

from workflow_components.resources import get_ai_resource

class MemoryConflictCommitMixin:
    @staticmethod
    def _flatten_dict(data: Any, prefix: str = "") -> Dict[str, Any]:
        if not isinstance(data, dict):
            return {prefix or "$": data}
        flat: Dict[str, Any] = {}
        for key, value in data.items():
            next_prefix = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                flat.update(MemoryConflictCommitMixin._flatten_dict(value, next_prefix))
            else:
                flat[next_prefix] = value
        return flat

    @staticmethod
    def _json_diff_paths(incoming_obj: Any, existing_obj: Any) -> List[str]:
        incoming = MemoryConflictCommitMixin._flatten_dict(incoming_obj or {})
        existing = MemoryConflictCommitMixin._flatten_dict(existing_obj or {})
        keys = sorted(set(incoming.keys()) | set(existing.keys()))
        return [k for k in keys if incoming.get(k) != existing.get(k)]

    @staticmethod
    def _conflict_reason_label(conflict_type: str) -> str:
        if conflict_type.startswith("immutable_field_change:"):
            return "IMMUTABLE_FIELD_CHANGE"
        mapping = {
            "status_dead_to_alive": "CHARACTER_RESURRECTION_CONFLICT",
            "relationship_type_change": "RELATIONSHIP_TYPE_MUTATION",
            "strict_rule_contradiction": "STRICT_RULE_CONTRADICTION",
            "timeline_dead_character_involved": "DEAD_CHARACTER_ACTIVE_EVENT",
            "timeline_rule_contradiction": "EVENT_RULE_CONTRADICTION",
            "timeline_event_version_conflict": "EVENT_VERSION_CONFLICT",
            "relationship_dead_character_involved": "DEAD_CHARACTER_RELATIONSHIP_MUTATION",
        }
        return mapping.get(conflict_type, "UNKNOWN_CONFLICT")

    def _infer_blocking_level(self, conflict_type: str) -> str:
        non_blocking_types = {
            "relationship_type_change",
            "relationship_dead_character_involved",
        }
        if conflict_type in non_blocking_types:
            return self.NON_BLOCKING
        return self.BLOCKING

    def _infer_priority(self, conflict_type: str, blocking_level: str) -> int:
        high_priority = {
            "status_dead_to_alive",
            "timeline_dead_character_involved",
            "timeline_rule_contradiction",
            "strict_rule_contradiction",
        }
        medium_priority = {
            "timeline_event_version_conflict",
            "immutable_field_change",
        }
        if conflict_type.startswith("immutable_field_change:"):
            return 2
        if conflict_type in high_priority:
            return 3 if blocking_level == self.BLOCKING else 2
        if conflict_type in medium_priority:
            return 2
        if blocking_level == self.NON_BLOCKING:
            return 1
        return 2

    def _infer_suggested_action(self, conflict_type: str, blocking_level: str) -> str:
        if conflict_type == "status_dead_to_alive":
            return "manual_review_apply_or_keep"
        if conflict_type in {"timeline_dead_character_involved", "timeline_rule_contradiction"}:
            return "revise_event_payload"
        if conflict_type.startswith("immutable_field_change:"):
            return "keep_existing"
        if conflict_type in {"relationship_type_change", "relationship_dead_character_involved"}:
            return "manual_review_non_blocking"
        if blocking_level == self.BLOCKING:
            return "manual_review_blocking"
        return "manual_review"

    def _log_revision(
        self,
        entity_type: str,
        entity_key: str,
        action: str,
        before_obj: Optional[Dict],
        after_obj: Optional[Dict],
        source: str = "unknown",
        chapter_num: Optional[int] = None,
    ):
        payload = {
            "entity_type": entity_type,
            "entity_key": entity_key,
            "action": action,
            "before": before_obj,
            "after": after_obj,
            "source": source,
        }
        try:
            self._audit_database_operation(
                "revision_writes",
                "log_fact_revision",
                payload,
                chapter_num,
            )
        except Exception:
            # Standalone fact writes have already staged their parent row by the
            # time the exact before/after revision is known. Ensure a rejected
            # revision audit cannot leave that parent mutation pending.
            if self.conn and not self._in_batch:
                self.conn.rollback()
            raise
        self.cursor.execute(
            """INSERT INTO fact_revisions
               (entity_type, entity_key, action, before_json, after_json, source, chapter_num)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                entity_type,
                entity_key,
                action,
                json.dumps(before_obj, ensure_ascii=False) if before_obj is not None else None,
                json.dumps(after_obj, ensure_ascii=False) if after_obj is not None else None,
                source,
                chapter_num,
            ),
        )

    def queue_conflict(
        self,
        entity_type: str,
        entity_key: str,
        conflict_type: str,
        incoming_obj: Optional[Dict],
        existing_obj: Optional[Dict],
        source: str = "unknown",
        chapter_num: Optional[int] = None,
        notes: str = "",
        blocking_level: Optional[str] = None,
        priority: Optional[int] = None,
        suggested_action: Optional[str] = None,
    ) -> int:
        self._audit_database_operation(
            "conflict_queue_writes",
            "queue_conflict",
            {
                "entity_type": entity_type,
                "entity_key": entity_key,
                "conflict_type": conflict_type,
                "incoming": incoming_obj,
                "existing": existing_obj,
                "source": source,
            },
            chapter_num,
        )
        normalized_blocking = (blocking_level or self._infer_blocking_level(conflict_type)).upper()
        if normalized_blocking not in {self.BLOCKING, self.NON_BLOCKING}:
            normalized_blocking = self.BLOCKING
        normalized_priority = priority if priority is not None else self._infer_priority(conflict_type, normalized_blocking)
        normalized_suggested_action = suggested_action or self._infer_suggested_action(conflict_type, normalized_blocking)
        incoming_json = json.dumps(incoming_obj, ensure_ascii=False) if incoming_obj is not None else None
        existing_json = json.dumps(existing_obj, ensure_ascii=False) if existing_obj is not None else None
        self.cursor.execute(
            """SELECT id FROM conflict_queue
               WHERE status = 'PENDING'
                 AND entity_type = ?
                 AND entity_key = ?
                 AND conflict_type = ?
                 AND blocking_level = ?
                 AND IFNULL(incoming_json, '') = IFNULL(?, '')
                 AND IFNULL(existing_json, '') = IFNULL(?, '')
               ORDER BY id ASC
               LIMIT 1""",
            (entity_type, entity_key, conflict_type, normalized_blocking, incoming_json, existing_json),
        )
        row = self.cursor.fetchone()
        if row:
            self.cursor.execute(
                """UPDATE conflict_queue
                   SET blocking_level = ?,
                       priority = ?,
                       suggested_action = ?,
                       notes = CASE
                           WHEN notes IS NULL OR notes = '' THEN ?
                           ELSE notes
                       END
                   WHERE id = ?""",
                (
                    normalized_blocking,
                    normalized_priority,
                    normalized_suggested_action,
                    notes or "",
                    int(row[0]),
                ),
            )
            self._maybe_commit()
            return int(row[0])

        self.cursor.execute(
            """INSERT INTO conflict_queue
               (entity_type, entity_key, conflict_type, incoming_json, existing_json, source, chapter_num, blocking_level, priority, suggested_action, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                entity_type,
                entity_key,
                conflict_type,
                incoming_json,
                existing_json,
                source,
                chapter_num,
                normalized_blocking,
                normalized_priority,
                normalized_suggested_action,
                notes,
            ),
        )
        self._maybe_commit()
        return self.cursor.lastrowid

    def begin_chapter_commit(self, chapter_num: int, source: str, payload: Optional[Dict] = None) -> str:
        self._audit_database_operation(
            "chapter_commit_metadata",
            "begin_chapter_commit",
            {"source": source, "payload": payload or {}},
            chapter_num,
        )
        commit_id = str(uuid.uuid4())
        self.cursor.execute(
            """INSERT INTO chapter_commits (commit_id, chapter_num, source, payload_json, status)
               VALUES (?, ?, ?, ?, 'STARTED')""",
            (commit_id, chapter_num, source, json.dumps(payload, ensure_ascii=False) if payload else None),
        )
        self._maybe_commit()
        return commit_id

    def finalize_chapter_commit(
        self,
        commit_id: str,
        status: str,
        conflicts_count: int = 0,
        error_message: str = "",
    ):
        row = self.get_chapter_commit(commit_id)
        self._audit_database_operation(
            "chapter_commit_metadata",
            "finalize_chapter_commit",
            {
                "commit_id": commit_id,
                "from_status": row[4] if row else None,
                "to_status": status,
                "conflicts_count": conflicts_count,
                "error_message": error_message,
            },
            row[1] if row else None,
        )
        self.cursor.execute(
            """UPDATE chapter_commits
               SET status = ?,
                   conflicts_count = ?,
                   error_message = ?,
                   replay_count = CASE
                       WHEN ? = 'COMPLETED' AND status = 'FAILED' THEN replay_count + 1
                       ELSE replay_count
                   END,
                   last_replayed_at = CASE
                       WHEN ? = 'COMPLETED' AND status = 'FAILED' THEN CURRENT_TIMESTAMP
                       ELSE last_replayed_at
                   END
               WHERE commit_id = ?""",
            (status, conflicts_count, error_message or "", status, status, commit_id),
        )
        self._maybe_commit()

    def get_chapter_commit(self, commit_id: str):
        self.cursor.execute(
            """SELECT commit_id, chapter_num, source, payload_json, status, conflicts_count,
                      error_message, replay_count, last_replayed_at, created_at
               FROM chapter_commits
               WHERE commit_id = ?
               LIMIT 1""",
            (commit_id,),
        )
        return self.cursor.fetchone()

    def get_chapter_commits(self, chapter_num: int, source: Optional[str] = None, limit: int = 50):
        if source:
            self.cursor.execute(
                """SELECT commit_id, chapter_num, source, payload_json, status, conflicts_count,
                          error_message, replay_count, last_replayed_at, created_at
                   FROM chapter_commits
                   WHERE chapter_num = ? AND source = ?
                   ORDER BY created_at DESC
                   LIMIT ?""",
                (chapter_num, source, limit),
            )
        else:
            self.cursor.execute(
                """SELECT commit_id, chapter_num, source, payload_json, status, conflicts_count,
                          error_message, replay_count, last_replayed_at, created_at
                   FROM chapter_commits
                   WHERE chapter_num = ?
                   ORDER BY created_at DESC
                   LIMIT ?""",
                (chapter_num, limit),
            )
        return self.cursor.fetchall()

    def purge_incomplete_chapter_commits(self, chapter_num: int, source: Optional[str] = None) -> int:
        self._audit_database_operation(
            "chapter_commit_metadata",
            "purge_incomplete_chapter_commits",
            {"chapter_num": chapter_num, "source": source},
            chapter_num,
        )
        if source:
            self.cursor.execute(
                """DELETE FROM chapter_commits
                   WHERE chapter_num = ? AND source = ? AND status IN ('STARTED', 'FAILED')""",
                (chapter_num, source),
            )
        else:
            self.cursor.execute(
                """DELETE FROM chapter_commits
                   WHERE chapter_num = ? AND status IN ('STARTED', 'FAILED')""",
                (chapter_num,),
            )
        deleted = self.cursor.rowcount or 0
        self._maybe_commit()
        return int(deleted)

    def get_failed_chapter_commits(self, limit: int = 20):
        self.cursor.execute(
            """SELECT commit_id, chapter_num, source, status, conflicts_count, error_message, replay_count, created_at
               FROM chapter_commits
               WHERE status = 'FAILED'
               ORDER BY created_at DESC
               LIMIT ?""",
            (limit,),
        )
        return self.cursor.fetchall()

    def get_pending_conflicts(
        self,
        limit: int = 50,
        blocking_only: bool = False,
        blocking_level: Optional[str] = None,
    ):
        level_filter = (blocking_level or "").upper().strip()
        if blocking_only:
            level_filter = self.BLOCKING
        if level_filter == self.BLOCKING:
            self.cursor.execute(
                """SELECT id, entity_type, entity_key, conflict_type, source, chapter_num, created_at, blocking_level, priority, suggested_action
                   FROM conflict_queue WHERE status = 'PENDING' AND blocking_level = 'BLOCKING'
                   ORDER BY priority DESC, id ASC LIMIT ?""",
                (limit,),
            )
        elif level_filter == self.NON_BLOCKING:
            self.cursor.execute(
                """SELECT id, entity_type, entity_key, conflict_type, source, chapter_num, created_at, blocking_level, priority, suggested_action
                   FROM conflict_queue WHERE status = 'PENDING' AND blocking_level = 'NON_BLOCKING'
                   ORDER BY priority DESC, id ASC LIMIT ?""",
                (limit,),
            )
        else:
            self.cursor.execute(
                """SELECT id, entity_type, entity_key, conflict_type, source, chapter_num, created_at, blocking_level, priority, suggested_action
                   FROM conflict_queue WHERE status = 'PENDING'
                   ORDER BY CASE WHEN blocking_level = 'BLOCKING' THEN 1 ELSE 0 END DESC, priority DESC, id ASC LIMIT ?""",
                (limit,),
            )
        return self.cursor.fetchall()

    def get_pending_conflict_diagnostics(
        self,
        limit: int = 50,
        blocking_level: Optional[str] = None,
    ) -> List[Dict]:
        level_filter = (blocking_level or "").upper().strip()
        if level_filter in {self.BLOCKING, self.NON_BLOCKING}:
            self.cursor.execute(
                """SELECT id, entity_type, entity_key, conflict_type, incoming_json, existing_json,
                          source, chapter_num, notes, created_at, blocking_level, priority, suggested_action
                   FROM conflict_queue
                   WHERE status = 'PENDING' AND blocking_level = ?
                   ORDER BY priority DESC, id ASC
                   LIMIT ?""",
                (level_filter, limit),
            )
        else:
            self.cursor.execute(
                """SELECT id, entity_type, entity_key, conflict_type, incoming_json, existing_json,
                          source, chapter_num, notes, created_at, blocking_level, priority, suggested_action
                   FROM conflict_queue
                   WHERE status = 'PENDING'
                   ORDER BY CASE WHEN blocking_level = 'BLOCKING' THEN 1 ELSE 0 END DESC, priority DESC, id ASC
                   LIMIT ?""",
                (limit,),
            )
        rows = self.cursor.fetchall()
        diagnostics: List[Dict] = []
        for row in rows:
            incoming = json.loads(row[4] or "{}")
            existing = json.loads(row[5] or "{}")
            diagnostics.append(
                {
                    "id": row[0],
                    "entity_type": row[1],
                    "entity_key": row[2],
                    "conflict_type": row[3],
                    "reason_label": self._conflict_reason_label(row[3]),
                    "diff_paths": self._json_diff_paths(incoming, existing),
                    "incoming": incoming,
                    "existing": existing,
                    "source": row[6],
                    "chapter_num": row[7],
                    "notes": row[8],
                    "created_at": row[9],
                    "blocking_level": row[10],
                    "priority": row[11],
                    "suggested_action": row[12],
                }
            )
        return diagnostics

    def get_pending_conflict_triage(
        self,
        limit: int = 50,
        blocking_level: Optional[str] = None,
    ) -> List[Dict]:
        return self.get_pending_conflict_diagnostics(limit=limit, blocking_level=blocking_level)

    def get_pending_conflict_count(
        self,
        blocking_only: bool = False,
        blocking_level: Optional[str] = None,
    ) -> int:
        level_filter = (blocking_level or "").upper().strip()
        if blocking_only:
            level_filter = self.BLOCKING
        if level_filter == self.BLOCKING:
            self.cursor.execute(
                "SELECT COUNT(*) FROM conflict_queue WHERE status = 'PENDING' AND blocking_level = 'BLOCKING'"
            )
        elif level_filter == self.NON_BLOCKING:
            self.cursor.execute(
                "SELECT COUNT(*) FROM conflict_queue WHERE status = 'PENDING' AND blocking_level = 'NON_BLOCKING'"
            )
        else:
            self.cursor.execute("SELECT COUNT(*) FROM conflict_queue WHERE status = 'PENDING'")
        row = self.cursor.fetchone()
        return int(row[0]) if row else 0

    def get_pending_blocking_conflict_count(self) -> int:
        return self.get_pending_conflict_count(blocking_only=True)

    def get_conflict_by_id(self, conflict_id: int):
        self.cursor.execute(
            """SELECT id, entity_type, entity_key, conflict_type, incoming_json, existing_json,
                      source, chapter_num, status, notes, created_at, resolved_at, blocking_level
               FROM conflict_queue WHERE id = ?""",
            (conflict_id,),
        )
        return self.cursor.fetchone()

    def resolve_conflict(
        self,
        conflict_id: int,
        action: str,
        resolver_note: str = "",
        source: str = "manual_resolve",
    ) -> bool:
        row = self.get_conflict_by_id(conflict_id)
        if not row:
            return False
        if row[8] != "PENDING":
            return False

        entity_type = row[1]
        entity_key = row[2]
        conflict_type = row[3]
        incoming_json = json.loads(row[4] or "{}")
        chapter_num = row[7]

        if action not in {"keep_existing", "apply_incoming"}:
            return False

        self._audit_database_operation(
            "conflict_resolution",
            "resolve_conflict",
            {
                "conflict_id": conflict_id,
                "entity_type": entity_type,
                "entity_key": entity_key,
                "conflict_type": conflict_type,
                "incoming": incoming_json,
                "action": action,
                "source": source,
            },
            chapter_num,
        )
        if action == "apply_incoming" and not (
            (entity_type == "character" and conflict_type == "status_dead_to_alive")
            or (
                entity_type == "relationship"
                and conflict_type == "relationship_type_change"
            )
        ):
            return False

        status_note = resolver_note or get_ai_resource(
            "memory.manual_resolution_note", action=action
        )
        self._audit_database_operation(
            "conflict_queue_writes",
            "resolve_conflict_queue_row",
            {
                "conflict_id": conflict_id,
                "from_status": row[8],
                "to_status": "RESOLVED",
                "action": action,
                "resolver_note": status_note,
            },
            chapter_num,
        )

        started_batch = not self._in_batch
        if started_batch:
            self.begin_batch()
        try:
            if action == "apply_incoming":
                if entity_type == "character":
                    self.upsert_character(
                        name=entity_key,
                        core_traits=incoming_json.get("core_traits"),
                        attributes=incoming_json.get("attributes"),
                        status=incoming_json.get("status"),
                        source=source,
                        chapter_num=chapter_num,
                        conflict_safe=True,
                    )
                else:
                    self.add_relationship(
                        source=incoming_json.get("source_name"),
                        target=incoming_json.get("target_name"),
                        relation_type=incoming_json.get("relation_type"),
                        details=incoming_json.get("details"),
                        source_tag=source,
                        chapter_num=chapter_num,
                        conflict_safe=True,
                    )

            self.cursor.execute(
                """UPDATE conflict_queue
                   SET status = 'RESOLVED',
                       notes = CASE
                           WHEN notes IS NULL OR notes = '' THEN ?
                           ELSE notes || '\n' || ?
                       END,
                       resolved_at = CURRENT_TIMESTAMP
                   WHERE id = ? AND status = 'PENDING'""",
                (status_note, status_note, conflict_id),
            )
            resolved = self.cursor.rowcount == 1
            if started_batch:
                self.end_batch(success=resolved)
            return resolved
        except Exception:
            if started_batch and self._in_batch:
                self.end_batch(success=False)
            raise
