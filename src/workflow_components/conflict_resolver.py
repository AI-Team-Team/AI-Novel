import os
import json
import time
import logging
from typing import Dict, Optional, List

import config

CRITIC_SYSTEM_INSTRUCTION = (
    "You are the Critic (Historian) on a Multi-Agent Narrative Consensus Panel.\n"
    "Your primary objective is to defend historical continuity, spatiotemporal logic, database integrity, and existing established facts in the world.\n"
    "You are highly skeptical of new changes that contradict established facts (such as a character who died suddenly reviving without explanation, or relationships shifting overnight without foundation).\n"
    "You must argue why we should keep the existing facts (action: keep_existing) or point out potential issues with accepting the incoming facts.\n"
    "Always base your arguments on the provided global rules, character histories, and chapter prose. Keep your tone analytical, precise, and professional."
)

SCANNER_SYSTEM_INSTRUCTION = (
    "You are the Scanner (Prose Advocate) on a Multi-Agent Narrative Consensus Panel.\n"
    "Your primary objective is to defend the creative choices made in the newly generated prose, narrative momentum, character progression, and the scanned incoming facts.\n"
    "You argue why we should apply the incoming facts (action: apply_incoming) because they represent the dynamic flow of the story and the active choices of the writer.\n"
    "You explain why the change makes creative sense or how the story context justifies it.\n"
    "Always base your arguments on the newly generated chapter prose and the surrounding context. Keep your tone creative, narrative-focused, and persuasive."
)

PLANNER_SYSTEM_INSTRUCTION = (
    "You are the Planner (Arbitrator) on a Multi-Agent Narrative Consensus Panel.\n"
    "Your objective is to lead the panel, moderate the debate between the Critic (Historian) and the Scanner (Prose Advocate), and build a consensus or creative compromise.\n"
    "In the final round, you must make the final executive decision and output exactly a JSON payload choosing one of two actions: \"keep_existing\" or \"apply_incoming\".\n\n"
    "Your output format for the final round MUST be a valid JSON block containing:\n"
    "{\n"
    "  \"action\": \"keep_existing\" | \"apply_incoming\",\n"
    "  \"reasoning\": \"Detailed logical and narrative reasoning behind the choice.\",\n"
    "  \"narrative_compromise\": \"Suggested prose adjustments or explanation to bridge the gap.\"\n"
    "}\n"
    "Do not include any other text besides the JSON block. Ensure the JSON is perfectly formatted."
)

