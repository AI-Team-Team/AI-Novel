import json
import logging
from typing import Any, Dict, List, Optional

from memory import MemoryManager

from workflow_components.resources import get_resource

class StoryStateManager:
    """
    DB-facing state manager for write/scan workflows.
    Keeps database logic out of workflow orchestration code.
    """

    def __init__(
        self,
        memory: MemoryManager,
        embedding_client: Any,
        tier_3_search_limit: int = 5,
    ):
        self.memory = memory
        self.embedding_client = embedding_client
        self.tier_3_search_limit = tier_3_search_limit
        self.logger = logging.getLogger("StoryStateManager")

    @staticmethod
    def _contains_token(text: str, token: str) -> bool:
        return token.lower() in (text or "").lower()

    @staticmethod
    def _format_semantic_lines(
        intent: Dict[str, object],
        hits: List[Dict],
    ) -> str:
        lines = [get_resource("ui.semantic_header")]
        lines.append(
            get_resource("ui.semantic_retrieval_intent", mode=intent['mode'], rationale=intent['rationale'])
        )
        focus_entities = intent.get("focus_entities", []) or []
        focus_locations = intent.get("focus_locations", []) or []
        if focus_entities:
            lines.append(
                get_resource("ui.semantic_focus_entities", entities=', '.join(focus_entities))
            )
        if focus_locations:
            lines.append(
                get_resource("ui.semantic_focus_locations", locations=', '.join(focus_locations))
            )
        for hit in hits:
            meta = hit.get("metadata") or {}
            location = meta.get("location", "-")
            detail_type = meta.get("type", "-")
            content = hit.get("content", "")
            lines.append(
                get_resource("ui.semantic_item", type=detail_type, content=content, location=location)
            )
        return "\n".join(lines) + "\n"

    @staticmethod
    def extract_focus_from_state(db_chars: List[tuple], db_events: List[tuple]) -> Dict[str, List[str]]:
        entities = set()
        locations = set()
        for name, _, status in db_chars:
            if status != "dead":
                entities.add(name)
        for event in db_events:
            related = event[5]
            location = event[6]
            if location and location != "Unknown":
                locations.add(location)
            if related:
                try:
                    for e in json.loads(related):
                        if isinstance(e, str) and e.strip():
                            entities.add(e.strip())
                except Exception:
                    pass
        return {
            "entities": sorted(entities),
            "locations": sorted(locations),
        }

    def build_planner_retrieval_intent(
        self,
        chapter_num: int,
        previous_summary: Optional[str],
        db_chars: List[tuple],
        db_events: List[tuple],
        pending_conflicts: List[tuple],
    ) -> Dict[str, object]:
        return self.classify_query_intent(
            task_type="planner",
            chapter_num=chapter_num,
            previous_summary=previous_summary,
            db_chars=db_chars,
            db_events=db_events,
            pending_conflicts=pending_conflicts,
            user_request="chapter guide generation",
        )

    def classify_query_intent(
        self,
        task_type: str,
        chapter_num: int,
        previous_summary: Optional[str],
        db_chars: List[tuple],
        db_events: List[tuple],
        pending_conflicts: List[tuple],
        user_request: str = "",
    ) -> Dict[str, object]:
        focus = self.extract_focus_from_state(db_chars, db_events)
        has_state = bool(db_chars or db_events or previous_summary)
        has_conflict = bool(pending_conflicts)
        requires_high_recall = task_type in {"writer", "scanner", "review"}
        should_semantic = has_state and (
            chapter_num > 1 or has_conflict or len(db_events) > 0 or bool(previous_summary)
        )
        if requires_high_recall and has_state:
            should_semantic = True
        mode = "continuity_guard" if has_conflict else "continuity_build"
        strict_mode = has_conflict or task_type in {"scanner", "review"}
        
        rationale_key = "prompt.intent_rationale_conflict" if has_conflict else "prompt.intent_rationale_continuity"
        rationale = get_resource(rationale_key)

        return {
            "task_type": task_type,
            "mode": mode,
            "strict_mode": strict_mode,
            "required_tiers": [1, 2, 3] if should_semantic else [1, 2],
            "should_semantic": should_semantic,
            "focus_entities": focus["entities"][:8],
            "focus_locations": focus["locations"][:5],
            "rationale": rationale,
            "user_request": user_request,
        }

    def get_state_snapshot(self, recent_events_limit: int = 5, conflicts_limit: int = 10) -> Dict[str, List[tuple]]:
        return {
            "characters": self.memory.get_all_characters(),
            "rules": self.memory.get_rules_by_category(),
            "events": self.memory.get_events(limit=recent_events_limit),
            "conflicts": self.memory.get_pending_conflicts(limit=conflicts_limit),
        }

    def sqlite_prefilter_for_intent(
        self,
        intent: Dict[str, object],
        recent_events_limit: int = 5,
        conflicts_limit: int = 10,
    ) -> Dict[str, List[tuple]]:
        snapshot = self.get_state_snapshot(recent_events_limit=recent_events_limit, conflicts_limit=conflicts_limit)
        if intent.get("strict_mode"):
            return snapshot
        focus_entities = intent.get("focus_entities", []) or []
        if not focus_entities:
            return snapshot
        focused_events: List[tuple] = []
        for event in snapshot["events"]:
            related_raw = event[5]
            related_entities: List[str] = []
            if related_raw:
                try:
                    related_entities = json.loads(related_raw)
                except Exception:
                    related_entities = []
            if any(entity in focus_entities for entity in related_entities):
                focused_events.append(event)
        if focused_events:
            snapshot["events"] = focused_events[:recent_events_limit]
        return snapshot

    @staticmethod
    def rerank_semantic_hits(
        hits: List[Dict],
        focus_entities: List[str],
        focus_locations: List[str],
        target_chapter: Optional[int] = None,
    ) -> List[Dict]:
        ranked = []
        entity_tokens = [x.lower() for x in focus_entities]
        location_tokens = [x.lower() for x in focus_locations]
        for hit in hits:
            meta = hit.get("metadata") or {}
            content = (hit.get("content") or "").lower()
            location = str(meta.get("location", "")).lower()
            base = -float(hit.get("score", 0.0))
            entity_bonus = 0.0
            location_bonus = 0.0
            for token in entity_tokens:
                if token and token in content:
                    entity_bonus += 0.35
            for token in location_tokens:
                if token and (token == location or token in content):
                    location_bonus += 0.5
            
            # Temporal proximity boost for active chapters (chapter > 0)
            time_bonus = 0.0
            if target_chapter is not None:
                detail_chap = meta.get("chapter")
                if isinstance(detail_chap, (int, float)) and detail_chap > 0:
                    time_bonus = 0.40 / (1.0 + abs(detail_chap - target_chapter))
                    
            rank_score = base + entity_bonus + location_bonus + time_bonus
            ranked.append((rank_score, hit))
        ranked.sort(key=lambda x: x[0], reverse=True)
        return [x[1] for x in ranked]

    def semantic_retrieve_for_intent(
        self,
        intent: Dict[str, object],
        chapter_num: int,
        previous_summary: Optional[str],
        db_events: List[tuple],
    ) -> List[Dict]:
        if not intent.get("should_semantic"):
            return []
        event_fragments = [f"{e[1]} {e[2]}" for e in db_events[:3]]
        focus_entities = intent.get("focus_entities", []) or []
        focus_locations = intent.get("focus_locations", []) or []
        queries = [
            "\n".join(x for x in [f"Chapter {chapter_num}", previous_summary or "", " ".join(event_fragments)] if x),
            f"Entities: {' '.join(focus_entities)}",
            f"Locations: {' '.join(focus_locations)}",
        ]
        merged_hits: List[Dict] = []
        seen = set()
        for q in queries:
            q = q.strip()
            if not q:
                continue
            query_embedding = self.embedding_client.get_embedding(q)
            if not query_embedding:
                continue
            meta_filter = {"location": focus_locations[0]} if focus_locations else None
            hits = self.memory.search_semantic(
                query_embedding,
                k=max(self.tier_3_search_limit, 3),
                filter_metadata=meta_filter,
            )
            for hit in hits:
                key = (hit.get("content"), json.dumps(hit.get("metadata", {}), sort_keys=True, ensure_ascii=False))
                if key in seen:
                    continue
                seen.add(key)
                merged_hits.append(hit)
        return merged_hits

    def cross_tier_align_semantic_hits(
        self,
        hits: List[Dict],
        db_chars: List[tuple],
        strict_mode: bool,
        current_chapter_num: Optional[int] = None,
    ) -> List[Dict]:
        """Filter semantic hits that are from the future or mention dead characters in strict mode."""
        if not hits:
            return []

        # 1. Spatiotemporal Future Gate: filter out any future chapter leaks
        gated_hits = []
        for hit in hits:
            meta = hit.get("metadata") or {}
            chapter_val = meta.get("chapter")
            if current_chapter_num is not None and isinstance(chapter_val, (int, float)) and chapter_val > current_chapter_num:
                continue
            gated_hits.append(hit)

        if not gated_hits:
            return []

        # 2. Dead characters pre-alignment (strict mode check)
        dead_entities = {name for name, _, status in db_chars if status == "dead"}
        if not dead_entities or not strict_mode:
            return gated_hits

        aligned: List[Dict] = []
        for hit in gated_hits:
            content = hit.get("content") or ""
            lowered = content.lower()
            blocked = False
            for dead_name in dead_entities:
                if self._contains_token(lowered, dead_name.lower()):
                    blocked = True
                    break
            if not blocked:
                aligned.append(hit)
        return aligned

    def build_context_package(
        self,
        task_type: str,
        chapter_num: int,
        previous_summary: Optional[str],
        recent_events_limit: int = 5,
        conflicts_limit: int = 10,
        user_request: str = "",
    ) -> Dict[str, object]:
        base_snapshot = self.get_state_snapshot(
            recent_events_limit=recent_events_limit,
            conflicts_limit=conflicts_limit,
        )
        intent = self.classify_query_intent(
            task_type=task_type,
            chapter_num=chapter_num,
            previous_summary=previous_summary,
            db_chars=base_snapshot["characters"],
            db_events=base_snapshot["events"],
            pending_conflicts=base_snapshot["conflicts"],
            user_request=user_request,
        )
        snapshot = self.sqlite_prefilter_for_intent(
            intent=intent,
            recent_events_limit=recent_events_limit,
            conflicts_limit=conflicts_limit,
        )
        raw_hits = self.semantic_retrieve_for_intent(
            intent=intent,
            chapter_num=chapter_num,
            previous_summary=previous_summary,
            db_events=snapshot["events"],
        )
        aligned_hits = self.cross_tier_align_semantic_hits(
            hits=raw_hits,
            db_chars=snapshot["characters"],
            strict_mode=bool(intent.get("strict_mode")),
            current_chapter_num=chapter_num,
        )
        ranked_hits = self.rerank_semantic_hits(
            aligned_hits,
            intent.get("focus_entities", []) or [],
            intent.get("focus_locations", []) or [],
            target_chapter=chapter_num,
        )
        top_hits = ranked_hits[: self.tier_3_search_limit]
        semantic_summary = get_resource("ui.semantic_skipped")
        if intent.get("should_semantic"):
            semantic_summary = get_resource("ui.semantic_no_hits")
            if top_hits:
                semantic_summary = self._format_semantic_lines(intent, top_hits)
        return {
            "intent": intent,
            "characters": snapshot["characters"],
            "rules": snapshot["rules"],
            "events": snapshot["events"],
            "conflicts": snapshot["conflicts"],
            "semantic_hits": top_hits,
            "semantic_summary": semantic_summary,
        }

    def semantic_context_for_planner(
        self,
        chapter_num: int,
        previous_summary: Optional[str],
        db_chars: List[tuple],
        db_events: List[tuple],
        pending_conflicts: List[tuple],
    ) -> str:
        intent = self.classify_query_intent(
            task_type="planner",
            chapter_num=chapter_num,
            previous_summary=previous_summary,
            db_chars=db_chars,
            db_events=db_events,
            pending_conflicts=pending_conflicts,
            user_request="planner semantic context",
        )
        raw_hits = self.semantic_retrieve_for_intent(
            intent=intent,
            chapter_num=chapter_num,
            previous_summary=previous_summary,
            db_events=db_events,
        )
        aligned_hits = self.cross_tier_align_semantic_hits(
            hits=raw_hits,
            db_chars=db_chars,
            strict_mode=bool(intent.get("strict_mode")),
            current_chapter_num=chapter_num,
        )
        if not intent["should_semantic"]:
            return get_resource("ui.semantic_skipped")
        if not aligned_hits:
            return get_resource("ui.semantic_no_hits")
        ranked = self.rerank_semantic_hits(
            aligned_hits,
            intent.get("focus_entities", []) or [],
            intent.get("focus_locations", []) or [],
            target_chapter=chapter_num,
        )
        return self._format_semantic_lines(intent, ranked[: self.tier_3_search_limit])

    def apply_fact_payload(
        self,
        data: Dict,
        summary_lines: Optional[List[str]] = None,
        source: str = "unknown",
        chapter_num: Optional[int] = None,
        source_commit_id: Optional[str] = None,
        intent_tag: str = "",
    ) -> int:
        conflicts_before = self.memory.get_pending_conflict_count()
        for char in data.get("new_characters", []):
            self.memory.upsert_character(
                name=char.get("name"),
                core_traits=char.get("core_traits") or {},
                attributes=char.get("attributes") or {},
                status=char.get("status"),
                source=source,
                chapter_num=chapter_num,
            )
            if summary_lines is not None:
                summary_lines.append(get_resource("label.new_character") + f": {char.get('name')}")

        for char in data.get("updated_characters", []):
            self.memory.upsert_character(
                name=char.get("name"),
                core_traits=char.get("core_traits"),
                attributes=char.get("attributes"),
                status=char.get("status"),
                source=source,
                chapter_num=chapter_num,
            )
            if summary_lines is not None:
                summary_lines.append(get_resource("label.updated_character") + f": {char.get('name')}")

        for rule in data.get("new_rules", []):
            self.memory.add_rule(
                rule.get("category", "General"),
                rule.get("content", ""),
                rule.get("strictness", 1),
                source=source,
                chapter_num=chapter_num,
                source_commit_id=source_commit_id,
                intent_tag=intent_tag,
            )
            if summary_lines is not None:
                summary_lines.append(get_resource("label.new_rule") + f": {rule.get('content')}")

        for rel in data.get("relationships", []):
            self.memory.add_relationship(
                rel.get("source"),
                rel.get("target"),
                rel.get("relation_type"),
                rel.get("details"),
                source_tag=source,
                chapter_num=chapter_num,
            )
            if summary_lines is not None:
                summary_lines.append(
                    get_resource("label.relationship") + f": {rel.get('source')} <-> {rel.get('target')}"
                )

        for ev in data.get("events", []):
            self.memory.add_event(
                event_name=ev.get("event_name", "Untitled Event"),
                description=ev.get("description", ""),
                timestamp_str=ev.get("timestamp_str", "Unknown Time"),
                impact_level=ev.get("impact_level", 1),
                related_entities=ev.get("related_entities", []),
                location=ev.get("location", "Unknown"),
                source=source,
                chapter_num=chapter_num,
                source_commit_id=source_commit_id,
                intent_tag=intent_tag,
            )
            if summary_lines is not None:
                summary_lines.append(get_resource("label.event") + f": {ev.get('event_name')}")

        for det in data.get("details", []):
            content = det.get("content")
            if content:
                embedding = self.embedding_client.get_embedding(content)
                if embedding:
                    self.memory.add_semantic_fact(
                        content,
                        embedding,
                        det.get("metadata", {}),
                        source=source,
                        chapter_num=chapter_num,
                        source_commit_id=source_commit_id,
                        intent_tag=intent_tag,
                    )

        conflicts_after = self.memory.get_pending_conflict_count()
        new_conflicts = max(0, conflicts_after - conflicts_before)
        if summary_lines is not None and new_conflicts > 0:
            summary_lines.append(get_resource("label.conflicts_pending", count=new_conflicts))
        return new_conflicts

    def sync_compact_archives(self) -> Dict[str, str]:
        chars = self.memory.get_all_characters()
        char_lines = [get_resource("archive.char_header"), ""]
        if not chars:
            char_lines.append(get_resource("archive.char_no_records"))
        else:
            for name, _, status in chars:
                char_lines.append("- " + name + get_resource("ui.status_label", status=status))

        rules = self.memory.get_rules_by_category()
        rule_lines = [get_resource("archive.rule_header"), ""]
        if not rules:
            rule_lines.append(get_resource("archive.rule_no_records"))
        else:
            for category, content, strictness in rules:
                rule_lines.append(
                    get_resource("ui.rule_item_no_newline", category=category, content=content, strictness=strictness)
                )
        return {
            "characters_compact.md": "\n".join(char_lines),
            "world_rules_compact.md": "\n".join(rule_lines),
        }

    def auto_resolve_pending_conflicts(self) -> int:
        """
        Strict mode: never auto-apply incoming hard contradiction.
        Keep existing hard facts and resolve all pending conflicts with keep_existing policy.
        """
        rows = self.memory.get_pending_conflicts(limit=500, blocking_only=True)
        resolved = 0
        if rows:
            self.logger.warning("================================================================================")
            self.logger.warning("WARNING: Auto-resolving pending BLOCKING conflicts using 'keep_existing' policy!")
            self.logger.warning("This bypasses strict human triage. To enforce manual approval and block downstream tasks,")
            self.logger.warning("change 'blocking_conflict_mode' in 'config.yaml' to 'manual_block'.")
            self.logger.warning("--------------------------------------------------------------------------------")
            for row in rows:
                conflict_id = row[0]
                entity_type = row[1]
                entity_key = row[2]
                conflict_type = row[3]
                chapter_num = row[5]
                self.logger.warning(
                    f"-> Auto-resolved BLOCKING Conflict #{conflict_id}: type='{conflict_type}', "
                    f"entity='{entity_type}:{entity_key}', chapter={chapter_num} (action=keep_existing)"
                )
                ok = self.memory.resolve_conflict(
                    conflict_id,
                    action="keep_existing",
                    resolver_note=f"auto-strict keep_existing for {conflict_type}",
                    source="auto_resolver",
                )
                if ok:
                    resolved += 1
            self.logger.warning("================================================================================")
        return resolved
