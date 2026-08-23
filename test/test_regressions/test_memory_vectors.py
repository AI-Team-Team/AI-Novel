from .common import *
from .memory_base import MemoryTestCase


class MemoryVectorReliabilityTests(MemoryTestCase):
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
        self.mm.cursor.execute(
            "SELECT status, reason FROM vector_rebuild_audit WHERE run_id = ?",
            (stats["run_id"],),
        )
        audit_rows = self.mm.cursor.fetchall()
        self.assertTrue(any(status == "SKIPPED" and reason == "empty_embedding" for status, reason in audit_rows))
        self.mm.cursor.execute(
            "SELECT status, rebuilt_count, skipped_count FROM vector_rebuild_runs WHERE run_id = ?",
            (stats["run_id"],),
        )
        run_row = self.mm.cursor.fetchone()
        self.assertEqual(run_row, ("COMPLETED", 1, 1))

    def test_init_faiss_load_failure_preserves_metadata_for_rebuild(self):
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
        self.assertEqual(count, 1)
        health = self.mm.reconcile_vector_store()
        self.assertTrue(health["requires_rebuild"])
        self.assertIsNotNone(health["load_error"])

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

    def test_vector_reset_rollback_restores_in_memory_dimension_and_load_error(self):
        fake_faiss = _FakeFaissModule()
        with mock.patch("memory.faiss", fake_faiss):
            self.mm.index = _FakeFaissIndex(768)
            self.mm.index.ntotal = 1
            self.mm.embedding_dim = 768
            self.mm.faiss_load_error = "previous load failure"
            self.mm.cursor.execute(
                """INSERT INTO vector_metadata
                   (faiss_id, content, metadata, is_deleted)
                   VALUES (0, 'stable detail', '{}', 0)"""
            )
            self.mm.set_schema_meta("embedding_dim", "768")

            self.mm.begin_batch()
            self.mm._reset_vector_store(16, preserve_metadata=True, reason="test")
            self.assertEqual(self.mm.embedding_dim, 16)
            self.assertIsNone(self.mm.faiss_load_error)
            self.mm.end_batch(success=False)

            self.assertEqual(self.mm.embedding_dim, 768)
            self.assertEqual(self.mm.index.d, 768)
            self.assertEqual(self.mm.index.ntotal, 1)
            self.assertEqual(self.mm.faiss_load_error, "previous load failure")
            self.assertEqual(self.mm.get_schema_meta("embedding_dim"), "768")
            self.mm.cursor.execute(
                "SELECT is_deleted FROM vector_metadata WHERE content='stable detail'"
            )
            self.assertEqual(self.mm.cursor.fetchone()[0], 0)

    def test_begin_batch_fails_closed_when_faiss_snapshot_cannot_be_cloned(self):
        fake_faiss = _FakeFaissModule()
        self.mm.index = _FakeFaissIndex(768)
        with mock.patch("memory.faiss", fake_faiss), mock.patch.object(
            fake_faiss, "clone_index", side_effect=RuntimeError("clone failed")
        ):
            with self.assertRaisesRegex(RuntimeError, "clone failed"):
                self.mm.begin_batch()
        self.assertFalse(self.mm._in_batch)
        self.mm.cursor.execute("SELECT 1")
        self.assertEqual(self.mm.cursor.fetchone()[0], 1)

    def test_faiss_install_failure_rolls_back_sqlite_and_memory_state(self):
        fake_faiss = _FakeFaissModule()
        with mock.patch("memory.faiss", fake_faiss):
            self.mm.index = _FakeFaissIndex(768)
            self.mm.begin_batch()
            self.mm.cursor.execute(
                """INSERT INTO vector_metadata
                   (faiss_id, content, metadata, is_deleted)
                   VALUES (0, 'must roll back', '{}', 0)"""
            )
            self.mm.index.add([[0.1] * 768])
            self.mm._faiss_dirty = True
            with mock.patch.object(
                self.mm,
                "_install_staged_faiss",
                side_effect=OSError("install failed"),
            ):
                with self.assertRaisesRegex(OSError, "install failed"):
                    self.mm.end_batch(success=True)

            self.assertFalse(self.mm._in_batch)
            self.assertEqual(self.mm.index.ntotal, 0)
            self.mm.cursor.execute(
                "SELECT COUNT(*) FROM vector_metadata WHERE content='must roll back'"
            )
            self.assertEqual(self.mm.cursor.fetchone()[0], 0)
            self.assertFalse(
                any(name.startswith("test_regressions.faiss.staged-") for name in os.listdir(os.path.dirname(self.faiss_path)))
            )

    def test_sqlite_commit_failure_restores_installed_faiss_file(self):
        class _CommitFailureConnection:
            def __init__(self, connection):
                self.connection = connection

            def __getattr__(self, name):
                return getattr(self.connection, name)

            def commit(self):
                raise OSError("simulated SQLite commit failure")

        fake_faiss = _FakeFaissModule()
        with mock.patch("memory.faiss", fake_faiss):
            self.mm.index = _FakeFaissIndex(768)
            fake_faiss.write_index(self.mm.index, self.faiss_path)
            self.mm.begin_batch()
            self.mm.cursor.execute(
                """INSERT INTO vector_metadata
                   (faiss_id, content, metadata, is_deleted)
                   VALUES (0, 'must roll back after install', '{}', 0)"""
            )
            self.mm.index.add([[0.1] * 768])
            self.mm._faiss_dirty = True
            self.mm.conn = _CommitFailureConnection(self.mm.conn)

            with self.assertRaisesRegex(OSError, "simulated SQLite commit failure"):
                self.mm.end_batch(success=True)

            self.assertEqual(self.mm.index.ntotal, 0)
            self.assertEqual(fake_faiss.read_index(self.faiss_path).ntotal, 0)
            self.mm.cursor.execute(
                "SELECT COUNT(*) FROM vector_metadata WHERE content='must roll back after install'"
            )
            self.assertEqual(self.mm.cursor.fetchone()[0], 0)

    def test_faiss_load_failure_reconciliation_preserves_metadata_without_native_faiss(self):
        self.mm.cursor.execute(
            """INSERT INTO vector_metadata
               (faiss_id, content, metadata, is_deleted)
               VALUES (0, 'preserved detail', '{}', 0)"""
        )
        self.mm.conn.commit()
        self.mm.close()
        with open(self.faiss_path, "wb") as handle:
            handle.write(b"corrupt")

        with mock.patch("memory.faiss", _FailingReadFaissModule()):
            self.mm = MemoryManager(self.db_path, self.faiss_path)
            self.mm.cursor.execute("SELECT COUNT(*) FROM vector_metadata")
            self.assertEqual(self.mm.cursor.fetchone()[0], 1)
            health = self.mm.reconcile_vector_store()
            self.assertTrue(health["requires_rebuild"])
            self.assertIn("simulated corrupt index", health["load_error"])

    def test_rebuild_retains_skipped_row_audit_without_native_faiss(self):
        with mock.patch("memory.faiss", _FakeFaissModule()):
            self.mm.index = _FakeFaissIndex(768)
            self.mm.add_semantic_fact("keep me", [0.1] * 768, {"location": "A"})
            self.mm.add_semantic_fact("skip me", [0.2] * 768, {"location": "B"})

            stats = self.mm.rebuild_vector_index_from_metadata(
                lambda text: None if text == "skip me" else [0.3] * 768
            )
            self.assertEqual(stats["rebuilt"], 1)
            self.assertEqual(stats["skipped"], 1)
            self.mm.cursor.execute(
                """SELECT status, reason, new_faiss_id
                   FROM vector_rebuild_audit
                   WHERE run_id = ? AND content = 'skip me'""",
                (stats["run_id"],),
            )
            self.assertEqual(self.mm.cursor.fetchone(), ("SKIPPED", "empty_embedding", -1))
            self.mm.cursor.execute(
                "SELECT is_deleted, faiss_id FROM vector_metadata WHERE content = 'skip me'"
            )
            self.assertEqual(self.mm.cursor.fetchone(), (1, -1))

    def test_rebuild_records_failed_run_when_index_creation_fails(self):
        self.mm.cursor.execute(
            """INSERT INTO vector_metadata
               (faiss_id, content, metadata, is_deleted)
               VALUES (0, 'rebuild source', '{}', 0)"""
        )
        self.mm.conn.commit()
        fake_faiss = _FakeFaissModule()
        with mock.patch("memory.faiss", fake_faiss), mock.patch.object(
            fake_faiss,
            "IndexFlatL2",
            side_effect=RuntimeError("index creation failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "index creation failed"):
                self.mm.rebuild_vector_index_from_metadata(lambda text: [0.1] * 768)

        self.mm.cursor.execute(
            """SELECT status, error_message
               FROM vector_rebuild_runs ORDER BY started_at DESC, rowid DESC LIMIT 1"""
        )
        status, error = self.mm.cursor.fetchone()
        self.assertEqual(status, "FAILED")
        self.assertIn("index creation failed", error)
