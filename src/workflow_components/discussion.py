import os
import time
from typing import Optional

class DiscussionLogger:
    def __init__(self, log_dir: str):
        self.log_dir = log_dir

    def all_log_path(self) -> str:
        return os.path.join(self.log_dir, "All_Discussion.log")

    def world_log_path(self) -> str:
        return os.path.join(self.log_dir, "Novel_World_building_Discussion.log")

    def chapter_log_path(self, chapter_num: int, num3_func) -> str:
        return os.path.join(self.log_dir, f"chapter_{num3_func(chapter_num)}_Discussion.log")

    def ensure_logs(self):
        os.makedirs(self.log_dir, exist_ok=True)
        for path in [self.all_log_path(), self.world_log_path()]:
            if not os.path.exists(path):
                with open(path, "w", encoding="utf-8") as f:
                    f.write("")

    def append(
        self,
        title: str,
        content: str,
        num3_func,
        chapter_num: Optional[int] = None,
        world_building: bool = False,
    ):
        self.ensure_logs()
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        entry_id = f"entry-{time.time_ns()}"
        body = content if content.endswith("\n") else f"{content}\n"
        block = (
            f"{'=' * 108}\n"
            f"ENTRY_ID: {entry_id}\n"
            f"TIMESTAMP: {timestamp}\n"
            f"TITLE: {title}\n"
            f"{'-' * 108}\n"
            f"{body}"
            f"{'=' * 108}\n"
        )

        with open(self.all_log_path(), "a", encoding="utf-8") as f:
            f.write(block)

        if world_building:
            with open(self.world_log_path(), "a", encoding="utf-8") as f:
                f.write(block)

        if chapter_num is not None:
            chapter_path = self.chapter_log_path(chapter_num, num3_func)
            if not os.path.exists(chapter_path):
                with open(chapter_path, "w", encoding="utf-8") as f:
                    f.write("")
            with open(chapter_path, "a", encoding="utf-8") as f:
                f.write(block)

    def att_log_dir(self) -> str:
        return os.path.join(self.log_dir, "att")

    def att_log_path(self, team_id: str) -> str:
        return os.path.join(self.att_log_dir(), f"{team_id}.log")

    def append_att(
        self,
        team_id: str,
        title: str,
        content: str,
        chapter_num: Optional[int] = None,
        num3_func = None,
    ):
        self.ensure_logs()
        os.makedirs(self.att_log_dir(), exist_ok=True)
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        entry_id = f"entry-{time.time_ns()}"
        body = content if content.endswith("\n") else f"{content}\n"
        block = (
            f"{'=' * 108}\n"
            f"ENTRY_ID: {entry_id}\n"
            f"TIMESTAMP: {timestamp}\n"
            f"TEAM_ID: {team_id}\n"
            f"TITLE: {title}\n"
            f"{'-' * 108}\n"
            f"{body}"
            f"{'=' * 108}\n"
        )

        with open(self.all_log_path(), "a", encoding="utf-8") as f:
            f.write(block)

        with open(self.att_log_path(team_id), "a", encoding="utf-8") as f:
            f.write(block)

        if chapter_num is not None:
            n3 = num3_func if num3_func else (lambda val: f"{val:03d}")
            chapter_path = self.chapter_log_path(chapter_num, n3)
            if not os.path.exists(chapter_path):
                with open(chapter_path, "w", encoding="utf-8") as f:
                    f.write("")
            with open(chapter_path, "a", encoding="utf-8") as f:
                f.write(block)

