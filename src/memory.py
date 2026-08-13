import json
import os
import re
import uuid
import numpy as np
from typing import List, Dict, Optional
from memory_components.schema_mixin import MemorySchemaMixin
from memory_components.conflict_commit_mixin import MemoryConflictCommitMixin
from utils.helpers import get_nested, set_nested, normalize_text
from workflow_components.resources import get_ai_resource, get_message

# Conditional import for FAISS
try:
    import faiss
except ImportError:
    faiss = None

class MemoryManager(MemorySchemaMixin, MemoryConflictCommitMixin):
    BLOCKING = "BLOCKING"
    NON_BLOCKING = "NON_BLOCKING"

    def __init__(self, db_path: str, faiss_path: str, embedding_dim: int = 768):
        self.db_path = db_path
        self.faiss_path = faiss_path
        self.embedding_dim = embedding_dim
        self.conn = None
        self.cursor = None
        self.index = None
        self._in_batch = False
        self._faiss_dirty = False
        self._faiss_backup = None
        self._faiss_had_index = False
        self._embedding_dim_backup = None
        self._faiss_load_error_backup = None
        self.faiss_load_error = None
        self.db_committee = None
        
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        
        self._init_sqlite()
        
        # Load embedding dimension from SQLite schema_meta if present
        db_dim = self.get_schema_meta("embedding_dim")
        if db_dim:
            try:
                self.embedding_dim = int(db_dim)
            except (ValueError, TypeError):
                pass
                
        self._init_faiss()
        
    def set_db_committee(self, db_committee):
        self.db_committee = db_committee

    def _audit_database_operation(
        self,
        scope: str,
        operation: str,
        payload,
        chapter_num: Optional[int] = None,
    ) -> None:
        committee = self.db_committee
        if committee is None or not committee.should_audit(scope):
            return
        approved, reason = committee.audit_operation(
            scope, operation, payload, chapter_num
        )
        if not approved:
            raise PermissionError(
                get_message(
                    "error.database_audit_denied", operation=operation, reason=reason
                )
            )

    def _maybe_commit(self):
        if self.conn and not self._in_batch:
            self.conn.commit()

    def _mark_faiss_dirty(self):
        self._faiss_dirty = True
        if not self._in_batch:
            self.save_faiss()

    def _stage_faiss_index(self, index=None) -> str:
        target = index if index is not None else self.index
        if target is None or faiss is None:
            raise RuntimeError(get_message("runtime.vector_index_unavailable"))
        parent = os.path.dirname(self.faiss_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        staged = f"{self.faiss_path}.staged-{uuid.uuid4().hex}"
        try:
            faiss.write_index(target, staged)
        except Exception:
            self._safe_remove(staged)
            raise
        return staged

    @staticmethod
    def _safe_remove(path: Optional[str]) -> None:
        if not path:
            return
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass

    def _install_staged_faiss(self, staged: str):
        backup = None
        if os.path.exists(self.faiss_path):
            backup = f"{self.faiss_path}.backup-{uuid.uuid4().hex}"
            os.replace(self.faiss_path, backup)
        try:
            os.replace(staged, self.faiss_path)
        except Exception:
            if backup and os.path.exists(backup):
                os.replace(backup, self.faiss_path)
            raise
        return backup

    def _restore_faiss_file(self, backup: Optional[str]) -> None:
        if backup and os.path.exists(backup):
            if os.path.exists(self.faiss_path):
                os.remove(self.faiss_path)
            os.replace(backup, self.faiss_path)
        elif os.path.exists(self.faiss_path):
            os.remove(self.faiss_path)

    def begin_batch(self):
        if not self.conn or self._in_batch:
            return
        self.conn.execute("BEGIN")
        self._in_batch = True
        self._faiss_had_index = self.index is not None
        self._embedding_dim_backup = self.embedding_dim
        self._faiss_load_error_backup = self.faiss_load_error
        # Keep an in-memory snapshot so failed batches can restore FAISS state.
        if faiss is not None and self.index is not None:
            try:
                self._faiss_backup = faiss.clone_index(self.index)
            except Exception:
                self.conn.rollback()
                self._in_batch = False
                self._faiss_backup = None
                self._faiss_had_index = False
                self._embedding_dim_backup = None
                self._faiss_load_error_backup = None
                raise

    def _restore_batch_memory_state(self) -> None:
        if self._faiss_backup is not None:
            self.index = self._faiss_backup
        elif not self._faiss_had_index:
            self.index = None
        if self._embedding_dim_backup is not None:
            self.embedding_dim = self._embedding_dim_backup
        self.faiss_load_error = self._faiss_load_error_backup

    def end_batch(self, success: bool = True):
        if not self.conn or not self._in_batch:
            return
        staged = None
        disk_backup = None
        disk_installed = False
        committed = False
        try:
            if not success:
                self.conn.rollback()
                self._restore_batch_memory_state()
                return

            if self._faiss_dirty:
                staged = self._stage_faiss_index()
                disk_backup = self._install_staged_faiss(staged)
                staged = None
                disk_installed = True
            self.conn.commit()
            committed = True
            self._safe_remove(disk_backup)
        except Exception as exc:
            if not committed:
                restore_error = None
                try:
                    self.conn.rollback()
                    if disk_installed:
                        self._restore_faiss_file(disk_backup)
                except Exception as rollback_exc:
                    restore_error = rollback_exc
                finally:
                    self._restore_batch_memory_state()
                if restore_error is not None:
                    raise restore_error from exc
            raise
        finally:
            self._safe_remove(staged)
            self._in_batch = False
            self._faiss_dirty = False
            self._faiss_backup = None
            self._faiss_had_index = False
            self._embedding_dim_backup = None
            self._faiss_load_error_backup = None

    def _init_faiss(self):
        """Initialize FAISS index for Tier 3."""
        if faiss is None:
            print(get_message("runtime.faiss_unavailable"))
            self.index = None
            return

        if os.path.exists(self.faiss_path):
            try:
                self.index = faiss.read_index(self.faiss_path)
                self.embedding_dim = self.index.d
            except Exception as e:
                print(get_message("runtime.faiss_load_failed", error=e))
                self.faiss_load_error = str(e)
                self.index = None
        else:
            self.index = None

    def save_faiss(self):
        if self.index and faiss:
            staged = self._stage_faiss_index()
            backup = None
            try:
                backup = self._install_staged_faiss(staged)
                staged = None
                self._faiss_dirty = False
            finally:
                self._safe_remove(staged)
                self._safe_remove(backup)

    def reconcile_vector_store(self) -> Dict[str, object]:
        """Compare active SQLite vector ids with the loaded FAISS index."""

        self.cursor.execute(
            "SELECT faiss_id FROM vector_metadata WHERE is_deleted = 0 ORDER BY faiss_id"
        )
        ids = [int(row[0]) for row in self.cursor.fetchall()]
        index_total = int(self.index.ntotal) if self.index is not None else 0
        expected_ids = list(range(index_total))
        reasons = []
        if self.faiss_load_error:
            reasons.append(f"index_load_error: {self.faiss_load_error}")
        if self.index is None and ids:
            reasons.append("active metadata exists without a loaded index")
        elif ids != expected_ids:
            reasons.append(
                f"active metadata ids {ids} do not match index ids {expected_ids}"
            )
        return {
            "healthy": not reasons,
            "requires_rebuild": bool(reasons),
            "active_metadata_count": len(ids),
            "index_total": index_total,
            "load_error": self.faiss_load_error,
            "reasons": reasons,
        }

    def _reset_vector_store(
        self,
        new_dim: int,
        preserve_metadata: bool = True,
        reason: str = "manual_reset",
    ) -> Dict[str, int]:
        """Reset FAISS and SQLite metadata as one recoverable batch."""

        if faiss is None:
            raise RuntimeError(get_message("runtime.faiss_unavailable"))
        if new_dim < 1:
            raise ValueError(get_message("validation.vector_dimension"))
        self._audit_database_operation(
            "maintenance",
            "reset_vector_store",
            {"new_dim": new_dim, "preserve_metadata": preserve_metadata, "reason": reason},
        )
        started_batch = not self._in_batch
        if started_batch:
            self.begin_batch()
        try:
            self.cursor.execute("SELECT COUNT(*) FROM vector_metadata WHERE is_deleted = 0")
            active_count = int(self.cursor.fetchone()[0])
            if preserve_metadata:
                self.cursor.execute("UPDATE vector_metadata SET is_deleted = 1 WHERE is_deleted = 0")
            else:
                self.cursor.execute("DELETE FROM vector_metadata")
            self.index = faiss.IndexFlatL2(new_dim)
            self.embedding_dim = new_dim
            self.faiss_load_error = None
            self.set_schema_meta("embedding_dim", str(new_dim))
            self._faiss_dirty = True
            if started_batch:
                self.end_batch(success=True)
            return {"preserved": active_count if preserve_metadata else 0, "deleted": 0 if preserve_metadata else active_count}
        except Exception:
            if started_batch:
                self.end_batch(success=False)
            raise

    @staticmethod
    def _deep_merge_dict(base: Dict, incoming: Dict) -> Dict:
        """Recursively merge dict-like character fields for partial updates."""
        merged = dict(base or {})
        for key, value in (incoming or {}).items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = MemoryManager._deep_merge_dict(merged[key], value)
            else:
                merged[key] = value
        return merged



    # NOTE: Heuristic conflict detection functions (_weighted_overlap_score,
    # _is_negation_text, _polarity_score, _has_antonym_pair, _rule_maybe_contradict,
    # _event_implies_active_participation, _event_is_historical_or_memorial) have been
    # removed. Fuzzy/semantic contradiction detection is now handled by the LLM Critic
    # in the workflow layer (see workflow.py _critic_review_extracted_facts).
    # Only deterministic checks remain in this module (dead->alive, identity fields, exact dedup).

    @staticmethod
    def _row_to_character_dict(row: tuple) -> Dict:
        return {
            "id": row[0],
            "name": row[1],
            "core_traits": json.loads(row[2] or "{}"),
            "status": row[3],
            "attributes": json.loads(row[4] or "{}"),
        }

    # ==========================
    # TIER 1 OPERATIONS
    # ==========================

    def upsert_character(
        self,
        name: str,
        core_traits: Optional[Dict] = None,
        attributes: Optional[Dict] = None,
        status: Optional[str] = None,
        source: str = "unknown",
        chapter_num: Optional[int] = None,
        conflict_safe: bool = False,
    ) -> int:
        if not name:
            return -1
        self._audit_database_operation(
            "character_writes",
            "upsert_character",
            {
                "name": name,
                "core_traits": core_traits,
                "attributes": attributes,
                "status": status,
                "source": source,
            },
            chapter_num,
        )
        existing = self.get_character(name)
        if existing:
            before_state = self._row_to_character_dict(existing)
            old_core_traits = json.loads(existing[2] or "{}")
            old_attributes = json.loads(existing[4] or "{}")

            if core_traits is None:
                merged_core_traits = old_core_traits
            elif isinstance(core_traits, dict):
                merged_core_traits = self._deep_merge_dict(old_core_traits, core_traits)
            else:
                merged_core_traits = core_traits

            if attributes is None:
                merged_attributes = old_attributes
            elif isinstance(attributes, dict):
                merged_attributes = self._deep_merge_dict(old_attributes, attributes)
            else:
                merged_attributes = attributes

            merged_status = status if status is not None else existing[3]

            # Hard identity constraints: preserve existing critical identity fields and queue conflicts.
            protected_paths = [
                "core_traits.identity",
                "core_traits.species",
                "attributes.identity",
                "attributes.species",
                "attributes.birth_name",
            ]
            for path in protected_paths:
                old_value = get_nested(before_state, path)
                new_value = get_nested(
                    {"core_traits": merged_core_traits, "attributes": merged_attributes},
                    path,
                )
                if old_value is not None and new_value is not None and old_value != new_value:
                    self.queue_conflict(
                        entity_type="character",
                        entity_key=name,
                        conflict_type=f"immutable_field_change:{path}",
                        incoming_obj={"path": path, "value": new_value},
                        existing_obj={"path": path, "value": old_value},
                        source=source,
                        chapter_num=chapter_num,
                        notes=get_ai_resource("memory.identity_conflict"),
                    )
                    if path.startswith("core_traits."):
                        set_nested(merged_core_traits, path.replace("core_traits.", "", 1), old_value)
                    else:
                        set_nested(merged_attributes, path.replace("attributes.", "", 1), old_value)

            # Hard conflict rule (minimal): dead cannot silently become alive.
            if (not conflict_safe) and existing[3] == "dead" and merged_status == "alive":
                incoming_obj = {
                    "name": name,
                    "core_traits": core_traits,
                    "attributes": attributes,
                    "status": status,
                }
                self.queue_conflict(
                    entity_type="character",
                    entity_key=name,
                    conflict_type="status_dead_to_alive",
                    incoming_obj=incoming_obj,
                    existing_obj=before_state,
                    source=source,
                    chapter_num=chapter_num,
                    notes=get_ai_resource("memory.resurrection_conflict"),
                )
                merged_status = existing[3]

            self.cursor.execute(
                """UPDATE characters
                   SET core_traits = ?, status = ?, attributes = ?, last_updated = CURRENT_TIMESTAMP
                   WHERE name = ?""",
                (json.dumps(merged_core_traits), merged_status, json.dumps(merged_attributes), name)
            )
            after_row = self.get_character(name)
            if after_row:
                self._log_revision(
                    entity_type="character",
                    entity_key=name,
                    action="update",
                    before_obj=before_state,
                    after_obj=self._row_to_character_dict(after_row),
                    source=source,
                    chapter_num=chapter_num,
                )
            self._maybe_commit()
            return existing[0]

        self.cursor.execute(
            "INSERT INTO characters (name, core_traits, status, attributes) VALUES (?, ?, ?, ?)",
            (name, json.dumps(core_traits or {}), status or "alive", json.dumps(attributes or {}))
        )
        inserted_id = self.cursor.lastrowid
        inserted = self.get_character(name)
        if inserted:
            self._log_revision(
                entity_type="character",
                entity_key=name,
                action="insert",
                before_obj=None,
                after_obj=self._row_to_character_dict(inserted),
                source=source,
                chapter_num=chapter_num,
            )
        self._maybe_commit()
        return inserted_id

    def get_character(self, name: str):
        self.cursor.execute("SELECT * FROM characters WHERE name = ?", (name,))
        return self.cursor.fetchone()

    def get_all_characters(self):
        self.cursor.execute("SELECT name, core_traits, status FROM characters")
        return self.cursor.fetchall()

    def add_relationship(
        self,
        source: str,
        target: str,
        relation_type: str,
        details: str = None,
        source_tag: str = "unknown",
        chapter_num: Optional[int] = None,
        conflict_safe: bool = False,
    ):
        """Adds or updates a relationship."""
        source = (source or "").strip()
        target = (target or "").strip()
        relation_type = (relation_type or "").strip()
        details = (details or "").strip()
        if not source or not target:
            return
        self._audit_database_operation(
            "relationship_writes",
            "add_relationship",
            {
                "source": source,
                "target": target,
                "relation_type": relation_type,
                "details": details,
                "source_tag": source_tag,
            },
            chapter_num,
        )

        self.cursor.execute(
            "SELECT source_name, target_name, relation_type, details FROM relationships WHERE source_name = ? AND target_name = ?",
            (source, target),
        )
        before = self.cursor.fetchone()
        if (not conflict_safe) and before and (before[2] or "").strip() != relation_type and relation_type:
            self.queue_conflict(
                entity_type="relationship",
                entity_key=f"{source}->{target}",
                conflict_type="relationship_type_change",
                incoming_obj={
                    "source_name": source,
                    "target_name": target,
                    "relation_type": relation_type,
                    "details": details,
                },
                existing_obj={
                    "source_name": before[0],
                    "target_name": before[1],
                    "relation_type": before[2],
                    "details": before[3],
                },
                source=source_tag,
                chapter_num=chapter_num,
                notes=get_ai_resource("memory.relationship_conflict"),
                blocking_level=self.NON_BLOCKING,
            )
            return

        source_char = self.get_character(source)
        target_char = self.get_character(target)
        dead_refs = []
        if source_char and source_char[3] == "dead":
            dead_refs.append(source)
        if target_char and target_char[3] == "dead":
            dead_refs.append(target)
        if dead_refs:
            self.queue_conflict(
                entity_type="relationship",
                entity_key=f"{source}->{target}",
                conflict_type="relationship_dead_character_involved",
                incoming_obj={
                    "source_name": source,
                    "target_name": target,
                    "relation_type": relation_type,
                    "details": details,
                },
                existing_obj={"dead_entities": dead_refs},
                source=source_tag,
                chapter_num=chapter_num,
                notes=get_ai_resource("memory.dead_relationship"),
                blocking_level=self.NON_BLOCKING,
            )

        self.cursor.execute(
            '''INSERT INTO relationships (source_name, target_name, relation_type, details) 
               VALUES (?, ?, ?, ?)
               ON CONFLICT(source_name, target_name) 
               DO UPDATE SET relation_type=excluded.relation_type, details=excluded.details''',
            (source, target, relation_type, details)
        )
        self.cursor.execute(
            "SELECT source_name, target_name, relation_type, details FROM relationships WHERE source_name = ? AND target_name = ?",
            (source, target),
        )
        after = self.cursor.fetchone()
        self._log_revision(
            entity_type="relationship",
            entity_key=f"{source}->{target}",
            action="update" if before else "insert",
            before_obj={
                "source_name": before[0],
                "target_name": before[1],
                "relation_type": before[2],
                "details": before[3],
            } if before else None,
            after_obj={
                "source_name": after[0],
                "target_name": after[1],
                "relation_type": after[2],
                "details": after[3],
            } if after else None,
            source=source_tag,
            chapter_num=chapter_num,
        )
        self._maybe_commit()

    def get_relationships(self, character_name: str):
        """Get all relationships involving a character."""
        self.cursor.execute(
            "SELECT source_name, target_name, relation_type, details FROM relationships WHERE source_name = ? OR target_name = ?", 
            (character_name, character_name)
        )
        return self.cursor.fetchall()

    def add_rule(
        self,
        category: str,
        content: str,
        strictness: int = 1,
        source: str = "unknown",
        chapter_num: Optional[int] = None,
        source_commit_id: Optional[str] = None,
        intent_tag: str = "",
    ):
        default_category = get_ai_resource("default.general")
        normalized_category = (category or default_category).strip()
        normalized_content = (content or "").strip()
        normalized_intent = (intent_tag or "").strip()
        if not normalized_content:
            return -1
        self._audit_database_operation(
            "world_rule_writes",
            "add_rule",
            {
                "category": normalized_category,
                "content": normalized_content,
                "strictness": strictness,
                "source": source,
            },
            chapter_num,
        )
        # NOTE: Heuristic rule contradiction checks have been removed from this layer.
        # Semantic contradiction detection between rules is now handled by the LLM Critic
        # in the workflow layer before facts are committed to the database.

        self.cursor.execute(
            """SELECT id, category, rule_content, strictness
               FROM world_rules
               WHERE category = ? AND rule_content = ? AND strictness = ? AND is_deleted = 0
               ORDER BY id ASC
               LIMIT 1""",
            (normalized_category, normalized_content, strictness),
        )
        existing = self.cursor.fetchone()
        if existing:
            return int(existing[0])

        self.cursor.execute(
            """INSERT INTO world_rules
               (category, rule_content, strictness, source_commit_id, version, is_deleted, intent_tag)
               VALUES (?, ?, ?, ?, 1, 0, ?)""",
            (normalized_category, normalized_content, strictness, source_commit_id, normalized_intent),
        )
        rule_id = self.cursor.lastrowid
        self._log_revision(
            entity_type="world_rule",
            entity_key=str(rule_id),
            action="insert",
            before_obj=None,
            after_obj={"id": rule_id, "category": normalized_category, "content": normalized_content, "strictness": strictness},
            source=source,
            chapter_num=chapter_num,
        )
        self._maybe_commit()
        return int(rule_id)

    def get_rules_by_category(self, category: str = None):
        if category:
            self.cursor.execute(
                "SELECT rule_content, strictness FROM world_rules WHERE category = ? AND is_deleted = 0",
                (category,),
            )
        else:
            self.cursor.execute("SELECT category, rule_content, strictness FROM world_rules WHERE is_deleted = 0")
        return self.cursor.fetchall()

    # ==========================
    # TIER 2 OPERATIONS
    # ==========================

    def add_event(
        self,
        event_name: str,
        description: str,
        timestamp_str: str,
        impact_level: int = 1,
        related_entities: List[str] = None,
        location: str = None,
        source: str = "unknown",
        chapter_num: Optional[int] = None,
        source_commit_id: Optional[str] = None,
        intent_tag: str = "",
    ):
        default_event_name = get_ai_resource("default.untitled_event")
        default_time = get_ai_resource("default.unknown_time")
        default_location = get_ai_resource("default.unknown")
        normalized_event_name = (event_name or default_event_name).strip() or default_event_name
        normalized_description = (description or "").strip()
        normalized_timestamp = (timestamp_str or default_time).strip() or default_time
        normalized_location = (location or default_location).strip() or default_location
        normalized_intent = (intent_tag or "").strip()
        normalized_related_entities = [str(x).strip() for x in (related_entities or []) if str(x).strip()]
        entities_json = json.dumps(normalized_related_entities, ensure_ascii=False, sort_keys=True)
        self._audit_database_operation(
            "timeline_event_writes",
            "add_event",
            {
                "event_name": normalized_event_name,
                "description": normalized_description,
                "timestamp": normalized_timestamp,
                "impact_level": impact_level,
                "related_entities": normalized_related_entities,
                "location": normalized_location,
                "source": source,
            },
            chapter_num,
        )

        # Deterministic guard: flag events that reference dead characters.
        # The event is still inserted (not blocked) — semantic judgment about whether
        # this is a genuine contradiction vs. memorial/flashback is delegated to the
        # LLM Critic in the workflow layer.
        dead_entities = []
        for entity in normalized_related_entities:
            char = self.get_character(entity)
            if char and char[3] == "dead":
                dead_entities.append(entity)
        if dead_entities:
            self.queue_conflict(
                entity_type="timeline_event",
                entity_key=f"{normalized_event_name}@{normalized_timestamp}",
                conflict_type="timeline_dead_character_involved",
                incoming_obj={
                    "event_name": normalized_event_name,
                    "description": normalized_description,
                    "timestamp_str": normalized_timestamp,
                    "related_entities": normalized_related_entities,
                    "location": normalized_location,
                },
                existing_obj={"dead_entities": dead_entities},
                source=source,
                chapter_num=chapter_num,
                notes=get_ai_resource("memory.dead_event"),
                blocking_level=self.NON_BLOCKING,
            )

        # NOTE: Strict world rule contradiction checks have been removed from this layer.
        # Semantic contradiction detection is now handled by the LLM Critic in the
        # workflow layer before facts are committed to the database.

        self.cursor.execute(
            """SELECT id
               FROM timeline_events
               WHERE event_name = ? AND description = ? AND timestamp_str = ? AND location = ? AND related_entities = ? AND is_deleted = 0
               ORDER BY id ASC
               LIMIT 1""",
            (
                normalized_event_name,
                normalized_description,
                normalized_timestamp,
                normalized_location,
                entities_json,
            ),
        )
        existing = self.cursor.fetchone()
        if existing:
            return int(existing[0])

        self.cursor.execute(
            """SELECT id, description, location, related_entities
               FROM timeline_events
               WHERE event_name = ? AND timestamp_str = ? AND is_deleted = 0
               ORDER BY id ASC
               LIMIT 1""",
            (normalized_event_name, normalized_timestamp),
        )
        same_key = self.cursor.fetchone()
        if same_key:
            existing_id, existing_desc, existing_loc, existing_related = same_key
            incoming_signature = {
                "description": normalized_description,
                "location": normalized_location,
                "related_entities": json.loads(entities_json or "[]"),
            }
            existing_signature = {
                "description": existing_desc or "",
                "location": existing_loc or "",
                "related_entities": json.loads(existing_related or "[]"),
            }
            self.queue_conflict(
                entity_type="timeline_event",
                entity_key=f"{normalized_event_name}@{normalized_timestamp}",
                conflict_type="timeline_event_version_conflict",
                incoming_obj=incoming_signature,
                existing_obj=existing_signature,
                source=source,
                chapter_num=chapter_num,
                notes=get_ai_resource("memory.event_conflict"),
            )
            return int(existing_id)

        self.cursor.execute(
            '''INSERT INTO timeline_events 
               (event_name, description, timestamp_str, impact_level, related_entities, location, source_commit_id, version, is_deleted, intent_tag) 
               VALUES (?, ?, ?, ?, ?, ?, ?, 1, 0, ?)''',
            (
                normalized_event_name,
                normalized_description,
                normalized_timestamp,
                impact_level,
                entities_json,
                normalized_location,
                source_commit_id,
                normalized_intent,
            ),
        )
        event_id = self.cursor.lastrowid
        self._log_revision(
            entity_type="timeline_event",
            entity_key=str(event_id),
            action="insert",
            before_obj=None,
            after_obj={
                "id": event_id,
                "event_name": normalized_event_name,
                "description": normalized_description,
                "timestamp_str": normalized_timestamp,
                "impact_level": impact_level,
                "related_entities": related_entities or [],
                "location": normalized_location,
            },
            source=source,
            chapter_num=chapter_num,
        )
        self._maybe_commit()
        return int(event_id)

    def get_events(self, entity_filter: str = None, limit: int = 10):
        """Retrieve events. If entity_filter is provided, perform a basic text search in JSON."""
        if entity_filter:
            # Note: This is a simple LIKE query, adequate for prototypes but not highly performant for massive DBs
            search_pattern = f'%"{entity_filter}"%'
            self.cursor.execute(
                "SELECT * FROM timeline_events WHERE is_deleted = 0 AND related_entities LIKE ? ORDER BY id DESC LIMIT ?", 
                (search_pattern, limit)
            )
        else:
            self.cursor.execute("SELECT * FROM timeline_events WHERE is_deleted = 0 ORDER BY id DESC LIMIT ?", (limit,))
        return self.cursor.fetchall()

    # ==========================
    # TIER 3 OPERATIONS (Vector)
    # ==========================

    def add_semantic_fact(
        self,
        content: str,
        embedding: List[float],
        metadata: Dict = None,
        source: str = "unknown",
        chapter_num: Optional[int] = None,
        source_commit_id: Optional[str] = None,
        intent_tag: str = "",
    ):
        if faiss is None:
            return

        if not embedding:
            return

        self._audit_database_operation(
            "vector_writes",
            "add_semantic_fact",
            {"content": content, "metadata": metadata or {}, "source": source},
            chapter_num,
        )

        embedding_np = np.array([embedding], dtype=np.float32)
        if embedding_np.ndim != 2:
            return

        actual_dim = embedding_np.shape[1]
        if actual_dim <= 0:
            return

        normalized_content = (content or "").strip()
        if not normalized_content:
            return
        normalized_intent = (intent_tag or "").strip()
        normalized_metadata = metadata or {}
        if chapter_num is not None:
            normalized_metadata["chapter"] = chapter_num
        metadata_json = json.dumps(normalized_metadata, ensure_ascii=False, sort_keys=True)
        started_batch = not self._in_batch
        if started_batch:
            self.begin_batch()
        try:
            if self.index is None:
                self.embedding_dim = actual_dim
                self.index = faiss.IndexFlatL2(actual_dim)
                self.set_schema_meta("embedding_dim", str(actual_dim))
            elif self.index.d != actual_dim:
                raise RuntimeError(get_message("runtime.vector_dim_mismatch", expected=self.index.d, actual=actual_dim))

            self.cursor.execute(
                """SELECT faiss_id
                   FROM vector_metadata
                   WHERE content = ? AND metadata = ? AND is_deleted = 0
                   ORDER BY faiss_id ASC
                   LIMIT 1""",
                (normalized_content, metadata_json),
            )
            if self.cursor.fetchone():
                if started_batch:
                    self.end_batch(success=True)
                return

            self.index.add(embedding_np)
            faiss_id = self.index.ntotal - 1
            self.cursor.execute(
                """INSERT INTO vector_metadata
                   (faiss_id, content, metadata, source_commit_id, version, is_deleted, intent_tag)
                   VALUES (?, ?, ?, ?, 1, 0, ?)""",
                (faiss_id, normalized_content, metadata_json, source_commit_id, normalized_intent),
            )
            self._log_revision(
                entity_type="vector_detail",
                entity_key=str(faiss_id),
                action="insert",
                before_obj=None,
                after_obj={"faiss_id": faiss_id, "content": normalized_content, "metadata": normalized_metadata},
                source=source,
                chapter_num=chapter_num,
            )
            self._faiss_dirty = True
            if started_batch:
                self.end_batch(success=True)
        except Exception:
            if started_batch and self._in_batch:
                self.end_batch(success=False)
            raise

    def search_semantic(self, query_embedding: List[float], k: int = 5, filter_metadata: Dict = None) -> List[Dict]:
        """
        Search for semantic matches. 
        Optional: filter_metadata (key-value exact match on top-level JSON keys) - applied POST-retrieval (approximate).
        For strict filtering, we'd need a FAISS IDMap or pre-filtering strategy. 
        Here we over-fetch and filter for simplicity.
        """
        if self.index is None or faiss is None:
            return []

        if not query_embedding:
            return []

        query_np = np.array([query_embedding], dtype=np.float32)
        if query_np.ndim != 2 or query_np.shape[1] != self.index.d:
            return []

        search_k = k * 3 if filter_metadata else k # Fetch more if we plan to filter
        distances, indices = self.index.search(query_np, search_k)
        
        results = []
        for idx in indices[0]:
            if idx == -1: continue
            
            self.cursor.execute(
                "SELECT content, metadata FROM vector_metadata WHERE faiss_id = ? AND is_deleted = 0",
                (int(idx),),
            )
            row = self.cursor.fetchone()
            if row:
                content = row[0]
                meta = json.loads(row[1])
                
                # Simple post-filtering
                if filter_metadata:
                    match = True
                    for k_filter, v_filter in filter_metadata.items():
                        if meta.get(k_filter) != v_filter:
                            match = False
                            break
                    if not match:
                        continue
                
                results.append({
                    "content": content,
                    "metadata": meta,
                    "score": float(distances[0][np.where(indices[0] == idx)][0]) # L2 distance
                })
                
            if len(results) >= k:
                break
                
        return results

    def rebuild_vector_index_from_metadata(self, embedding_fn, include_deleted: bool = False) -> Dict[str, object]:
        """Rebuild FAISS deterministically and retain a row-level audit trail."""

        if faiss is None:
            raise RuntimeError(get_message("runtime.faiss_unavailable"))
        if self._in_batch:
            raise RuntimeError(get_message("runtime.vector_batch_nested"))
        self._audit_database_operation(
            "maintenance",
            "rebuild_vector_index",
            {"include_deleted": include_deleted},
        )
        if self.index is None:
            print(get_message("runtime.faiss_rebuild_notice"))

        run_id = str(uuid.uuid4())
        where_clause = "" if include_deleted else "WHERE is_deleted = 0"
        self.cursor.execute(
            f"""SELECT faiss_id, content, metadata, source_commit_id, version, is_deleted, intent_tag
                FROM vector_metadata {where_clause} ORDER BY faiss_id ASC"""
        )
        rows = self.cursor.fetchall()
        self.cursor.execute(
            """INSERT INTO vector_rebuild_runs
               (run_id, status, source_count) VALUES (?, 'STARTED', ?)""",
            (run_id, len(rows)),
        )
        self.conn.commit()

        rebuilt_rows = []
        skipped_rows = []
        target_dim = None
        for row in rows:
            old_id, content, metadata_json, source_commit_id, version, is_deleted, intent_tag = row
            reason = None
            try:
                raw_embedding = embedding_fn(content)
                embedding = list(raw_embedding) if raw_embedding is not None else None
            except Exception as exc:
                embedding = None
                reason = f"embedding_error: {exc}"
            if not embedding:
                reason = reason or "empty_embedding"
            elif target_dim is None:
                target_dim = len(embedding)
            elif len(embedding) != target_dim:
                reason = f"dimension_mismatch: expected {target_dim}, got {len(embedding)}"
            if reason:
                skipped_rows.append((row, reason))
            else:
                rebuilt_rows.append((row, embedding))

        target_dim = target_dim or self.embedding_dim
        try:
            new_index = faiss.IndexFlatL2(target_dim)
            started_batch = not self._in_batch
            if started_batch:
                self.begin_batch()
        except Exception as exc:
            self.cursor.execute(
                """UPDATE vector_rebuild_runs
                   SET status='FAILED', completed_at=CURRENT_TIMESTAMP, error_message=?
                   WHERE run_id=?""",
                (str(exc), run_id),
            )
            self.conn.commit()
            raise
        try:
            self.cursor.execute("DELETE FROM vector_metadata")
            for new_id, (row, embedding) in enumerate(rebuilt_rows):
                old_id, content, metadata_json, source_commit_id, version, is_deleted, intent_tag = row
                new_index.add(np.array([embedding], dtype=np.float32))
                self.cursor.execute(
                    """INSERT INTO vector_metadata
                       (faiss_id, content, metadata, source_commit_id, version, is_deleted, intent_tag)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (new_id, content, metadata_json, source_commit_id, int(version or 1), int(is_deleted or 0), intent_tag or ""),
                )
                self.cursor.execute(
                    """INSERT INTO vector_rebuild_audit
                       (run_id, old_faiss_id, new_faiss_id, content, status, reason, metadata)
                       VALUES (?, ?, ?, ?, 'REBUILT', '', ?)""",
                    (run_id, old_id, new_id, content, metadata_json),
                )

            tombstone_id = -1
            for row, reason in skipped_rows:
                old_id, content, metadata_json, source_commit_id, version, _, intent_tag = row
                self.cursor.execute(
                    """INSERT INTO vector_metadata
                       (faiss_id, content, metadata, source_commit_id, version, is_deleted, intent_tag)
                       VALUES (?, ?, ?, ?, ?, 1, ?)""",
                    (tombstone_id, content, metadata_json, source_commit_id, int(version or 1), intent_tag or ""),
                )
                self.cursor.execute(
                    """INSERT INTO vector_rebuild_audit
                       (run_id, old_faiss_id, new_faiss_id, content, status, reason, metadata)
                       VALUES (?, ?, ?, ?, 'SKIPPED', ?, ?)""",
                    (run_id, old_id, tombstone_id, content, reason, metadata_json),
                )
                tombstone_id -= 1

            self.index = new_index
            self.embedding_dim = target_dim
            self.set_schema_meta("embedding_dim", str(target_dim))
            self._faiss_dirty = True
            self.cursor.execute(
                """UPDATE vector_rebuild_runs
                   SET status='COMPLETED', completed_at=CURRENT_TIMESTAMP,
                       rebuilt_count=?, skipped_count=?, target_dim=?
                   WHERE run_id=?""",
                (len(rebuilt_rows), len(skipped_rows), target_dim, run_id),
            )
            if started_batch:
                self.end_batch(success=True)
            self.faiss_load_error = None
            return {
                "rebuilt": len(rebuilt_rows),
                "skipped": len(skipped_rows),
                "run_id": run_id,
            }
        except Exception as exc:
            if started_batch and self._in_batch:
                self.end_batch(success=False)
            self.cursor.execute(
                """UPDATE vector_rebuild_runs
                   SET status='FAILED', completed_at=CURRENT_TIMESTAMP, error_message=?
                   WHERE run_id=?""",
                (str(exc), run_id),
            )
            self.conn.commit()
            raise

    def close(self):
        if self.conn:
            self.conn.close()
