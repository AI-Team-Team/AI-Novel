import json
import os
import time
from typing import Dict, List, Optional

import config
from workflow_components.discussion import DiscussionLogger

class WorkflowIOMixin:
    def _discussion_logger(self) -> DiscussionLogger:
        log_dir = getattr(self, "discussion_log_dir", os.path.join("novel", "Discussion_Log"))
        self.discussion_log_dir = log_dir
        logger_obj = getattr(self, "_discussion_logger_obj", None)
        if logger_obj is None or logger_obj.log_dir != log_dir:
            logger_obj = DiscussionLogger(log_dir)
            self._discussion_logger_obj = logger_obj
        return logger_obj

    def _all_discussion_log_path(self) -> str:
        return self._discussion_logger().all_log_path()

    def _world_discussion_log_path(self) -> str:
        return self._discussion_logger().world_log_path()

    def _chapter_discussion_log_path(self, chapter_num: int) -> str:
        return self._discussion_logger().chapter_log_path(chapter_num, self._num3)

    def _facts_json_path(self, chapter_num: int) -> str:
        return os.path.join(self.facts_dir, f"chapter_{self._num3(chapter_num)}_facts.json")

    def _facts_summary_path(self, chapter_num: int) -> str:
        return os.path.join(self.facts_dir, f"chapter_{self._num3(chapter_num)}_facts_summary.md")

    def _save_file(self, filename: str, content: str, subdir: str = config.FRAME_DIR) -> str:
        path = os.path.join(subdir, filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        self.logger.info(f"Saved {filename} to {path}")
        return path

    def _ensure_discussion_logs(self):
        self._discussion_logger().ensure_logs()

    def _append_discussion_log(
        self,
        title: str,
        content: str,
        chapter_num: Optional[int] = None,
        world_building: bool = False,
    ):
        self._discussion_logger().append(
            title=title,
            content=content,
            num3_func=self._num3,
            chapter_num=chapter_num,
            world_building=world_building,
        )

    def _log_att_interaction(
        self,
        team_id: str,
        title: str,
        content: str,
        chapter_num: Optional[int] = None,
    ):
        self._discussion_logger().append_att(
            team_id=team_id,
            title=title,
            content=content,
            chapter_num=chapter_num,
            num3_func=self._num3,
        )

    def _log_llm_interaction(
        self,
        role: str,
        phase: str,
        prompt: str,
        response: str,
        system_instruction: str = "",
        chapter_num: Optional[int] = None,
        world_building: bool = False,
        extra: str = "",
    ):
        # Push to dashboard activity panel if active
        if hasattr(self, "att_manager") and getattr(self.att_manager, "dashboard", None):
            db = self.att_manager.dashboard
            preview = response.strip()
            clean_preview = " ".join(preview.split())
            if len(clean_preview) > 120:
                clean_preview = clean_preview[:117] + "..."
            db.add_activity(role, "Final Answer", clean_preview)

        payload = (
            f"ROLE: {role}\n"
            f"PHASE: {phase}\n"
            f"{extra}\n"
            f"--- SYSTEM INSTRUCTION BEGIN ---\n"
            f"{system_instruction}\n"
            f"--- SYSTEM INSTRUCTION END ---\n"
            f"--- PROMPT BEGIN ---\n"
            f"{prompt}\n"
            f"--- PROMPT END ---\n"
            f"--- RESPONSE BEGIN ---\n"
            f"{response}\n"
            f"--- RESPONSE END ---\n"
        )
        title = f"{phase} | {role}"
        self._append_discussion_log(
            title=title,
            content=payload,
            chapter_num=chapter_num,
            world_building=world_building,
        )

    def _discussion_index_path(self) -> str:
        return os.path.join(self.discussions_dir, "discussion_index.jsonl")

    def _structured_discussion_path(self, phase_type: str, chapter_num: Optional[int]) -> str:
        if phase_type == "world":
            return os.path.join(self.discussions_dir, "world_discussion.md")
        if phase_type == "plot":
            return os.path.join(self.discussions_dir, "plot_discussion.md")
        if phase_type == "guide":
            suffix = "na" if chapter_num is None else self._num3(chapter_num)
            return os.path.join(self.discussions_dir, f"chapter_{suffix}_guide_discussion.md")
        if phase_type == "chapter_text":
            suffix = "na" if chapter_num is None else self._num3(chapter_num)
            return os.path.join(self.discussions_dir, f"chapter_{suffix}_text_discussion.md")
        suffix = "na" if chapter_num is None else self._num3(chapter_num)
        return os.path.join(self.discussions_dir, f"chapter_{suffix}_{phase_type}_discussion.md")

    @staticmethod
    def _summary_text(text: str, limit: int = 300) -> str:
        normalized = " ".join((text or "").split())
        if len(normalized) <= limit:
            return normalized
        return normalized[: limit - 3] + "..."

    def _role_model_name(self, role: str) -> str:
        role_upper = (role or "").upper()
        client_attr = f"{role.lower()}_client"
        client = getattr(self, client_attr, None)
        if client is not None:
            model_name = getattr(client, "model_name", None)
            if model_name:
                return str(model_name)
        openai_attr = f"{role_upper}_OPENAI_MODEL_NAME"
        gemini_attr = f"{role_upper}_GEMINI_MODEL_NAME"
        return str(getattr(config, openai_attr, None) or getattr(config, gemini_attr, None) or "unknown")

    def _append_structured_discussion(
        self,
        phase_type: str,
        role: str,
        prompt_text: str,
        response_text: str,
        chapter_num: Optional[int] = None,
        round_index: Optional[int] = None,
        decision: str = "",
        needs_revision: Optional[bool] = None,
        artifact_paths: Optional[List[str]] = None,
    ):
        os.makedirs(self.discussions_dir, exist_ok=True)
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        log_id = f"disc-{time.time_ns()}"
        chapter_value = chapter_num if chapter_num is not None else "NA"
        round_value = round_index if round_index is not None else "NA"
        model_name = self._role_model_name(role)
        entry = {
            "log_id": log_id,
            "timestamp": timestamp,
            "phase_type": phase_type,
            "chapter_num": chapter_num,
            "round": round_index,
            "role": role,
            "model": model_name,
            "input_summary": self._summary_text(prompt_text),
            "output_summary": self._summary_text(response_text),
            "decision": decision,
            "needs_revision": needs_revision,
            "artifact_paths": artifact_paths or [],
        }
        title = f"[{phase_type}] chapter={chapter_value} round={round_value} role={role}"
        md_lines = [
            f"## {title}",
            "",
            f"- `log_id`: {entry['log_id']}",
            f"- `timestamp`: {entry['timestamp']}",
            f"- `phase_type`: {entry['phase_type']}",
            f"- `chapter_num`: {chapter_value}",
            f"- `round`: {round_value}",
            f"- `role`: {entry['role']}",
            f"- `model`: {entry['model']}",
            f"- `decision`: {entry['decision'] or '-'}",
            f"- `needs_revision`: {entry['needs_revision'] if entry['needs_revision'] is not None else '-'}",
            f"- `input_summary`: {entry['input_summary']}",
            f"- `output_summary`: {entry['output_summary']}",
            "- `artifact_paths`:",
        ]
        for path in entry["artifact_paths"]:
            md_lines.append(f"  - {path}")
        if not entry["artifact_paths"]:
            md_lines.append("  - -")
        md_block = "\n".join(md_lines) + "\n\n"

        discussion_path = self._structured_discussion_path(phase_type, chapter_num)
        with open(discussion_path, "a", encoding="utf-8") as f:
            f.write(md_block)

        with open(self._discussion_index_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    @staticmethod
    def _read_text_if_exists(path: str) -> str:
        if not os.path.exists(path):
            return ""
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
