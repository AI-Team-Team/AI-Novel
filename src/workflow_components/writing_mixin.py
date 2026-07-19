import os
import logging
from typing import Dict, Tuple, Optional

import config
from llm_client import LLMClientError
from workflow_components.resources import get_resource

class WritingWorkflowMixin:
    def _critic_review_chapter(self, chapter_num: int, guide_content: str, chapter_text: str, prompts: Dict[str, str]) -> str:
        review_task = get_resource("prompt.critic_review_task")
        output_format = get_resource("prompt.critic_output_format")
        contract_label = get_resource("label.contract")
        chapter_label = get_resource("label.chapter_text")
        critic_prompt = (
            f"{contract_label}:\n{guide_content}\n\n"
            f"{chapter_label}:\n{chapter_text}\n\n{review_task}\n{output_format}\n{self._language_rule()}"
        )
        if hasattr(self, "att_manager") and getattr(self.att_manager, "dashboard", None):
            self.att_manager.dashboard.active_stage = f"Reviewing Chapter {chapter_num}"
            self.att_manager.dashboard.add_activity("Critic", "Thought", f"Reviewing prose draft against chapter guide for alignment...")
            self.att_manager.dashboard.refresh()

        review = self.critic_client.generate(
            prompt=critic_prompt,
            system_instruction=prompts["critic"],
        )
        review = self._enforce_output_language(
            self.critic_client, "Critic", review, prompts["critic"], chapter_num=chapter_num
        )
        self._log_llm_interaction(
            role="Critic",
            phase=f"Chapter {self._num3(chapter_num)} Review",
            prompt=critic_prompt,
            response=review,
            system_instruction=prompts["critic"],
            chapter_num=chapter_num,
        )
        self._save_file(f"chapter_{self._num3(chapter_num)}_review.md", review, self.reviews_dir)
        return review

    def _review_and_revise_chapter(self, chapter_num: int, guide: str, chapter_text: str, prompts: Dict[str, str]) -> Tuple[str, str]:
        current_text = chapter_text
        if not getattr(config, "ENABLE_AUTONOMY_SUITE", True):
            self.logger.info(f"Autonomy suite disabled. Bypassing Chapter Editorial Committee revision for Ch {chapter_num}.")
            return current_text, "Editorial committee review bypassed because autonomy suite is disabled."

        rounds = max(0, config.CHAPTER_TEXT_DISCUSSION_ROUNDS)
        self.logger.info(f"Spawning Chapter Editorial Committee to review and revise Ch {chapter_num}...")
        
        preset = self.att_manager.get_preset("editorial")
        
        team = self.att_manager.create_agent_team(
            creator=self.att_manager.root_ai,
            member_count=3,
            roles_and_presets=preset["roles"],
            preset_name="editorial",
            system_instructions=preset["system_instructions"]
        )
        team.chapter_num = chapter_num

        prompt = (
            f"Please review and revise the draft for Chapter {chapter_num}.\n\n"
            f"Chapter Writing Contract:\n{guide}\n\n"
            f"Current Chapter Draft:\n{current_text}\n\n"
            f"Style_Critic must critique style and voice, "
            f"Creative_Writer must revise prose blocks, "
            f"and Editor_In_Chief must compile the comments and write the final polished prose draft (specifying 'Final Answer: <polished text>')."
        )

        try:
            transcript = self.att_manager.execute_team_discussion_sync(team, prompt, rounds=rounds)
            if "final answer:" in transcript.lower():
                final_text = transcript.split("Final Answer:", 1)[1].strip()
            else:
                final_text = current_text
                
            self._save_file(f"chapter_{self._num3(chapter_num)}.md", final_text, self.chapters_dir)
            self._append_structured_discussion(
                phase_type="chapter_text",
                role="Chapter_Editorial_Committee",
                prompt_text=prompt,
                response_text=final_text,
                chapter_num=chapter_num,
                round_index=rounds,
                decision="chapter_text_finalized",
                needs_revision=False,
                artifact_paths=[self.get_chapter_path(chapter_num)],
            )
            return final_text, "Review completed and approved by Chapter Editorial Committee."
        except Exception as e:
            self.logger.warning(f"Chapter Editorial Committee execution failed, using initial draft: {e}")
            return current_text, f"Editorial committee review bypassed due to error: {e}"

    def write_chapter(self, chapter_num: int, guide_content: str) -> str:
        self.logger.info(f"Writing Chapter {chapter_num}...")
        self._enforce_conflict_free_state(stage=f"chapter_{self._num3(chapter_num)}_writing")
        prompts = self._get_system_prompts()

        previous_summary = self._load_previous_summary(chapter_num - 1) if chapter_num > 1 else None
        context_pkg = self.state_manager.build_context_package(
            task_type="writer",
            chapter_num=chapter_num,
            previous_summary=previous_summary,
            recent_events_limit=8,
            conflicts_limit=10,
            user_request=f"chapter_{self._num3(chapter_num)}_writing",
        )
        db_chars = context_pkg["characters"]  # type: ignore[assignment]
        db_rules = context_pkg["rules"]  # type: ignore[assignment]
        db_events = context_pkg["events"]  # type: ignore[assignment]
        pending_conflicts = context_pkg["conflicts"]  # type: ignore[assignment]
        semantic_context = str(context_pkg["semantic_summary"])
        retrieval_intent = context_pkg["intent"]  # type: ignore[assignment]

        char_lines = []
        for c in db_chars:
            char_lines.append(
                "- " + c[0] + get_resource("ui.status_label", status=c[2])
            )
        if not char_lines:
            char_lines.append(get_resource("ui.no_records_simple"))

        rule_lines = []
        for r in db_rules:
            rule_lines.append(
                get_resource("ui.rule_item_no_newline", category=r[0], content=r[1], strictness=r[2])
            )
        if not rule_lines:
            rule_lines.append(get_resource("ui.rule_no_records_simple"))

        event_lines = []
        for e in db_events:
            event_lines.append(
                get_resource("ui.event_item_no_newline", timestamp=e[3], name=e[1], description=e[2], location=e[6])
            )
        if not event_lines:
            event_lines.append(get_resource("ui.event_no_records_simple"))

        conflict_lines = []
        for conflict in pending_conflicts:
            conflict_lines.append(
                f"- #{conflict[0]} [{conflict[3]}] {conflict[1]}:{conflict[2]} (chapter={conflict[5]})"
            )
        if not conflict_lines:
            conflict_lines.append(get_resource("ui.none_simple"))

        contract_prefix = get_resource("label.contract") + "："
        write_instruction = get_resource("prompt.writer_task", chapter_num=chapter_num)
        write_instruction += f"\n{self._language_rule()}"
        writer_context_label = get_resource("label.writer_context")
        previous_label = get_resource("label.previous_summary")
        chars_label = get_resource("label.character_state")
        rules_label = get_resource("label.world_rules")
        events_label = get_resource("label.recent_events")
        conflicts_label = get_resource("label.pending_conflicts")
        semantic_label = get_resource("label.semantic_details")
        prev_text = previous_summary or get_resource("ui.none_bracket")
        retrieval_policy = (
            get_resource("label.retrieval_policy_detailed", task_type=retrieval_intent.get('task_type'), mode=retrieval_intent.get('mode'), tiers=retrieval_intent.get('required_tiers'))
        )
        writer_prompt = (
            f"{contract_prefix}\n{guide_content}\n\n"
            f"{writer_context_label}:\n"
            f"{previous_label}:\n{prev_text}\n\n"
            f"{retrieval_policy}"
            f"{chars_label}:\n{'\n'.join(char_lines)}\n\n"
            f"{rules_label}:\n{'\n'.join(rule_lines)}\n\n"
            f"{events_label}:\n{'\n'.join(event_lines)}\n\n"
            f"{conflicts_label}:\n{'\n'.join(conflict_lines)}\n\n"
            f"{semantic_label}:\n{semantic_context}\n"
            f"{write_instruction}"
        )

        if hasattr(self, "att_manager") and getattr(self.att_manager, "dashboard", None):
            self.att_manager.dashboard.active_stage = f"Writing Chapter {chapter_num}"
            self.att_manager.dashboard.add_activity("Writer", "Thought", f"Generating draft prose for Chapter {chapter_num} based on Guide...")
            self.att_manager.dashboard.refresh()

        try:
            chapter_text = self.writer_client.generate(
                prompt=writer_prompt,
                system_instruction=prompts["writer"],
            )
        except LLMClientError as e:
            raise RuntimeError(str(e)) from e
        chapter_text = self._enforce_output_language(
            self.writer_client, "Writer", chapter_text, prompts["writer"], chapter_num=chapter_num
        )
        self._log_llm_interaction(
            role="Writer",
            phase=f"Chapter {self._num3(chapter_num)} Draft",
            prompt=writer_prompt,
            response=chapter_text,
            system_instruction=prompts["writer"],
            chapter_num=chapter_num,
        )
        self._save_file(f"chapter_{self._num3(chapter_num)}.md", chapter_text, self.chapters_dir)
        return chapter_text

    def review_revise_and_scan(self, chapter_num: int, guide_content: str, chapter_text: str) -> str:
        prompts = self._get_system_prompts()
        revised_text, _ = self._review_and_revise_chapter(chapter_num, guide_content, chapter_text, prompts)
        if revised_text != chapter_text:
            self._save_file(f"chapter_{self._num3(chapter_num)}.md", revised_text, self.chapters_dir)
        return self.scan_chapter(chapter_num)
