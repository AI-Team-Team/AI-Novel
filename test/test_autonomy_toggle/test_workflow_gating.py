from .base import *


class WorkflowGatingTests(AutonomyTestCase):
    def test_planning_mixin_respects_toggle(self):
        # 1. Test when ENABLE_AUTONOMY_SUITE is True (should call att_manager)
        config.ENABLE_AUTONOMY_SUITE = True
        self.wf._append_structured_discussion = unittest.mock.MagicMock()
        self.wf.get_guide_path = lambda chapter_num: "guide_path"
        team = make_team("Reviewer_Arbitrator")
        self.wf._create_att_team = unittest.mock.MagicMock(return_value=team)
        self.wf._execute_att_discussion = unittest.mock.MagicMock(
            return_value=make_discussion_result(
                team, {"Reviewer_Arbitrator": "Refined Guide"}
            )
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
        team = make_team("Editor_In_Chief")
        self.wf._create_att_team = unittest.mock.MagicMock(return_value=team)
        self.wf._execute_att_discussion = unittest.mock.MagicMock(
            return_value=make_discussion_result(
                team, {"Editor_In_Chief": "Polished Prose"}
            )
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
        team = make_team("Arc_Arbitrator")
        self.wf._create_att_team = unittest.mock.MagicMock(return_value=team)
        self.wf._execute_att_discussion = unittest.mock.MagicMock(
            return_value=make_discussion_result(
                team, {"Arc_Arbitrator": "Refined Outline"}
            )
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
