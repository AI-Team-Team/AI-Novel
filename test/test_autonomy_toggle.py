import unittest
import unittest.mock
import os
import sys
import tempfile
import shutil
from types import SimpleNamespace

# Add src and root to path
CURRENT_DIR = os.path.dirname(__file__)
ROOT_DIR = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
SRC_DIR = os.path.join(ROOT_DIR, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import config
from workflow import WorkflowManager

class TestAutonomyToggleGating(unittest.TestCase):
    def setUp(self):
        self.old_cwd = os.getcwd()
        self.tmpdir = tempfile.mkdtemp(prefix="ai_novel_autonomy_toggle_")
        os.chdir(self.tmpdir)

        # Mock config values
        self.old_autonomy_suite = getattr(config, "ENABLE_AUTONOMY_SUITE", True)
        self.old_autonomous_queries = getattr(config, "ENABLE_AUTONOMOUS_QUERIES", True)
        self.old_att_state_path = config.ATT_STATE_DB_PATH
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
        os.chdir(self.old_cwd)
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_planning_mixin_respects_toggle(self):
        # 1. Test when ENABLE_AUTONOMY_SUITE is True (should call att_manager)
        config.ENABLE_AUTONOMY_SUITE = True
        self.wf._append_structured_discussion = unittest.mock.MagicMock()
        self.wf.get_guide_path = lambda chapter_num: "guide_path"
        team = SimpleNamespace(members=[SimpleNamespace(name="Reviewer_Arbitrator")])
        self.wf._create_att_team = unittest.mock.MagicMock(return_value=team)
        self.wf._execute_att_discussion = unittest.mock.MagicMock(
            return_value="Reviewer_Arbitrator: Final Answer: Refined Guide"
        )
        refined = self.wf._refine_chapter_guide_with_discussion(1, "Initial Guide", {})
        self.assertEqual(refined, "Refined Guide")
        self.wf._create_att_team.assert_called()

        # Reset mock
        self.wf._create_att_team.reset_mock()

        # 2. Test when ENABLE_AUTONOMY_SUITE is False (should bypass discussion)
        config.ENABLE_AUTONOMY_SUITE = False
        refined_bypassed = self.wf._refine_chapter_guide_with_discussion(1, "Initial Guide", {})
        self.assertEqual(refined_bypassed, "Initial Guide")
        self.wf._create_att_team.assert_not_called()

    def test_writing_mixin_respects_toggle(self):
        # 1. Test when ENABLE_AUTONOMY_SUITE is True
        config.ENABLE_AUTONOMY_SUITE = True
        self.wf._append_structured_discussion = unittest.mock.MagicMock()
        self.wf.get_chapter_path = lambda chapter_num: "chapter_path"
        team = SimpleNamespace(members=[SimpleNamespace(name="Editor_In_Chief")])
        self.wf._create_att_team = unittest.mock.MagicMock(return_value=team)
        self.wf._execute_att_discussion = unittest.mock.MagicMock(
            return_value="Editor_In_Chief: Final Answer: Polished Prose"
        )
        
        revised, _ = self.wf._review_and_revise_chapter(1, "Guide", "Initial Prose", {})
        self.assertEqual(revised, "Polished Prose")
        self.wf._create_att_team.assert_called()

        # Reset mock
        self.wf._create_att_team.reset_mock()

        # 2. Test when ENABLE_AUTONOMY_SUITE is False
        config.ENABLE_AUTONOMY_SUITE = False
        revised_bypassed, _ = self.wf._review_and_revise_chapter(1, "Guide", "Initial Prose", {})
        self.assertEqual(revised_bypassed, "Initial Prose")
        self.wf._create_att_team.assert_not_called()

    def test_project_mixin_respects_toggle(self):
        # Setup planner client mock for direct generation
        self.wf.planner_client = unittest.mock.MagicMock()
        self.wf.planner_client.generate.return_value = "Direct Generated Outline"
        self.wf._append_structured_discussion = unittest.mock.MagicMock()

        # 1. Test when ENABLE_AUTONOMY_SUITE is True
        config.ENABLE_AUTONOMY_SUITE = True
        team = SimpleNamespace(members=[SimpleNamespace(name="Arc_Arbitrator")])
        self.wf._create_att_team = unittest.mock.MagicMock(return_value=team)
        self.wf._execute_att_discussion = unittest.mock.MagicMock(
            return_value="Arc_Arbitrator: Final Answer: Refined Outline"
        )

        outline = self.wf._generate_outline_with_discussion(
            phase_name="test_phase",
            draft_prompt="draft",
            revise_prompt_builder=None,
            rounds=1,
            output_filename="test_outline.md",
            prompts={"planner": "planner"}
        )
        self.assertEqual(outline, "Refined Outline")
        self.wf._create_att_team.assert_called()

        # Reset mock
        self.wf._create_att_team.reset_mock()

        # 2. Test when ENABLE_AUTONOMY_SUITE is False
        config.ENABLE_AUTONOMY_SUITE = False
        outline_bypassed = self.wf._generate_outline_with_discussion(
            phase_name="test_phase",
            draft_prompt="draft",
            revise_prompt_builder=None,
            rounds=1,
            output_filename="test_outline.md",
            prompts={"planner": "planner"}
        )
        self.assertEqual(outline_bypassed, "Direct Generated Outline")
        self.wf._create_att_team.assert_not_called()

    def test_workflow_enforce_conflict_respects_toggle(self):
        self.wf.memory = unittest.mock.MagicMock()
        self.wf.memory.get_pending_blocking_conflict_count.side_effect = [1, 0, 1]
        self.wf.memory.get_pending_conflicts.return_value = [(1, "entity", "key", "conflict_type")]
        self.wf.ai_resolve_conflicts = True

        # 1. Test when ENABLE_AUTONOMY_SUITE is True (should call ai_debate_resolve_conflict)
        config.ENABLE_AUTONOMY_SUITE = True
        self.wf.ai_debate_resolve_conflict = unittest.mock.MagicMock(return_value=True)
        config.BLOCKING_CONFLICT_MODE = "manual_block"

        self.wf._enforce_conflict_free_state("stage")
        self.wf.ai_debate_resolve_conflict.assert_called_with(1)

        # Reset mock
        self.wf.ai_debate_resolve_conflict.reset_mock()

        # 2. Test when ENABLE_AUTONOMY_SUITE is False (should NOT call ai_debate_resolve_conflict)
        config.ENABLE_AUTONOMY_SUITE = False
        self.wf.state_manager = unittest.mock.MagicMock()
        
        # When conflict mode is manual_block, it should just pass/return without debate (or raise if not resolved)
        with self.assertRaises(RuntimeError):
            self.wf._enforce_conflict_free_state("stage")
        self.wf.ai_debate_resolve_conflict.assert_not_called()

    @unittest.mock.patch("ai_team_team.ATTManager.register_generator_handler")
    def test_generator_handler_routing(self, mock_register):
        import asyncio
        from workflow_components.autonomy_mixin import AutonomyWorkflowMixin
        self.wf.initialize_autonomy = AutonomyWorkflowMixin.initialize_autonomy.__get__(self.wf)
        
        # Setup dummy clients
        self.wf.architect_client = unittest.mock.MagicMock()
        self.wf.planner_client = unittest.mock.MagicMock()
        self.wf.critic_client = unittest.mock.MagicMock()
        
        # Run init
        self.wf.initialize_autonomy()
        
        # Force collision: map "gemma4-26b" (or whatever the model name is) to critic_client
        model_key = config.models_section.get("critic_model")
        self.wf.llm_clients[model_key] = self.wf.critic_client
        
        # Capture generator_handler
        handler = mock_register.call_args[0][0]
        
        # 1. Test routing with "planner" keyword in system_instruction
        asyncio.run(handler(model_name=model_key, prompt="test", system_instruction="You are a Consensus_Planner."))
        self.wf.planner_client.generate.assert_called_once()
        self.wf.critic_client.generate.assert_not_called()
        
        self.wf.planner_client.generate.reset_mock()
        
        # 2. Test routing with "architect" keyword in system_instruction
        asyncio.run(handler(model_name=model_key, prompt="test", system_instruction="You are the Lore_Architect."))
        self.wf.architect_client.generate.assert_called_once()
        self.wf.critic_client.generate.assert_not_called()
        
        self.wf.architect_client.generate.reset_mock()
        
        # 3. Test routing with no keywords (should fall back to model_name, which is critic_client due to collision)
        asyncio.run(handler(model_name=model_key, prompt="test", system_instruction="Hello World."))
        self.wf.critic_client.generate.assert_called_once()

    def test_autonomous_query_toggle_controls_custom_att_tools(self):
        from workflow_components.autonomy_mixin import AutonomyWorkflowMixin

        self.wf.initialize_autonomy = AutonomyWorkflowMixin.initialize_autonomy.__get__(self.wf)
        self.wf.architect_client = unittest.mock.MagicMock()
        self.wf.planner_client = unittest.mock.MagicMock()
        self.wf.writer_client = unittest.mock.MagicMock()
        self.wf.critic_client = unittest.mock.MagicMock()
        self.wf.scanner_client = unittest.mock.MagicMock()
        config.ENABLE_AUTONOMY_SUITE = True
        config.ENABLE_AUTONOMOUS_QUERIES = False

        self.wf.initialize_autonomy()

        custom_tools = {"query_sqlite", "search_faiss", "read_file_chunk", "read_file_tail"}
        self.assertTrue(custom_tools.isdisjoint(self.wf.att_manager.global_tools))
        self.assertNotIn("query_sqlite", self.wf.att_manager.tool_auditors)
        for preset_name in (
            "conflict_resolution",
            "database_management",
            "planning",
            "editorial",
            "world_bible",
            "plot_outline",
        ):
            self.assertEqual(len(self.wf.att_manager.get_preset(preset_name)["roles"]), 3)

    def test_att_sql_tool_commits_with_thread_safe_connection_and_dmc_has_no_tools(self):
        import asyncio
        from memory import MemoryManager
        from workflow_components.autonomy_mixin import AutonomyWorkflowMixin

        memory = MemoryManager(
            os.path.join(self.tmpdir, "att_tool.db"),
            os.path.join(self.tmpdir, "att_tool.faiss"),
        )
        self.wf.memory = memory
        self.wf.initialize_autonomy = AutonomyWorkflowMixin.initialize_autonomy.__get__(self.wf)
        self.wf.architect_client = unittest.mock.MagicMock()
        self.wf.planner_client = unittest.mock.MagicMock()
        self.wf.writer_client = unittest.mock.MagicMock()
        self.wf.critic_client = unittest.mock.MagicMock()
        self.wf.scanner_client = unittest.mock.MagicMock()
        self.wf.embedding_client = unittest.mock.MagicMock()
        config.ENABLE_AUTONOMY_SUITE = True
        config.ENABLE_AUTONOMOUS_QUERIES = True

        try:
            self.wf.initialize_autonomy()
            tool = self.wf.att_manager.global_tools["query_sqlite"]
            result = asyncio.run(
                tool(
                    "INSERT INTO schema_meta (key, value) "
                    "VALUES ('att_tool_commit', 'yes')"
                )
            )
            self.assertIn("Rows affected: 1", result)
            self.assertEqual(memory.get_schema_meta("att_tool_commit"), "yes")

            committee_team = self.wf.db_committee._create_team()
            self.assertEqual(committee_team.tools, {})
        finally:
            memory.close()
            self.wf.memory = None

    def test_faiss_rebuild_and_recovery(self):
        # 1. Verify rebuild_vector_index_from_metadata is non-blocking when index is None
        from memory import MemoryManager
        import io
        from contextlib import redirect_stdout
        
        db_path = os.path.join(self.tmpdir, "test_memory.db")
        faiss_path = os.path.join(self.tmpdir, "test_faiss.faiss")
        
        mem = MemoryManager(db_path=db_path, faiss_path=faiss_path, embedding_dim=4)
        mem.index = None # Simulate missing/corrupted index file
        
        # Seed metadata table so there is something to rebuild
        mem.cursor.execute(
            "INSERT INTO vector_metadata (faiss_id, content, metadata, source_commit_id, is_deleted) VALUES (?, ?, ?, ?, ?)",
            (0, "Lore detail", "{}", "commit_1", 0)
        )
        mem.conn.commit()
        
        # Dummy embedding function returning a 4-dimensional vector
        def dummy_embedding(text):
            return [0.1, 0.2, 0.3, 0.4]
            
        f = io.StringIO()
        with redirect_stdout(f):
            stats = mem.rebuild_vector_index_from_metadata(dummy_embedding)
            
        self.assertEqual(stats["rebuilt"], 1)
        self.assertIsNotNone(mem.index)
        self.assertEqual(mem.index.ntotal, 1)
        # Verify check result was outputted to stdout
        self.assertIn("Warning: FAISS index file is missing or corrupted.", f.getvalue())
        
        # 2. Verify automatic rebuild on WorkflowManager startup when memory index is None
        from workflow import WorkflowManager
        
        with unittest.mock.patch("workflow.WorkflowManager.rebuild_vector_index") as mock_rebuild:
            with unittest.mock.patch("workflow.LLMClient") as mock_client:
                with unittest.mock.patch("workflow.config") as mock_cfg:
                    mock_cfg.DB_PATH = db_path
                    mock_cfg.FAISS_INDEX_PATH = faiss_path
                    mock_cfg.TIER_3_SEARCH_LIMIT = 5
                    mock_cfg.retrieval_section = {"tier_3_search_limit": 5}
                    
                    with unittest.mock.patch("workflow.MemoryManager") as mock_mem_class:
                        mock_mem_inst = unittest.mock.MagicMock()
                        mock_mem_inst.index = None
                        mock_mem_inst.cursor.fetchone.return_value = (1,)
                        mock_mem_class.return_value = mock_mem_inst
                        
                        # Initialize workflow manager
                        wf_mgr = WorkflowManager()
                        
                        # It should have automatically triggered rebuild_vector_index on startup!
                        mock_rebuild.assert_called_once()
                        wf_mgr.close()

if __name__ == "__main__":
    unittest.main()
