import os
import json
import logging
from typing import Dict

import config
from llm_client import LLMClientError
from workflow_components.resources import get_ai_resource, get_message

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
            char_lines.append(
                "- " + c[0] + get_ai_resource("ui.status_label", status=c[2])
            )
        rule_lines = []
        for r in db_rules:
            rule_lines.append(get_ai_resource(
                "ui.rule_item_no_newline",
                category=r[0],
                content=r[1],
                strictness=r[2],
            ))
        event_lines = []
        for e in db_events:
            event_lines.append(get_ai_resource(
                "ui.event_item_no_newline",
                timestamp=e[3],
                name=e[1],
                description=e[2],
                location=e[6],
            ))

        review_task = get_ai_resource("prompt.critic_fact_review_task")
        review_format = get_ai_resource("prompt.critic_fact_review_format")
        review_prompt = get_ai_resource(
            "prompt.critic_fact_review_wrapper",
            characters="\n".join(char_lines) if char_lines else get_ai_resource("ui.none_bracket"),
            rules="\n".join(rule_lines) if rule_lines else get_ai_resource("ui.none_bracket"),
            events="\n".join(event_lines) if event_lines else get_ai_resource("ui.none_bracket"),
            chapter_num=chapter_num,
            facts_json=json.dumps(facts_data, indent=2, ensure_ascii=False),
            chapter_text=chapter_text,
            review_task=review_task,
            review_format=review_format,
            language_rule=self._language_rule(),
        )

        if hasattr(self, "att_manager") and getattr(self.att_manager, "dashboard", None):
            self.att_manager.dashboard.active_stage = get_message("dashboard.scanning", chapter_num=chapter_num)
            self.att_manager.dashboard.add_activity("Critic", "Thought", get_message("dashboard.fact_review_detail"))
            self.att_manager.dashboard.refresh()

        try:
            response = self.critic_client.generate(
                prompt=review_prompt,
                system_instruction=prompts["critic"],
                require_json=True,
            )
            self._log_llm_interaction(
                role="Critic",
                phase=get_message("phase.chapter_fact_review", chapter=self._num3(chapter_num)),
                prompt=review_prompt,
                response=response,
                system_instruction=prompts["critic"],
                chapter_num=chapter_num,
            )
            review_result = json.loads(response)
        except Exception as err:
            self.logger.warning(get_message("runtime.critic_failed", error=err))
            return facts_data

        issues = review_result.get("issues", [])
        if not issues:
            self.logger.info(get_message("runtime.critic_clean"))
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
                notes=get_ai_resource("runtime.critic_review_note", reason=reason),
                blocking_level=(
                    self.memory.BLOCKING if severity == "BLOCKING" else self.memory.NON_BLOCKING
                ),
            )
            self.logger.info(get_message(
                "runtime.critic_issue",
                severity=severity,
                fact_type=fact_type,
                fact_index=fact_index,
                reason=reason,
            ))

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
            self.logger.info(get_message(
                "runtime.critic_removed",
                count=removed_count,
                payload_key=key,
            ))

        return facts_data

    def scan_chapter(self, chapter_num: int) -> str:
        self.logger.info(get_message("runtime.scan_chapter", chapter_num=chapter_num))
        prompts = self._get_system_prompts()

        path = self.get_chapter_path(chapter_num)
        try:
            with open(path, "r", encoding="utf-8") as f:
                chapter_text = f.read()
        except FileNotFoundError:
            raise RuntimeError(get_message("runtime.chapter_missing", chapter_num=chapter_num))

        text_prefix = get_ai_resource("label.chapter_text") + "："
        extract_instruction = get_ai_resource("prompt.scanner_task")
        scanner_prompt = f"{text_prefix}\n{chapter_text}\n\n{extract_instruction}\n{self._language_rule()}"

        if hasattr(self, "att_manager") and getattr(self.att_manager, "dashboard", None):
            self.att_manager.dashboard.active_stage = get_message("dashboard.scanning", chapter_num=chapter_num)
            self.att_manager.dashboard.add_activity("Scanner", "Thought", get_message("dashboard.scanning_detail"))
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
            phase=get_message("phase.chapter_extraction", chapter=self._num3(chapter_num)),
            prompt=scanner_prompt,
            response=raw_response,
            system_instruction=prompts["scanner"],
            chapter_num=chapter_num,
        )

        data = self._extract_json(raw_response)
        summary_lines = [get_ai_resource("label.chapter_summary_prefix", chapter_num=chapter_num)]

        if not data:
            self._save_file(
                f"chapter_{self._num3(chapter_num)}_facts_raw.txt",
                raw_response,
                self.facts_dir,
            )
            raise RuntimeError(get_message("runtime.scanner_invalid_json"))
        validation_errors = self._validate_fact_payload(data)
        if validation_errors:
            self._save_file(
                f"chapter_{self._num3(chapter_num)}_facts_invalid.json",
                json.dumps({"errors": validation_errors, "payload": data}, indent=2, ensure_ascii=False),
                self.facts_dir,
            )
            raise RuntimeError(get_message("runtime.scanner_schema_error"))

        # Critic pre-review: LLM-based contradiction detection before DB commit.
        # BLOCKING issues are removed from the payload; NON_BLOCKING issues are
        # queued as conflicts but facts are kept.
        data = self._critic_review_extracted_facts(
            chapter_num=chapter_num,
            facts_data=data,
            chapter_text=chapter_text,
            prompts=prompts,
        )

        self._audit_database_batch(
            "chapter_fact_batches",
            "scan_chapter_fact_batch",
            data,
            chapter_num,
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
            self.memory.finalize_chapter_commit(commit_id, status="COMPLETED", conflicts_count=new_conflicts)
            self.memory.end_batch(success=True)
        except Exception as e:
            self.memory.end_batch(success=False)
            self.memory.finalize_chapter_commit(
                commit_id,
                status="FAILED",
                conflicts_count=0,
                error_message=str(e),
            )
            raise

        summary_lines.append(get_ai_resource("label.commit_id", commit_id=commit_id))

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
