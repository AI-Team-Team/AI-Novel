import glob
import json
import os
import re
import time
from typing import Dict, List, Optional, Set, Tuple

import config


from workflow_components.resources import get_resource

class WorkflowResumeMixin:
    def _load_previous_summary(self, chapter_num: int) -> Optional[str]:
        summary_path = self._facts_summary_path(chapter_num)
        if os.path.exists(summary_path):
            with open(summary_path, "r", encoding="utf-8") as f:
                return f.read()

        json_path = self._facts_json_path(chapter_num)
        if not os.path.exists(json_path):
            return None

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            lines = [get_resource("label.chapter_summary_prefix", chapter_num=chapter_num)]
            labels = [
                ("new_characters", get_resource("label.new_character")),
                ("updated_characters", get_resource("label.updated_character")),
                ("new_rules", get_resource("label.new_rule")),
                ("events", get_resource("label.event")),
            ]
            for key, label in labels:
                for item in data.get(key, []):
                    name = item.get("name") or item.get("content") or item.get("event_name")
                    if name:
                        lines.append(f"{label}: {name}")
            return "\n".join(lines)
        except Exception:
            return None

    def _chapter_scan_completed(self, chapter_num: int) -> bool:
        return bool(self._load_previous_summary(chapter_num))

    @staticmethod
    def _file_non_empty(path: str) -> bool:
        return os.path.exists(path) and os.path.getsize(path) > 0

    @staticmethod
    def _safe_remove(path: str):
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass

    def _validate_discussion_index_integrity(self) -> bool:
        path = self._discussion_index_path()
        if not os.path.exists(path):
            return True
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    text = line.strip()
                    if not text:
                        continue
                    json.loads(text)
            return True
        except Exception:
            self.logger.warning(
                "Detected corrupted discussion index JSONL. Discarding file: %s",
                path,
            )
            self._safe_remove(path)
            return False

    @staticmethod
    def _extract_chapter_num_from_filename(path: str) -> Optional[int]:
        name = os.path.basename(path)
        m = re.search(r"chapter_(\d{3})", name)
        if not m:
            return None
        try:
            return int(m.group(1))
        except ValueError:
            return None

    @staticmethod
    def _is_global_critical_generated_file(path: str) -> bool:
        base = os.path.basename(path)
        return base in {"world_bible.md", "plot_outline.md", "detailed_plot_outline.md"}

    def _validate_generated_file(self, path: str) -> Tuple[bool, str]:
        _, ext = os.path.splitext(path.lower())
        base = os.path.basename(path)

        if ext in {".db", ".faiss"}:
            return True, "binary_ignored"

        if not os.path.exists(path):
            return False, "missing"

        if ext == ".jsonl":
            try:
                with open(path, "r", encoding="utf-8") as f:
                    has_line = False
                    for line in f:
                        text = line.strip()
                        if not text:
                            continue
                        has_line = True
                        json.loads(text)
                if base == "discussion_index.jsonl" and not has_line:
                    return False, "jsonl_empty"
                return True, "ok"
            except Exception:
                return False, "jsonl_invalid"

        if ext == ".json":
            if os.path.getsize(path) <= 0:
                return False, "json_empty"
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if base.startswith("chapter_") and base.endswith("_facts.json"):
                    if self._validate_fact_payload(data):
                        return False, "facts_json_schema_invalid"
                return True, "ok"
            except Exception:
                return False, "json_invalid"

        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except Exception:
            return False, "text_decode_invalid"

        if ext in {".md", ".txt", ".log"}:
            chapter_num = self._extract_chapter_num_from_filename(path)
            if chapter_num is not None and not text.strip():
                return False, "chapter_text_empty"
            if self._is_global_critical_generated_file(path) and not text.strip():
                return False, "global_text_empty"
            if base == "discussion_index.jsonl":
                return False, "jsonl_wrong_type"
        return True, "ok"

    def _collect_generated_runtime_files(self) -> List[str]:
        files: List[str] = []
        roots = [
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
        ]
        for root in roots:
            if not os.path.exists(root):
                continue
            for entry_root, _, names in os.walk(root):
                for name in names:
                    files.append(os.path.join(entry_root, name))
        return files

    def _validate_runtime_artifacts_integrity(self) -> Tuple[Set[int], List[Tuple[str, str]]]:
        invalid_chapters: Set[int] = set()
        invalid_global: List[Tuple[str, str]] = []
        for path in self._collect_generated_runtime_files():
            ok, reason = self._validate_generated_file(path)
            if ok:
                continue
            chapter_num = self._extract_chapter_num_from_filename(path)
            if chapter_num is not None:
                invalid_chapters.add(chapter_num)
                continue
            invalid_global.append((path, reason))
        return invalid_chapters, invalid_global

    def _handle_invalid_global_generated_artifact(self, path: str, reason: str):
        base = os.path.basename(path)
        self.logger.warning(
            "Discarding invalid global generated artifact: %s (reason=%s)",
            path,
            reason,
        )
        self._safe_remove(path)
        if base == "discussion_index.jsonl":
            return
        if base in {"characters_compact.md", "world_rules_compact.md"}:
            self._sync_compact_archives()
            return
        if self._is_global_critical_generated_file(path):
            raise RuntimeError(
                f"Critical generated artifact invalid and discarded: {path}. "
                "Please regenerate project frames (run --start again) before --auto resume."
            )

    def _chapter_related_paths(self, chapter_num: int) -> Dict[str, List[str]]:
        suffix = self._num3(chapter_num)
        files: List[str] = [
            self.get_guide_path(chapter_num),
            self.get_chapter_path(chapter_num),
            os.path.join(self.facts_dir, f"chapter_{suffix}_facts.json"),
            os.path.join(self.facts_dir, f"chapter_{suffix}_facts_summary.md"),
            os.path.join(self.facts_dir, f"chapter_{suffix}_facts_raw.txt"),
            os.path.join(self.facts_dir, f"chapter_{suffix}_facts_invalid.json"),
            os.path.join(self.reviews_dir, f"chapter_{suffix}_review.md"),
            os.path.join(self.discussions_dir, f"chapter_{suffix}_guide_discussion.md"),
            os.path.join(self.discussions_dir, f"chapter_{suffix}_text_discussion.md"),
        ]
        revision_pattern = os.path.join(self.revisions_dir, f"chapter_{suffix}_revision_round_*.md")
        return {
            "files": files,
            "revision_glob": [revision_pattern],
        }

    def _chapter_has_any_artifacts(self, chapter_num: int) -> bool:
        paths = self._chapter_related_paths(chapter_num)
        if any(os.path.exists(path) for path in paths["files"]):
            return True
        for pattern in paths["revision_glob"]:
            if glob.glob(pattern):
                return True
        commits = self.memory.get_chapter_commits(chapter_num=chapter_num, source="scan_chapter", limit=1)
        return bool(commits)

    def _is_valid_scanner_payload_file(self, path: str) -> bool:
        if not self._file_non_empty(path):
            return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return len(self._validate_fact_payload(data)) == 0
        except Exception:
            return False

    def _validate_chapter_completion_integrity(self, chapter_num: int) -> Tuple[bool, str]:
        chapter_path = self.get_chapter_path(chapter_num)
        summary_path = self._facts_summary_path(chapter_num)
        facts_path = self._facts_json_path(chapter_num)

        if not self._file_non_empty(chapter_path):
            return False, "chapter_text_missing_or_empty"
        if not self._file_non_empty(summary_path):
            return False, "facts_summary_missing_or_empty"
        if not self._is_valid_scanner_payload_file(facts_path):
            return False, "facts_json_missing_or_invalid"

        commits = self.memory.get_chapter_commits(chapter_num=chapter_num, source="scan_chapter", limit=10)
        if not commits:
            return False, "scan_commit_missing"
        latest = commits[0]
        latest_status = str(latest[4] or "")
        if latest_status != "COMPLETED":
            return False, f"latest_scan_commit_not_completed:{latest_status}"

        payload_json = latest[3]
        if not payload_json:
            return False, "completed_scan_commit_missing_payload"
        try:
            payload_data = json.loads(payload_json)
        except Exception:
            return False, "completed_scan_commit_payload_invalid_json"
        payload_errors = self._validate_fact_payload(payload_data)
        if payload_errors:
            return False, "completed_scan_commit_payload_schema_invalid"

        return True, "ok"

    def _discard_chapter_artifacts(self, chapter_num: int, reason: str):
        suffix = self._num3(chapter_num)
        self.logger.warning(
            "Discarding chapter artifacts and incomplete commits for Chapter %s (reason=%s).",
            suffix,
            reason,
        )
        paths = self._chapter_related_paths(chapter_num)
        for path in paths["files"]:
            self._safe_remove(path)
        for pattern in paths["revision_glob"]:
            for path in glob.glob(pattern):
                self._safe_remove(path)
        self.memory.purge_incomplete_chapter_commits(chapter_num=chapter_num, source="scan_chapter")

    def run_continuous_loop(self, start_chapter: int, count: int):
        self.in_auto_mode = True
        try:
            self._run_continuous_loop_impl(start_chapter, count)
        finally:
            self.in_auto_mode = False

    def _run_continuous_loop_impl(self, start_chapter: int, count: int):
        self.logger.info(f"Starting continuous generation from Chapter {start_chapter} for {count} chapters.")
        if count <= 0:
            self.logger.info("Count <= 0. Skip continuous generation.")
            return

        if hasattr(self, "att_manager") and getattr(self.att_manager, "dashboard", None):
            self.att_manager.dashboard.total_auto_chapters = count
        self._validate_discussion_index_integrity()
        invalid_chapters, invalid_global = self._validate_runtime_artifacts_integrity()
        for path, reason in invalid_global:
            self._handle_invalid_global_generated_artifact(path, reason)

        previous_summary = None
        if start_chapter > 1:
            previous_summary = self._load_previous_summary(start_chapter - 1)

        prompts = self._get_system_prompts()

        for i in range(count):
            current_chapter = start_chapter + i
            if hasattr(self, "att_manager") and getattr(self.att_manager, "dashboard", None):
                self.att_manager.dashboard.current_auto_chapter = i + 1
                self.att_manager.dashboard.refresh()
            self.logger.info(f"--- Processing Chapter {current_chapter} ---")
            
            max_retries = getattr(config, "AUTO_GENERATION_MAX_RETRIES", 3)
            for attempt in range(max_retries):
                chapter_discarded = False

                if current_chapter in invalid_chapters:
                    self._discard_chapter_artifacts(current_chapter, reason="runtime_file_integrity_failed")
                    chapter_discarded = True
                    invalid_chapters.discard(current_chapter)

                completed_ok, completed_reason = self._validate_chapter_completion_integrity(current_chapter)
                if completed_ok:
                    self.logger.info(
                        "Chapter %s already has scanned facts. Skip generation and continue from existing summary.",
                        self._num3(current_chapter),
                    )
                    previous_summary = self._load_previous_summary(current_chapter)
                    break # Success, move to next chapter

                if (not chapter_discarded) and self._chapter_has_any_artifacts(current_chapter):
                    self._discard_chapter_artifacts(current_chapter, reason=completed_reason)

                try:
                    guide = self.generate_chapter_guide(current_chapter, previous_summary)
                    chapter_text = self.write_chapter(current_chapter, guide)
                    chapter_text, _ = self._review_and_revise_chapter(current_chapter, guide, chapter_text, prompts)
                    facts = self.scan_chapter(current_chapter)
                    previous_summary = facts
                    break # Success, move to next chapter
                except Exception as e:
                    self.logger.error(f"Error generating Chapter {current_chapter} (Attempt {attempt + 1}/{max_retries}): {e}")
                    if attempt == max_retries - 1:
                        raise RuntimeError(f"Failed to generate Chapter {current_chapter} after {max_retries} attempts. Aborting.")
                    self.logger.info("Discarding artifacts and retrying...")
                    self._discard_chapter_artifacts(current_chapter, reason=f"generation_error_attempt_{attempt + 1}")
                    time.sleep(2)

            time.sleep(1)

        self.logger.info("Continuous generation complete.")
