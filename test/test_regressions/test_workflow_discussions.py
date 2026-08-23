from .common import *


class WorkflowJsonExtractionTests(unittest.TestCase):
    def setUp(self):
        self.wf = WorkflowManager.__new__(WorkflowManager)
        self.wf.logger = logging.getLogger("workflow-test")

    def test_extract_json_from_noisy_output(self):
        noisy = """Some analysis before.
```text
not json
```
Final payload:
{"events":[{"event_name":"E1"}],"details":[]}
extra tail"""
        data = self.wf._extract_json(noisy)
        self.assertIsInstance(data, dict)
        self.assertEqual(data["events"][0]["event_name"], "E1")

    def test_validate_fact_payload_rejects_wrong_shape(self):
        errors = self.wf._validate_fact_payload({"events": {}, "details": []})
        self.assertTrue(any("events" in e for e in errors))

    def test_planner_intent_gate_skips_empty_chapter1(self):
        intent = self.wf._build_planner_retrieval_intent(
            chapter_num=1,
            previous_summary=None,
            db_chars=[],
            db_events=[],
            pending_conflicts=[],
        )
        self.assertFalse(intent["should_semantic"])

    def test_rerank_prefers_entity_and_location_matches(self):
        hits = [
            {"content": "Alice enters the Harbor in rain.", "metadata": {"location": "Harbor"}, "score": 0.7},
            {"content": "Random market detail.", "metadata": {"location": "Market"}, "score": 0.2},
        ]
        ranked = self.wf._rerank_semantic_hits(hits, focus_entities=["Alice"], focus_locations=["Harbor"])
        self.assertEqual(ranked[0]["metadata"]["location"], "Harbor")

    def test_language_detector(self):
        old_lang = config.LANGUAGE
        try:
            config.LANGUAGE = "zh-CN"
            self.assertTrue(self.wf._is_expected_language("这是中文输出。"))
            self.assertFalse(self.wf._is_expected_language("This is English."))
            config.LANGUAGE = "en"
            self.assertTrue(self.wf._is_expected_language("This is English."))
            self.assertFalse(self.wf._is_expected_language("这是中文输出。"))
        finally:
            config.LANGUAGE = old_lang

    def test_language_confidence_scores(self):
        zh_score = language_confidence("这是中文句子。")
        en_score = language_confidence("This is an English sentence.")
        mixed_score = language_confidence("这是 mixed English 文本")
        self.assertGreater(zh_score["chinese"], zh_score["english"])
        self.assertGreater(en_score["english"], en_score["chinese"])
        self.assertGreater(mixed_score["chinese"], 0.0)
        self.assertGreater(mixed_score["english"], 0.0)

class WorkflowGuideDiscussionTests(unittest.TestCase):
    class _StubClient:
        def __init__(self, outputs):
            self.outputs = list(outputs)

        def generate(self, prompt, system_instruction=None, temperature=0.7, require_json=False, **kwargs):
            if require_json:
                return '{"is_healthy": true, "reason": "ok"}'
            if not self.outputs:
                raise RuntimeError("No output configured")
            return self.outputs.pop(0)

    def test_guide_discussion_revises_contract(self):
        tmpdir = tempfile.mkdtemp(prefix="guide_discussion_")
        wf = WorkflowManager.__new__(WorkflowManager)
        wf.logger = logging.getLogger("workflow-guide-discussion-test")
        wf.critic_client = self._StubClient([
            "Final Answer: Needs revision: strengthen midpoint conflict.",
            "Final Answer: Revised guide with stronger midpoint conflict.",
            "Final Answer: Final Answer: Revised guide with stronger midpoint conflict."
        ])
        wf.planner_client = wf.critic_client
        wf._enforce_output_language = lambda client, role, text, system_instruction, chapter_num=None, world_building=False: text
        wf._log_llm_interaction = lambda **kwargs: None
        wf._language_rule = lambda: "Use English only."
        wf.discussions_dir = os.path.join(tmpdir, "process", "discussions")
        wf.guides_dir = os.path.join(tmpdir, "frame", "chapter_guides")
        os.makedirs(wf.guides_dir, exist_ok=True)
        old_att_state_path = config.ATT_STATE_DB_PATH
        config.ATT_STATE_DB_PATH = os.path.join(tmpdir, "att_state.db")
        wf.initialize_autonomy()

        old_lang = config.LANGUAGE
        old_rounds = config.CHAPTER_GUIDE_DISCUSSION_ROUNDS
        try:
            config.LANGUAGE = "en"
            config.CHAPTER_GUIDE_DISCUSSION_ROUNDS = 1
            revised = wf._refine_chapter_guide_with_discussion(
                chapter_num=1,
                guide="Initial guide.",
                prompts={"critic": "critic", "planner": "planner"},
            )
        finally:
            config.LANGUAGE = old_lang
            config.CHAPTER_GUIDE_DISCUSSION_ROUNDS = old_rounds
            wf.close_autonomy()
            config.ATT_STATE_DB_PATH = old_att_state_path
            shutil.rmtree(tmpdir, ignore_errors=True)

        self.assertIn("Revised guide", revised)

