import os
import shutil
import sys
import tempfile
import unittest
import unittest.mock

CURRENT_DIR = os.path.dirname(__file__)
TEST_DIR = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
ROOT_DIR = os.path.abspath(os.path.join(TEST_DIR, ".."))
SRC_DIR = os.path.join(ROOT_DIR, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)
if TEST_DIR not in sys.path:
    sys.path.insert(0, TEST_DIR)
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import config
from att_result_helpers import make_discussion_result, make_team
from workflow import WorkflowManager

__all__ = [
    "os", "shutil", "sys", "tempfile", "unittest", "config",
    "make_discussion_result", "make_team", "WorkflowManager",
    "AutonomyTestCase",
]


class AutonomyTestCase(unittest.TestCase):
    def setUp(self):
        self.old_cwd = os.getcwd()
        self.tmpdir = tempfile.mkdtemp(prefix="ai_novel_autonomy_toggle_")
        os.chdir(self.tmpdir)

        # Mock config values
        self.old_autonomy_suite = getattr(config, "ENABLE_AUTONOMY_SUITE", True)
        self.old_autonomous_queries = getattr(config, "ENABLE_AUTONOMOUS_QUERIES", True)
        self.old_att_state_path = config.ATT_STATE_DB_PATH
        self.old_episodic_memory_enabled = getattr(
            config, "EPISODIC_MEMORY_ENABLED", False
        )
        self.old_episodic_memory_settings = dict(
            getattr(config, "EPISODIC_MEMORY_SETTINGS", {"enabled": False})
        )
        self.old_tool_memory_capture = dict(
            getattr(config, "TOOL_MEMORY_CAPTURE_POLICIES", {})
        )
        config.ATT_STATE_DB_PATH = os.path.join(self.tmpdir, "att_state.db")

        # Create minimal WorkflowManager subclass/instance with mocked clients and logs
        self.wf = WorkflowManager.__new__(WorkflowManager)
        self.wf.logger = unittest.mock.MagicMock()
        self.wf.att_manager = unittest.mock.MagicMock()

        # Mock file paths and directories
        self.wf.world_dir = "world"
        self.wf.plot_dir = "plot"
        self.wf.chapters_dir = "chapters"
        os.makedirs(self.wf.plot_dir, exist_ok=True)
        os.makedirs(self.wf.world_dir, exist_ok=True)

        self.wf._language_rule = lambda: "Use English."
        self.wf._enforce_output_language = lambda client, role, text, prompt, world_building=False: text
        self.wf._save_file = lambda filename, content, dir_path: os.path.join(dir_path, filename)
    def tearDown(self):
        manager = getattr(self.wf, "att_manager", None)
        if manager is not None and manager.__class__.__module__.startswith("ai_team_team"):
            self.wf.close_autonomy()
        config.ENABLE_AUTONOMY_SUITE = self.old_autonomy_suite
        config.ENABLE_AUTONOMOUS_QUERIES = self.old_autonomous_queries
        config.ATT_STATE_DB_PATH = self.old_att_state_path
        config.EPISODIC_MEMORY_ENABLED = self.old_episodic_memory_enabled
        config.EPISODIC_MEMORY_SETTINGS = self.old_episodic_memory_settings
        config.TOOL_MEMORY_CAPTURE_POLICIES = self.old_tool_memory_capture
        os.chdir(self.old_cwd)
        shutil.rmtree(self.tmpdir, ignore_errors=True)
