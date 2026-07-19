import os
import json
import logging
from typing import Dict

import config
from llm_client import LLMClientError
from workflow_components.resources import get_resource

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
            self.logger.info(f"Autonomy suite disabled. Bypassing Plot Outline Committee for phase: {phase_name}.")
            try:
                planner_prompt = prompts.get("planner", "")
                outline = self.planner_client.generate(prompt=draft_prompt, system_instruction=planner_prompt)
                final_outline = self._enforce_output_language(self.planner_client, "Planner", outline, planner_prompt, world_building=True)
                self._save_file(output_filename, final_outline, self.plot_dir)
                return final_outline
            except Exception as err:
                raise RuntimeError(str(err)) from err

        self.logger.info(f"Spawning Plot Outline Committee for phase: {phase_name}...")
        
        preset = self.att_manager.get_preset("plot_outline")
        
        team = self.att_manager.create_agent_team(
            creator=self.att_manager.root_ai,
            member_count=3,
            roles_and_presets=preset["roles"],
            preset_name="plot_outline",
            system_instructions=preset["system_instructions"]
        )
        team.chapter_num = 0

        prompt = (
            f"Please generate the outline for '{phase_name}'.\n\n"
            f"Initial Context and Prompt:\n{draft_prompt}\n\n"
            f"Narrative_Arc_Planner must design progression and character paths, "
            f"Continuity_Critic must ensure cause-and-effect integrity, "
            f"and Arc_Arbitrator must output the final polished outline block (specifying 'Final Answer: <outline content>')."
        )

        try:
            transcript = self.att_manager.execute_team_discussion_sync(team, prompt, rounds=rounds)
            if "final answer:" in transcript.lower():
                final_outline = transcript.split("Final Answer:", 1)[1].strip()
            else:
                final_outline = transcript
                
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
            self.logger.warning(f"Plot Outline Committee execution failed, using direct generation: {e}")
            try:
                outline = self.planner_client.generate(prompt=draft_prompt, system_instruction=prompts["planner"])
                return self._enforce_output_language(self.planner_client, "Planner", outline, prompts["planner"], world_building=True)
            except Exception as err:
                raise RuntimeError(str(err)) from err

    def start_new_project(self, user_instruction: str) -> str:
        self.logger.info(f"Starting new project ({config.LANGUAGE}) with instruction: {user_instruction}")
        prompts = self._get_system_prompts()

        user_prompt_prefix = get_resource("label.user_request_prefix")
        task_instruction = get_resource("prompt.architect_task")
        architect_prompt = f"{user_prompt_prefix} {user_instruction}\n\n{task_instruction}\n\n{self._language_rule()}"

        if hasattr(self, "att_manager") and getattr(self.att_manager, "dashboard", None):
            self.att_manager.dashboard.active_stage = "World Building"
            self.att_manager.dashboard.add_activity("Architect", "Thought", "Drafting World Bible structure based on overview instructions...")
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
            phase="World Building Draft",
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
            self.logger.info("Spawning World Bible Committee to refine World Bible...")
            
            preset = self.att_manager.get_preset("world_bible")
            
            team = self.att_manager.create_agent_team(
                creator=self.att_manager.root_ai,
                member_count=3,
                roles_and_presets=preset["roles"],
                preset_name="world_bible",
                system_instructions=preset["system_instructions"]
            )
            team.chapter_num = 0

            prompt = (
                f"Please refine the initial World Bible based on the user instruction.\n\n"
                f"User Request:\n{user_instruction}\n\n"
                f"Initial World Bible Draft:\n{world_bible}\n\n"
                f"Lore_Architect must check lore rules and constraints, "
                f"Narrative_Critic must check for logic gaps or bottlenecks, "
                f"and World_Arbitrator must integrate the refinements and write the final polished World Bible (specifying 'Final Answer: <world bible content>')."
            )

            try:
                transcript = self.att_manager.execute_team_discussion_sync(team, prompt, rounds=rounds)
                if "final answer:" in transcript.lower():
                    world_bible = transcript.split("Final Answer:", 1)[1].strip()
                else:
                    world_bible = world_bible
                    
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
                self.logger.warning(f"World Bible Committee execution failed, using initial draft: {e}")
        else:
            self.logger.info("Autonomy suite disabled. Bypassing World Bible Committee refinement.")

        plot_draft_prompt = get_resource("prompt.plot_outline_draft", world_bible=world_bible)
        plot_draft_prompt += f"\n\n{self._language_rule()}"
        self._generate_outline_with_discussion(
            phase_name=get_resource("label.plot_outline").strip("："),
            draft_prompt=plot_draft_prompt,
            revise_prompt_builder=(
                lambda current, critique: get_resource("prompt.plot_outline_revise", current=current, critique=critique)
            ),
            rounds=config.PLOT_DISCUSSION_ROUNDS,
            output_filename="plot_outline.md",
            prompts=prompts,
        )
        plot_outline = self._read_text_if_exists(self._plot_outline_path())

        detailed_plot_draft_prompt = get_resource("prompt.detailed_plot_outline_draft", world_bible=world_bible, plot_outline=plot_outline)
        detailed_plot_draft_prompt += f"\n\n{self._language_rule()}"
        self._generate_outline_with_discussion(
            phase_name=get_resource("label.detailed_plot_outline").strip("："),
            draft_prompt=detailed_plot_draft_prompt,
            revise_prompt_builder=(
                lambda current, critique: get_resource("prompt.detailed_plot_outline_revise", current=current, critique=critique)
            ),
            rounds=config.DETAILED_PLOT_DISCUSSION_ROUNDS,
            output_filename="detailed_plot_outline.md",
            prompts=prompts,
        )

        # Seed memory with initial structured facts extracted from the approved world bible.
        scan_prefix = get_resource("label.world_background")
        scan_task = get_resource("prompt.scanner_seed_task")
        scan_task += f" {self._language_rule()}"
        try:
            raw_seed = self.scanner_client.generate(
                prompt=f"{scan_prefix}\n{world_bible}\n\n{scan_task}",
                system_instruction=prompts["scanner"],
            )
            self._log_llm_interaction(
                role="Scanner",
                phase="World Building Seed Extraction",
                prompt=f"{scan_prefix}\n{world_bible}\n\n{scan_task}",
                response=raw_seed,
                system_instruction=prompts["scanner"],
                world_building=True,
            )
            seed_data = self._extract_json(raw_seed)
            if seed_data:
                seed_errors = self._validate_fact_payload(seed_data)
                if seed_errors:
                    self.logger.warning("Initial seed payload validation failed; skip DB seed.")
                    self._save_file(
                        "world_init_facts_invalid.json",
                        json.dumps({"errors": seed_errors, "payload": seed_data}, indent=2, ensure_ascii=False),
                        self.facts_dir,
                    )
                    return bible_path
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
            self.logger.warning(f"Initial fact seeding skipped due to scanner error: {e}")

        return bible_path
