import os
import json
import logging
from typing import Dict

import config
from llm_client import LLMClientError
from workflow_components.resources import get_resource

class ScanningWorkflowMixin:
    def _critic_review_extracted_facts(
        self,
        chapter_num: int,
        facts_data: Dict,
        chapter_text: str,
        prompts: Dict[str, str],
    ) -> Dict:
        """LLM Critic reviews extracted facts for contradictions before DB commit.

        Returns the (possibly filtered) facts_data dict.  BLOCKING issues cause
        the offending facts to be removed from the payload and queued as conflicts.
        NON_BLOCKING issues are queued as conflicts but the facts remain.
        """
        # Build a state snapshot for the Critic
        snapshot = self.state_manager.get_state_snapshot(recent_events_limit=10, conflicts_limit=20)
        db_chars = snapshot["characters"]
        db_rules = snapshot["rules"]
        db_events = snapshot["events"]

        char_lines = []
        for c in db_chars:
            char_lines.append(f"- {c[0]} (status: {c[2]})")
        rule_lines = []
        for r in db_rules:
            rule_lines.append(f"- [{r[0]}] {r[1]} (strictness: {r[2]})")
        event_lines = []
        for e in db_events:
            event_lines.append(f"- {e[1]}: {e[2]} (location: {e[6]})")

        review_task = get_resource("prompt.critic_fact_review_task")
        review_format = get_resource("prompt.critic_fact_review_format")
        review_prompt = (
            f"## Current Story State\n\n"
            f"### Characters\n{'\n'.join(char_lines) if char_lines else '(none)'}\n\n"
            f"### Strict World Rules\n{'\n'.join(rule_lines) if rule_lines else '(none)'}\n\n"
            f"### Recent Events\n{'\n'.join(event_lines) if event_lines else '(none)'}\n\n"
            f"## Extracted Facts (Chapter {chapter_num})\n"
            f"```json\n{json.dumps(facts_data, indent=2, ensure_ascii=False)}\n```\n\n"
            f"## Chapter Text\n{chapter_text}\n\n"
            f"{review_task}\n\n{review_format}\n{self._language_rule()}"
        )

        if hasattr(self, "att_manager") and getattr(self.att_manager, "dashboard", None):
            self.att_manager.dashboard.active_stage = f"Scanning Chapter {chapter_num}"
            self.att_manager.dashboard.add_activity("Critic", "Thought", f"Checking extracted facts for continuity and timeline consistency...")
            self.att_manager.dashboard.refresh()

        try:
            response = self.critic_client.generate(
                prompt=review_prompt,
                system_instruction=prompts["critic"],
                require_json=True,
            )
            self._log_llm_interaction(
                role="Critic",
                phase=f"Chapter {self._num3(chapter_num)} Fact Review",
                prompt=review_prompt,
                response=response,
                system_instruction=prompts["critic"],
                chapter_num=chapter_num,
            )
            review_result = json.loads(response)
        except Exception as err:
            self.logger.warning(f"Critic fact review failed, proceeding without review: {err}")
            return facts_data

        issues = review_result.get("issues", [])
        if not issues:
            self.logger.info("Critic fact review: no issues found.")
            return facts_data

        # Map fact_type to payload keys
        type_to_key = {
            "character": None,  # handled separately for new/updated
            "new_character": "new_characters",
            "updated_character": "updated_characters",
            "event": "events",
            "rule": "new_rules",
            "relationship": "relationships",
            "detail": "details",
        }

        # Collect indices to remove for BLOCKING issues
        blocking_removals: Dict[str, set] = {}
        for issue in issues:
            fact_type = issue.get("fact_type", "")
            fact_index = issue.get("fact_index", -1)
            severity = issue.get("severity", "NON_BLOCKING")
            reason = issue.get("reason", "")

            # Resolve the payload key
            payload_key = type_to_key.get(fact_type)
            if payload_key is None and fact_type == "character":
                # Critic may use generic "character" — check both lists
                if fact_index < len(facts_data.get("new_characters", [])):
                    payload_key = "new_characters"
                else:
                    adjusted = fact_index - len(facts_data.get("new_characters", []))
                    if adjusted >= 0 and adjusted < len(facts_data.get("updated_characters", [])):
                        payload_key = "updated_characters"
                        fact_index = adjusted

            # Queue the conflict
            entity_key = f"critic_review:ch{chapter_num}:{fact_type}:{fact_index}"
            incoming_obj = {}
            if payload_key and 0 <= fact_index < len(facts_data.get(payload_key, [])):
                incoming_obj = facts_data[payload_key][fact_index]

            self.memory.queue_conflict(
                entity_type=fact_type,
                entity_key=entity_key,
                conflict_type=f"critic_detected:{severity.lower()}",
                incoming_obj=incoming_obj,
                existing_obj={"reason": reason, "severity": severity},
                source="critic_fact_review",
                chapter_num=chapter_num,
                notes=f"Critic review: {reason}",
                blocking_level=(
                    self.memory.BLOCKING if severity == "BLOCKING" else self.memory.NON_BLOCKING
                ),
            )
            self.logger.info(
                "Critic flagged %s issue: %s[%d] — %s",
                severity, fact_type, fact_index, reason,
            )

            # Mark BLOCKING facts for removal
            if severity == "BLOCKING" and payload_key:
                blocking_removals.setdefault(payload_key, set()).add(fact_index)

        # Remove BLOCKING facts from payload (iterate in reverse to preserve indices)
        for key, indices in blocking_removals.items():
            original = facts_data.get(key, [])
            facts_data[key] = [
                item for i, item in enumerate(original) if i not in indices
            ]
            removed_count = len(indices)
            self.logger.info("Removed %d BLOCKING %s from payload.", removed_count, key)

        return facts_data

    def scan_chapter(self, chapter_num: int) -> str:
        self.logger.info(f"Scanning Chapter {chapter_num} for facts...")
        prompts = self._get_system_prompts()

        path = self.get_chapter_path(chapter_num)
        try:
            with open(path, "r", encoding="utf-8") as f:
                chapter_text = f.read()
        except FileNotFoundError:
            raise RuntimeError(f"Chapter {chapter_num} not found.")

        text_prefix = get_resource("label.chapter_text") + "："
        extract_instruction = get_resource("prompt.scanner_task")
        scanner_prompt = f"{text_prefix}\n{chapter_text}\n\n{extract_instruction}\n{self._language_rule()}"

        if hasattr(self, "att_manager") and getattr(self.att_manager, "dashboard", None):
            self.att_manager.dashboard.active_stage = f"Scanning Chapter {chapter_num}"
            self.att_manager.dashboard.add_activity("Scanner", "Thought", f"Extracting events, character changes, and rule facts from prose...")
            self.att_manager.dashboard.refresh()

        try:
            raw_response = self.scanner_client.generate(
                prompt=scanner_prompt,
                system_instruction=prompts["scanner"],
                require_json=True,
            )
        except LLMClientError as e:
            raise RuntimeError(str(e)) from e
        self._log_llm_interaction(
            role="Scanner",
            phase=f"Chapter {self._num3(chapter_num)} Extraction",
            prompt=scanner_prompt,
            response=raw_response,
            system_instruction=prompts["scanner"],
            chapter_num=chapter_num,
        )

        data = self._extract_json(raw_response)
        summary_lines = [get_resource("label.chapter_summary_prefix", chapter_num=chapter_num)]

        if not data:
            self._save_file(
                f"chapter_{self._num3(chapter_num)}_facts_raw.txt",
                raw_response,
                self.facts_dir,
            )
            raise RuntimeError("Scanner returned invalid JSON.")
        validation_errors = self._validate_fact_payload(data)
        if validation_errors:
            self._save_file(
                f"chapter_{self._num3(chapter_num)}_facts_invalid.json",
                json.dumps({"errors": validation_errors, "payload": data}, indent=2, ensure_ascii=False),
                self.facts_dir,
            )
            raise RuntimeError("Scanner payload failed schema validation.")

        # Critic pre-review: LLM-based contradiction detection before DB commit.
        # BLOCKING issues are removed from the payload; NON_BLOCKING issues are
        # queued as conflicts but facts are kept.
        data = self._critic_review_extracted_facts(
            chapter_num=chapter_num,
            facts_data=data,
            chapter_text=chapter_text,
            prompts=prompts,
        )

        commit_id = self.memory.begin_chapter_commit(chapter_num, source="scan_chapter", payload=data)
        try:
            self.memory.begin_batch()
            new_conflicts = self._apply_fact_payload(
                data,
                summary_lines=summary_lines,
                source="scan_chapter",
                chapter_num=chapter_num,
                source_commit_id=commit_id,
                intent_tag="scan_extract",
            )
            self.memory.end_batch(success=True)
            self.memory.finalize_chapter_commit(commit_id, status="COMPLETED", conflicts_count=new_conflicts)
        except Exception as e:
            self.memory.end_batch(success=False)
            self.memory.finalize_chapter_commit(
                commit_id,
                status="FAILED",
                conflicts_count=0,
                error_message=str(e),
            )
            raise

        summary_lines.append(get_resource("label.commit_id", commit_id=commit_id))

        self._save_file(
            f"chapter_{self._num3(chapter_num)}_facts.json",
            json.dumps(data, indent=2, ensure_ascii=False),
            self.facts_dir,
        )

        summary_text = "\n".join(summary_lines)
        self._save_file(
            f"chapter_{self._num3(chapter_num)}_facts_summary.md",
            summary_text,
            self.facts_dir,
        )
        self._sync_compact_archives()
        self._enforce_conflict_free_state(stage=f"chapter_{self._num3(chapter_num)}_post_scan")
        return summary_text
