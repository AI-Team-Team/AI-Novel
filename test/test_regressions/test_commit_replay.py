from .common import *


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
