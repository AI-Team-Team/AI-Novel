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

# Import stage mixins
from workflow_components.project_mixin import ProjectWorkflowMixin
from workflow_components.planning_mixin import PlanningWorkflowMixin
from workflow_components.writing_mixin import WritingWorkflowMixin
from workflow_components.scanning_mixin import ScanningWorkflowMixin
from workflow_components.conflict_resolver import ConflictResolverWorkflowMixin
from workflow_components.autonomy_mixin import AutonomyWorkflowMixin

class WorkflowManager(
    WorkflowResumeMixin,
    WorkflowIOMixin,
    WorkflowLanguageMixin,
    ProjectWorkflowMixin,
    PlanningWorkflowMixin,
    WritingWorkflowMixin,
    ScanningWorkflowMixin,
    ConflictResolverWorkflowMixin,
    AutonomyWorkflowMixin
):
    def __init__(self):
        self.logger = logging.getLogger("WorkflowManager")
        logging.basicConfig(level=logging.INFO)

        self.architect_client = LLMClient(model_config=config.ARCHITECT_CONFIG)
        self.planner_client = LLMClient(model_config=config.PLANNER_CONFIG)
        self.writer_client = LLMClient(model_config=config.WRITER_CONFIG)
        self.critic_client = LLMClient(model_config=config.CRITIC_CONFIG)
        self.scanner_client = LLMClient(model_config=config.SCANNER_CONFIG)
        # Shared embedding client
        self.embedding_client = LLMClient(model_config=config.EMBEDDING_CONFIG, enable_embedding=True)

        self.memory = MemoryManager(config.DB_PATH, config.FAISS_INDEX_PATH)
        
        self.ai_resolve_conflicts = False
        self.in_auto_mode = False
        
        self.initialize_autonomy()
        self.att_manager.discussion_logger = self._discussion_logger()
        
        # Setup get_embedding proxy wrapper for validation
        original_get_embedding = self.embedding_client.get_embedding
        self.embedding_client._fingerprint_verified = False
        self.embedding_client._bypass_all_checks = False
        self.embedding_client._original_get_embedding = original_get_embedding

        def wrapped_get_embedding(text: str) -> Optional[list]:
            # A. Bypass all validations during rebuild/migration if flag is set
            if getattr(self.embedding_client, "_bypass_all_checks", False):
                return original_get_embedding(text)

            # B. Lazy, single-run validation of the Hello World vector fingerprint
            if not getattr(self.embedding_client, "_fingerprint_verified", False):
                # Mark as verified immediately to avoid recursive infinite loops
                self.embedding_client._fingerprint_verified = True
                
                hw_vector = original_get_embedding("Hello World!")
                if hw_vector:
                    hw_dim = len(hw_vector)
                    
                    # 1. Fetch any existing fingerprint and dimension from SQLite schema_meta
                    existing_fp_json = self.memory.get_schema_meta("embedding_fingerprint")
                    existing_dim_str = self.memory.get_schema_meta("embedding_dim")
                    
                    # 2. Check fingerprint match if exists
                    if existing_fp_json:
                        try:
                            existing_fp = json.loads(existing_fp_json)
                            import numpy as np
                            if not np.allclose(hw_vector, existing_fp, atol=1e-5):
                                raise RuntimeError(
                                    "Embedding Model Mismatch: The registered embedding model does not match the model used to build the existing database. "
                                    "To switch models, please run --rebuild-vectors."
                                )
                        except (json.JSONDecodeError, TypeError, ValueError) as e:
                            self.logger.warning(f"Could not parse stored embedding fingerprint: {e}")
                    else:
                        # Initialize SQLite schema_meta fingerprint & dim
                        self.memory.set_schema_meta("embedding_fingerprint", json.dumps(hw_vector))
                        self.memory.set_schema_meta("embedding_dim", str(hw_dim))
                        self.memory.embedding_dim = hw_dim
                        
                    # 3. Check dimension match if exists
                    if existing_dim_str:
                        try:
                            existing_dim = int(existing_dim_str)
                            if hw_dim != existing_dim:
                                raise RuntimeError(
                                    f"Embedding Dimension Mismatch: expected {existing_dim}, got {hw_dim}. "
                                    "Please run --rebuild-vectors to safely migrate existing vector data."
                                )
                        except (ValueError, TypeError):
                            pass
                    else:
                        self.memory.set_schema_meta("embedding_dim", str(hw_dim))
                        self.memory.embedding_dim = hw_dim

            # C. Fetch vector for target text
            vector = original_get_embedding(text)
            
            # D. Verify dimension on EVERY returned vector (local, inexpensive check)
            if vector:
                expected_dim = None
                if self.memory.index is not None:
                    expected_dim = self.memory.index.d
                else:
                    db_dim = self.memory.get_schema_meta("embedding_dim")
                    if db_dim:
                        try:
                            expected_dim = int(db_dim)
                        except (ValueError, TypeError):
                            pass
                if expected_dim is None:
                    expected_dim = self.memory.embedding_dim
                
                if expected_dim is not None and len(vector) != expected_dim:
                    raise RuntimeError(
                        f"Embedding dimension mismatch: expected {expected_dim}, got {len(vector)}. "
                        "Please run --rebuild-vectors to safely migrate existing vector data."
                    )
            return vector

        self.embedding_client.get_embedding = wrapped_get_embedding

        self.state_manager = StoryStateManager(self.memory, self.embedding_client, tier_3_search_limit=config.TIER_3_SEARCH_LIMIT)

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

        # Check if FAISS database file is missing or corrupted and rebuild it immediately
        try:
            import faiss
        except ImportError:
            faiss = None

        if self.memory.index is None and faiss is not None:
            # Check if there are active records in SQLite vector_metadata
            try:
                self.memory.cursor.execute("SELECT COUNT(*) FROM vector_metadata WHERE is_deleted = 0")
                row = self.memory.cursor.fetchone()
                has_records = row and row[0] > 0
            except Exception:
                has_records = False

            if has_records:
                self.logger.warning("FAISS database file is missing or corrupted but existing metadata was found. Automatically rebuilding vector index...")
                try:
                    self.rebuild_vector_index()
                except Exception as e:
                    self.logger.error(f"Failed to automatically rebuild vector index: {e}")

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
        blocking_pending = self.memory.get_pending_blocking_conflict_count()
        
        # Trigger AI debate if option/auto mode enabled, there are blocking conflicts, and autonomy is enabled
        if blocking_pending > 0 and (getattr(self, "ai_resolve_conflicts", False) or getattr(self, "in_auto_mode", False)) and getattr(config, "ENABLE_AUTONOMY_SUITE", True):
            self.logger.info(f"[AUTO] Conflict(s) detected in {stage}. Spawning AI Debate Panel...")
            
            # Fetch all pending blocking conflicts
            rows = self.memory.get_pending_conflicts(limit=50, blocking_only=True)
            for row in rows:
                conflict_id = row[0]
                entity_type = row[1]
                entity_key = row[2]
                conflict_type = row[3]
                
                # Run Multi-Agent debate resolution
                resolved = self.ai_debate_resolve_conflict(conflict_id)
                if resolved:
                    self.logger.info(f"[AUTO] Conflict #{conflict_id} successfully resolved by AI panel.")
                else:
                    self.logger.error(f"[AUTO] Conflict #{conflict_id} debate ended in a STANDOFF.")
                    raise RuntimeError(
                        f"Conflict Resolution Standoff: Multi-Agent Debate Panel failed to agree on a resolution "
                        f"for BLOCKING conflict #{conflict_id} ({conflict_type} for {entity_type}:{entity_key}). "
                        f"Fail-Fast triggered. Execution halted."
                    )
            
            # Recalculate blocking conflicts after AI debate
            blocking_pending = self.memory.get_pending_blocking_conflict_count()

        # Fallback to standard blocking/auto logic if not in auto-debate mode
        mode = (getattr(config, "BLOCKING_CONFLICT_MODE", "manual_block") or "manual_block").lower()
        if mode == "auto_keep_existing":
            self.state_manager.auto_resolve_pending_conflicts()
            blocking_pending = self.memory.get_pending_blocking_conflict_count()
        elif mode == "manual_block":
            pass
        else:
            raise RuntimeError(
                f"Invalid BLOCKING_CONFLICT_MODE={mode}. "
                "Expected one of: auto_keep_existing, manual_block."
            )

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
            manager = StoryStateManager(
                getattr(self, "memory", None),
                getattr(self, "embedding_client", None),
                tier_3_search_limit=config.TIER_3_SEARCH_LIMIT,
            )
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
            manager = StoryStateManager(
                getattr(self, "memory", None),
                getattr(self, "embedding_client", None),
                tier_3_search_limit=config.TIER_3_SEARCH_LIMIT,
            )
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
            manager = StoryStateManager(
                getattr(self, "memory", None),
                getattr(self, "embedding_client", None),
                tier_3_search_limit=config.TIER_3_SEARCH_LIMIT,
            )
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

    def _sync_compact_archives(self):
        manager = getattr(self, "state_manager", None)
        if manager is None:
            manager = StoryStateManager(
                getattr(self, "memory", None),
                getattr(self, "embedding_client", None),
                tier_3_search_limit=config.TIER_3_SEARCH_LIMIT,
            )
        archives = manager.sync_compact_archives()
        for filename, content in archives.items():
            self._save_file(filename, content, self.archives_dir)



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
        # Bypass all checks during rebuild
        self.embedding_client._bypass_all_checks = True
        try:
            stats = self.memory.rebuild_vector_index_from_metadata(self.embedding_client.get_embedding)
            
            # Post-rebuild: overwrite fingerprint and dimension in SQLite schema_meta with the new model's values
            original_get_embedding = getattr(self.embedding_client, "_original_get_embedding", self.embedding_client.get_embedding)
            hw_vector = original_get_embedding("Hello World!")
            if hw_vector:
                new_dim = len(hw_vector)
                self.memory.set_schema_meta("embedding_fingerprint", json.dumps(hw_vector))
                self.memory.set_schema_meta("embedding_dim", str(new_dim))
                self.memory.embedding_dim = new_dim
                
            return stats
        finally:
            self.embedding_client._bypass_all_checks = False
            self.embedding_client._fingerprint_verified = True # Re-mark as verified since we just updated

    def run_with_dashboard(self, func, *args, **kwargs):
        """Runs the specified workflow function inside the ConsoleDashboard context."""
        from rich.live import Live
        from utils.dashboard import ConsoleDashboard
        import time

        dashboard = ConsoleDashboard(self)
        self.att_manager.dashboard = dashboard

        # Set active stage based on function name or custom attribute
        func_name = func.__name__
        if func_name == "start_new_project":
            dashboard.active_stage = "Initializing World Bible & Outlines"
        elif func_name == "write_novel_automatically":
            dashboard.active_stage = "Writing Novel Automatically"
        else:
            dashboard.active_stage = f"Executing {func_name}"

        dashboard.start_capture()
        try:
            with Live(dashboard.render(), screen=True, auto_refresh=True, refresh_per_second=4) as live:
                dashboard.set_live(live)
                result = func(*args, **kwargs)
                dashboard.active_stage = "Finished successfully"
                dashboard.refresh()
                time.sleep(1) # Brief pause so user can see completion
                return result
        except Exception as e:
            dashboard.active_stage = f"Error: {e}"
            dashboard.refresh()
            self.logger.exception(f"Error in dashboard execution: {e}")
            raise
        finally:
            dashboard.stop_capture()
            self.att_manager.dashboard = None
