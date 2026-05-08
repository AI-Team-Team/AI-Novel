import json
import os
import re
import numpy as np
from typing import List, Dict, Optional
from memory_components.schema_mixin import MemorySchemaMixin
from memory_components.conflict_commit_mixin import MemoryConflictCommitMixin

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
        
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        
        self._init_sqlite()
        self._init_faiss()

    def _maybe_commit(self):
        if self.conn and not self._in_batch:
            self.conn.commit()

    def _mark_faiss_dirty(self):
        self._faiss_dirty = True
        if not self._in_batch:
            self.save_faiss()

    def begin_batch(self):
        if not self.conn or self._in_batch:
            return
        self._in_batch = True
        self.conn.execute("BEGIN")
        # Keep an in-memory snapshot so failed batches can restore FAISS state.
        if faiss is not None and self.index is not None:
            try:
                self._faiss_backup = faiss.clone_index(self.index)
            except Exception:
                self._faiss_backup = None

    def end_batch(self, success: bool = True):
        if not self.conn or not self._in_batch:
            return
        try:
            if success:
                self.conn.commit()
                if self._faiss_dirty:
                    self.save_faiss()
            else:
                self.conn.rollback()
                if self._faiss_backup is not None:
                    self.index = self._faiss_backup
        finally:
            self._in_batch = False
            self._faiss_dirty = False
            self._faiss_backup = None

    def _init_faiss(self):
        """Initialize FAISS index for Tier 3."""
        if faiss is None:
            print("Warning: FAISS not installed. Vector search will be disabled.")
            self.index = None
            return

        if os.path.exists(self.faiss_path):
            try:
                self.index = faiss.read_index(self.faiss_path)
                self.embedding_dim = self.index.d
            except Exception as e:
                print(f"Error loading FAISS index: {e}. Vector search will start empty. Please run --rebuild-vectors to restore.")
                self.index = None
        else:
            self.index = None

    def save_faiss(self):
        if self.index and faiss:
            faiss.write_index(self.index, self.faiss_path)

    def _reset_vector_store(self, new_dim: int):
        """Protect vector_metadata from silent deletion during dimension mismatch."""
        raise RuntimeError(
            f"Embedding dimension mismatch detected (new: {new_dim}, existing: {self.embedding_dim}). "
            "Please run --rebuild-vectors to safely migrate existing vector data."
        )

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

    @staticmethod
    def _get_nested(obj: Optional[Dict], path: str):
        if not isinstance(obj, dict):
            return None
        cur = obj
        parts = path.split(".")
        for part in parts:
            if not isinstance(cur, dict) or part not in cur:
                return None
            cur = cur[part]
        return cur

    @staticmethod
    def _set_nested(obj: Dict, path: str, value):
        parts = path.split(".")
        cur = obj
        for part in parts[:-1]:
            if part not in cur or not isinstance(cur[part], dict):
                cur[part] = {}
            cur = cur[part]
        cur[parts[-1]] = value

    @staticmethod
    def _normalize_text(text: str) -> str:
        return re.sub(r"\s+", " ", (text or "").strip().lower())

    @staticmethod
    def _tokenize_text(text: str) -> set:
        normalized = MemoryManager._normalize_text(text)
        tokens = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", normalized)
        return set(tokens)

    @staticmethod
    def _weighted_overlap_score(a_tokens: set, b_tokens: set) -> float:
        if not a_tokens or not b_tokens:
            return 0.0
        domain_weight = {
            "resurrection": 2.4,
            "revive": 2.2,
            "alive": 2.0,
            "dead": 2.0,
            "death": 1.8,
            "forbid": 2.0,
            "forbidden": 2.0,
            "allow": 2.0,
            "allowed": 2.0,
            "禁止": 2.0,
            "允许": 2.0,
            "复活": 2.6,
            "死亡": 2.0,
            "活着": 2.0,
            "不可": 1.8,
            "可以": 1.8,
        }

        union = a_tokens | b_tokens
        overlap = a_tokens & b_tokens
        union_weight = sum(domain_weight.get(t, 1.0) for t in union)
        overlap_weight = sum(domain_weight.get(t, 1.0) for t in overlap)
        return overlap_weight / max(1e-9, union_weight)

    @staticmethod
    def _is_negation_text(text: str) -> bool:
        normalized = MemoryManager._normalize_text(text)
        zh_markers = ["不", "禁止", "不可", "不能", "无", "不得"]
        en_markers = [" no ", " not ", " never ", " forbid ", " cannot ", " can't "]
        if any(marker in normalized for marker in zh_markers):
            return True
        padded = f" {normalized} "
        return any(marker in padded for marker in en_markers)

    @staticmethod
    def _polarity_score(text: str) -> int:
        normalized = MemoryManager._normalize_text(text)
        positive_markers = [
            "allow", "allowed", "can", "permitted", "enable", "enabled", "可以", "允许", "能够",
        ]
        negative_markers = [
            "forbid", "forbidden", "cannot", "can't", "never", "disallow", "ban", "banned",
            "禁止", "不得", "不能", "不可",
        ]
        score = 0
        for marker in positive_markers:
            if marker in normalized:
                score += 1
        for marker in negative_markers:
            if marker in normalized:
                score -= 1
        return score

    @staticmethod
    def _has_antonym_pair(a: str, b: str) -> bool:
        pairs = [
            ("alive", "dead"),
            ("life", "death"),
            ("allow", "forbid"),
            ("allowed", "forbidden"),
            ("permit", "ban"),
            ("resurrection", "no resurrection"),
            ("可以", "禁止"),
            ("允许", "禁止"),
            ("活着", "死亡"),
            ("复活", "不可复活"),
        ]
        for x, y in pairs:
            if (x in a and y in b) or (y in a and x in b):
                return True
        return False

    @staticmethod
    def _event_implies_active_participation(text: str) -> bool:
        normalized = MemoryManager._normalize_text(text)
        markers = [
            "returns", "appears", "talks", "fights", "arrives", "joins", "speaks", "wakes",
            "归来", "出现", "说话", "战斗", "到达", "参加", "苏醒", "复活",
        ]
        return any(marker in normalized for marker in markers)

    @staticmethod
    def _event_is_historical_or_memorial(text: str) -> bool:
        normalized = MemoryManager._normalize_text(text)
        markers = [
            "flashback", "memory", "memorial", "funeral", "grave", "past", "history",
            "回忆", "追悼", "葬礼", "墓", "往事", "史料", "纪念",
        ]
        return any(marker in normalized for marker in markers)

    @staticmethod
    def _rule_maybe_contradict(existing_rule: str, incoming_rule: str) -> bool:
        a = MemoryManager._normalize_text(existing_rule)
        b = MemoryManager._normalize_text(incoming_rule)
        if not a or not b or a == b:
            return False
        a_tokens = MemoryManager._tokenize_text(a)
        b_tokens = MemoryManager._tokenize_text(b)
        if not a_tokens or not b_tokens:
            return False
        overlap = MemoryManager._weighted_overlap_score(a_tokens, b_tokens)
        if overlap < 0.12:
            return False

        if MemoryManager._has_antonym_pair(a, b):
            return True

        polarity_a = MemoryManager._polarity_score(a)
        polarity_b = MemoryManager._polarity_score(b)
        if polarity_a * polarity_b < 0 and overlap >= 0.18:
            return True

        return MemoryManager._is_negation_text(a) != MemoryManager._is_negation_text(b) and overlap >= 0.2

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
                old_value = self._get_nested(before_state, path)
                new_value = self._get_nested(
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
                        notes="Blocked automatic mutation of protected identity field.",
                    )
                    if path.startswith("core_traits."):
                        self._set_nested(merged_core_traits, path.replace("core_traits.", "", 1), old_value)
                    else:
                        self._set_nested(merged_attributes, path.replace("attributes.", "", 1), old_value)

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
                    notes="Blocked automatic status resurrection. Keep existing status until resolved.",
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
    ):
        """Adds or updates a relationship."""
        source = (source or "").strip()
        target = (target or "").strip()
        relation_type = (relation_type or "").strip()
        details = (details or "").strip()
        if not source or not target:
            return

        self.cursor.execute(
            "SELECT source_name, target_name, relation_type, details FROM relationships WHERE source_name = ? AND target_name = ?",
            (source, target),
        )
        before = self.cursor.fetchone()
        if before and (before[2] or "").strip() != relation_type and relation_type:
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
                notes="Blocked automatic relationship type overwrite.",
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
                notes="Relationship mutation references dead character(s). Review for timeline consistency.",
                blocking_level=self.NON_BLOCKING,
            )

        self.cursor.execute(
            '''INSERT INTO relationships (source_name, target_name, relation_type, details) 
               VALUES (?, ?, ?, ?)
               ON CONFLICT(source_name, target_name) 
               DO UPDATE SET relation_type=excluded.relation_type, details=excluded.details''',
            (source, target, relation_type, details)
        )
        self._maybe_commit()
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
        normalized_category = (category or "General").strip()
        normalized_content = (content or "").strip()
        normalized_intent = (intent_tag or "").strip()
        if not normalized_content:
            return -1
        self.cursor.execute(
            """SELECT id, category, rule_content, strictness
               FROM world_rules
               WHERE category = ? AND strictness = 1 AND is_deleted = 0""",
            (normalized_category,),
        )
        strict_rows = self.cursor.fetchall()
        for strict_row in strict_rows:
            existing_id, _, existing_content, _ = strict_row
            if self._rule_maybe_contradict(existing_content or "", normalized_content):
                self.queue_conflict(
                    entity_type="world_rule",
                    entity_key=f"{normalized_category}:{existing_id}",
                    conflict_type="strict_rule_contradiction",
                    incoming_obj={
                        "category": normalized_category,
                        "content": normalized_content,
                        "strictness": strictness,
                    },
                    existing_obj={
                        "id": existing_id,
                        "category": normalized_category,
                        "content": existing_content,
                        "strictness": 1,
                    },
                    source=source,
                    chapter_num=chapter_num,
                    notes="Blocked insertion due to potential contradiction with strict rule.",
                )
                return int(existing_id)

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
        normalized_event_name = (event_name or "Untitled Event").strip() or "Untitled Event"
        normalized_description = (description or "").strip()
        normalized_timestamp = (timestamp_str or "Unknown Time").strip() or "Unknown Time"
        normalized_location = (location or "Unknown").strip() or "Unknown"
        normalized_intent = (intent_tag or "").strip()
        normalized_related_entities = [str(x).strip() for x in (related_entities or []) if str(x).strip()]
        entities_json = json.dumps(normalized_related_entities, ensure_ascii=False, sort_keys=True)

        # Causality guard 1: dead characters should not silently participate in new events.
        dead_entities = []
        for entity in normalized_related_entities:
            char = self.get_character(entity)
            if char and char[3] == "dead":
                dead_entities.append(entity)
        if dead_entities:
            event_payload_text = f"{normalized_event_name}. {normalized_description}"
            active_participation = self._event_implies_active_participation(event_payload_text)
            historical_context = self._event_is_historical_or_memorial(event_payload_text)
            # Allow memorial/flashback style events that mention dead characters without implying revival.
            if historical_context and not active_participation:
                dead_entities = []
            else:
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
                    existing_obj={
                        "dead_entities": dead_entities,
                        "active_participation": active_participation,
                        "historical_context": historical_context,
                    },
                    source=source,
                    chapter_num=chapter_num,
                    notes=(
                        "Blocked event insertion because dead character appears as active participant."
                        if active_participation
                        else "Blocked event insertion because dead character appears in event without clear historical context."
                    ),
                    blocking_level=self.BLOCKING,
                )
                return -1

        # Causality guard 2: strict world rules contradiction check against event payload.
        self.cursor.execute(
            """SELECT id, category, rule_content
               FROM world_rules
               WHERE strictness = 1"""
        )
        strict_rules = self.cursor.fetchall()
        incoming_event_text = f"{normalized_event_name}. {normalized_description}".strip()
        for rid, rcat, rcontent in strict_rules:
            if self._rule_maybe_contradict(rcontent or "", incoming_event_text):
                self.queue_conflict(
                    entity_type="timeline_event",
                    entity_key=f"{normalized_event_name}@{normalized_timestamp}",
                    conflict_type="timeline_rule_contradiction",
                    incoming_obj={
                        "event_name": normalized_event_name,
                        "description": normalized_description,
                        "timestamp_str": normalized_timestamp,
                        "related_entities": normalized_related_entities,
                        "location": normalized_location,
                    },
                    existing_obj={
                        "rule_id": rid,
                        "category": rcat,
                        "rule_content": rcontent,
                    },
                    source=source,
                    chapter_num=chapter_num,
                    notes="Blocked event insertion due to potential contradiction with strict world rule.",
                )
                return -1

        self.cursor.execute(
            """SELECT id
               FROM timeline
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
               FROM timeline
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
                notes="Blocked insertion due to conflicting event payload for same event key.",
            )
            return int(existing_id)

        self.cursor.execute(
            '''INSERT INTO timeline 
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
                "SELECT * FROM timeline WHERE is_deleted = 0 AND related_entities LIKE ? ORDER BY id DESC LIMIT ?", 
                (search_pattern, limit)
            )
        else:
            self.cursor.execute("SELECT * FROM timeline WHERE is_deleted = 0 ORDER BY id DESC LIMIT ?", (limit,))
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

        embedding_np = np.array([embedding], dtype=np.float32)
        if embedding_np.ndim != 2:
            return

        actual_dim = embedding_np.shape[1]
        if actual_dim <= 0:
            return

        if self.index is None:
            self.embedding_dim = actual_dim
            self.index = faiss.IndexFlatL2(actual_dim)
        elif self.index.d != actual_dim:
            raise RuntimeError(
                f"Embedding dimension mismatch detected (new: {actual_dim}, existing: {self.index.d}). "
                "Please run --rebuild-vectors to safely migrate existing vector data."
            )

        normalized_content = (content or "").strip()
        if not normalized_content:
            return
        normalized_intent = (intent_tag or "").strip()
        normalized_metadata = metadata or {}
        metadata_json = json.dumps(normalized_metadata, ensure_ascii=False, sort_keys=True)
        self.cursor.execute(
            """SELECT faiss_id
               FROM vector_metadata
               WHERE content = ? AND metadata = ? AND is_deleted = 0
               ORDER BY faiss_id ASC
               LIMIT 1""",
            (normalized_content, metadata_json),
        )
        existing = self.cursor.fetchone()
        if existing:
            return

        self.index.add(embedding_np)
        
        # faiss_id corresponds to the sequential index
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
        self._maybe_commit()
        self._mark_faiss_dirty()

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

    def rebuild_vector_index_from_metadata(self, embedding_fn, include_deleted: bool = False) -> Dict[str, int]:
        """
        Rebuild FAISS index deterministically from vector_metadata content.
        Active rows are loaded in stable order by old faiss_id, then remapped to contiguous ids.
        """
        if self.index is None or faiss is None:
            return {"rebuilt": 0, "skipped": 0}
        where_clause = "" if include_deleted else "WHERE is_deleted = 0"
        self.cursor.execute(
            f"""SELECT faiss_id, content, metadata, source_commit_id, version, is_deleted, intent_tag
                FROM vector_metadata
                {where_clause}
                ORDER BY faiss_id ASC"""
        )
        rows = self.cursor.fetchall()
        if not rows:
            self.index = faiss.IndexFlatL2(self.embedding_dim)
            self.save_faiss()
            return {"rebuilt": 0, "skipped": 0}

        old_index_backup = None
        if faiss is not None and self.index is not None:
            try:
                old_index_backup = faiss.clone_index(self.index)
            except Exception:
                old_index_backup = None

        rebuilt_rows = []
        skipped_rows = []
        skipped = 0
        target_dim = None
        for old_faiss_id, content, metadata_json, source_commit_id, version, is_deleted, intent_tag in rows:
            emb = embedding_fn(content)
            if not emb:
                skipped += 1
                skipped_rows.append(
                    (old_faiss_id, content, metadata_json, source_commit_id, int(version or 1), int(is_deleted or 0), intent_tag or "")
                )
                continue
            if target_dim is None:
                target_dim = len(emb)
            if len(emb) != target_dim:
                skipped += 1
                skipped_rows.append(
                    (old_faiss_id, content, metadata_json, source_commit_id, int(version or 1), int(is_deleted or 0), intent_tag or "")
                )
                continue
            rebuilt_rows.append(
                (
                    old_faiss_id,
                    content,
                    metadata_json,
                    source_commit_id,
                    int(version or 1),
                    int(is_deleted or 0),
                    intent_tag or "",
                    emb,
                )
            )

        if target_dim is None:
            return {"rebuilt": 0, "skipped": skipped}

        new_index = faiss.IndexFlatL2(target_dim)
        
        started_batch = False
        if not self._in_batch:
            self.begin_batch()
            started_batch = True
            
        try:
            self.cursor.execute("DELETE FROM vector_metadata")
            for new_id, row in enumerate(rebuilt_rows):
                _, content, metadata_json, source_commit_id, version, is_deleted, intent_tag, emb = row
                emb_np = np.array([emb], dtype=np.float32)
                new_index.add(emb_np)
                self.cursor.execute(
                    """INSERT INTO vector_metadata
                       (faiss_id, content, metadata, source_commit_id, version, is_deleted, intent_tag)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (new_id, content, metadata_json, source_commit_id, version, is_deleted, intent_tag),
                )
            # Preserve skipped rows as soft-deleted metadata for audit/retry visibility.
            used_ids = {idx for idx, _ in enumerate(rebuilt_rows)}
            tombstone_id = -1
            for _, content, metadata_json, source_commit_id, version, is_deleted, intent_tag in skipped_rows:
                while tombstone_id in used_ids:
                    tombstone_id -= 1
                self.cursor.execute(
                    """INSERT INTO vector_metadata
                       (faiss_id, content, metadata, source_commit_id, version, is_deleted, intent_tag)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (tombstone_id, content, metadata_json, source_commit_id, version, 1, intent_tag),
                )
                used_ids.add(tombstone_id)
                tombstone_id -= 1
                
            if started_batch:
                self.end_batch(success=True)
                
            self.index = new_index
            self.embedding_dim = target_dim
            if not self._in_batch:
                self.save_faiss()
            else:
                self._mark_faiss_dirty()
            return {"rebuilt": len(rebuilt_rows), "skipped": skipped}
        except Exception:
            if started_batch:
                self.end_batch(success=False)
            if old_index_backup is not None:
                self.index = old_index_backup
            raise

    def close(self):
        if self.conn:
            self.conn.close()
