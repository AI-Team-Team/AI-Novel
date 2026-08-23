from .common import *


class LLMClientErrorFlowTests(unittest.TestCase):
    def test_generate_raises_structured_error_when_client_missing(self):
        client = LLMClient.__new__(LLMClient)
        client.model_type = "openai"
        client.openai_client = None
        client.gemini_client = None
        client.logger = logging.getLogger("llm-test")
        with self.assertRaises(LLMClientError):
            client.generate("hello")

class QueryIntentPipelineTests(unittest.TestCase):
    class _EmbeddingStub:
        def get_embedding(self, text):
            return [0.1] * 768

    def setUp(self):
        self.db_path = os.path.join(ROOT_DIR, "novel", "process", "test_query_intent.db")
        self.faiss_path = os.path.join(ROOT_DIR, "novel", "process", "test_query_intent.faiss")
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        if os.path.exists(self.faiss_path):
            os.remove(self.faiss_path)
        self.mm = MemoryManager(self.db_path, self.faiss_path)
        self.mgr = StoryStateManager(self.mm, embedding_client=self._EmbeddingStub())

    def tearDown(self):
        self.mm.close()

    def test_build_context_package_runs_full_chain(self):
        self.mm.upsert_character(name="Alice", status="alive")
        self.mm.add_event(
            "Arrival",
            "Alice arrives at Harbor.",
            "Day 1",
            2,
            ["Alice"],
            "Harbor",
        )
        self.mm.add_semantic_fact(
            "Alice notices a torn blue flag at Harbor.",
            [0.1] * 768,
            {"location": "Harbor", "type": "visual"},
        )
        pkg = self.mgr.build_context_package(
            task_type="planner",
            chapter_num=2,
            previous_summary="Alice reached Harbor",
            recent_events_limit=5,
            conflicts_limit=10,
            user_request="plan chapter 2",
        )
        self.assertIn("intent", pkg)
        self.assertIn("characters", pkg)
        self.assertIn("events", pkg)
        self.assertIn("semantic_summary", pkg)
        self.assertTrue(pkg["intent"]["should_semantic"])

    def test_writer_intent_forces_semantic_when_state_exists(self):
        self.mm.upsert_character(name="Bob", status="alive")
        pkg = self.mgr.build_context_package(
            task_type="writer",
            chapter_num=1,
            previous_summary=None,
            recent_events_limit=5,
            conflicts_limit=10,
            user_request="write chapter 1",
        )
        self.assertTrue(pkg["intent"]["should_semantic"])
