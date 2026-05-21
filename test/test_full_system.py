import os
import sys
import unittest
import logging

CURRENT_DIR = os.path.dirname(__file__)
ROOT_DIR = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
SRC_DIR = os.path.join(ROOT_DIR, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from memory import MemoryManager
from state_manager import StoryStateManager
from workflow import WorkflowManager

class RelationshipSystemTests(unittest.TestCase):
    def setUp(self):
        self.db_path = os.path.join(ROOT_DIR, "novel", "process", "verify_facts.db")
        self.faiss_path = os.path.join(ROOT_DIR, "novel", "process", "verify_index.faiss")
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        if os.path.exists(self.faiss_path):
            os.remove(self.faiss_path)
        self.mm = MemoryManager(self.db_path, self.faiss_path)

    def tearDown(self):
        self.mm.close()

    def test_relationship_upsert_and_query(self):
        self.mm.add_relationship("Romeo", "Juliet", "lovers", "Star-crossed")
        self.mm.add_relationship("Tybalt", "Romeo", "enemies", "Family feud")

        rels_romeo = self.mm.get_relationships("Romeo")
        self.assertEqual(len(rels_romeo), 2)

        self.mm.add_relationship("Romeo", "Juliet", "spouse", "Secretly married")
        rels_updated = self.mm.get_relationships("Romeo")
        self.assertTrue(any(r[1] == "Juliet" and r[2] == "lovers" for r in rels_updated))
        self.assertEqual(self.mm.get_pending_conflict_count(), 1)

class PipelineIntegrationTests(unittest.TestCase):
    class _EmbeddingStub:
        def get_embedding(self, text):
            return [0.1] * 768

    def setUp(self):
        self.db_path = os.path.join(ROOT_DIR, "novel", "process", "verify_pipeline.db")
        self.faiss_path = os.path.join(ROOT_DIR, "novel", "process", "verify_pipeline.faiss")
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        if os.path.exists(self.faiss_path):
            os.remove(self.faiss_path)
        self.mm = MemoryManager(self.db_path, self.faiss_path)
        self.state = StoryStateManager(self.mm, self._EmbeddingStub())

    def tearDown(self):
        self.mm.close()

    def _workflow_stub(self):
        wf = WorkflowManager.__new__(WorkflowManager)
        wf.logger = logging.getLogger("pipeline-integration")
        wf.memory = self.mm
        wf.embedding_client = self._EmbeddingStub()
        wf.state_manager = self.state
        wf._sync_compact_archives = lambda: None
        return wf

    def test_retrieval_conflict_and_replay_chain(self):
        self.mm.add_rule(
            "Magic",
            "No resurrection is allowed.",
            strictness=1,
            source="test",
            chapter_num=1,
            source_commit_id="seed-1",
            intent_tag="seed",
        )
        self.mm.upsert_character(name="Hero", status="dead", source="test", chapter_num=1)

        eid = self.mm.add_event(
            "Hero Returns",
            "Hero appears and speaks to the crowd.",
            "Day 2",
            4,
            ["Hero"],
            "Square",
            source="test",
            chapter_num=2,
            source_commit_id="scan-2",
            intent_tag="scan_extract",
        )
        self.assertGreater(eid, 0)  # Event is now inserted (not blocked)
        self.assertEqual(self.mm.get_pending_blocking_conflict_count(), 0)  # NON_BLOCKING, not BLOCKING
        self.assertGreaterEqual(self.mm.get_pending_conflict_count(), 1)  # NON_BLOCKING conflict queued

        pkg = self.state.build_context_package(
            task_type="planner",
            chapter_num=2,
            previous_summary="Hero died in previous chapter",
            recent_events_limit=5,
            conflicts_limit=10,
            user_request="plan chapter 2",
        )
        self.assertIn("intent", pkg)
        self.assertIn("semantic_summary", pkg)

        payload = {
            "new_characters": [{"name": "ReplayNPC", "core_traits": {}, "attributes": {}}],
            "updated_characters": [],
            "new_rules": [],
            "relationships": [],
            "events": [],
            "details": [],
        }
        commit_id = self.mm.begin_chapter_commit(3, "scan_chapter", payload=payload)
        self.mm.finalize_chapter_commit(commit_id, status="FAILED", conflicts_count=0, error_message="simulated")
        wf = self._workflow_stub()
        ok = wf.replay_chapter_commit(commit_id)
        self.assertTrue(ok)
        self.assertIsNotNone(self.mm.get_character("ReplayNPC"))

if __name__ == "__main__":
    unittest.main()
