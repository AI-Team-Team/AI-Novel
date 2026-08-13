import os
import json
import logging
from typing import Dict

import config
from llm_client import LLMClientError
from workflow_components.resources import get_ai_resource, get_message
from workflow_components.parsing import extract_att_member_answer

class ProjectWorkflowMixin:
    def _generate_outline_with_discussion(
        self,
        phase_name: str,
        draft_prompt: str,
        revise_prompt_builder,
        rounds: int,
        output_filename: str,
        prompts: Dict[str, str],
    ) -> str:
        if not getattr(config, "ENABLE_AUTONOMY_SUITE", True):
            self.logger.info(get_message("runtime.autonomy_bypass_plot", phase=phase_name))
            try:
                planner_prompt = prompts.get("planner", "")
                outline = self.planner_client.generate(prompt=draft_prompt, system_instruction=planner_prompt)
                final_outline = self._enforce_output_language(self.planner_client, "Planner", outline, planner_prompt, world_building=True)
                self._save_file(output_filename, final_outline, self.plot_dir)
                return final_outline
            except Exception as err:
                raise RuntimeError(str(err)) from err

        self.logger.info(get_message("runtime.spawn_plot", phase=phase_name))
        
        if rounds < 1:
            outline = self.planner_client.generate(
                prompt=draft_prompt, system_instruction=prompts["planner"]
            )
            return self._enforce_output_language(
                self.planner_client,
                "Planner",
                outline,
                prompts["planner"],
                world_building=True,
            )
        team = self._create_att_team("plot_outline", 0)

        prompt = get_ai_resource(
            "prompt.att.plot", phase_name=phase_name, draft_prompt=draft_prompt
        )

        try:
            transcript = self._execute_att_discussion(team, prompt, rounds)
            final_outline = (
                extract_att_member_answer(transcript, team, "Arc_Arbitrator")
                or transcript
            )
                
            outline_path = self._save_file(output_filename, final_outline, self.plot_dir)
            self._append_structured_discussion(
                phase_type="plot",
                role="Plot_Outline_Committee",
                prompt_text=prompt,
                response_text=final_outline,
                round_index=rounds,
                decision=f"{phase_name}_finalized",
                needs_revision=False,
                artifact_paths=[outline_path],
            )
            return final_outline
        except Exception as e:
            self.logger.warning(get_message("runtime.plot_failed", error=e))
            try:
                outline = self.planner_client.generate(prompt=draft_prompt, system_instruction=prompts["planner"])
                return self._enforce_output_language(self.planner_client, "Planner", outline, prompts["planner"], world_building=True)
            except Exception as err:
                raise RuntimeError(str(err)) from err

    def start_new_project(self, user_instruction: str) -> str:
        self.logger.info(get_message("runtime.start_project", language=config.LANGUAGE))
        prompts = self._get_system_prompts()

        user_prompt_prefix = get_ai_resource("label.user_request_prefix")
        task_instruction = get_ai_resource("prompt.architect_task")
        architect_prompt = f"{user_prompt_prefix} {user_instruction}\n\n{task_instruction}\n\n{self._language_rule()}"

        if hasattr(self, "att_manager") and getattr(self.att_manager, "dashboard", None):
            self.att_manager.dashboard.active_stage = get_message("dashboard.world_building")
            self.att_manager.dashboard.add_activity("Architect", "Thought", get_message("dashboard.world_draft"))
            self.att_manager.dashboard.refresh()

        try:
            world_bible = self.architect_client.generate(
                prompt=architect_prompt,
                system_instruction=prompts["architect"],
            )
        except LLMClientError as e:
            raise RuntimeError(str(e)) from e
        world_bible = self._enforce_output_language(
            self.architect_client, "Architect", world_bible, prompts["architect"], world_building=True
        )
        self._log_llm_interaction(
            role="Architect",
            phase=get_message("phase.world_draft"),
            prompt=architect_prompt,
            response=world_bible,
            system_instruction=prompts["architect"],
            world_building=True,
        )

        bible_path = self._save_file("world_bible.md", world_bible, self.world_dir)
        self._append_structured_discussion(
            phase_type="world",
            role="Architect",
            prompt_text=architect_prompt,
            response_text=world_bible,
            round_index=0,
            decision="world_bible_draft_ready",
            needs_revision=None,
            artifact_paths=[bible_path],
        )

        if getattr(config, "ENABLE_AUTONOMY_SUITE", True):
            rounds = max(0, config.WORLD_DISCUSSION_ROUNDS)
            self.logger.info(get_message("runtime.spawn_world"))
            
            team = self._create_att_team("world_bible", 0) if rounds > 0 else None

            prompt = get_ai_resource(
                "prompt.att.world",
                user_instruction=user_instruction,
                world_bible=world_bible,
            )

            try:
                if team is not None:
                    transcript = self._execute_att_discussion(team, prompt, rounds)
                    world_bible = (
                        extract_att_member_answer(transcript, team, "World_Arbitrator")
                        or world_bible
                    )
                    
                bible_path = self._save_file("world_bible.md", world_bible, self.world_dir)
                self._append_structured_discussion(
                    phase_type="world",
                    role="World_Bible_Committee",
                    prompt_text=prompt,
                    response_text=world_bible,
                    round_index=rounds,
                    decision="world_bible_finalized",
                    needs_revision=False,
                    artifact_paths=[bible_path],
                )
            except Exception as e:
                self.logger.warning(get_message("runtime.world_failed", error=e))
        else:
            self.logger.info(get_message("runtime.world_bypassed"))

        plot_draft_prompt = get_ai_resource("prompt.plot_outline_draft", world_bible=world_bible)
        plot_draft_prompt += f"\n\n{self._language_rule()}"
        self._generate_outline_with_discussion(
            phase_name=get_ai_resource("label.plot_outline").strip("："),
            draft_prompt=plot_draft_prompt,
            revise_prompt_builder=(
                lambda current, critique: get_ai_resource("prompt.plot_outline_revise", current=current, critique=critique)
            ),
            rounds=config.PLOT_DISCUSSION_ROUNDS,
            output_filename="plot_outline.md",
            prompts=prompts,
        )
        plot_outline = self._read_text_if_exists(self._plot_outline_path())

        detailed_plot_draft_prompt = get_ai_resource("prompt.detailed_plot_outline_draft", world_bible=world_bible, plot_outline=plot_outline)
        detailed_plot_draft_prompt += f"\n\n{self._language_rule()}"
        self._generate_outline_with_discussion(
            phase_name=get_ai_resource("label.detailed_plot_outline").strip("："),
            draft_prompt=detailed_plot_draft_prompt,
            revise_prompt_builder=(
                lambda current, critique: get_ai_resource("prompt.detailed_plot_outline_revise", current=current, critique=critique)
            ),
            rounds=config.DETAILED_PLOT_DISCUSSION_ROUNDS,
            output_filename="detailed_plot_outline.md",
            prompts=prompts,
        )

        # Seed memory with initial structured facts extracted from the approved world bible.
        scan_prefix = get_ai_resource("label.world_background")
        scan_task = get_ai_resource("prompt.scanner_seed_task")
        scan_task += f" {self._language_rule()}"
        try:
            raw_seed = self.scanner_client.generate(
                prompt=f"{scan_prefix}\n{world_bible}\n\n{scan_task}",
                system_instruction=prompts["scanner"],
            )
            self._log_llm_interaction(
                role="Scanner",
                phase=get_message("phase.world_seed"),
                prompt=f"{scan_prefix}\n{world_bible}\n\n{scan_task}",
                response=raw_seed,
                system_instruction=prompts["scanner"],
                world_building=True,
            )
            seed_data = self._extract_json(raw_seed)
            if seed_data:
                seed_errors = self._validate_fact_payload(seed_data)
                if seed_errors:
                    self.logger.warning(get_message("runtime.seed_invalid"))
                    self._save_file(
                        "world_init_facts_invalid.json",
                        json.dumps({"errors": seed_errors, "payload": seed_data}, indent=2, ensure_ascii=False),
                        self.facts_dir,
                    )
                    return bible_path
                self._audit_database_batch(
                    "chapter_fact_batches",
                    "world_seed_fact_batch",
                    seed_data,
                    0,
                )
                self.memory.begin_batch()
                try:
                    self._apply_fact_payload(
                        seed_data,
                        source="init_world",
                        chapter_num=0,
                        source_commit_id="init_world_seed",
                        intent_tag="init_seed",
                    )
                    self.memory.end_batch(success=True)
                except Exception:
                    self.memory.end_batch(success=False)
                    raise
                self._save_file(
                    "world_init_facts.json",
                    json.dumps(seed_data, indent=2, ensure_ascii=False),
                    self.facts_dir,
                )
                self._sync_compact_archives()
        except LLMClientError as e:
            self.logger.warning(get_message("runtime.seed_failed", error=e))

        return bible_path
