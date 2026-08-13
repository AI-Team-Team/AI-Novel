import os
import json
import time
import logging
from typing import Dict, Optional, List

import config
from workflow_components.parsing import extract_att_member_answer
from workflow_components.resources import get_ai_resource, get_message


class ConflictResolverWorkflowMixin:
    """Mixin implementing the ATT-driven Conflict Resolution Committee."""

    def ai_debate_resolve_conflict(self, conflict_id: int) -> bool:
        """
        Spawns the dynamic 3-AI Conflict Resolution Committee AT
        to resolve the given conflict in a bounded debate loop.
        """
        row = self.memory.get_conflict_by_id(conflict_id)
        if not row:
            self.logger.error(get_message("runtime.conflict_not_found", conflict_id=conflict_id))
            return False
        
        status = row[8]
        if status != "PENDING":
            self.logger.warning(get_message("runtime.conflict_not_pending", conflict_id=conflict_id, status=status))
            return False

        entity_type = row[1]
        entity_key = row[2]
        conflict_type = row[3]
        incoming_json_str = row[4] or "{}"
        existing_json_str = row[5] or "{}"
        source = row[6]
        chapter_num = row[7]
        blocking_level = row[12] if len(row) > 12 else "BLOCKING"

        # Determine rounds
        rounds = getattr(config, "CONFLICT_DISCUSSION_ROUNDS", 2)
        if rounds < 1:
            rounds = 1

        self.logger.info(get_message(
            "runtime.conflict_detected",
            chapter_num=chapter_num,
            conflict_type=conflict_type,
            entity_type=entity_type,
            entity_key=entity_key,
        ))
        self.logger.info(get_message("runtime.conflict_spawn"))

        # 1. Deep Context Window Construction
        context_markdown = self._assemble_deep_context(
            conflict_id=conflict_id,
            entity_type=entity_type,
            entity_key=entity_key,
            conflict_type=conflict_type,
            incoming_json_str=incoming_json_str,
            existing_json_str=existing_json_str,
            source=source,
            chapter_num=chapter_num,
            blocking_level=blocking_level
        )

        # 2. Dynamic AT Spawning via ATT
        team = self._create_att_team("conflict_resolution", chapter_num)

        prompt = get_ai_resource("prompt.att.conflict", context=context_markdown)

        # 3. Bounded Debate Loop
        try:
            transcript_text = self._execute_att_discussion(team, prompt, rounds)
            planner_answer = extract_att_member_answer(
                transcript_text, team, "Consensus_Planner"
            )
            planner_decision = self._extract_json(planner_answer or "")
        except Exception as e:
            self.logger.error(get_message("runtime.conflict_debate_failed", error=e))
            return False

        # 4. Consensus Gating & Mutative Commit
        if not planner_decision or "action" not in planner_decision:
            self.logger.error(get_message("runtime.conflict_decision_unparseable"))
            self._write_discussion_log(conflict_id, context_markdown, [transcript_text], "STANDOFF", None)
            return False

        action = str(planner_decision.get("action")).strip().lower()
        reasoning = planner_decision.get("reasoning", get_ai_resource("runtime.committee_reason_missing"))
        compromise = planner_decision.get("narrative_compromise", "")

        if action not in {"keep_existing", "apply_incoming"}:
            self.logger.error(get_message("runtime.conflict_action_invalid", action=action))
            self._write_discussion_log(conflict_id, context_markdown, [transcript_text], "STANDOFF", planner_decision)
            return False

        # Consensus agreed! Apply the transaction atomically
        resolver_note = get_ai_resource(
            "runtime.conflict_resolution_note",
            action=action,
            reasoning=reasoning,
            compromise=compromise,
        )
        self.logger.info(get_message("runtime.conflict_commit", action=action))

        ok = self.memory.resolve_conflict(
            conflict_id=conflict_id,
            action=action,
            resolver_note=resolver_note,
            source="ai_debate"
        )

        if ok:
            self._write_discussion_log(conflict_id, context_markdown, [transcript_text], "RESOLVED", planner_decision)
            return True
        else:
            self.logger.error(get_message(
                "runtime.conflict_transaction_failed",
                action=action,
                conflict_id=conflict_id,
            ))
            self._write_discussion_log(conflict_id, context_markdown, [transcript_text], "TRANSACTION_FAILED", planner_decision)
            return False

    def _assemble_deep_context(
        self,
        conflict_id: int,
        entity_type: str,
        entity_key: str,
        conflict_type: str,
        incoming_json_str: str,
        existing_json_str: str,
        source: str,
        chapter_num: int,
        blocking_level: str
    ) -> str:
        # A. Preceding chapter prose
        preceding_prose = get_ai_resource("context.no_preceding_chapter")
        if chapter_num > 1:
            preceding_path = self.get_chapter_path(chapter_num - 1)
            if os.path.exists(preceding_path):
                with open(preceding_path, "r", encoding="utf-8") as f:
                    preceding_prose = f.read().strip()

        # B. Conflict chapter prose
        conflict_prose = get_ai_resource("context.no_conflict_prose")
        conflict_path = self.get_chapter_path(chapter_num)
        if os.path.exists(conflict_path):
            with open(conflict_path, "r", encoding="utf-8") as f:
                conflict_prose = f.read().strip()

        # C. Succeeding chapter prose
        succeeding_prose = get_ai_resource("context.no_succeeding_chapter")
        succeeding_path = self.get_chapter_path(chapter_num + 1)
        if os.path.exists(succeeding_path):
            with open(succeeding_path, "r", encoding="utf-8") as f:
                succeeding_prose = f.read().strip()

        # D. Character Profiles
        character_profile = get_ai_resource("context.not_applicable")
        if entity_type == "character":
            profile_row = self.memory.get_character(entity_key)
            if profile_row:
                character_profile = get_ai_resource(
                    "context.character_profile",
                    name=profile_row[1],
                    traits=profile_row[2],
                    status=profile_row[3],
                    attributes=profile_row[4],
                )
            else:
                character_profile = get_ai_resource("context.character_missing", name=entity_key)

        # E. All Characters overview
        chars_overview_list = []
        all_chars = self.memory.get_all_characters()
        for char in all_chars:
            chars_overview_list.append(
                get_ai_resource("context.character_item", name=char[0], traits=char[1], status=char[2])
            )
        characters_overview = "\n".join(chars_overview_list) if chars_overview_list else get_ai_resource("context.no_characters")

        # F. World Rules
        rules_list = []
        self.memory.cursor.execute("SELECT category, rule_content, strictness FROM world_rules WHERE is_deleted = 0")
        rules = self.memory.cursor.fetchall()
        for rule in rules:
            rules_list.append(
                get_ai_resource("context.rule_item", category=rule[0], rule=rule[1], strictness=rule[2])
            )
        world_rules = "\n".join(rules_list) if rules_list else get_ai_resource("context.no_rules")

        # G. Timeline
        events_list = []
        events = self.memory.get_events(limit=10)
        for ev in events:
            events_list.append(
                get_ai_resource(
                    "context.event_item",
                    event=ev[1],
                    description=ev[2],
                    time=ev[3],
                    impact=ev[4],
                    entities=ev[5],
                    location=ev[6],
                )
            )
        timeline_events = "\n".join(events_list) if events_list else get_ai_resource("context.no_events")

        return get_ai_resource(
            "prompt.conflict_context",
            conflict_id=conflict_id,
            entity_type=entity_type,
            entity_key=entity_key,
            conflict_type=conflict_type,
            source=source,
            chapter_num=chapter_num,
            blocking_level=blocking_level,
            incoming_json=incoming_json_str,
            existing_json=existing_json_str,
            preceding_chapter=chapter_num - 1,
            preceding_prose=preceding_prose,
            conflict_prose=conflict_prose,
            succeeding_chapter=chapter_num + 1,
            succeeding_prose=succeeding_prose,
            character_profile=character_profile,
            characters_overview=characters_overview,
            world_rules=world_rules,
            timeline_events=timeline_events,
        )

    def _write_discussion_log(
        self,
        conflict_id: int,
        context: str,
        transcript: List[str],
        status: str,
        decision: Optional[Dict]
    ):
        discussions_dir = os.path.join(self.process_dir, "discussions")
        os.makedirs(discussions_dir, exist_ok=True)
        log_path = os.path.join(discussions_dir, f"conflict_{conflict_id}_resolution_discussion.md")

        title = get_message("log.conflict_title", conflict_id=conflict_id)
        meta = get_message(
            "log.conflict_meta",
            status=status,
            timestamp=time.strftime('%Y-%m-%d %H:%M:%S'),
        )
        if decision:
            meta += get_message(
                "log.conflict_decision",
                action=decision.get('action'),
                reasoning=decision.get('reasoning'),
                compromise=decision.get('narrative_compromise'),
            )

        transcript_body = "\n".join(transcript)

        full_doc = get_message(
            "log.conflict_document",
            title=title,
            metadata=meta,
            transcript=transcript_body,
            context=context,
        )

        with open(log_path, "w", encoding="utf-8") as f:
            f.write(full_doc)

        self.logger.info(get_message("runtime.conflict_log_saved", path=log_path))
