from .common import *


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
