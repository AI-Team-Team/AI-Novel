import os
import logging
import time
import json
from typing import Dict, Optional, Tuple, List

import config
from llm_client import LLMClient, LLMClientError
from memory import MemoryManager
from state_manager import StoryStateManager
from workflow_components.parsing import (
    extract_json_payload,
    needs_revision,
    validate_fact_payload,
)
from workflow_components.prompts import load_system_prompts
from workflow_components.resources import get_resource
from workflow_components.resume_mixin import WorkflowResumeMixin
from workflow_components.io_mixin import WorkflowIOMixin
from workflow_components.language_mixin import WorkflowLanguageMixin

class WorkflowManager(WorkflowResumeMixin, WorkflowIOMixin, WorkflowLanguageMixin):
    def __init__(self):
        self.logger = logging.getLogger("WorkflowManager")
        logging.basicConfig(level=logging.INFO)

        self.architect_client = LLMClient(
            model_type=config.ARCHITECT_MODEL_TYPE,
            model_name=(
                config.ARCHITECT_OPENAI_MODEL_NAME
                if config.ARCHITECT_MODEL_TYPE == "openai"
                else config.ARCHITECT_GEMINI_MODEL_NAME
            ),
            enable_embedding=False,
        )
        self.planner_client = LLMClient(
            model_type=config.PLANNER_MODEL_TYPE,
            model_name=(
                config.PLANNER_OPENAI_MODEL_NAME
                if config.PLANNER_MODEL_TYPE == "openai"
                else config.PLANNER_GEMINI_MODEL_NAME
            ),
            enable_embedding=False,
        )
        self.writer_client = LLMClient(
            model_type=config.WRITER_MODEL_TYPE,
            model_name=(
                config.WRITER_OPENAI_MODEL_NAME
                if config.WRITER_MODEL_TYPE == "openai"
                else config.WRITER_GEMINI_MODEL_NAME
            ),
            enable_embedding=False,
        )
        self.critic_client = LLMClient(
            model_type=config.CRITIC_MODEL_TYPE,
            model_name=(
                config.CRITIC_OPENAI_MODEL_NAME
                if config.CRITIC_MODEL_TYPE == "openai"
                else config.CRITIC_GEMINI_MODEL_NAME
            ),
            enable_embedding=False,
        )
        self.scanner_client = LLMClient(
            model_type=config.SCANNER_MODEL_TYPE,
            model_name=(
                config.SCANNER_OPENAI_MODEL_NAME
                if config.SCANNER_MODEL_TYPE == "openai"
                else config.SCANNER_GEMINI_MODEL_NAME
            ),
            enable_embedding=False,
        )
        # Shared embedding client
        self.embedding_client = LLMClient(model_type=config.PRIMARY_MODEL_TYPE, enable_embedding=True)

        self.memory = MemoryManager(config.DB_PATH, config.FAISS_INDEX_PATH, embedding_dim=config.EMBEDDING_DIM)
        self.state_manager = StoryStateManager(self.memory, self.embedding_client)

        self.world_dir = os.path.join(config.FRAME_DIR, "world")
        self.plot_dir = os.path.join(config.FRAME_DIR, "plot")
        self.guides_dir = os.path.join(config.FRAME_DIR, "chapter_guides")
        self.archives_dir = os.path.join(config.FRAME_DIR, "archives")
        self.chapters_dir = os.path.join(config.OUTPUT_DIR, "chapters")
        self.critiques_dir = os.path.join(config.PROCESS_DIR, "critiques")
        self.discussions_dir = os.path.join(config.PROCESS_DIR, "discussions")
        self.facts_dir = os.path.join(config.PROCESS_DIR, "facts")
        self.reviews_dir = os.path.join(config.PROCESS_DIR, "reviews")
        self.revisions_dir = os.path.join(config.PROCESS_DIR, "revisions")
        self.discussion_log_dir = os.path.join("novel", "Discussion_Log")

        for d in [
            config.OUTPUT_DIR,
            config.FRAME_DIR,
            config.PROCESS_DIR,
            self.world_dir,
            self.plot_dir,
            self.guides_dir,
            self.archives_dir,
            self.chapters_dir,
            self.critiques_dir,
            self.discussions_dir,
            self.facts_dir,
            self.reviews_dir,
            self.revisions_dir,
            self.discussion_log_dir,
        ]:
            os.makedirs(d, exist_ok=True)
        self._ensure_discussion_logs()

    @staticmethod
    def _num3(value: int) -> str:
        return f"{value:03d}"

    def get_guide_path(self, chapter_num: int) -> str:
        return os.path.join(self.guides_dir, f"chapter_{self._num3(chapter_num)}_guide.md")

    def get_chapter_path(self, chapter_num: int) -> str:
        return os.path.join(self.chapters_dir, f"chapter_{self._num3(chapter_num)}.md")

    def get_overview_path(self) -> str:
        return os.path.join("novel", "Novel_Overview.md")

    def _plot_outline_path(self) -> str:
        return os.path.join(self.plot_dir, "plot_outline.md")

    def _detailed_plot_outline_path(self) -> str:
        return os.path.join(self.plot_dir, "detailed_plot_outline.md")

    @staticmethod
    def _default_overview_template() -> str:
        return (
            "# Novel Overview\n\n"
            "Write your high-level novel setup here.\n\n"
            "This file should contain only your requirements for the novel project.\n"
            "The system will read the full document as input.\n\n"
            "Include at least:\n\n"
            "* Genre and tone\n"
            "* Main characters and core relationships\n"
            "* World constraints and major rules\n"
            "* Initial conflict and rough long arc\n"
        )

    def initialize_novel_workspace(self) -> str:
        """
        Initialize only the novel workspace and create Novel_Overview.md.
        This intentionally does not trigger any LLM generation.
        """
        os.makedirs("novel", exist_ok=True)
        for d in [
            config.OUTPUT_DIR,
            config.FRAME_DIR,
            config.PROCESS_DIR,
            self.world_dir,
            self.plot_dir,
            self.guides_dir,
            self.archives_dir,
            self.chapters_dir,
            self.critiques_dir,
            self.discussions_dir,
            self.facts_dir,
            self.reviews_dir,
            self.revisions_dir,
            self.discussion_log_dir,
        ]:
            os.makedirs(d, exist_ok=True)
        self._ensure_discussion_logs()

        overview_path = self.get_overview_path()
        if not os.path.exists(overview_path):
            template = self._default_overview_template()
            with open(overview_path, "w", encoding="utf-8") as f:
                f.write(template)
            self.logger.info(f"Created overview template at {overview_path}")
        return overview_path

    def load_novel_overview(self) -> str:
        overview_path = self.get_overview_path()
        if not os.path.exists(overview_path):
            raise RuntimeError(
                f"Novel overview not found at {overview_path}. Run --init first."
            )

        with open(overview_path, "r", encoding="utf-8") as f:
            overview = f.read().strip()

        if not overview or overview == self._default_overview_template().strip():
            raise RuntimeError(
                f"Novel overview at {overview_path} is empty. Fill it first, then run --start."
            )
        return overview

    def _enforce_conflict_free_state(self, stage: str):
        mode = (getattr(config, "BLOCKING_CONFLICT_MODE", "auto_keep_existing") or "auto_keep_existing").lower()
        if mode == "auto_keep_existing":
            self.state_manager.auto_resolve_pending_conflicts()
        elif mode == "manual_block":
            pass
        else:
            raise RuntimeError(
                f"Invalid BLOCKING_CONFLICT_MODE={mode}. "
                "Expected one of: auto_keep_existing, manual_block."
            )
        blocking_pending = self.memory.get_pending_blocking_conflict_count()
        total_pending = self.memory.get_pending_conflict_count()
        if blocking_pending > 0:
            raise RuntimeError(
                f"Blocked at {stage}: {blocking_pending} unresolved BLOCKING conflicts remain in DB "
                f"(total pending={total_pending}, mode={mode})."
            )

    def _extract_json(self, text: str) -> Optional[Dict]:
        return extract_json_payload(text, logger=self.logger)

    @staticmethod
    def _validate_fact_payload(data: Dict) -> List[str]:
        return validate_fact_payload(data)

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

    def _apply_fact_payload(
        self,
        data: Dict,
        summary_lines: Optional[List[str]] = None,
        source: str = "unknown",
        chapter_num: Optional[int] = None,
        source_commit_id: Optional[str] = None,
        intent_tag: str = "",
    ) -> int:
        manager = getattr(self, "state_manager", None)
        if manager is None:
            manager = StoryStateManager(getattr(self, "memory", None), getattr(self, "embedding_client", None))
        return manager.apply_fact_payload(
            data=data,
            summary_lines=summary_lines,
            source=source,
            chapter_num=chapter_num,
            source_commit_id=source_commit_id,
            intent_tag=intent_tag,
        )

    @staticmethod
    def _extract_focus_from_state(db_chars: List[tuple], db_events: List[tuple]) -> Dict[str, List[str]]:
        return StoryStateManager.extract_focus_from_state(db_chars, db_events)

    def _build_planner_retrieval_intent(
        self,
        chapter_num: int,
        previous_summary: Optional[str],
        db_chars: List[tuple],
        db_events: List[tuple],
        pending_conflicts: List[tuple],
    ) -> Dict[str, object]:
        manager = getattr(self, "state_manager", None)
        if manager is None:
            manager = StoryStateManager(getattr(self, "memory", None), getattr(self, "embedding_client", None))
        return manager.build_planner_retrieval_intent(
            chapter_num=chapter_num,
            previous_summary=previous_summary,
            db_chars=db_chars,
            db_events=db_events,
            pending_conflicts=pending_conflicts,
        )

    @staticmethod
    def _rerank_semantic_hits(
        hits: List[Dict],
        focus_entities: List[str],
        focus_locations: List[str],
    ) -> List[Dict]:
        return StoryStateManager.rerank_semantic_hits(hits, focus_entities, focus_locations)

    def _semantic_context_for_planner(
        self,
        chapter_num: int,
        previous_summary: Optional[str],
        db_chars: List[tuple],
        db_events: List[tuple],
        pending_conflicts: List[tuple],
    ) -> str:
        manager = getattr(self, "state_manager", None)
        if manager is None:
            manager = StoryStateManager(getattr(self, "memory", None), getattr(self, "embedding_client", None))
        return manager.semantic_context_for_planner(
            chapter_num=chapter_num,
            previous_summary=previous_summary,
            db_chars=db_chars,
            db_events=db_events,
            pending_conflicts=pending_conflicts,
        )

    def _get_system_prompts(self) -> Dict[str, str]:
        return load_system_prompts(config.LANGUAGE, os.path.dirname(__file__))

    def _latest_world_bible_path(self) -> str:
        canonical = os.path.join(self.world_dir, "world_bible.md")
        return canonical

    def _generate_outline_with_discussion(
        self,
        phase_name: str,
        draft_prompt: str,
        revise_prompt_builder,
        rounds: int,
        output_filename: str,
        prompts: Dict[str, str],
    ) -> str:
        try:
            outline = self.planner_client.generate(
                prompt=draft_prompt,
                system_instruction=prompts["planner"],
            )
        except LLMClientError as e:
            raise RuntimeError(str(e)) from e
        outline = self._enforce_output_language(
            self.planner_client,
            "Planner",
            outline,
            prompts["planner"],
            world_building=True,
        )
        self._log_llm_interaction(
            role="Planner",
            phase=f"{phase_name} Draft",
            prompt=draft_prompt,
            response=outline,
            system_instruction=prompts["planner"],
            world_building=True,
        )

        outline_path = self._save_file(output_filename, outline, self.plot_dir)
        self._append_structured_discussion(
            phase_type="plot",
            role="Planner",
            prompt_text=draft_prompt,
            response_text=outline,
            round_index=0,
            decision=f"{phase_name}_draft_ready",
            needs_revision=None,
            artifact_paths=[outline_path],
        )

        for i in range(max(0, rounds)):
            critique_prompt = get_resource("prompt.world_bible_draft_critique", world_bible=outline)
            critique_prompt += f"\n\n{self._language_rule()}"
            try:
                critique = self.critic_client.generate(
                    prompt=critique_prompt,
                    system_instruction=prompts["critic"],
                )
            except LLMClientError as e:
                raise RuntimeError(str(e)) from e
            critique = self._enforce_output_language(
                self.critic_client,
                "Critic",
                critique,
                prompts["critic"],
                world_building=True,
            )
            self._log_llm_interaction(
                role="Critic",
                phase=f"{phase_name} Critique Round {i + 1}",
                prompt=critique_prompt,
                response=critique,
                system_instruction=prompts["critic"],
                world_building=True,
            )
            self._append_structured_discussion(
                phase_type="plot",
                role="Critic",
                prompt_text=critique_prompt,
                response_text=critique,
                round_index=i + 1,
                decision=f"{phase_name}_critique_generated",
                needs_revision=True,
                artifact_paths=[outline_path],
            )

            revise_prompt = revise_prompt_builder(outline, critique)
            revise_prompt += f"\n\n{self._language_rule()}"
            try:
                outline = self.planner_client.generate(
                    prompt=revise_prompt,
                    system_instruction=prompts["planner"],
                )
            except LLMClientError as e:
                raise RuntimeError(str(e)) from e
            outline = self._enforce_output_language(
                self.planner_client,
                "Planner",
                outline,
                prompts["planner"],
                world_building=True,
            )
            self._log_llm_interaction(
                role="Planner",
                phase=f"{phase_name} Revision Round {i + 1}",
                prompt=revise_prompt,
                response=outline,
                system_instruction=prompts["planner"],
                world_building=True,
            )
            outline_path = self._save_file(output_filename, outline, self.plot_dir)
            self._append_structured_discussion(
                phase_type="plot",
                role="Planner",
                prompt_text=revise_prompt,
                response_text=outline,
                round_index=i + 1,
                decision=f"{phase_name}_revision_applied",
                needs_revision=False,
                artifact_paths=[outline_path],
            )

        return outline_path

    def _sync_compact_archives(self):
        manager = getattr(self, "state_manager", None)
        if manager is None:
            manager = StoryStateManager(getattr(self, "memory", None), getattr(self, "embedding_client", None))
        archives = manager.sync_compact_archives()
        for filename, content in archives.items():
            self._save_file(filename, content, self.archives_dir)

    def _critic_review_chapter(self, chapter_num: int, guide_content: str, chapter_text: str, prompts: Dict[str, str]) -> str:
        review_task = get_resource("prompt.critic_review_task")
        output_format = get_resource("prompt.critic_output_format")
        contract_label = get_resource("label.contract")
        chapter_label = get_resource("label.chapter_text")
        critic_prompt = (
            f"{contract_label}:\n{guide_content}\n\n"
            f"{chapter_label}:\n{chapter_text}\n\n{review_task}\n{output_format}\n{self._language_rule()}"
        )
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

    def _refine_chapter_guide_with_discussion(self, chapter_num: int, guide: str, prompts: Dict[str, str]) -> str:
        rounds = max(0, config.CHAPTER_GUIDE_DISCUSSION_ROUNDS)
        current_guide = guide
        self._append_structured_discussion(
            phase_type="guide",
            role="Planner",
            prompt_text=f"initial_chapter_guide_chapter_{self._num3(chapter_num)}",
            response_text=current_guide,
            chapter_num=chapter_num,
            round_index=0,
            decision="guide_draft_ready",
            needs_revision=None,
            artifact_paths=[self.get_guide_path(chapter_num)],
        )
        for i in range(rounds):
            critique_prompt = get_resource("prompt.planner_critique", guide=current_guide)
            critique_prompt += f"\n\n{self._language_rule()}"
            critique = self.critic_client.generate(
                prompt=critique_prompt,
                system_instruction=prompts["critic"],
            )
            critique = self._enforce_output_language(
                self.critic_client, "Critic", critique, prompts["critic"], chapter_num=chapter_num
            )
            self._log_llm_interaction(
                role="Critic",
                phase=f"Chapter {self._num3(chapter_num)} Guide Critique Round {i + 1}",
                prompt=critique_prompt,
                response=critique,
                system_instruction=prompts["critic"],
                chapter_num=chapter_num,
            )
            self._append_structured_discussion(
                phase_type="guide",
                role="Critic",
                prompt_text=critique_prompt,
                response_text=critique,
                chapter_num=chapter_num,
                round_index=i + 1,
                decision="guide_critique_generated",
                needs_revision=True,
                artifact_paths=[self.get_guide_path(chapter_num)],
            )

            revise_prompt = get_resource("prompt.planner_revise", current_guide=current_guide, critique=critique)
            revise_prompt += f"\n\n{self._language_rule()}"
            current_guide = self.planner_client.generate(
                prompt=revise_prompt,
                system_instruction=prompts["planner"],
            )
            current_guide = self._enforce_output_language(
                self.planner_client, "Planner", current_guide, prompts["planner"], chapter_num=chapter_num
            )
            self._log_llm_interaction(
                role="Planner",
                phase=f"Chapter {self._num3(chapter_num)} Guide Revision Round {i + 1}",
                prompt=revise_prompt,
                response=current_guide,
                system_instruction=prompts["planner"],
                chapter_num=chapter_num,
            )
            self._append_structured_discussion(
                phase_type="guide",
                role="Planner",
                prompt_text=revise_prompt,
                response_text=current_guide,
                chapter_num=chapter_num,
                round_index=i + 1,
                decision="guide_revision_applied",
                needs_revision=False,
                artifact_paths=[self.get_guide_path(chapter_num)],
            )
        return current_guide

    @staticmethod
    def _needs_revision(review_text: str) -> bool:
        return needs_revision(review_text)

    def start_new_project(self, user_instruction: str) -> str:
        self.logger.info(f"Starting new project ({config.LANGUAGE}) with instruction: {user_instruction}")
        prompts = self._get_system_prompts()

        user_prompt_prefix = get_resource("label.user_request_prefix")
        task_instruction = get_resource("prompt.architect_task")
        architect_prompt = f"{user_prompt_prefix} {user_instruction}\n\n{task_instruction}\n\n{self._language_rule()}"

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

        rounds = max(0, config.WORLD_DISCUSSION_ROUNDS)
        for i in range(rounds):
            critique_prompt = get_resource("prompt.world_bible_draft_critique", world_bible=world_bible)
            critique_prompt += f"\n\n{self._language_rule()}"
            try:
                critique = self.critic_client.generate(
                    prompt=critique_prompt,
                    system_instruction=prompts["critic"],
                )
            except LLMClientError as e:
                raise RuntimeError(str(e)) from e
            critique = self._enforce_output_language(
                self.critic_client, "Critic", critique, prompts["critic"], world_building=True
            )
            self._log_llm_interaction(
                role="Critic",
                phase=f"World Building Critique Round {i + 1}",
                prompt=critique_prompt,
                response=critique,
                system_instruction=prompts["critic"],
                world_building=True,
            )
            self._append_structured_discussion(
                phase_type="world",
                role="Critic",
                prompt_text=critique_prompt,
                response_text=critique,
                round_index=i + 1,
                decision="world_bible_critique_generated",
                needs_revision=True,
                artifact_paths=[bible_path],
            )
            self._save_file("critique.md", critique, self.critiques_dir)

            revise_prompt = get_resource("prompt.world_bible_revise", world_bible=world_bible, critique=critique)
            revise_prompt += f"\n\n{self._language_rule()}"
            try:
                revised = self.architect_client.generate(
                    prompt=revise_prompt,
                    system_instruction=prompts["architect"],
                )
            except LLMClientError as e:
                raise RuntimeError(str(e)) from e
            revised = self._enforce_output_language(
                self.architect_client, "Architect", revised, prompts["architect"], world_building=True
            )
            self._log_llm_interaction(
                role="Architect",
                phase=f"World Building Revision Round {i + 1}",
                prompt=revise_prompt,
                response=revised,
                system_instruction=prompts["architect"],
                world_building=True,
            )

            world_bible = revised
            bible_path = self._save_file("world_bible.md", world_bible, self.world_dir)
            self._append_structured_discussion(
                phase_type="world",
                role="Architect",
                prompt_text=revise_prompt,
                response_text=revised,
                round_index=i + 1,
                decision="world_bible_revision_applied",
                needs_revision=False,
                artifact_paths=[bible_path],
            )

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

    def _review_and_revise_chapter(self, chapter_num: int, guide: str, chapter_text: str, prompts: Dict[str, str]) -> Tuple[str, str]:
        current_text = chapter_text
        latest_review = ""
        rounds = max(0, config.CHAPTER_TEXT_DISCUSSION_ROUNDS)
        self._append_structured_discussion(
            phase_type="chapter_text",
            role="Writer",
            prompt_text=f"chapter_{self._num3(chapter_num)}_draft_ready",
            response_text=current_text,
            chapter_num=chapter_num,
            round_index=0,
            decision="chapter_draft_ready",
            needs_revision=None,
            artifact_paths=[self.get_chapter_path(chapter_num)],
        )
        for round_idx in range(rounds):
            review = self._critic_review_chapter(chapter_num, guide, current_text, prompts)
            latest_review = review
            if not self._needs_revision(review):
                self._append_structured_discussion(
                    phase_type="chapter_text",
                    role="Critic",
                    prompt_text=f"chapter_{self._num3(chapter_num)}_review",
                    response_text=review,
                    chapter_num=chapter_num,
                    round_index=round_idx + 1,
                    decision="review_pass_no_revision",
                    needs_revision=False,
                    artifact_paths=[self.get_chapter_path(chapter_num)],
                )
                break
            self._append_structured_discussion(
                phase_type="chapter_text",
                role="Critic",
                prompt_text=f"chapter_{self._num3(chapter_num)}_review",
                response_text=review,
                chapter_num=chapter_num,
                round_index=round_idx + 1,
                decision="review_requests_revision",
                needs_revision=True,
                artifact_paths=[self.get_chapter_path(chapter_num)],
            )

            revise_instruction = get_resource("prompt.writer_revise")
            revise_instruction += f"\n{self._language_rule()}"
            contract_label = get_resource("label.contract")
            chapter_label = get_resource("label.current_chapter")
            critique_label = get_resource("label.critique")
            revise_prompt = (
                f"{contract_label}:\n{guide}\n\n"
                f"{chapter_label}:\n{current_text}\n\n"
                f"{critique_label}:\n{review}\n\n{revise_instruction}"
            )
            try:
                revised = self.writer_client.generate(
                    prompt=revise_prompt,
                    system_instruction=prompts["writer"],
                )
            except LLMClientError as e:
                raise RuntimeError(str(e)) from e
            revised = self._enforce_output_language(
                self.writer_client, "Writer", revised, prompts["writer"], chapter_num=chapter_num
            )
            self._log_llm_interaction(
                role="Writer",
                phase=f"Chapter {self._num3(chapter_num)} Revision Round {round_idx + 1}",
                prompt=revise_prompt,
                response=revised,
                system_instruction=prompts["writer"],
                chapter_num=chapter_num,
            )
            current_text = revised
            self._save_file(
                f"chapter_{self._num3(chapter_num)}_revision_round_{self._num3(round_idx + 1)}.md",
                revised,
                self.revisions_dir,
            )
            self._save_file(f"chapter_{self._num3(chapter_num)}.md", current_text, self.chapters_dir)
            self._append_structured_discussion(
                phase_type="chapter_text",
                role="Writer",
                prompt_text=revise_prompt,
                response_text=current_text,
                chapter_num=chapter_num,
                round_index=round_idx + 1,
                decision="chapter_revision_applied",
                needs_revision=True,
                artifact_paths=[
                    self.get_chapter_path(chapter_num),
                    os.path.join(
                        self.revisions_dir,
                        f"chapter_{self._num3(chapter_num)}_revision_round_{self._num3(round_idx + 1)}.md",
                    ),
                ],
            )
        return current_text, latest_review

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

    def review_revise_and_scan(self, chapter_num: int, guide_content: str, chapter_text: str) -> str:
        prompts = self._get_system_prompts()
        revised_text, _ = self._review_and_revise_chapter(chapter_num, guide_content, chapter_text, prompts)
        if revised_text != chapter_text:
            self._save_file(f"chapter_{self._num3(chapter_num)}.md", revised_text, self.chapters_dir)
        return self.scan_chapter(chapter_num)

    def list_pending_conflicts(self, limit: int = 50, level: Optional[str] = None) -> List[tuple]:
        return self.memory.get_pending_conflicts(limit=limit, blocking_level=level)

    def list_pending_conflicts_detailed(self, limit: int = 50, level: Optional[str] = None) -> List[Dict]:
        return self.memory.get_pending_conflict_diagnostics(limit=limit, blocking_level=level)

    def list_pending_conflict_triage(self, limit: int = 50, level: Optional[str] = None) -> List[Dict]:
        return self.memory.get_pending_conflict_triage(limit=limit, blocking_level=level)

    def resolve_pending_conflict(self, conflict_id: int, action: str, note: str = "") -> bool:
        return self.memory.resolve_conflict(conflict_id=conflict_id, action=action, resolver_note=note)

    def batch_triage_non_blocking(self, limit: int = 50, note: str = "batch triage keep_existing") -> int:
        rows = self.memory.get_pending_conflicts(limit=limit, blocking_level=self.memory.NON_BLOCKING)
        resolved = 0
        for row in rows:
            conflict_id = row[0]
            ok = self.memory.resolve_conflict(
                conflict_id=conflict_id,
                action="keep_existing",
                resolver_note=note,
                source="batch_triage",
            )
            if ok:
                resolved += 1
        return resolved

    def list_failed_chapter_commits(self, limit: int = 20) -> List[tuple]:
        return self.memory.get_failed_chapter_commits(limit=limit)

    def replay_chapter_commit(self, commit_id: str) -> bool:
        row = self.memory.get_chapter_commit(commit_id)
        if not row:
            return False
        status = row[4]
        payload_json = row[3]
        chapter_num = row[1]
        if status == "COMPLETED":
            return True
        if not payload_json:
            self.memory.finalize_chapter_commit(
                commit_id,
                status="FAILED",
                conflicts_count=0,
                error_message="Replay skipped: empty payload_json.",
            )
            return False
        try:
            payload = json.loads(payload_json)
        except json.JSONDecodeError:
            self.memory.finalize_chapter_commit(
                commit_id,
                status="FAILED",
                conflicts_count=0,
                error_message="Replay failed: payload_json decode error.",
            )
            return False
        try:
            self.memory.begin_batch()
            conflicts = self._apply_fact_payload(
                payload,
                summary_lines=None,
                source="replay_commit",
                chapter_num=chapter_num,
                source_commit_id=commit_id,
                intent_tag="replay_commit",
            )
            self.memory.end_batch(success=True)
            self.memory.finalize_chapter_commit(
                commit_id,
                status="COMPLETED",
                conflicts_count=conflicts,
                error_message="",
            )
            self._sync_compact_archives()
            return True
        except Exception as e:
            self.memory.end_batch(success=False)
            self.memory.finalize_chapter_commit(
                commit_id,
                status="FAILED",
                conflicts_count=0,
                error_message=f"Replay failed: {e}",
            )
            return False

    def rebuild_vector_index(self) -> Dict[str, int]:
        return self.memory.rebuild_vector_index_from_metadata(self.embedding_client.get_embedding)
