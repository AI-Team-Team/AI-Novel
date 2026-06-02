import os
import json
import time
import logging
from typing import Dict, Optional, List

import config
from att.presets import get_preset

class ConflictResolverWorkflowMixin:
    """Mixin implementing the ATT-driven Conflict Resolution Committee."""

    def ai_debate_resolve_conflict(self, conflict_id: int) -> bool:
        """
        Spawns the dynamic 3-AI Conflict Resolution Committee AT
        to resolve the given conflict in a bounded debate loop.
        """
        row = self.memory.get_conflict_by_id(conflict_id)
        if not row:
            self.logger.error(f"Conflict #{conflict_id} not found in database.")
            return False
        
        status = row[8]
        if status != "PENDING":
            self.logger.warning(f"Conflict #{conflict_id} is already in status '{status}'. Skipping.")
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

        self.logger.info(f"[AUTO] Conflict detected in Ch {chapter_num} scan: {conflict_type} ({entity_type} {entity_key}).")
        self.logger.info("[AUTO] Spawning ATT Conflict Resolution Committee...")

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
        preset = get_preset("conflict_resolution")
        team = self.att_manager.create_agent_team(
            creator=self.att_manager.root_ai,
            member_count=3,
            roles_and_presets=preset["roles"],
            preset_name="conflict_resolution",
            system_instructions=preset["system_instructions"]
        )
        team.chapter_num = chapter_num

        prompt = (
            f"Please resolve the narrative conflict detailed below:\n\n"
            f"{context_markdown}\n\n"
            f"Discuss the best approach. Historian_Critic should present continuity points, "
            f"Prose_Scanner should present creative pros, and Consensus_Planner must arbitrate "
            f"and make the final decision in JSON format choosing exactly 'keep_existing' or 'apply_incoming'."
        )

        # 3. Bounded Debate Loop
        try:
            transcript_text = self.att_manager.execute_team_discussion(team, prompt, rounds=rounds)
            planner_decision = self._extract_json(transcript_text)
        except Exception as e:
            self.logger.error(f"[AUTO] ATT debate failed: {e}")
            return False

        # 4. Consensus Gating & Mutative Commit
        if not planner_decision or "action" not in planner_decision:
            self.logger.error("[AUTO] Committee failed to output a parseable JSON decision block.")
            self._write_discussion_log(conflict_id, context_markdown, [transcript_text], "STANDOFF", None)
            return False

        action = str(planner_decision.get("action")).strip().lower()
        reasoning = planner_decision.get("reasoning", "No detailed reasoning provided by Committee.")
        compromise = planner_decision.get("narrative_compromise", "")

        if action not in {"keep_existing", "apply_incoming"}:
            self.logger.error(f"[AUTO] Committee output invalid consensus action: '{action}'. Must be keep_existing or apply_incoming.")
            self._write_discussion_log(conflict_id, context_markdown, [transcript_text], "STANDOFF", planner_decision)
            return False

        # Consensus agreed! Apply the transaction atomically
        resolver_note = (
            f"resolved via ATT Conflict Resolution Committee.\n"
            f"Committee Choice: {action}\n"
            f"Reasoning: {reasoning}\n"
            f"Narrative Compromise: {compromise}"
        )
        self.logger.info(f"[AUTO] Resolution agreed: {action}. Committing mutations atomically...")

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
            self.logger.error(f"[AUTO] Database transaction failed while applying action '{action}' for conflict #{conflict_id}.")
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
        preceding_prose = "*No preceding chapter exists.*"
        if chapter_num > 1:
            preceding_path = self.get_chapter_path(chapter_num - 1)
            if os.path.exists(preceding_path):
                with open(preceding_path, "r", encoding="utf-8") as f:
                    preceding_prose = f.read().strip()

        # B. Conflict chapter prose
        conflict_prose = "*Conflict chapter prose file is empty or not yet written.*"
        conflict_path = self.get_chapter_path(chapter_num)
        if os.path.exists(conflict_path):
            with open(conflict_path, "r", encoding="utf-8") as f:
                conflict_prose = f.read().strip()

        # C. Succeeding chapter prose
        succeeding_prose = "*No succeeding chapter is available at this stage.*"
        succeeding_path = self.get_chapter_path(chapter_num + 1)
        if os.path.exists(succeeding_path):
            with open(succeeding_path, "r", encoding="utf-8") as f:
                succeeding_prose = f.read().strip()

        # D. Character Profiles
        character_profile = "*N/A*"
        if entity_type == "character":
            profile_row = self.memory.get_character(entity_key)
            if profile_row:
                character_profile = (
                    f"Name: {profile_row[1]}\n"
                    f"Core Traits: {profile_row[2]}\n"
                    f"Status: {profile_row[3]}\n"
                    f"Attributes: {profile_row[4]}"
                )
            else:
                character_profile = f"Character '{entity_key}' has no record in the database."

        # E. All Characters overview
        chars_overview_list = []
        all_chars = self.memory.get_all_characters()
        for char in all_chars:
            chars_overview_list.append(f"- Name: {char[0]} | Core Traits: {char[1]} | Status: {char[2]}")
        characters_overview = "\n".join(chars_overview_list) if chars_overview_list else "*No characters in database.*"

        # F. World Rules
        rules_list = []
        self.memory.cursor.execute("SELECT category, rule_content, strictness FROM world_rules WHERE is_deleted = 0")
        rules = self.memory.cursor.fetchall()
        for rule in rules:
            rules_list.append(f"- Category: {rule[0]} | Rule: {rule[1]} | Strictness: {rule[2]}")
        world_rules = "\n".join(rules_list) if rules_list else "*No global rules in database.*"

        # G. Timeline
        events_list = []
        events = self.memory.get_events(limit=10)
        for ev in events:
            events_list.append(
                f"- Event: {ev[1]} | Description: {ev[2]} | Time: {ev[3]} | "
                f"Impact: {ev[4]} | Entities: {ev[5]} | Location: {ev[6]}"
            )
        timeline_events = "\n".join(events_list) if events_list else "*No timeline events recorded yet.*"

        return (
            f"# CONFLICT CONTEXT PACKAGE\n\n"
            f"## 1. Conflict Details\n"
            f"- **Conflict ID**: {conflict_id}\n"
            f"- **Entity Type**: {entity_type}\n"
            f"- **Entity Key**: {entity_key}\n"
            f"- **Conflict Type**: {conflict_type}\n"
            f"- **Source**: {source}\n"
            f"- **Chapter**: {chapter_num}\n"
            f"- **Blocking Level**: {blocking_level}\n\n"
            f"### Incoming Scanned Fact:\n"
            f"```json\n"
            f"{incoming_json_str}\n"
            f"```\n\n"
            f"### Existing Database Fact:\n"
            f"```json\n"
            f"{existing_json_str}\n"
            f"```\n\n"
            f"## 2. Multi-Chapter Prose Window\n\n"
            f"### Preceding Chapter (Chapter {chapter_num - 1} Prose):\n"
            f"{preceding_prose}\n\n"
            f"### Conflict Chapter (Chapter {chapter_num} Prose):\n"
            f"{conflict_prose}\n\n"
            f"### Succeeding Chapter (Chapter {chapter_num + 1} Prose):\n"
            f"{succeeding_prose}\n\n"
            f"## 3. Structured Database Context\n\n"
            f"### Entity Character Profile:\n"
            f"{character_profile}\n\n"
            f"### All Active Characters:\n"
            f"{characters_overview}\n\n"
            f"### Global World Bible Rules:\n"
            f"{world_rules}\n\n"
            f"### Last 10 Timeline Events:\n"
            f"{timeline_events}"
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

        title = f"# Multi-Agent Conflict Resolution Debate - Conflict #{conflict_id}"
        meta = (
            f"**Status**: {status}\n"
            f"**Timestamp**: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        )
        if decision:
            meta += (
                f"**Consensus Action**: {decision.get('action')}\n\n"
                f"### Planner Reasoning:\n"
                f"{decision.get('reasoning')}\n\n"
                f"### Narrative Compromise:\n"
                f"{decision.get('narrative_compromise')}\n"
            )

        transcript_body = "\n".join(transcript)

        full_doc = (
            f"{title}\n\n"
            f"## Metadata\n"
            f"{meta}\n"
            f"## Debate Transcript\n\n"
            f"{transcript_body}\n\n"
            f"## Context Details\n\n"
            f"{context}\n"
        )

        with open(log_path, "w", encoding="utf-8") as f:
            f.write(full_doc)

        self.logger.info(f"[AUTO] Discussion transcript saved to: {log_path}")
