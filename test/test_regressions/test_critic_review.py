from .common import *


class CriticFactReviewTests(unittest.TestCase):
    """Tests for the LLM Critic batch fact review that replaced the old Arbiter."""

    class _StubCriticClient:
        def __init__(self, response_json):
            self.response_json = response_json

        def generate(self, prompt, system_instruction=None, temperature=0.7, require_json=False):
            return json.dumps(self.response_json)

    def setUp(self):
        self.db_path = os.path.join(ROOT_DIR, "novel", "process", "test_critic_review.db")
        self.faiss_path = os.path.join(ROOT_DIR, "novel", "process", "test_critic_review.faiss")
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        if os.path.exists(self.faiss_path):
            os.remove(self.faiss_path)
        self.mm = MemoryManager(self.db_path, self.faiss_path)

    def tearDown(self):
        self.mm.close()

    def _build_workflow_stub(self, critic_response):
        wf = WorkflowManager.__new__(WorkflowManager)
        wf.logger = logging.getLogger("critic-review-test")
        wf.memory = self.mm
        wf.state_manager = StoryStateManager(self.mm, embedding_client=None)
        wf.critic_client = self._StubCriticClient(critic_response)
        wf._log_llm_interaction = lambda **kwargs: None
        wf._language_rule = lambda: "Use English only."
        return wf

    def test_critic_review_removes_blocking_facts(self):
        self.mm.upsert_character(name="Hero", status="dead", source="test", chapter_num=1)
        self.mm.add_rule("Magic", "No resurrection allowed.", strictness=1, source="test", chapter_num=1)
        facts_data = {
            "new_characters": [],
            "updated_characters": [{"name": "Hero", "status": "alive"}],
            "new_rules": [],
            "relationships": [],
            "events": [
                {"event_name": "Hero Revives", "description": "Hero comes back to life.",
                 "timestamp_str": "Day 5", "impact_level": 5, "related_entities": ["Hero"], "location": "Temple"},
                {"event_name": "Market Opens", "description": "The market opens at dawn.",
                 "timestamp_str": "Day 5", "impact_level": 1, "related_entities": [], "location": "Town"},
            ],
            "details": [],
        }
        critic_response = {
            "issues": [
                {"fact_type": "updated_character", "fact_index": 0, "severity": "BLOCKING",
                 "reason": "Hero is dead but being set to alive without narrative justification."},
                {"fact_type": "event", "fact_index": 0, "severity": "BLOCKING",
                 "reason": "Contradicts 'No resurrection allowed' rule."},
            ]
        }
        wf = self._build_workflow_stub(critic_response)
        result = wf._critic_review_extracted_facts(
            chapter_num=2, facts_data=facts_data,
            chapter_text="Hero comes back to life in the temple.",
            prompts={"critic": "system"},
        )
        # BLOCKING facts removed
        self.assertEqual(len(result["updated_characters"]), 0)
        self.assertEqual(len(result["events"]), 1)  # Only Market Opens remains
        self.assertEqual(result["events"][0]["event_name"], "Market Opens")
        # Conflicts queued
        conflicts = self.mm.get_pending_conflict_count()
        self.assertEqual(conflicts, 2)

    def test_critic_review_keeps_non_blocking_facts(self):
        facts_data = {
            "new_characters": [],
            "updated_characters": [],
            "new_rules": [],
            "relationships": [{"source": "A", "target": "B", "relation_type": "rivals", "details": "competition"}],
            "events": [],
            "details": [],
        }
        critic_response = {
            "issues": [
                {"fact_type": "relationship", "fact_index": 0, "severity": "NON_BLOCKING",
                 "reason": "A and B were previously described as friends."},
            ]
        }
        wf = self._build_workflow_stub(critic_response)
        result = wf._critic_review_extracted_facts(
            chapter_num=3, facts_data=facts_data,
            chapter_text="A and B compete fiercely.",
            prompts={"critic": "system"},
        )
        # NON_BLOCKING: fact remains in payload
        self.assertEqual(len(result["relationships"]), 1)
        # But conflict is queued
        self.assertEqual(self.mm.get_pending_conflict_count(), 1)
        diagnostics = self.mm.get_pending_conflict_diagnostics(limit=10)
        self.assertEqual(diagnostics[0]["blocking_level"], "NON_BLOCKING")

    def test_critic_review_no_issues_returns_unchanged(self):
        facts_data = {
            "new_characters": [{"name": "Alice", "core_traits": {}, "attributes": {}}],
            "updated_characters": [],
            "new_rules": [],
            "relationships": [],
            "events": [],
            "details": [],
        }
        wf = self._build_workflow_stub({"issues": []})
        result = wf._critic_review_extracted_facts(
            chapter_num=1, facts_data=facts_data,
            chapter_text="Alice enters the story.",
            prompts={"critic": "system"},
        )
        self.assertEqual(len(result["new_characters"]), 1)
        self.assertEqual(self.mm.get_pending_conflict_count(), 0)

    def test_critic_review_failure_returns_data_unchanged(self):
        """If Critic LLM call fails, facts pass through unchanged."""
        facts_data = {
            "new_characters": [{"name": "Bob"}],
            "updated_characters": [],
            "new_rules": [],
            "relationships": [],
            "events": [],
            "details": [],
        }
        wf = WorkflowManager.__new__(WorkflowManager)
        wf.logger = logging.getLogger("critic-review-fail-test")
        wf.memory = self.mm
        wf.state_manager = StoryStateManager(self.mm, embedding_client=None)
        # Critic that raises exception
        class _FailingClient:
            def generate(self, *args, **kwargs):
                raise RuntimeError("LLM unavailable")
        wf.critic_client = _FailingClient()
        wf._log_llm_interaction = lambda **kwargs: None
        wf._language_rule = lambda: "Use English only."
        result = wf._critic_review_extracted_facts(
            chapter_num=1, facts_data=facts_data,
            chapter_text="Bob appears.",
            prompts={"critic": "system"},
        )
        self.assertEqual(len(result["new_characters"]), 1)
        self.assertEqual(self.mm.get_pending_conflict_count(), 0)
