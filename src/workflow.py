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
from workflow_components.resources import get_ai_resource, get_message
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
                                raise RuntimeError(get_message("runtime.vector_model_mismatch"))
                        except (json.JSONDecodeError, TypeError, ValueError) as e:
                            self.logger.warning(get_message("runtime.embedding_fingerprint_parse", error=e))
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
                                raise RuntimeError(get_message("runtime.vector_dim_mismatch", expected=existing_dim, actual=hw_dim))
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
                    raise RuntimeError(get_message("runtime.vector_dim_mismatch", expected=expected_dim, actual=len(vector)))
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

        health = self.memory.reconcile_vector_store()
        if not isinstance(health, dict):
            self.memory.cursor.execute(
                "SELECT COUNT(*) FROM vector_metadata WHERE is_deleted = 0"
            )
            row = self.memory.cursor.fetchone()
            active_count = int(row[0]) if row else 0
            health = {
                "healthy": self.memory.index is not None or active_count == 0,
                "requires_rebuild": self.memory.index is None and active_count > 0,
                "active_metadata_count": active_count,
                "index_total": 0,
                "load_error": None,
                "reasons": ["active metadata exists without a loaded index"],
            }
        self.vector_health = health
        if faiss is not None and self.vector_health["requires_rebuild"]:
            self.logger.warning(get_message("runtime.faiss_reconcile", reasons="; ".join(self.vector_health["reasons"])))
            try:
                self.rebuild_vector_index()
                self.vector_health = self.memory.reconcile_vector_store()
            except Exception as e:
                self.logger.error(get_message("runtime.faiss_rebuild_failed", error=e))

    def close(self) -> None:
        """Flush ATT state and close the story database deterministically."""

        att_error = None
        try:
            self.close_autonomy()
        except Exception as exc:
            att_error = exc
            self.logger.warning(get_message("runtime.att_shutdown_failed", error=exc))
        finally:
            self.memory.close()
        if att_error is not None:
            raise att_error

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()
        return False

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
        return get_message("template.novel_overview")

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
            self.logger.info(get_message("runtime.overview_created", path=overview_path))
        return overview_path

    def load_novel_overview(self) -> str:
        overview_path = self.get_overview_path()
        if not os.path.exists(overview_path):
            raise RuntimeError(get_message("runtime.overview_missing", path=overview_path))

        with open(overview_path, "r", encoding="utf-8") as f:
            overview = f.read().strip()

        if not overview or overview == self._default_overview_template().strip():
            raise RuntimeError(get_message("runtime.overview_empty", path=overview_path))
        return overview

    def _enforce_conflict_free_state(self, stage: str):
        blocking_pending = self.memory.get_pending_blocking_conflict_count()
        
        # Trigger AI debate if option/auto mode enabled, there are blocking conflicts, and autonomy is enabled
        if blocking_pending > 0 and (getattr(self, "ai_resolve_conflicts", False) or getattr(self, "in_auto_mode", False)) and getattr(config, "ENABLE_AUTONOMY_SUITE", True):
            self.logger.info(get_message("runtime.auto_conflicts_detected", stage=stage))
            
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
                    self.logger.info(get_message("runtime.auto_conflict_resolved", conflict_id=conflict_id))
                else:
                    self.logger.error(get_message("runtime.auto_conflict_standoff", conflict_id=conflict_id))
                    raise RuntimeError(get_message("runtime.conflict_standoff", conflict_id=conflict_id))
            
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
            raise RuntimeError(get_message("runtime.invalid_conflict_mode", mode=mode))

        total_pending = self.memory.get_pending_conflict_count()
        if blocking_pending > 0:
            raise RuntimeError(get_message("runtime.blocked_conflicts", stage=stage, count=blocking_pending))

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

    def batch_triage_non_blocking(self, limit: int = 50, note: Optional[str] = None) -> int:
        note = note or get_ai_resource("runtime.batch_triage_note")
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

    def preview_failed_chapter_commits(self, limit: int = 50) -> List[Dict]:
        """Validate failed commit payloads without mutating database state."""

        preview: List[Dict] = []
        for row in self.memory.get_failed_chapter_commits(limit=limit):
            commit_id, chapter_num, source, status = row[:4]
            full_row = self.memory.get_chapter_commit(commit_id)
            errors: List[str] = []
            payload = None
            if not full_row or not full_row[3]:
                errors.append(get_message("status.replay.preview_empty"))
            else:
                try:
                    payload = json.loads(full_row[3])
                except json.JSONDecodeError as exc:
                    errors.append(get_message("status.replay.preview_decode", error=exc))
            if payload is not None:
                errors.extend(validate_fact_payload(payload))
            preview.append(
                {
                    "commit_id": commit_id,
                    "chapter_num": chapter_num,
                    "source": source,
                    "status_before": status,
                    "can_replay": not errors,
                    "validation_errors": errors,
                    "previous_replay_count": row[6],
                    "previous_error": row[5] or "",
                }
            )
        return preview

    def bulk_replay_failed_commits(
        self,
        *,
        limit: int = 50,
        dry_run: bool = False,
        max_attempts: int = 3,
        retry_policy: str = "continue",
    ) -> Dict:
        """Preview or replay failed commits with bounded retries and reports."""

        if limit < 1:
            raise ValueError(get_message("validation.replay_limit"))
        if max_attempts < 1:
            raise ValueError(get_message("validation.replay_attempts"))
        policy = retry_policy.strip().lower()
        if policy not in {"continue", "stop"}:
            raise ValueError(get_message("validation.replay_policy"))

        reports = self.preview_failed_chapter_commits(limit=limit)
        result = {
            "dry_run": dry_run,
            "retry_policy": policy,
            "max_attempts": max_attempts,
            "requested_limit": limit,
            "total": len(reports),
            "succeeded": 0,
            "failed": 0,
            "skipped": 0,
            "commits": reports,
        }
        if dry_run:
            for report in reports:
                report["attempts"] = 0
                report["status_after"] = report["status_before"]
                report["error_after"] = report["previous_error"]
                report["outcome"] = (
                    "preview_ready" if report["can_replay"] else "preview_invalid"
                )
                if not report["can_replay"]:
                    result["skipped"] += 1
            return result

        stopped = False
        for report in reports:
            report["attempts"] = 0
            if stopped:
                report["status_after"] = report["status_before"]
                report["error_after"] = report["previous_error"]
                report["outcome"] = "not_attempted_after_stop"
                result["skipped"] += 1
                continue
            if not report["can_replay"]:
                report["status_after"] = report["status_before"]
                report["error_after"] = report["previous_error"]
                report["outcome"] = "skipped_invalid_payload"
                result["skipped"] += 1
                if policy == "stop":
                    stopped = True
                continue

            success = False
            for attempt in range(1, max_attempts + 1):
                report["attempts"] = attempt
                if self.replay_chapter_commit(report["commit_id"]):
                    success = True
                    break
            current = self.memory.get_chapter_commit(report["commit_id"])
            report["status_after"] = current[4] if current else "MISSING"
            report["error_after"] = current[6] if current else get_message("status.commit_disappeared")
            report["outcome"] = "replayed" if success else "failed"
            if success:
                result["succeeded"] += 1
            else:
                result["failed"] += 1
                if policy == "stop":
                    stopped = True
        return result

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
                error_message=get_message("status.replay.empty_payload"),
            )
            return False
        try:
            payload = json.loads(payload_json)
        except json.JSONDecodeError:
            self.memory.finalize_chapter_commit(
                commit_id,
                status="FAILED",
                conflicts_count=0,
                error_message=get_message("status.replay.decode_error"),
            )
            return False
        validation_errors = validate_fact_payload(payload)
        if validation_errors:
            self.memory.finalize_chapter_commit(
                commit_id,
                status="FAILED",
                conflicts_count=0,
                error_message=get_message(
                    "status.replay.schema_error", errors="; ".join(validation_errors)
                ),
            )
            return False
        try:
            self._audit_database_batch(
                "commit_replay",
                "replay_chapter_commit",
                payload,
                chapter_num,
            )
            self.memory.begin_batch()
            conflicts = self._apply_fact_payload(
                payload,
                summary_lines=None,
                source="replay_commit",
                chapter_num=chapter_num,
                source_commit_id=commit_id,
                intent_tag="replay_commit",
            )
            self.memory.finalize_chapter_commit(
                commit_id,
                status="COMPLETED",
                conflicts_count=conflicts,
                error_message="",
            )
            self.memory.end_batch(success=True)
            self._sync_compact_archives()
            return True
        except Exception as e:
            self.memory.end_batch(success=False)
            self.memory.finalize_chapter_commit(
                commit_id,
                status="FAILED",
                conflicts_count=0,
                error_message=get_message("status.replay.execution_error", error=e),
            )
            return False

    def rebuild_vector_index(self) -> Dict[str, object]:
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
        dashboard.running = True

        # Set active stage based on function name or custom attribute
        func_name = func.__name__
        if func_name == "start_new_project":
            dashboard.active_stage = get_message("dashboard.initializing")
        elif func_name == "write_novel_automatically":
            dashboard.active_stage = get_message("dashboard.auto_writing")
        else:
            dashboard.active_stage = get_message("dashboard.executing", name=func_name)

        dashboard.start_capture()
        try:
            with Live(dashboard.render(), screen=True, auto_refresh=True, refresh_per_second=4) as live:
                dashboard.set_live(live)
                result = func(*args, **kwargs)
                dashboard.running = False
                dashboard.active_stage = get_message("dashboard.finished")
                dashboard.refresh()
                time.sleep(1) # Brief pause so user can see completion
                return result
        except Exception as e:
            dashboard.running = False
            dashboard.active_stage = get_message("dashboard.error", error=e)
            dashboard.refresh()
            self.logger.exception(get_message("dashboard.error", error=e))
            raise
        finally:
            dashboard.stop_capture()
            self.att_manager.dashboard = None
