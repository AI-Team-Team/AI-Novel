import logging
import os
import shutil
import sys
import tempfile
import json
import unittest
from unittest.mock import MagicMock

# Setup paths
CURRENT_DIR = os.path.dirname(__file__)
ROOT_DIR = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
SRC_DIR = os.path.join(ROOT_DIR, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from workflow import WorkflowManager
from memory import MemoryManager

class AIDebateConflictResolverTests(unittest.TestCase):
    def setUp(self):
        self.old_cwd = os.getcwd()
        self.tmpdir = tempfile.mkdtemp(prefix="ai_debate_test_")
        os.chdir(self.tmpdir)

        # Setup standard subdirs
        os.makedirs(os.path.join("novel", "process", "discussions"), exist_ok=True)
        os.makedirs(os.path.join("novel", "main_text", "chapters"), exist_ok=True)

        self.db_path = "test_debate.db"
        self.faiss_path = "test_debate.faiss"

        # Instantiate WorkflowManager manually and copy essential attributes
        self.workflow = WorkflowManager.__new__(WorkflowManager)
        self.workflow.logger = logging.getLogger("ai-debate-test")
        self.workflow.process_dir = os.path.join("novel", "process")
        self.workflow.chapters_dir = os.path.join("novel", "main_text", "chapters")

        self.workflow.memory = MemoryManager(self.db_path, self.faiss_path)
        self.workflow.state_manager = MagicMock()

        # Bind the resolver mixin methods manually
        from workflow_components.conflict_resolver import ConflictResolverWorkflowMixin
        for name in dir(ConflictResolverWorkflowMixin):
            if not name.startswith("__") and callable(getattr(ConflictResolverWorkflowMixin, name)):
                setattr(self.workflow, name, getattr(ConflictResolverWorkflowMixin, name).__get__(self.workflow))

        # Bind essential methods from WorkflowManager that conflict_resolver uses
        self.workflow.get_chapter_path = self._get_chapter_path
        self.workflow._extract_json = self._extract_json

        # Mock LLM Clients
        self.workflow.critic_client = MagicMock()
        self.workflow.scanner_client = MagicMock()
        self.workflow.planner_client = MagicMock()

        self.workflow.ai_resolve_conflicts = True
        self.workflow.in_auto_mode = False

    def tearDown(self):
        self.workflow.memory.close()
        os.chdir(self.old_cwd)
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _get_chapter_path(self, chapter_num: int) -> str:
        return os.path.join(self.workflow.chapters_dir, f"chapter_{chapter_num:03d}.md")

    def _extract_json(self, text: str):
        try:
            return json.loads(text)
        except Exception:
            return None

    def test_ai_debate_consensus_apply(self):
        # 1. Setup a dead character and queue a resurrection conflict
        self.workflow.memory.upsert_character(name="Iris", status="dead", source="test", chapter_num=1)
        self.workflow.memory.upsert_character(name="Iris", status="alive", source="test", chapter_num=2)

        conflicts = self.workflow.memory.get_pending_conflicts(limit=1)
        self.assertEqual(len(conflicts), 1)
        conflict_id = conflicts[0][0]

        # 2. Mock LLM outputs
        self.workflow.critic_client.generate.return_value = "Keep Iris dead for tragic impact!"
        self.workflow.scanner_client.generate.return_value = "Iris must live because she has an ongoing harbor arc."
        
        planner_decision = {
            "action": "apply_incoming",
            "reasoning": "Iris surviving makes narrative sense to continue her harbor arc.",
            "narrative_compromise": "Iris was barely alive, rescued by harbor fishermen."
        }
        self.workflow.planner_client.generate.return_value = json.dumps(planner_decision)

        # 3. Trigger debate resolution
        resolved = self.workflow.ai_debate_resolve_conflict(conflict_id)
        self.assertTrue(resolved)

        # 4. Verify DB state mutation
        char_row = self.workflow.memory.get_character("Iris")
        self.assertEqual(char_row[3], "alive") # Status changed to incoming 'alive'!

        conflict_row = self.workflow.memory.get_conflict_by_id(conflict_id)
        self.assertEqual(conflict_row[8], "RESOLVED")

        # 5. Verify transcript log file exists and is populated
        transcript_path = os.path.join("novel", "process", "discussions", f"conflict_{conflict_id}_resolution_discussion.md")
        self.assertTrue(os.path.exists(transcript_path))
        with open(transcript_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("Iris surviving makes narrative sense", content)
        self.assertIn("Keep Iris dead for tragic impact", content)

    def test_ai_debate_consensus_keep(self):
        # 1. Setup a dead character and queue a resurrection conflict
        self.workflow.memory.upsert_character(name="Iris", status="dead", source="test", chapter_num=1)
        self.workflow.memory.upsert_character(name="Iris", status="alive", source="test", chapter_num=2)

        conflicts = self.workflow.memory.get_pending_conflicts(limit=1)
        self.assertEqual(len(conflicts), 1)
        conflict_id = conflicts[0][0]

        # 2. Mock LLM outputs choosing keep_existing
        self.workflow.critic_client.generate.return_value = "Keep dead!"
        self.workflow.scanner_client.generate.return_value = "Prose advocate arg."
        
        planner_decision = {
            "action": "keep_existing",
            "reasoning": "Continuity rules strictly dictate dead remains dead.",
            "narrative_compromise": "Iris remains dead."
        }
        self.workflow.planner_client.generate.return_value = json.dumps(planner_decision)

        # 3. Trigger debate resolution
        resolved = self.workflow.ai_debate_resolve_conflict(conflict_id)
        self.assertTrue(resolved)

        # 4. Verify DB state stays keep_existing
        char_row = self.workflow.memory.get_character("Iris")
        self.assertEqual(char_row[3], "dead") # Remains dead!

        conflict_row = self.workflow.memory.get_conflict_by_id(conflict_id)
        self.assertEqual(conflict_row[8], "RESOLVED")

    def test_ai_debate_standoff_fails(self):
        # 1. Setup a dead character and queue a resurrection conflict
        self.workflow.memory.upsert_character(name="Iris", status="dead", source="test", chapter_num=1)
        self.workflow.memory.upsert_character(name="Iris", status="alive", source="test", chapter_num=2)

        conflicts = self.workflow.memory.get_pending_conflicts(limit=1)
        self.assertEqual(len(conflicts), 1)
        conflict_id = conflicts[0][0]

        # 2. Mock LLM outputs choosing an invalid action (standoff)
        self.workflow.critic_client.generate.return_value = "Keep dead!"
        self.workflow.scanner_client.generate.return_value = "Let live!"
        
        # Standoff / Invalid JSON response
        self.workflow.planner_client.generate.return_value = "We cannot agree on anything."

        # 3. Trigger debate resolution
        resolved = self.workflow.ai_debate_resolve_conflict(conflict_id)
        self.assertFalse(resolved) # Standoff returns False!

        # 4. Verify DB state is unchanged and stays PENDING
        char_row = self.workflow.memory.get_character("Iris")
        self.assertEqual(char_row[3], "dead")

        conflict_row = self.workflow.memory.get_conflict_by_id(conflict_id)
        self.assertEqual(conflict_row[8], "PENDING")

        # 5. Verify transcript log file exists and records STANDOFF status
        transcript_path = os.path.join("novel", "process", "discussions", f"conflict_{conflict_id}_resolution_discussion.md")
        self.assertTrue(os.path.exists(transcript_path))
        with open(transcript_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("Status**: STANDOFF", content)

    def test_workflow_gating_fail_fast_standoff(self):
        # 1. Queue a resurrection conflict
        self.workflow.memory.upsert_character(name="Iris", status="dead", source="test", chapter_num=1)
        self.workflow.memory.upsert_character(name="Iris", status="alive", source="test", chapter_num=2)

        # 2. Bind the gating method from WorkflowManager (need to mock _enforce_conflict_free_state context)
        # Mock LLM Planner to return standoff
        self.workflow.critic_client.generate.return_value = "Arg1"
        self.workflow.scanner_client.generate.return_value = "Arg2"
        self.workflow.planner_client.generate.return_value = "STANDOFF"

        # Bind the target method
        from workflow import WorkflowManager as WM_original
        self.workflow._enforce_conflict_free_state = WM_original._enforce_conflict_free_state.__get__(self.workflow)

        # 3. Trigger gating check, verify it raises RuntimeError (Fail-Fast)
        with self.assertRaises(RuntimeError) as ctx:
            self.workflow._enforce_conflict_free_state(stage="chapter_002_post_scan")
        
        self.assertIn("Conflict Resolution Standoff", str(ctx.exception))
        self.assertIn("Fail-Fast triggered", str(ctx.exception))

if __name__ == "__main__":
    unittest.main()