class WorkflowTextDiscussionTests(unittest.TestCase):
    class _StubClient:
        def __init__(self, outputs):
            self.outputs = list(outputs)

        def generate(self, prompt, system_instruction=None, temperature=0.7, require_json=False, **kwargs):
            if require_json:
                return '{"is_healthy": true, "reason": "ok"}'
            if not self.outputs:
                raise RuntimeError("No output configured")
            return self.outputs.pop(0)

    def test_text_discussion_log_is_saved(self):
        tmpdir = tempfile.mkdtemp(prefix="text_discussion_")
        wf = WorkflowManager.__new__(WorkflowManager)
        wf.logger = logging.getLogger("workflow-text-discussion-test")
        wf.critic_client = self._StubClient([
            "Final Answer: NEEDS_REVISION: no\nRATIONALE: ok\nPATCH_GUIDANCE: none",
            "Final Answer: Revised chapter text.",
            "Final Answer: Revised chapter text."
        ])
        wf.writer_client = wf.critic_client
        wf.discussions_dir = os.path.join(tmpdir, "process", "discussions")
        wf.reviews_dir = os.path.join(tmpdir, "process", "reviews")
        wf.revisions_dir = os.path.join(tmpdir, "process", "revisions")
        wf.chapters_dir = os.path.join(tmpdir, "main_text", "chapters")
        os.makedirs(wf.chapters_dir, exist_ok=True)
        os.makedirs(wf.revisions_dir, exist_ok=True)
        old_att_state_path = config.ATT_STATE_DB_PATH
        config.ATT_STATE_DB_PATH = os.path.join(tmpdir, "att_state.db")
        wf.initialize_autonomy()

        wf._critic_review_chapter = lambda chapter_num, guide_content, chapter_text, prompts: (
            "NEEDS_REVISION: no\nRATIONALE: ok\nPATCH_GUIDANCE: none"
        )
        wf._needs_revision = lambda review_text: False
        wf._enforce_output_language = lambda client, role, text, system_instruction, chapter_num=None, world_building=False: text
        wf._log_llm_interaction = lambda **kwargs: None

        old_lang = config.LANGUAGE
        old_rounds = config.CHAPTER_TEXT_DISCUSSION_ROUNDS
        try:
            config.LANGUAGE = "en"
            config.CHAPTER_TEXT_DISCUSSION_ROUNDS = 1
            wf._review_and_revise_chapter(1, "guide", "chapter text", {"critic": "c", "writer": "w"})
        finally:
            config.LANGUAGE = old_lang
            config.CHAPTER_TEXT_DISCUSSION_ROUNDS = old_rounds
            wf.close_autonomy()
            config.ATT_STATE_DB_PATH = old_att_state_path

        discussion_path = os.path.join(tmpdir, "process", "discussions", "chapter_001_text_discussion.md")
        index_path = os.path.join(tmpdir, "process", "discussions", "discussion_index.jsonl")
        self.assertTrue(os.path.exists(discussion_path))
        self.assertTrue(os.path.exists(index_path))
        with open(discussion_path, "r", encoding="utf-8") as f:
             content = f.read()
        self.assertIn("[chapter_text] chapter=1 round=1 role=Chapter_Editorial_Committee", content)
        self.assertIn("`log_id`", content)
        self.assertIn("`input_summary`", content)
        self.assertIn("`output_summary`", content)
        self.assertIn("`att_discussion_id`", content)
        self.assertIn("`att_status`", content)
        self.assertIn("`att_operational_status`", content)
        self.assertIn("`artifact_paths`", content)
        with open(index_path, "r", encoding="utf-8") as f:
            first = json.loads(f.readline().strip())
        self.assertIn("phase_type", first)
        self.assertIn("decision", first)
        self.assertIn("artifact_paths", first)
        self.assertEqual(first["att_status"], "completed")
        self.assertEqual(first["att_operational_status"], "healthy")
        self.assertTrue(first["att_discussion_id"])
        shutil.rmtree(tmpdir, ignore_errors=True)
