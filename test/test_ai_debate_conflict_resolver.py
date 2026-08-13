import logging
import os
import shutil
import sys
import tempfile
import json
import unittest
from unittest.mock import MagicMock
from types import SimpleNamespace

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
        self.workflow.embedding_client = MagicMock()

        self.workflow.ai_resolve_conflicts = True
        self.workflow.in_auto_mode = False
        self.workflow._create_att_team = MagicMock(
            return_value=SimpleNamespace(
                members=[
                    SimpleNamespace(name="Historian_Critic"),
                    SimpleNamespace(name="Prose_Scanner"),
                    SimpleNamespace(name="Consensus_Planner"),
                ]
            )
        )
        self.workflow._execute_att_discussion = MagicMock()

    def tearDown(self):
        self.workflow.memory.close()
        os.chdir(self.old_cwd)
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _get_chapter_path(self, chapter_num: int) -> str:
        return os.path.join(self.workflow.chapters_dir, f"chapter_{chapter_num:03d}.md")

    def _extract_json(self, text: str):
        from workflow_components.parsing import extract_json_payload
        return extract_json_payload(text)

    def test_ai_debate_consensus_apply(self):
        # 1. Setup a dead character and queue a resurrection conflict
        self.workflow.memory.upsert_character(name="Iris", status="dead", source="test", chapter_num=1)
        self.workflow.memory.upsert_character(name="Iris", status="alive", source="test", chapter_num=2)

        conflicts = self.workflow.memory.get_pending_conflicts(limit=1)
        self.assertEqual(len(conflicts), 1)
        conflict_id = conflicts[0][0]

        # 2. Mock the ATT transcript using current member-prefixed format.
        self.workflow._execute_att_discussion.return_value = (
            "Historian_Critic: Final Answer: Keep Iris dead for tragic impact!\n"
            "Prose_Scanner: Final Answer: Iris must live because she has an ongoing harbor arc.\n"
            "Consensus_Planner: Final Answer: " + json.dumps({
                "action": "apply_incoming",
                "reasoning": "Iris surviving makes narrative sense to continue her harbor arc.",
                "narrative_compromise": "Iris was barely alive, rescued by harbor fishermen."
            })
        )

        # 3. Trigger debate resolution under 1 round
        import config
        old_rounds = getattr(config, "CONFLICT_DISCUSSION_ROUNDS", 2)
        config.CONFLICT_DISCUSSION_ROUNDS = 1
        try:
            resolved = self.workflow.ai_debate_resolve_conflict(conflict_id)
        finally:
            config.CONFLICT_DISCUSSION_ROUNDS = old_rounds

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

        self.workflow._execute_att_discussion.return_value = (
            "Historian_Critic: Final Answer: Keep dead!\n"
            "Prose_Scanner: Final Answer: Prose advocate arg.\n"
            "Consensus_Planner: Final Answer: " + json.dumps({
                "action": "keep_existing",
                "reasoning": "Continuity rules strictly dictate dead remains dead.",
                "narrative_compromise": "Iris remains dead."
            })
        )

        # 3. Trigger debate resolution
        import config
        old_rounds = getattr(config, "CONFLICT_DISCUSSION_ROUNDS", 2)
        config.CONFLICT_DISCUSSION_ROUNDS = 1
        try:
            resolved = self.workflow.ai_debate_resolve_conflict(conflict_id)
        finally:
            config.CONFLICT_DISCUSSION_ROUNDS = old_rounds

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

        self.workflow._execute_att_discussion.return_value = (
            "Historian_Critic: Final Answer: Keep dead!\n"
            "Prose_Scanner: Final Answer: Let live!\n"
            "Consensus_Planner: Final Answer: We cannot agree on anything."
        )

        # 3. Trigger debate resolution
        import config
        old_rounds = getattr(config, "CONFLICT_DISCUSSION_ROUNDS", 2)
        config.CONFLICT_DISCUSSION_ROUNDS = 1
        try:
            resolved = self.workflow.ai_debate_resolve_conflict(conflict_id)
        finally:
            config.CONFLICT_DISCUSSION_ROUNDS = old_rounds

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
        self.workflow._execute_att_discussion.return_value = (
            "Historian_Critic: Final Answer: Arg1\n"
            "Prose_Scanner: Final Answer: Arg2\n"
            "Consensus_Planner: Final Answer: STANDOFF"
        )

        # Bind the target method
        from workflow import WorkflowManager as WM_original
        self.workflow._enforce_conflict_free_state = WM_original._enforce_conflict_free_state.__get__(self.workflow)

        # 3. Trigger gating check, verify it raises RuntimeError (Fail-Fast)
        import config
        old_rounds = getattr(config, "CONFLICT_DISCUSSION_ROUNDS", 2)
        config.CONFLICT_DISCUSSION_ROUNDS = 1
        try:
            with self.assertRaises(RuntimeError) as ctx:
                self.workflow._enforce_conflict_free_state(stage="chapter_002_post_scan")
        finally:
            config.CONFLICT_DISCUSSION_ROUNDS = old_rounds
        
        self.assertIn("Conflict Resolution Standoff", str(ctx.exception))
        self.assertIn("Fail-Fast triggered", str(ctx.exception))

if __name__ == "__main__":
    unittest.main()