class ConflictResolverWorkflowMixin:
    """Mixin implementing the Multi-Agent Debate Conflict Resolver."""

    def ai_debate_resolve_conflict(self, conflict_id: int) -> bool:
        """
        Spawns the Multi-Agent Debate Panel (Planner, Critic, Scanner)
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
        self.logger.info("[AUTO] Spawning Triage Panel (Planner, Critic, Scanner)...")

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

        # 2. Bounded Debate Loop
        transcript = []
        planner_decision = None

        for round_idx in range(1, rounds + 1):
            is_final_round = (round_idx == rounds)

            # A. Critic Turn
            self.logger.info(f"[AUTO] Round {round_idx}/{rounds}: Critic is analyzing continuity & rule constraints...")
            critic_prompt = self._build_critic_prompt(context_markdown, transcript, round_idx, is_final_round)
            critic_arg = self.critic_client.generate(
                prompt=critic_prompt,
                system_instruction=CRITIC_SYSTEM_INSTRUCTION,
                temperature=0.7
            ).strip()
            transcript.append(f"#### Round {round_idx} - Critic (Historian) Argument:\n{critic_arg}\n")

            # B. Scanner Turn
            self.logger.info(f"[AUTO] Round {round_idx}/{rounds}: Scanner is defending new prose intentions...")
            scanner_prompt = self._build_scanner_prompt(context_markdown, transcript, round_idx, is_final_round)
            scanner_arg = self.scanner_client.generate(
                prompt=scanner_prompt,
                system_instruction=SCANNER_SYSTEM_INSTRUCTION,
                temperature=0.7
            ).strip()
            transcript.append(f"#### Round {round_idx} - Scanner (Prose Advocate) Argument:\n{scanner_arg}\n")

            # C. Planner Turn
            if is_final_round:
                self.logger.info(f"[AUTO] Round {round_idx}/{rounds}: Planner is synthesizing the final narrative consensus...")
                planner_prompt = self._build_planner_final_prompt(context_markdown, transcript, round_idx)
                planner_res = self.planner_client.generate(
                    prompt=planner_prompt,
                    system_instruction=PLANNER_SYSTEM_INSTRUCTION,
                    temperature=0.3,
                    require_json=True
                ).strip()
                transcript.append(f"#### Round {round_idx} - Planner (Arbitrator - FINAL DECISION):\n{planner_res}\n")
                
                # Extract and parse the final JSON decision
                planner_decision = self._extract_json(planner_res)
            else:
                self.logger.info(f"[AUTO] Round {round_idx}/{rounds}: Planner is arbitrating and summarizing points...")
                planner_prompt = self._build_planner_summary_prompt(context_markdown, transcript, round_idx)
                planner_res = self.planner_client.generate(
                    prompt=planner_prompt,
                    system_instruction=PLANNER_SYSTEM_INSTRUCTION,
                    temperature=0.7
                ).strip()
                transcript.append(f"#### Round {round_idx} - Planner (Arbitrator - Round Summary):\n{planner_res}\n")

        # 3. Consensus Gating & Mutative Commit
        if not planner_decision or "action" not in planner_decision:
            self.logger.error("[AUTO] Planner failed to output a parseable JSON decision block in the final round.")
            self._write_discussion_log(conflict_id, context_markdown, transcript, "STANDOFF", None)
            return False

        action = str(planner_decision.get("action")).strip().lower()
        reasoning = planner_decision.get("reasoning", "No detailed reasoning provided by Planner.")
        compromise = planner_decision.get("narrative_compromise", "")

        if action not in {"keep_existing", "apply_incoming"}:
            self.logger.error(f"[AUTO] Planner output invalid consensus action: '{action}'. Must be keep_existing or apply_incoming.")
            self._write_discussion_log(conflict_id, context_markdown, transcript, "STANDOFF", planner_decision)
            return False

        # Consensus agreed! Apply the transaction atomically
        resolver_note = (
            f"resolved via Multi-Agent Debate Consensus.\n"
            f"Planner Choice: {action}\n"
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
            self._write_discussion_log(conflict_id, context_markdown, transcript, "RESOLVED", planner_decision)
            return True
        else:
            self.logger.error(f"[AUTO] Database transaction failed while applying action '{action}' for conflict #{conflict_id}.")
            self._write_discussion_log(conflict_id, context_markdown, transcript, "TRANSACTION_FAILED", planner_decision)
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
            # id, event_name, description, timestamp_str, impact_level, related_entities, location
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

    def _build_critic_prompt(self, context: str, transcript: List[str], round_idx: int, is_final: bool) -> str:
        transcript_str = "\n".join(transcript) if transcript else "*No preceding arguments yet.*"
        final_instruction = (
            "\nProvide your final, decisive arguments, synthesizing why accepted rules and past continuity must be protected."
            if is_final else "\nMake your arguments outlining continuity concerns and spatiotemporal database integrity risks."
        )
        return (
            f"{context}\n\n"
            f"--- DEBATE HISTORY ---\n"
            f"{transcript_str}\n\n"
            f"--- YOUR MISSION (ROUND {round_idx}) ---\n"
            f"You are the Critic (Historian). Analyze the conflict and any ongoing arguments above.{final_instruction}"
        )

    def _build_scanner_prompt(self, context: str, transcript: List[str], round_idx: int, is_final: bool) -> str:
        transcript_str = "\n".join(transcript) if transcript else "*No preceding arguments yet.*"
        final_instruction = (
            "\nProvide your final, decisive arguments, synthesizing why the writer's creative prose direction is justified and must be preserved."
            if is_final else "\nMake your arguments explaining the narrative weight, prose benefits, and setup that justifies this change."
        )
        return (
            f"{context}\n\n"
            f"--- DEBATE HISTORY ---\n"
            f"{transcript_str}\n\n"
            f"--- YOUR MISSION (ROUND {round_idx}) ---\n"
            f"You are the Scanner (Prose Advocate). Review the context and Critic's arguments above.{final_instruction}"
        )

    def _build_planner_summary_prompt(self, context: str, transcript: List[str], round_idx: int) -> str:
        transcript_str = "\n".join(transcript)
        return (
            f"{context}\n\n"
            f"--- DEBATE HISTORY ---\n"
            f"{transcript_str}\n\n"
            f"--- YOUR MISSION (ROUND {round_idx}) ---\n"
            f"You are the Planner (Arbitrator). Summarize the arguments of the Critic and Scanner in this round. "
            f"Point out compromise avenues. Do NOT output a final JSON choice yet; wait until the final round."
        )

    def _build_planner_final_prompt(self, context: str, transcript: List[str], round_idx: int) -> str:
        transcript_str = "\n".join(transcript)
        return (
            f"{context}\n\n"
            f"--- DEBATE HISTORY ---\n"
            f"{transcript_str}\n\n"
            f"--- YOUR MISSION (ROUND {round_idx} - FINAL DECISION) ---\n"
            f"You are the Planner (Arbitrator). The debate is concluded. You must make the final decision.\n"
            f"Select EXACTLY one action: 'keep_existing' (historian's victory) or 'apply_incoming' (scanner's victory).\n"
            f"Provide a clear narrative justification and suggest a prose compromise to bridge the narrative transition.\n\n"
            f"Output your decision in a strict JSON payload block:\n"
            f"{{\n"
            f"  \"action\": \"keep_existing\" | \"apply_incoming\",\n"
            f"  \"reasoning\": \"Your detailed narrative justification...\",\n"
            f"  \"narrative_compromise\": \"Your suggested narrative bridge...\"\n"
            f"}}\n"
            f"Do not include any other markdown formatting or prefix/suffix outside the JSON object itself."
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
