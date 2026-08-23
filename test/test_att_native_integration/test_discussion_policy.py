from .common import *


class StructuredDiscussionPolicyTests(unittest.TestCase):
    def setUp(self):
        self.team = make_team("Critic", "Designated_Arbitrator")

    def test_creative_partial_accepts_completed_designated_final_turn(self):
        result = make_discussion_result(
            self.team,
            {
                "Critic": "Critique",
                "Designated_Arbitrator": "可采用的最终稿",
            },
            status=DiscussionStatus.PARTIAL,
            incomplete=("Critic",),
        )
        self.assertEqual(
            select_designated_answer(
                result,
                self.team,
                "Designated_Arbitrator",
                "editorial",
                "accept_designated_member",
            ),
            "可采用的最终稿",
        )

    def test_governance_partial_is_rejected_even_with_designated_answer(self):
        result = make_discussion_result(
            self.team,
            {"Designated_Arbitrator": '{"approved": true}'},
            status=DiscussionStatus.PARTIAL,
            incomplete=("Critic",),
        )
        with self.assertRaises(ATTDiscussionPolicyError):
            select_designated_answer(
                result,
                self.team,
                "Designated_Arbitrator",
                "database_management",
                "reject",
            )

    def test_creative_partial_rejects_incomplete_designated_turn(self):
        result = make_discussion_result(
            self.team,
            {"Critic": "Critique"},
            status=DiscussionStatus.PARTIAL,
            incomplete=("Designated_Arbitrator",),
        )
        with self.assertRaises(ATTDiscussionPolicyError):
            select_designated_answer(
                result,
                self.team,
                "Designated_Arbitrator",
                "editorial",
                "accept_designated_member",
            )

    def test_unknown_partial_policy_fails_closed(self):
        result = make_discussion_result(
            self.team,
            {"Designated_Arbitrator": "Final"},
        )
        with self.assertRaises(ATTDiscussionPolicyError):
            select_designated_answer(
                result,
                self.team,
                "Designated_Arbitrator",
                "editorial",
                "unexpected-policy",
            )
