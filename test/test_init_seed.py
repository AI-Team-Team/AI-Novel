import json
import logging
import os
import shutil
import sys
import tempfile
import unittest

CURRENT_DIR = os.path.dirname(__file__)
ROOT_DIR = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
SRC_DIR = os.path.join(ROOT_DIR, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import config
from memory import MemoryManager
from workflow import WorkflowManager

class _StubClient:
    def __init__(self, responses):
        self._responses = list(responses)

    def generate(self, prompt, system_instruction=None, temperature=0.7):
        if not self._responses:
            raise RuntimeError("No stub response left")
        return self._responses.pop(0)

class InitSeedingTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="ai_novel_init_seed_")
        self.db_path = os.path.join(self.tmpdir, "facts.db")
        self.faiss_path = os.path.join(self.tmpdir, "vector.faiss")
        self.memory = MemoryManager(self.db_path, self.faiss_path)

    def tearDown(self):
        self.memory.close()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_start_new_project_seeds_initial_facts(self):
        wf = WorkflowManager.__new__(WorkflowManager)
        wf.logger = logging.getLogger("init-seed-test")
        wf.memory = self.memory

        wf.world_dir = os.path.join(self.tmpdir, "frame", "world")
        wf.plot_dir = os.path.join(self.tmpdir, "frame", "plot")
        wf.critiques_dir = os.path.join(self.tmpdir, "process", "critiques")
        wf.discussions_dir = os.path.join(self.tmpdir, "process", "discussions")
        wf.facts_dir = os.path.join(self.tmpdir, "process", "facts")
        wf.archives_dir = os.path.join(self.tmpdir, "frame", "archives")
        wf.discussion_log_dir = os.path.join(self.tmpdir, "Discussion_Log")
        for d in [
            wf.world_dir,
            wf.plot_dir,
            wf.critiques_dir,
            wf.discussions_dir,
            wf.facts_dir,
            wf.archives_dir,
            wf.discussion_log_dir,
        ]:
            os.makedirs(d, exist_ok=True)

        wf._get_system_prompts = lambda: {
            "architect": "a",
            "critic": "c",
            "planner": "p",
            "writer": "w",
            "scanner": "s",
        }

        world_bible = "# World Bible\n- Rule: No resurrection."
        seed_json = {
            "new_characters": [{"name": "Hero", "core_traits": {"mbti": "INFJ"}, "attributes": {"age": 18}}],
            "updated_characters": [],
            "new_rules": [{"category": "Magic", "content": "No resurrection", "strictness": 1}],
            "relationships": [],
            "events": [{"event_name": "Prologue", "description": "Story starts", "timestamp_str": "Day 1", "impact_level": 3, "related_entities": ["Hero"], "location": "Town"}],
            "details": [],
        }

        wf.architect_client = _StubClient([world_bible])
        wf.planner_client = _StubClient(["# Plot\n- Arc A", "# Detailed Plot\n- Scene Cluster A"])
        wf.critic_client = _StubClient([])
        wf.scanner_client = _StubClient([json.dumps(seed_json, ensure_ascii=False)])
        wf.embedding_client = _StubClient([])  # not used because details is empty
        wf.initialize_autonomy()

        old_world_rounds = config.WORLD_DISCUSSION_ROUNDS
        old_plot_rounds = config.PLOT_DISCUSSION_ROUNDS
        old_detailed_plot_rounds = config.DETAILED_PLOT_DISCUSSION_ROUNDS
        old_lang = config.LANGUAGE
        try:
            config.WORLD_DISCUSSION_ROUNDS = 0
            config.PLOT_DISCUSSION_ROUNDS = 0
            config.DETAILED_PLOT_DISCUSSION_ROUNDS = 0
            config.LANGUAGE = "en"
            path = wf.start_new_project("write a story")
        finally:
            config.WORLD_DISCUSSION_ROUNDS = old_world_rounds
            config.PLOT_DISCUSSION_ROUNDS = old_plot_rounds
            config.DETAILED_PLOT_DISCUSSION_ROUNDS = old_detailed_plot_rounds
            config.LANGUAGE = old_lang

        self.assertTrue(os.path.exists(path))
        self.assertIsNotNone(self.memory.get_character("Hero"))
        self.assertGreaterEqual(len(self.memory.get_rules_by_category()), 1)
        self.assertGreaterEqual(len(self.memory.get_events(limit=10)), 1)
        self.assertTrue(os.path.exists(os.path.join(wf.facts_dir, "world_init_facts.json")))

if __name__ == "__main__":
    unittest.main()
