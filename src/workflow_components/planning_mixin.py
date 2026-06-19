import os
import logging
from typing import Dict, Optional

import config
from llm_client import LLMClientError
from workflow_components.resources import get_resource

class PlanningWorkflowMixin:
    def _refine_chapter_guide_with_discussion(self, chapter_num: int, guide: str, prompts: Dict[str, str]) -> str:
        rounds = max(0, config.CHAPTER_GUIDE_DISCUSSION_ROUNDS)
        self.logger.info(f"Spawning Chapter Planning Committee to refine guide for Ch {chapter_num}...")
        
        preset = self.att_manager.get_preset("planning")
        
        team = self.att_manager.create_agent_team(
            creator=self.att_manager.root_ai,
            member_count=3,
            roles_and_presets=preset["roles"],
            preset_name="planning",
            system_instructions=preset["system_instructions"]
        )
        team.chapter_num = chapter_num

        prompt = (
            f"Please refine the initial chapter guide for Chapter {chapter_num}.\n\n"
            f"Initial Chapter Guide:\n{guide}\n\n"
            f"Continuity_Auditor must check for timeline coherence, "
            f"Structural_Planner must optimize pacing and scene structures, "
            f"and Reviewer_Arbitrator must integrate the refinements and produce the finalized guide."
        )

        try:
            transcript = self.att_manager.execute_team_discussion_sync(team, prompt, rounds=rounds)
            if "final answer:" in transcript.lower():
                final_guide = transcript.split("Final Answer:", 1)[1].strip()
            else:
                final_guide = guide
                
            self._append_structured_discussion(
                phase_type="guide",
                role="Chapter_Planning_Committee",
                prompt_text=prompt,
                response_text=final_guide,
                chapter_num=chapter_num,
                round_index=rounds,
                decision="guide_finalized",
                needs_revision=False,
                artifact_paths=[self.get_guide_path(chapter_num)],
            )
            return final_guide
        except Exception as e:
            self.logger.warning(f"Chapter Planning Committee execution failed, using initial guide: {e}")
            return guide

    def generate_chapter_guide(self, chapter_num: int, previous_summary: str = None) -> str:
        self.logger.info(f"Generating guide for Chapter {chapter_num}...")
        self._enforce_conflict_free_state(stage=f"chapter_{self._num3(chapter_num)}_planning")
        prompts = self._get_system_prompts()

        world_bible_path = self._latest_world_bible_path()
        if not os.path.exists(world_bible_path):
            raise RuntimeError("World Bible not found. Run --init first.")

        with open(world_bible_path, "r", encoding="utf-8") as f:
            world_bible = f.read()
        plot_outline = self._read_text_if_exists(self._plot_outline_path())
        detailed_plot_outline = self._read_text_if_exists(self._detailed_plot_outline_path())

        context_pkg = self.state_manager.build_context_package(
            task_type="planner",
            chapter_num=chapter_num,
            previous_summary=previous_summary,
            recent_events_limit=5,
            conflicts_limit=10,
            user_request=f"chapter_{self._num3(chapter_num)}_guide",
        )
        db_chars = context_pkg["characters"]  # type: ignore[assignment]
        char_summary = get_resource("ui.char_summary_header")
        if db_chars:
            for c in db_chars:
                char_summary += "- " + c[0] + get_resource("ui.status_label", status=c[2]) + "\n"
        else:
            char_summary += get_resource("ui.no_records")

        db_rules = context_pkg["rules"]  # type: ignore[assignment]
        rule_summary = get_resource("ui.rule_summary_header")
        if db_rules:
            for r in db_rules:
                rule_summary += get_resource("ui.rule_item", category=r[0], content=r[1], strictness=r[2])
        else:
            rule_summary += get_resource("ui.rule_no_records")

        db_events = context_pkg["events"]  # type: ignore[assignment]
        event_summary = get_resource("ui.event_summary_header")
        if db_events:
            for e in db_events:
                event_summary += get_resource("ui.event_item", timestamp=e[3], name=e[1], description=e[2], location=e[6])
        else:
            event_summary += get_resource("ui.event_no_records")
        pending_conflicts = context_pkg["conflicts"]  # type: ignore[assignment]
        conflict_summary = get_resource("ui.conflict_summary_header")
        if pending_conflicts:
            for conflict in pending_conflicts:
                conflict_summary += (
                    f"- #{conflict[0]} [{conflict[3]}] {conflict[1]}:{conflict[2]} (chapter={conflict[5]})\n"
                )
        else:
            conflict_summary += get_resource("ui.none")
        semantic_summary = str(context_pkg["semantic_summary"])
        retrieval_intent = context_pkg["intent"]  # type: ignore[assignment]

        context_prefix = get_resource("label.world_background")
        plot_prefix = get_resource("label.plot_outline")
        detailed_plot_prefix = get_resource("label.detailed_plot_outline")
        prev_summary_prefix = get_resource("label.prev_summary_prefix")
        task_instruction = get_resource("prompt.planner_task", chapter_num=chapter_num)
        task_instruction += f"\n{self._language_rule()}"

        full_prompt = f"{context_prefix}\n{world_bible}\n\n"
        if plot_outline:
            full_prompt += f"{plot_prefix}\n{plot_outline}\n\n"
        if detailed_plot_outline:
            full_prompt += f"{detailed_plot_prefix}\n{detailed_plot_outline}\n\n"
        state_header = get_resource("ui.state_header")
        state_footer = get_resource("ui.state_footer")
        full_prompt += f"{state_header}\n{char_summary}\n{rule_summary}\n{event_summary}\n{state_footer}\n\n"
        full_prompt += conflict_summary + "\n"
        full_prompt += (
            get_resource("label.retrieval_policy", task_type=retrieval_intent.get('task_type'), mode=retrieval_intent.get('mode'), tiers=retrieval_intent.get('required_tiers'))
        )
        full_prompt += semantic_summary + "\n"
        if previous_summary:
            full_prompt += f"{prev_summary_prefix} {previous_summary}\n\n"
        full_prompt += task_instruction

        if hasattr(self, "att_manager") and getattr(self.att_manager, "dashboard", None):
            self.att_manager.dashboard.active_stage = f"Planning Chapter {chapter_num}"
            self.att_manager.dashboard.add_activity("Planner", "Thought", f"Generating draft Chapter Guide based on history, rules & memory...")
            self.att_manager.dashboard.refresh()

        try:
            guide = self.planner_client.generate(prompt=full_prompt, system_instruction=prompts["planner"])
        except LLMClientError as e:
            raise RuntimeError(str(e)) from e
        guide = self._enforce_output_language(
            self.planner_client, "Planner", guide, prompts["planner"], chapter_num=chapter_num
        )
        self._log_llm_interaction(
            role="Planner",
            phase=f"Chapter {self._num3(chapter_num)} Planning",
            prompt=full_prompt,
            response=guide,
            system_instruction=prompts["planner"],
            chapter_num=chapter_num,
        )
        guide = self._refine_chapter_guide_with_discussion(chapter_num, guide, prompts)

        self._save_file(f"chapter_{self._num3(chapter_num)}_guide.md", guide, self.guides_dir)
        return guide
