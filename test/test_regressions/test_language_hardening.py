from .common import *


class LanguageGuardNameExclusionTests(unittest.TestCase):
    """Tests for improved language guard that excludes known character names."""

    def test_language_confidence_with_name_exclusion(self):
        text = "The hero Lin Bai walked through the ancient forest."
        without_exclusion = language_confidence(text)
        with_exclusion = language_confidence(text, exclude_names=["\u6797\u767d"])
        # Without exclusion: if Chinese name was in text it would affect ratio
        # With exclusion: the name is removed so ratio changes
        # In this case the text is pure English (Latin chars), so both should be the same
        self.assertGreater(without_exclusion["english"], 0.9)

    def test_language_confidence_chinese_names_in_english(self):
        # Simulate English text with Chinese character names
        text = "The warrior \u82cf\u4e91\u6eaa fought beside \u9648\u660e\u8fdc in the battle."
        without_exclusion = language_confidence(text)
        with_exclusion = language_confidence(text, exclude_names=["\u82cf\u4e91\u6eaa", "\u9648\u660e\u8fdc"])
        # Without exclusion: CJK ratio is elevated
        self.assertGreater(without_exclusion["chinese"], 0.0)
        # With exclusion: CJK should be 0 or near 0
        self.assertLessEqual(with_exclusion["chinese"], 0.01)

class HardeningRegressionTests(unittest.TestCase):
    def setUp(self):
        self.db_path = os.path.join(ROOT_DIR, "novel", "process", "test_hardening.db")
        self.faiss_path = os.path.join(ROOT_DIR, "novel", "process", "test_hardening.faiss")
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        if os.path.exists(self.faiss_path):
            os.remove(self.faiss_path)
        self.mm = MemoryManager(self.db_path, self.faiss_path)

    def tearDown(self):
        self.mm.close()

    def test_temporal_vector_retrieval_future_gate_and_proximity_boost(self):
        if self.mm.index is None:
            self.skipTest("FAISS unavailable in this environment")

        # Insert test details with chapter attributes in metadata
        self.mm.add_semantic_fact("Permanent Lore detail", [0.1] * 768, {"location": "Town"}, chapter_num=0)
        self.mm.add_semantic_fact("Chapter 5 Proximity detail", [0.1] * 768, {"location": "Town"}, chapter_num=5)
        self.mm.add_semantic_fact("Future leak detail", [0.1] * 768, {"location": "Town"}, chapter_num=10)

        # Build context package for Chapter 5
        mgr = StoryStateManager(self.mm, embedding_client=None)

        # Mock embedding and search to return all three details with dummy scores
        hits = [
            {"content": "Permanent Lore detail", "metadata": {"location": "Town", "chapter": 0}, "score": 0.5},
            {"content": "Chapter 5 Proximity detail", "metadata": {"location": "Town", "chapter": 5}, "score": 0.5},
            {"content": "Future leak detail", "metadata": {"location": "Town", "chapter": 10}, "score": 0.5},
        ]

        # 1. Test Future Gate Filter: chapter 10 must be discarded when current_chapter_num is 5
        aligned_hits = mgr.cross_tier_align_semantic_hits(
            hits=hits,
            db_chars=[],
            strict_mode=False,
            current_chapter_num=5,
        )
        contents = [hit["content"] for hit in aligned_hits]
        self.assertIn("Permanent Lore detail", contents)
        self.assertIn("Chapter 5 Proximity detail", contents)
        self.assertNotIn("Future leak detail", contents)  # strictly gated!

        # 2. Test Proximity Reranking Boost: Chapter 5 should rank higher than Permanent Lore because of proximity bonus
        ranked_hits = mgr.rerank_semantic_hits(
            hits=aligned_hits,
            focus_entities=[],
            focus_locations=[],
            target_chapter=5,
        )
        self.assertEqual(ranked_hits[0]["content"], "Chapter 5 Proximity detail")
        self.assertEqual(ranked_hits[1]["content"], "Permanent Lore detail")

    def test_language_guard_bounded_rewrite_loop_success(self):
        class _MockClient:
            def __init__(self):
                self.calls = 0
            def generate(self, prompt, system_instruction=None):
                self.calls += 1
                if self.calls == 1:
                    return "这是混合语言 mixed text"
                return "This is pure English text."

        wf = WorkflowManager.__new__(WorkflowManager)
        wf.logger = logging.getLogger("language-guard-success-test")
        wf._is_expected_language = lambda text: "mixed" not in text.lower() and "这是" not in text
        wf._language_name = lambda: "English"
        wf._language_rule = lambda: "Use English only."
        wf._get_known_character_names = lambda: []
        wf._log_llm_interaction = lambda **kwargs: None

        old_lang = config.LANGUAGE
        try:
            config.LANGUAGE = "en"
            res = wf._enforce_output_language(
                client=_MockClient(),
                role="Writer",
                text="这是混合语言 mixed text",
                system_instruction="system",
                chapter_num=1,
            )
            self.assertEqual(res, "This is pure English text.")
        finally:
            config.LANGUAGE = old_lang

    def test_language_guard_bounded_rewrite_loop_failure_raises_error(self):
        class _MockClient:
            def generate(self, prompt, system_instruction=None):
                return "这是混合语言 mixed text"  # always wrong

        wf = WorkflowManager.__new__(WorkflowManager)
        wf.logger = logging.getLogger("language-guard-failure-test")
        wf._is_expected_language = lambda text: "mixed" not in text.lower() and "这是" not in text
        wf._language_name = lambda: "English"
        wf._language_rule = lambda: "Use English only."
        wf._get_known_character_names = lambda: []
        wf._log_llm_interaction = lambda **kwargs: None

        old_lang = config.LANGUAGE
        try:
            config.LANGUAGE = "en"
            with self.assertRaises(RuntimeError) as ctx:
                wf._enforce_output_language(
                    client=_MockClient(),
                    role="Writer",
                    text="这是混合语言 mixed text",
                    system_instruction="system",
                    chapter_num=1,
                )
            self.assertEqual(
                get_message(
                    "runtime.language_guard_error",
                    language="English",
                    attempts=2,
                    role="Writer",
                ),
                str(ctx.exception),
            )
        finally:
            config.LANGUAGE = old_lang
