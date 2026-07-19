import logging
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

CURRENT_DIR = os.path.dirname(__file__)
ROOT_DIR = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
SRC_DIR = os.path.join(ROOT_DIR, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import config
from memory import MemoryManager
from state_manager import StoryStateManager
from workflow import WorkflowManager
from llm_client import LLMClient, LLMClientError
from workflow_components.parsing import language_confidence

class MemoryMergeTests(unittest.TestCase):
    def setUp(self):
        self.db_path = os.path.join(ROOT_DIR, "novel", "process", "test_regressions.db")
        self.faiss_path = os.path.join(ROOT_DIR, "novel", "process", "test_regressions.faiss")
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        if os.path.exists(self.faiss_path):
            os.remove(self.faiss_path)
        self.mm = MemoryManager(self.db_path, self.faiss_path)

    def tearDown(self):
        self.mm.close()

    def test_upsert_character_deep_merge(self):
        self.mm.upsert_character(
            name="Alice",
            core_traits={"personality": {"mbti": "INFJ", "fears": {"dark": True}}},
            attributes={"profile": {"age": 20, "city": "A"}},
            status="alive",
        )
        self.mm.upsert_character(
            name="Alice",
            core_traits={"personality": {"fears": {"heights": True}}},
            attributes={"profile": {"age": 21}},
        )
        row = self.mm.get_character("Alice")
        self.assertIsNotNone(row)
        core_traits = row[2]
        attributes = row[4]
        self.assertIn('"mbti": "INFJ"', core_traits)
        self.assertIn('"dark": true', core_traits)
        self.assertIn('"heights": true', core_traits)
        self.assertIn('"age": 21', attributes)
        self.assertIn('"city": "A"', attributes)

    def test_status_resurrection_queues_conflict(self):
        self.mm.upsert_character(name="Bob", status="dead", source="test", chapter_num=1)
        self.mm.upsert_character(name="Bob", status="alive", source="test", chapter_num=2)
        row = self.mm.get_character("Bob")
        self.assertEqual(row[3], "dead")
        self.assertEqual(self.mm.get_pending_conflict_count(), 1)

    def test_resolve_conflict_keep_existing(self):
        self.mm.upsert_character(name="Carol", status="dead", source="test", chapter_num=1)
        self.mm.upsert_character(name="Carol", status="alive", source="test", chapter_num=2)
        conflicts = self.mm.get_pending_conflicts(limit=10)
        self.assertEqual(len(conflicts), 1)
        conflict_id = conflicts[0][0]
        ok = self.mm.resolve_conflict(conflict_id, "keep_existing", resolver_note="keep dead")
        self.assertTrue(ok)
        row = self.mm.get_character("Carol")
        self.assertEqual(row[3], "dead")
        detail = self.mm.get_conflict_by_id(conflict_id)
        self.assertEqual(detail[8], "RESOLVED")

    def test_resolve_conflict_apply_incoming(self):
        self.mm.upsert_character(name="Dave", status="dead", source="test", chapter_num=1)
        self.mm.upsert_character(name="Dave", status="alive", source="test", chapter_num=2)
        conflict_id = self.mm.get_pending_conflicts(limit=10)[0][0]
        ok = self.mm.resolve_conflict(conflict_id, "apply_incoming", resolver_note="allow revive")
        self.assertTrue(ok)
        row = self.mm.get_character("Dave")
        self.assertEqual(row[3], "alive")

    def test_chapter_commit_lifecycle(self):
        commit_id = self.mm.begin_chapter_commit(3, "scan_chapter", payload={"events": []})
        self.mm.finalize_chapter_commit(commit_id, status="COMPLETED", conflicts_count=2)
        self.mm.cursor.execute(
            "SELECT status, conflicts_count FROM chapter_commits WHERE commit_id = ?",
            (commit_id,),
        )
        row = self.mm.cursor.fetchone()
        self.assertEqual(row[0], "COMPLETED")
        self.assertEqual(row[1], 2)

    def test_batch_rollback_reverts_sqlite_writes(self):
        self.mm.begin_batch()
        self.mm.upsert_character(name="Eve", status="alive", source="test")
        self.mm.end_batch(success=False)
        self.assertIsNone(self.mm.get_character("Eve"))

    def test_add_rule_deduplicates_exact_payload(self):
        first_id = self.mm.add_rule(
            "Magic",
            "No resurrection",
            strictness=1,
            source="test",
            chapter_num=1,
            source_commit_id="commit-1",
            intent_tag="scan_extract",
        )
        second_id = self.mm.add_rule(
            "Magic",
            "No resurrection",
            strictness=1,
            source="test",
            chapter_num=2,
            source_commit_id="commit-2",
            intent_tag="scan_extract",
        )
        self.assertEqual(first_id, second_id)
        self.mm.cursor.execute("SELECT COUNT(*) FROM world_rules WHERE category = ? AND rule_content = ?", ("Magic", "No resurrection"))
        count = self.mm.cursor.fetchone()[0]
        self.assertEqual(count, 1)
        self.mm.cursor.execute(
            "SELECT source_commit_id, intent_tag FROM world_rules WHERE id = ?",
            (first_id,),
        )
        row = self.mm.cursor.fetchone()
        self.assertEqual(row[0], "commit-1")
        self.assertEqual(row[1], "scan_extract")

    def test_add_event_deduplicates_exact_payload(self):
        first_id = self.mm.add_event(
            "Prologue",
            "Story starts",
            "Day 1",
            3,
            ["Hero"],
            "Town",
            source="test",
            chapter_num=1,
            source_commit_id="event-commit-1",
            intent_tag="scan_extract",
        )
        second_id = self.mm.add_event(
            "Prologue",
            "Story starts",
            "Day 1",
            3,
            ["Hero"],
            "Town",
            source="test",
            chapter_num=2,
        )
        self.assertEqual(first_id, second_id)
        self.mm.cursor.execute("SELECT COUNT(*) FROM timeline_events WHERE event_name = ? AND timestamp_str = ?", ("Prologue", "Day 1"))
        count = self.mm.cursor.fetchone()[0]
        self.assertEqual(count, 1)
        self.mm.cursor.execute(
            "SELECT source_commit_id, intent_tag FROM timeline_events WHERE id = ?",
            (first_id,),
        )
        row = self.mm.cursor.fetchone()
        self.assertEqual(row[0], "event-commit-1")
        self.assertEqual(row[1], "scan_extract")

    def test_relationship_type_change_queues_conflict_and_keeps_existing(self):
        self.mm.add_relationship("Alice", "Bob", "friends", "childhood", source_tag="test", chapter_num=1)
        self.mm.add_relationship("Alice", "Bob", "siblings", "retcon", source_tag="test", chapter_num=2)
        rels = self.mm.get_relationships("Alice")
        self.assertTrue(any(r[1] == "Bob" and r[2] == "friends" for r in rels))
        self.assertEqual(self.mm.get_pending_conflict_count(), 1)
        diagnostics = self.mm.get_pending_conflict_diagnostics(limit=10)
        self.assertEqual(diagnostics[0]["blocking_level"], "NON_BLOCKING")

    def test_relationship_with_dead_character_generates_non_blocking_conflict(self):
        self.mm.upsert_character(name="Ghost", status="dead", source="test", chapter_num=1)
        self.mm.add_relationship(
            "Ghost",
            "Alice",
            "mentor",
            "legacy mentor relationship",
            source_tag="test",
            chapter_num=2,
        )
        rels = self.mm.get_relationships("Ghost")
        self.assertTrue(any(r[1] == "Alice" for r in rels))
        diagnostics = self.mm.get_pending_conflict_diagnostics(limit=10)
        self.assertTrue(any(d["conflict_type"] == "relationship_dead_character_involved" for d in diagnostics))
        self.assertTrue(any(d["blocking_level"] == "NON_BLOCKING" for d in diagnostics))

    def test_strict_rule_contradiction_no_longer_blocked_at_memory_layer(self):
        """Rule contradiction check removed from memory.py — now handled by LLM Critic."""
        self.mm.add_rule("Magic", "No resurrection is allowed.", strictness=1, source="test", chapter_num=1)
        rid = self.mm.add_rule("Magic", "Resurrection is allowed.", strictness=1, source="test", chapter_num=2)
        self.assertGreater(rid, 0)
        self.mm.cursor.execute("SELECT COUNT(*) FROM world_rules WHERE category = ?", ("Magic",))
        count = self.mm.cursor.fetchone()[0]
        self.assertEqual(count, 2)  # Both rules inserted (semantic contradiction check delegated to Critic)
        self.assertEqual(self.mm.get_pending_conflict_count(), 0)

    def test_timeline_same_key_conflict_is_blocked(self):
        self.mm.add_event(
            "Battle of Gate",
            "The first battle starts",
            "Year 1",
            4,
            ["A", "B"],
            "North Gate",
            source="test",
            chapter_num=1,
        )
        self.mm.add_event(
            "Battle of Gate",
            "The battle never happened",
            "Year 1",
            4,
            ["A", "B"],
            "South Gate",
            source="test",
            chapter_num=2,
        )
        self.mm.cursor.execute("SELECT COUNT(*) FROM timeline_events WHERE event_name = ? AND timestamp_str = ?", ("Battle of Gate", "Year 1"))
        count = self.mm.cursor.fetchone()[0]
        self.assertEqual(count, 1)
        self.assertEqual(self.mm.get_pending_conflict_count(), 1)

    def test_event_with_dead_character_is_flagged_non_blocking(self):
        """Events with dead characters are inserted but flagged NON_BLOCKING.
        Semantic judgment (memorial vs. active) is delegated to the LLM Critic."""
        self.mm.upsert_character(name="Hero", status="dead", source="test", chapter_num=1)
        eid = self.mm.add_event(
            "Hero Returns",
            "Hero appears in the city square.",
            "Day 3",
            3,
            ["Hero"],
            "City",
            source="test",
            chapter_num=2,
        )
        self.assertGreater(eid, 0)  # Event IS inserted
        self.mm.cursor.execute("SELECT COUNT(*) FROM timeline_events WHERE event_name = ?", ("Hero Returns",))
        count = self.mm.cursor.fetchone()[0]
        self.assertEqual(count, 1)  # Event exists in DB
        self.assertEqual(self.mm.get_pending_conflict_count(), 1)
        self.assertEqual(self.mm.get_pending_blocking_conflict_count(), 0)  # NON_BLOCKING, not BLOCKING
        diagnostics = self.mm.get_pending_conflict_diagnostics(limit=10)
        self.assertTrue(any(d["blocking_level"] == "NON_BLOCKING" for d in diagnostics))

    def test_memorial_event_with_dead_character_also_flagged(self):
        """Memorial events are also flagged NON_BLOCKING (memorial/active distinction
        removed from memory.py, delegated to LLM Critic)."""
        self.mm.upsert_character(name="Hero", status="dead", source="test", chapter_num=1)
        eid = self.mm.add_event(
            "Memorial Ceremony",
            "A funeral memorial recalls Hero's past deeds.",
            "Day 3",
            2,
            ["Hero"],
            "City",
            source="test",
            chapter_num=2,
        )
        self.assertGreater(eid, 0)
        self.mm.cursor.execute("SELECT COUNT(*) FROM timeline_events WHERE event_name = ?", ("Memorial Ceremony",))
        count = self.mm.cursor.fetchone()[0]
        self.assertEqual(count, 1)
        # Now a NON_BLOCKING conflict is queued (Critic will judge if it's truly a problem)
        self.assertEqual(self.mm.get_pending_conflict_count(), 1)
        self.assertEqual(self.mm.get_pending_blocking_conflict_count(), 0)

    def test_event_contradicting_strict_rule_no_longer_blocked(self):
        """Rule contradiction check removed from memory.py — handled by LLM Critic."""
        self.mm.add_rule("Magic", "No resurrection is allowed.", strictness=1, source="test", chapter_num=1)
        eid = self.mm.add_event(
            "Forbidden Ritual",
            "Resurrection is allowed through a ritual tonight.",
            "Day 9",
            5,
            ["Mage"],
            "Temple",
            source="test",
            chapter_num=2,
        )
        self.assertGreater(eid, 0)  # Event IS inserted (no rule check at memory layer)
        self.mm.cursor.execute("SELECT COUNT(*) FROM timeline_events WHERE event_name = ?", ("Forbidden Ritual",))
        count = self.mm.cursor.fetchone()[0]
        self.assertEqual(count, 1)
        self.assertEqual(self.mm.get_pending_conflict_count(), 0)  # No conflict queued at memory layer

    def test_pending_conflict_diagnostics_has_labels_and_diff(self):
        self.mm.upsert_character(name="Nina", status="dead", source="test", chapter_num=1)
        self.mm.upsert_character(name="Nina", status="alive", source="test", chapter_num=2)
        diagnostics = self.mm.get_pending_conflict_diagnostics(limit=10)
        self.assertEqual(len(diagnostics), 1)
        item = diagnostics[0]
        self.assertEqual(item["reason_label"], "CHARACTER_RESURRECTION_CONFLICT")
        self.assertIn("status", item["diff_paths"])
        self.assertIn("priority", item)
        self.assertIn("suggested_action", item)
        self.assertEqual(item["blocking_level"], "BLOCKING")

    def test_conflict_triage_orders_blocking_then_priority(self):
        self.mm.queue_conflict(
            entity_type="relationship",
            entity_key="A->B",
            conflict_type="relationship_type_change",
            incoming_obj={"relation_type": "siblings"},
            existing_obj={"relation_type": "friends"},
            source="test",
            chapter_num=1,
            blocking_level=self.mm.NON_BLOCKING,
            priority=1,
            suggested_action="manual_review_non_blocking",
        )
        self.mm.queue_conflict(
            entity_type="character",
            entity_key="Zed",
            conflict_type="status_dead_to_alive",
            incoming_obj={"status": "alive"},
            existing_obj={"status": "dead"},
            source="test",
            chapter_num=2,
            blocking_level=self.mm.BLOCKING,
            priority=3,
            suggested_action="manual_review_apply_or_keep",
        )
        rows = self.mm.get_pending_conflicts(limit=10)
        self.assertGreaterEqual(len(rows), 2)
        self.assertEqual(rows[0][7], "BLOCKING")
        self.assertGreaterEqual(rows[0][8], rows[1][8])
        nb_rows = self.mm.get_pending_conflicts(limit=10, blocking_level="NON_BLOCKING")
        self.assertTrue(all(r[7] == "NON_BLOCKING" for r in nb_rows))

    def test_batch_rollback_restores_faiss_index_state(self):
        if self.mm.index is None:
            self.skipTest("FAISS unavailable in this environment")
        baseline = self.mm.index.ntotal
        self.mm.begin_batch()
        self.mm.add_semantic_fact("rollback detail", [0.1] * 768, {"location": "test"})
        self.mm.end_batch(success=False)
        self.assertEqual(self.mm.index.ntotal, baseline)
        self.mm.cursor.execute("SELECT COUNT(*) FROM vector_metadata WHERE content = ?", ("rollback detail",))
        count = self.mm.cursor.fetchone()[0]
        self.assertEqual(count, 0)

    def test_rebuild_vector_index_from_metadata(self):
        if self.mm.index is None:
            self.skipTest("FAISS unavailable in this environment")
        self.mm.add_semantic_fact(
            "detail one",
            [0.1] * 768,
            {"location": "A"},
            source_commit_id="v1",
            intent_tag="scan_extract",
        )
        self.mm.add_semantic_fact(
            "detail two",
            [0.2] * 768,
            {"location": "B"},
            source_commit_id="v2",
            intent_tag="scan_extract",
        )
        stats = self.mm.rebuild_vector_index_from_metadata(lambda text: [0.3] * 768)
        self.assertEqual(stats["skipped"], 0)
        self.mm.cursor.execute("SELECT COUNT(*) FROM vector_metadata WHERE is_deleted = 0")
        count = self.mm.cursor.fetchone()[0]
        self.assertEqual(stats["rebuilt"], count)

    def test_rebuild_vector_index_preserves_skipped_rows_as_deleted(self):
        if self.mm.index is None:
            self.skipTest("FAISS unavailable in this environment")
        self.mm.add_semantic_fact("keep me", [0.1] * 768, {"location": "A"})
        self.mm.add_semantic_fact("skip me", [0.2] * 768, {"location": "B"})

        def emb_fn(text):
            if text == "skip me":
                return None
            return [0.3] * 768

        stats = self.mm.rebuild_vector_index_from_metadata(emb_fn)
        self.assertEqual(stats["rebuilt"], 1)
        self.assertEqual(stats["skipped"], 1)
        self.mm.cursor.execute("SELECT COUNT(*) FROM vector_metadata")
        total = self.mm.cursor.fetchone()[0]
        self.assertEqual(total, 2)
        self.mm.cursor.execute("SELECT COUNT(*) FROM vector_metadata WHERE is_deleted = 1")
        deleted_count = self.mm.cursor.fetchone()[0]
        self.assertEqual(deleted_count, 1)
        self.mm.cursor.execute("SELECT faiss_id FROM vector_metadata WHERE content = ?", ("skip me",))
        row = self.mm.cursor.fetchone()
        self.assertIsNotNone(row)
        self.assertLess(row[0], 0)

    def test_init_faiss_load_failure_clears_metadata(self):
        if self.mm.index is None:
            self.skipTest("FAISS unavailable in this environment")
        self.mm.add_semantic_fact("detail one", [0.1] * 768, {"location": "X"})
        self.mm.close()

        # Corrupt FAISS file to force load failure path.
        with open(self.faiss_path, "wb") as f:
            f.write(b"corrupted-faiss-index")

        self.mm = MemoryManager(self.db_path, self.faiss_path)
        self.mm.cursor.execute("SELECT COUNT(*) FROM vector_metadata")
        count = self.mm.cursor.fetchone()[0]
        self.assertEqual(count, 0)

    def test_batch_rollback_with_dimension_reset_keeps_disk_state(self):
        if self.mm.index is None:
            self.skipTest("FAISS unavailable in this environment")
        self.mm.add_semantic_fact("stable detail", [0.1] * 768, {"location": "Stable"})
        self.mm.save_faiss()
        original_dim = self.mm.index.d
        original_total = self.mm.index.ntotal

        self.mm.begin_batch()
        self.mm.add_semantic_fact("new dim detail", [0.4] * 16, {"location": "Temp"})
        self.mm.end_batch(success=False)

        reloaded = MemoryManager(self.db_path, self.faiss_path)
        try:
            self.assertEqual(reloaded.index.d, original_dim)
            self.assertEqual(reloaded.index.ntotal, original_total)
        finally:
            reloaded.close()

    def test_immutable_character_field_conflict_is_blocked(self):
        self.mm.upsert_character(
            name="Iris",
            core_traits={"identity": "agent-7"},
            attributes={"species": "human"},
            source="test",
            chapter_num=1,
        )
        self.mm.upsert_character(
            name="Iris",
            core_traits={"identity": "agent-8"},
            attributes={"species": "android"},
            source="test",
            chapter_num=2,
        )
        row = self.mm.get_character("Iris")
        self.assertIn('"identity": "agent-7"', row[2])
        self.assertIn('"species": "human"', row[4])
        self.assertGreaterEqual(self.mm.get_pending_conflict_count(), 1)

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
        self.assertIn("`artifact_paths`", content)
        with open(index_path, "r", encoding="utf-8") as f:
            first = json.loads(f.readline().strip())
        self.assertIn("phase_type", first)
        self.assertIn("decision", first)
        self.assertIn("artifact_paths", first)
        shutil.rmtree(tmpdir, ignore_errors=True)

class AutoConflictResolverTests(unittest.TestCase):
    def setUp(self):
        self.db_path = os.path.join(ROOT_DIR, "novel", "process", "test_auto_resolve.db")
        self.faiss_path = os.path.join(ROOT_DIR, "novel", "process", "test_auto_resolve.faiss")
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        if os.path.exists(self.faiss_path):
            os.remove(self.faiss_path)
        self.mm = MemoryManager(self.db_path, self.faiss_path)

    def tearDown(self):
        self.mm.close()

    def test_auto_resolver_keeps_existing_hard_fact(self):
        self.mm.upsert_character(name="Nora", status="dead", source="test", chapter_num=1)
        self.mm.upsert_character(name="Nora", status="alive", source="test", chapter_num=2)
        self.assertEqual(self.mm.get_pending_conflict_count(), 1)
        mgr = StoryStateManager(self.mm, embedding_client=None)
        resolved = mgr.auto_resolve_pending_conflicts()
        self.assertEqual(resolved, 1)
        row = self.mm.get_character("Nora")
        self.assertEqual(row[3], "dead")
        self.assertEqual(self.mm.get_pending_conflict_count(), 0)

    def test_auto_resolver_resolves_generic_pending_conflict(self):
        conflict_id = self.mm.queue_conflict(
            entity_type="world_rule",
            entity_key="Magic",
            conflict_type="strict_rule_conflict",
            incoming_obj={"rule": "A"},
            existing_obj={"rule": "B"},
            source="test",
            chapter_num=1,
            notes="test",
        )
        self.assertGreater(conflict_id, 0)
        mgr = StoryStateManager(self.mm, embedding_client=None)
        resolved = mgr.auto_resolve_pending_conflicts()
        self.assertEqual(resolved, 1)
        self.assertEqual(self.mm.get_pending_conflict_count(), 0)

    def test_auto_resolver_ignores_non_blocking_conflicts(self):
        self.mm.queue_conflict(
            entity_type="relationship",
            entity_key="A->B",
            conflict_type="relationship_type_change",
            incoming_obj={"relation_type": "siblings"},
            existing_obj={"relation_type": "friends"},
            source="test",
            chapter_num=1,
            notes="test",
            blocking_level=self.mm.NON_BLOCKING,
        )
        mgr = StoryStateManager(self.mm, embedding_client=None)
        resolved = mgr.auto_resolve_pending_conflicts()
        self.assertEqual(resolved, 0)
        self.assertEqual(self.mm.get_pending_conflict_count(), 1)
        self.assertEqual(self.mm.get_pending_blocking_conflict_count(), 0)

class ConflictGovernanceModeTests(unittest.TestCase):
    class _MemoryStub:
        def __init__(self, blocking_count: int = 0, total_count: int = 0):
            self._blocking_count = blocking_count
            self._total_count = total_count

        def get_pending_blocking_conflict_count(self):
            return self._blocking_count

        def get_pending_conflict_count(self):
            return self._total_count

    class _StateStub:
        def __init__(self):
            self.called = 0

        def auto_resolve_pending_conflicts(self):
            self.called += 1
            return 0

    def _workflow_stub(self, memory, state):
        wf = WorkflowManager.__new__(WorkflowManager)
        wf.logger = logging.getLogger("conflict-governance-mode-test")
        wf.memory = memory
        wf.state_manager = state
        return wf

    def test_auto_keep_existing_mode_calls_auto_resolver(self):
        old_mode = config.BLOCKING_CONFLICT_MODE
        try:
            config.BLOCKING_CONFLICT_MODE = "auto_keep_existing"
            state = self._StateStub()
            wf = self._workflow_stub(memory=self._MemoryStub(0, 0), state=state)
            wf._enforce_conflict_free_state("test_stage")
            self.assertEqual(state.called, 1)
        finally:
            config.BLOCKING_CONFLICT_MODE = old_mode

    def test_manual_block_mode_skips_auto_resolver_and_blocks(self):
        old_mode = config.BLOCKING_CONFLICT_MODE
        try:
            config.BLOCKING_CONFLICT_MODE = "manual_block"
            state = self._StateStub()
            wf = self._workflow_stub(memory=self._MemoryStub(1, 2), state=state)
            with self.assertRaises(RuntimeError):
                wf._enforce_conflict_free_state("test_stage")
            self.assertEqual(state.called, 0)
        finally:
            config.BLOCKING_CONFLICT_MODE = old_mode

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

class CommitReplayRecoveryTests(unittest.TestCase):
    class _EmbeddingStub:
        def get_embedding(self, text):
            return [0.1] * 768

    def setUp(self):
        self.db_path = os.path.join(ROOT_DIR, "novel", "process", "test_commit_replay.db")
        self.faiss_path = os.path.join(ROOT_DIR, "novel", "process", "test_commit_replay.faiss")
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        if os.path.exists(self.faiss_path):
            os.remove(self.faiss_path)
        self.mm = MemoryManager(self.db_path, self.faiss_path)

    def tearDown(self):
        self.mm.close()

    def _build_workflow_stub(self):
        wf = WorkflowManager.__new__(WorkflowManager)
        wf.logger = logging.getLogger("commit-replay-test")
        wf.memory = self.mm
        wf.embedding_client = self._EmbeddingStub()
        wf.state_manager = StoryStateManager(self.mm, wf.embedding_client)
        wf._sync_compact_archives = lambda: None
        return wf

    def test_replay_failed_commit_applies_payload(self):
        payload = {
            "new_characters": [{"name": "ReplayHero", "core_traits": {"mbti": "INTJ"}, "attributes": {}}],
            "updated_characters": [],
            "new_rules": [],
            "relationships": [],
            "events": [],
            "details": [],
        }
        commit_id = self.mm.begin_chapter_commit(7, "scan_chapter", payload=payload)
        self.mm.finalize_chapter_commit(commit_id, status="FAILED", conflicts_count=0, error_message="simulated")
        wf = self._build_workflow_stub()
        ok = wf.replay_chapter_commit(commit_id)
        self.assertTrue(ok)
        self.assertIsNotNone(self.mm.get_character("ReplayHero"))
        row = self.mm.get_chapter_commit(commit_id)
        self.assertEqual(row[4], "COMPLETED")
        self.assertEqual(row[7], 1)  # replay_count

    def test_list_failed_commits_returns_failed_only(self):
        cid_failed = self.mm.begin_chapter_commit(1, "scan", payload={"events": []})
        self.mm.finalize_chapter_commit(cid_failed, status="FAILED", conflicts_count=0, error_message="x")
        cid_ok = self.mm.begin_chapter_commit(2, "scan", payload={"events": []})
        self.mm.finalize_chapter_commit(cid_ok, status="COMPLETED", conflicts_count=0)
        wf = self._build_workflow_stub()
        rows = wf.list_failed_chapter_commits(limit=10)
        ids = {r[0] for r in rows}
        self.assertIn(cid_failed, ids)
        self.assertNotIn(cid_ok, ids)

    def test_batch_triage_non_blocking_resolves_only_non_blocking(self):
        wf = self._build_workflow_stub()
        self.mm.queue_conflict(
            entity_type="relationship",
            entity_key="A->B",
            conflict_type="relationship_type_change",
            incoming_obj={"relation_type": "siblings"},
            existing_obj={"relation_type": "friends"},
            source="test",
            chapter_num=1,
            blocking_level=self.mm.NON_BLOCKING,
            priority=1,
            suggested_action="manual_review_non_blocking",
        )
        self.mm.queue_conflict(
            entity_type="timeline_event",
            entity_key="E@T",
            conflict_type="timeline_rule_contradiction",
            incoming_obj={"event_name": "E"},
            existing_obj={"rule_id": 1},
            source="test",
            chapter_num=1,
            blocking_level=self.mm.BLOCKING,
            priority=3,
            suggested_action="revise_event_payload",
        )
        resolved = wf.batch_triage_non_blocking(limit=10)
        self.assertEqual(resolved, 1)
        self.assertEqual(self.mm.get_pending_conflict_count(blocking_level=self.mm.NON_BLOCKING), 0)
        self.assertEqual(self.mm.get_pending_conflict_count(blocking_level=self.mm.BLOCKING), 1)

class ContinuousLoopResumeTests(unittest.TestCase):
    class _MemoryStub:
        def __init__(self, commits_by_chapter=None):
            self.commits_by_chapter = commits_by_chapter or {}
            self.purged = []

        def get_chapter_commits(self, chapter_num: int, source: str = None, limit: int = 50):
            return list(self.commits_by_chapter.get(chapter_num, []))[:limit]

        def purge_incomplete_chapter_commits(self, chapter_num: int, source: str = None):
            self.purged.append((chapter_num, source))
            self.commits_by_chapter[chapter_num] = [
                row for row in self.commits_by_chapter.get(chapter_num, [])
                if str(row[4]) not in {"STARTED", "FAILED"}
            ]
            return 0

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="auto_resume_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _build_workflow_stub(self, memory):
        wf = WorkflowManager.__new__(WorkflowManager)
        wf.logger = logging.getLogger("auto-resume-test")
        wf.memory = memory
        wf.world_dir = os.path.join(self.tmpdir, "frame", "world")
        wf.plot_dir = os.path.join(self.tmpdir, "frame", "plot")
        wf.facts_dir = os.path.join(self.tmpdir, "process", "facts")
        wf.guides_dir = os.path.join(self.tmpdir, "frame", "chapter_guides")
        wf.archives_dir = os.path.join(self.tmpdir, "frame", "archives")
        wf.chapters_dir = os.path.join(self.tmpdir, "main_text", "chapters")
        wf.discussions_dir = os.path.join(self.tmpdir, "process", "discussions")
        wf.reviews_dir = os.path.join(self.tmpdir, "process", "reviews")
        wf.revisions_dir = os.path.join(self.tmpdir, "process", "revisions")
        wf.critiques_dir = os.path.join(self.tmpdir, "process", "critiques")
        wf.discussion_log_dir = os.path.join(self.tmpdir, "Discussion_Log")
        os.makedirs(wf.world_dir, exist_ok=True)
        os.makedirs(wf.plot_dir, exist_ok=True)
        os.makedirs(wf.facts_dir, exist_ok=True)
        os.makedirs(wf.guides_dir, exist_ok=True)
        os.makedirs(wf.archives_dir, exist_ok=True)
        os.makedirs(wf.chapters_dir, exist_ok=True)
        os.makedirs(wf.discussions_dir, exist_ok=True)
        os.makedirs(wf.reviews_dir, exist_ok=True)
        os.makedirs(wf.revisions_dir, exist_ok=True)
        os.makedirs(wf.critiques_dir, exist_ok=True)
        os.makedirs(wf.discussion_log_dir, exist_ok=True)
        wf._get_system_prompts = lambda: {"critic": "c", "writer": "w"}
        return wf

    def test_auto_loop_skips_completed_chapters_and_continues(self):
        payload = {
            "new_characters": [],
            "updated_characters": [],
            "new_rules": [],
            "relationships": [],
            "events": [{"event_name": "DoneEvent"}],
            "details": [],
        }
        commit_row = ("c1", 1, "scan_chapter", json.dumps(payload), "COMPLETED", 0, "", 0, None, "2026-01-01")
        memory = self._MemoryStub(commits_by_chapter={1: [commit_row], 2: []})
        wf = self._build_workflow_stub(memory)
        with open(os.path.join(wf.chapters_dir, "chapter_001.md"), "w", encoding="utf-8") as f:
            f.write("chapter 1 text")
        with open(os.path.join(wf.facts_dir, "chapter_001_facts_summary.md"), "w", encoding="utf-8") as f:
            f.write("Summary for Chapter 1: done")
        with open(os.path.join(wf.facts_dir, "chapter_001_facts.json"), "w", encoding="utf-8") as f:
            json.dump(payload, f)

        calls = []
        wf.generate_chapter_guide = lambda chapter_num, previous_summary=None: (
            calls.append(("plan", chapter_num, previous_summary)) or f"guide-{chapter_num}"
        )
        wf.write_chapter = lambda chapter_num, guide_content: (
            calls.append(("write", chapter_num, guide_content)) or f"chapter-{chapter_num}"
        )
        wf._review_and_revise_chapter = lambda chapter_num, guide, chapter_text, prompts: (
            calls.append(("review", chapter_num)) or (chapter_text, "ok")
        )
        wf.scan_chapter = lambda chapter_num: (
            calls.append(("scan", chapter_num)) or f"Summary for Chapter {chapter_num}: done"
        )

        with mock.patch("workflow.time.sleep", return_value=None):
            wf.run_continuous_loop(1, 2)

        planned = [x for x in calls if x[0] == "plan"]
        written = [x for x in calls if x[0] == "write"]
        scanned = [x for x in calls if x[0] == "scan"]
        self.assertEqual(len(planned), 1)
        self.assertEqual(planned[0][1], 2)
        self.assertEqual(len(written), 1)
        self.assertEqual(written[0][1], 2)
        self.assertEqual(len(scanned), 1)
        self.assertEqual(scanned[0][1], 2)

    def test_auto_loop_discards_incomplete_chapter_and_regenerates(self):
        memory = self._MemoryStub(commits_by_chapter={1: [("c1", 1, "scan_chapter", "{}", "STARTED", 0, "", 0, None, "2026-01-01")]})
        wf = self._build_workflow_stub(memory)
        with open(os.path.join(wf.guides_dir, "chapter_001_guide.md"), "w", encoding="utf-8") as f:
            f.write("Existing guide")
        with open(os.path.join(wf.chapters_dir, "chapter_001.md"), "w", encoding="utf-8") as f:
            f.write("Existing chapter draft")

        calls = []
        wf.generate_chapter_guide = lambda chapter_num, previous_summary=None: (
            calls.append(("plan", chapter_num, previous_summary)) or "new-guide"
        )
        wf.write_chapter = lambda chapter_num, guide_content: (
            calls.append(("write", chapter_num, guide_content)) or "new-chapter"
        )
        wf._review_and_revise_chapter = lambda chapter_num, guide, chapter_text, prompts: (
            calls.append(("review", chapter_num, guide, chapter_text)) or (chapter_text, "ok")
        )
        wf.scan_chapter = lambda chapter_num: (
            calls.append(("scan", chapter_num)) or f"Summary for Chapter {chapter_num}: done"
        )

        with mock.patch("workflow.time.sleep", return_value=None):
            wf.run_continuous_loop(1, 1)

        self.assertTrue(any(x[0] == "plan" for x in calls))
        self.assertTrue(any(x[0] == "write" for x in calls))
        self.assertEqual(memory.purged, [(1, "scan_chapter")])
        self.assertFalse(os.path.exists(os.path.join(wf.guides_dir, "chapter_001_guide.md")))
        self.assertFalse(os.path.exists(os.path.join(wf.chapters_dir, "chapter_001.md")))

    def test_auto_loop_discards_chapter_when_facts_json_corrupted(self):
        payload = {
            "new_characters": [],
            "updated_characters": [],
            "new_rules": [],
            "relationships": [],
            "events": [{"event_name": "DoneEvent"}],
            "details": [],
        }
        commit_row = ("c1", 1, "scan_chapter", json.dumps(payload), "COMPLETED", 0, "", 0, None, "2026-01-01")
        memory = self._MemoryStub(commits_by_chapter={1: [commit_row]})
        wf = self._build_workflow_stub(memory)
        with open(os.path.join(wf.guides_dir, "chapter_001_guide.md"), "w", encoding="utf-8") as f:
            f.write("Existing guide")
        with open(os.path.join(wf.chapters_dir, "chapter_001.md"), "w", encoding="utf-8") as f:
            f.write("Existing chapter text")
        with open(os.path.join(wf.facts_dir, "chapter_001_facts_summary.md"), "w", encoding="utf-8") as f:
            f.write("summary")
        with open(os.path.join(wf.facts_dir, "chapter_001_facts.json"), "w", encoding="utf-8") as f:
            f.write("{ invalid json")

        calls = []
        wf.generate_chapter_guide = lambda chapter_num, previous_summary=None: (
            calls.append(("plan", chapter_num, previous_summary)) or "new-guide"
        )
        wf.write_chapter = lambda chapter_num, guide_content: (
            calls.append(("write", chapter_num, guide_content)) or "new-chapter"
        )
        wf._review_and_revise_chapter = lambda chapter_num, guide, chapter_text, prompts: (
            calls.append(("review", chapter_num, guide, chapter_text)) or (chapter_text, "ok")
        )
        wf.scan_chapter = lambda chapter_num: (
            calls.append(("scan", chapter_num)) or f"Summary for Chapter {chapter_num}: done"
        )

        with mock.patch("workflow.time.sleep", return_value=None):
            wf.run_continuous_loop(1, 1)

        self.assertTrue(any(x[0] == "plan" for x in calls))
        self.assertTrue(any(x[0] == "write" for x in calls))
        self.assertEqual(memory.purged, [(1, "scan_chapter")])

    def test_auto_loop_blocks_on_corrupted_world_bible(self):
        memory = self._MemoryStub(commits_by_chapter={})
        wf = self._build_workflow_stub(memory)
        with open(os.path.join(wf.world_dir, "world_bible.md"), "w", encoding="utf-8") as f:
            f.write("")
        wf.generate_chapter_guide = lambda chapter_num, previous_summary=None: "x"
        wf.write_chapter = lambda chapter_num, guide_content: "y"
        wf._review_and_revise_chapter = lambda chapter_num, guide, chapter_text, prompts: (chapter_text, "ok")
        wf.scan_chapter = lambda chapter_num: "z"

        with mock.patch("workflow.time.sleep", return_value=None):
            with self.assertRaises(RuntimeError):
                wf.run_continuous_loop(1, 1)

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
            self.assertIn("Language Guard Error", str(ctx.exception))
        finally:
            config.LANGUAGE = old_lang


if __name__ == "__main__":
    unittest.main()
