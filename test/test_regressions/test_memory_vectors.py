from .common import *
from .memory_base import MemoryTestCase
from memory import faiss


class MemoryVectorReliabilityTests(MemoryTestCase):
    def test_direct_vector_write_rejects_invalid_faiss_values(self):
        if faiss is None:
            self.skipTest("FAISS unavailable in this environment")
        for embedding in ([float("nan")] * 4, [float("inf")] * 4, [1e100] * 4, [[0.1] * 4]):
            with self.subTest(embedding=embedding):
                with self.assertRaises(ValueError):
                    self.mm.add_semantic_fact("invalid detail", embedding)
        self.assertIsNone(self.mm.index)
        self.mm.cursor.execute("SELECT COUNT(*) FROM vector_metadata")
        self.assertEqual(self.mm.cursor.fetchone()[0], 0)

    def test_batch_rollback_restores_faiss_index_state(self):
        if faiss is None:
            self.skipTest("FAISS unavailable in this environment")
        baseline = self.mm.index.ntotal if self.mm.index is not None else 0
        self.mm.begin_batch()
        self.mm.add_semantic_fact("rollback detail", [0.1] * 768, {"location": "test"})
        self.mm.end_batch(success=False)
        self.assertEqual(self.mm.index.ntotal if self.mm.index is not None else 0, baseline)
        self.mm.cursor.execute("SELECT COUNT(*) FROM vector_metadata WHERE content = ?", ("rollback detail",))
        count = self.mm.cursor.fetchone()[0]
        self.assertEqual(count, 0)

    def test_rebuild_vector_index_from_metadata(self):
        if faiss is None:
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
        if faiss is None:
            self.skipTest("FAISS unavailable in this environment")
        self.mm.add_semantic_fact("keep me", [0.1] * 768, {"location": "A"})
        self.mm.add_semantic_fact("skip me", [0.2] * 768, {"location": "B"})

        def emb_fn(text):
            if text == "skip me":
                return None
            return [0.3] * 768

        stats = self.mm.rebuild_vector_index_from_metadata(emb_fn, allow_partial=True)
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

    def test_default_rebuild_preserves_existing_tombstones_without_embedding_them(self):
        if faiss is None:
            self.skipTest("FAISS unavailable in this environment")
        self.mm.add_semantic_fact(
            "old deleted fact", [0.1] * 4, {"location": "Old"},
            source_commit_id="old-commit", intent_tag="scan_extract",
        )
        self.mm.add_semantic_fact("active fact", [0.2] * 4, {"location": "Now"})
        self.mm.cursor.execute(
            "SELECT timestamp_created FROM vector_metadata WHERE content='old deleted fact'"
        )
        original_timestamp = self.mm.cursor.fetchone()[0]
        self.mm.cursor.execute(
            "UPDATE vector_metadata SET is_deleted=1 WHERE content='old deleted fact'"
        )
        self.mm.conn.commit()
        embedded = []

        def embed(content):
            embedded.append(content)
            return [0.3] * 4

        stats = self.mm.rebuild_vector_index_from_metadata(embed, target_dim=4)

        self.assertEqual(stats["rebuilt"], 1)
        self.assertEqual(embedded, ["active fact"])
        self.assertEqual(self.mm.index.ntotal, 1)
        self.assertTrue(self.mm.reconcile_vector_store()["healthy"])
        self.mm.cursor.execute(
            """SELECT faiss_id, is_deleted, source_commit_id, intent_tag,
                      timestamp_created FROM vector_metadata
               WHERE content='old deleted fact'"""
        )
        tombstone = self.mm.cursor.fetchone()
        self.assertEqual(tombstone, (-1, 1, "old-commit", "scan_extract", original_timestamp))
        self.mm.cursor.execute(
            """SELECT status, old_faiss_id, new_faiss_id FROM vector_rebuild_audit
               WHERE run_id=? AND content='old deleted fact'""",
            (stats["run_id"],),
        )
        self.assertEqual(self.mm.cursor.fetchone(), ("PRESERVED", 0, -1))
        self.assertEqual(
            [row["content"] for row in self.mm.search_semantic([0.3] * 4)],
            ["active fact"],
        )

        self.mm.close()
        self.mm = MemoryManager(self.db_path, self.faiss_path)
        self.assertTrue(self.mm.reconcile_vector_store()["healthy"])
        self.mm.cursor.execute(
            "SELECT faiss_id, is_deleted FROM vector_metadata WHERE content='old deleted fact'"
        )
        self.assertEqual(self.mm.cursor.fetchone(), (-1, 1))
        self.mm.rebuild_vector_index_from_metadata(lambda content: [0.4] * 4, target_dim=4)
        self.mm.cursor.execute(
            "SELECT faiss_id, is_deleted FROM vector_metadata WHERE content='old deleted fact'"
        )
        self.assertEqual(self.mm.cursor.fetchone(), (-1, 1))
        self.assertTrue(self.mm.reconcile_vector_store()["healthy"])

    def test_include_deleted_audits_tombstones_without_reactivating_them(self):
        if faiss is None:
            self.skipTest("FAISS unavailable in this environment")
        self.mm.add_semantic_fact("active fact", [0.1] * 4)
        self.mm.add_semantic_fact("deleted fact", [0.2] * 4)
        self.mm.cursor.execute(
            "UPDATE vector_metadata SET is_deleted=1 WHERE content='deleted fact'"
        )
        self.mm.conn.commit()
        embedded = []

        def embed(content):
            embedded.append(content)
            return [0.3] * 4

        stats = self.mm.rebuild_vector_index_from_metadata(
            embed, include_deleted=True, target_dim=4
        )

        self.assertEqual(embedded, ["active fact"])
        self.assertEqual(self.mm.index.ntotal, 1)
        self.assertTrue(self.mm.reconcile_vector_store()["healthy"])
        self.mm.cursor.execute(
            "SELECT faiss_id, is_deleted FROM vector_metadata WHERE content='deleted fact'"
        )
        self.assertEqual(self.mm.cursor.fetchone(), (-1, 1))
        self.mm.cursor.execute(
            "SELECT source_count, rebuilt_count, skipped_count FROM vector_rebuild_runs WHERE run_id=?",
            (stats["run_id"],),
        )
        self.assertEqual(self.mm.cursor.fetchone(), (2, 1, 0))
        self.mm.cursor.execute(
            """SELECT status, old_faiss_id, new_faiss_id FROM vector_rebuild_audit
               WHERE run_id=? AND content='deleted fact'""",
            (stats["run_id"],),
        )
        self.assertEqual(self.mm.cursor.fetchone(), ("PRESERVED", 1, -1))

        next_stats = self.mm.rebuild_vector_index_from_metadata(
            embed, include_deleted=True, target_dim=4
        )
        self.mm.cursor.execute(
            """SELECT status, old_faiss_id, new_faiss_id FROM vector_rebuild_audit
               WHERE run_id=? AND content='deleted fact'""",
            (next_stats["run_id"],),
        )
        self.assertEqual(self.mm.cursor.fetchone(), ("PRESERVED", -1, -1))
        self.assertTrue(self.mm.reconcile_vector_store()["healthy"])

    def test_partial_rebuild_keeps_old_and_new_tombstone_ids_distinct(self):
        if faiss is None:
            self.skipTest("FAISS unavailable in this environment")
        self.mm.add_semantic_fact("keep", [0.1] * 4)
        self.mm.add_semantic_fact("old tombstone", [0.2] * 4)
        self.mm.cursor.execute(
            "UPDATE vector_metadata SET faiss_id=-1, is_deleted=1 WHERE content='old tombstone'"
        )
        self.mm.conn.commit()
        self.mm.add_semantic_fact("new skip", [0.3] * 4)

        stats = self.mm.rebuild_vector_index_from_metadata(
            lambda content: None if content == "new skip" else [0.4] * 4,
            target_dim=4,
            allow_partial=True,
        )

        self.assertEqual((stats["rebuilt"], stats["skipped"]), (1, 1))
        self.mm.cursor.execute(
            "SELECT content, faiss_id, is_deleted FROM vector_metadata ORDER BY faiss_id"
        )
        self.assertEqual(
            self.mm.cursor.fetchall(),
            [("new skip", -2, 1), ("old tombstone", -1, 1), ("keep", 0, 0)],
        )
        self.assertTrue(self.mm.reconcile_vector_store()["healthy"])

    def test_rebuild_with_only_tombstones_creates_empty_healthy_index(self):
        if faiss is None:
            self.skipTest("FAISS unavailable in this environment")
        self.mm.cursor.execute(
            """INSERT INTO vector_metadata
               (faiss_id, content, metadata, is_deleted)
               VALUES (7, 'deleted fact', '{}', 1)"""
        )
        self.mm.conn.commit()

        stats = self.mm.rebuild_vector_index_from_metadata(
            lambda content: self.fail("deleted fact must not be embedded"),
            include_deleted=True,
            target_dim=4,
        )

        self.assertEqual((stats["rebuilt"], stats["skipped"]), (0, 0))
        self.assertEqual((self.mm.index.d, self.mm.index.ntotal), (4, 0))
        self.mm.cursor.execute(
            "SELECT faiss_id, is_deleted FROM vector_metadata WHERE content='deleted fact'"
        )
        self.assertEqual(self.mm.cursor.fetchone(), (-1, 1))
        self.assertTrue(self.mm.reconcile_vector_store()["healthy"])

    def test_include_deleted_requires_explicit_boolean(self):
        with self.assertRaises(ValueError):
            self.mm.rebuild_vector_index_from_metadata(
                lambda content: [0.1] * 4,
                include_deleted="true",
                target_dim=4,
            )
        self.mm.cursor.execute("SELECT COUNT(*) FROM vector_rebuild_runs")
        self.assertEqual(self.mm.cursor.fetchone()[0], 0)

    def test_init_faiss_load_failure_preserves_metadata_for_rebuild(self):
        if faiss is None:
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

    def test_reconciliation_detects_saved_dimension_mismatch_for_empty_index(self):
        if faiss is None:
            self.skipTest("FAISS unavailable in this environment")

        self.mm.index = faiss.IndexFlatL2(768)
        self.mm.set_schema_meta("embedding_dim", "4")
        health = self.mm.reconcile_vector_store()
        self.assertTrue(health["requires_rebuild"])
        self.assertIn(
            get_message("runtime.faiss_reason_dim_mismatch", index_dim=768, saved_dim=4),
            health["reasons"],
        )

    def test_failed_rebuild_preserves_active_rows_and_index(self):
        if faiss is None:
            self.skipTest("FAISS unavailable in this environment")
        self.mm.add_semantic_fact("keep me", [0.1] * 4, {"location": "A"})
        self.mm.add_semantic_fact("unavailable", [0.2] * 4, {"location": "B"})
        self.mm.save_faiss()
        with open(self.faiss_path, "rb") as handle:
            original_index_bytes = handle.read()
        original_dim = self.mm.index.d
        original_total = self.mm.index.ntotal

        with self.assertRaises(RuntimeError):
            self.mm.rebuild_vector_index_from_metadata(
                lambda content: None if content == "unavailable" else [0.3] * 4,
                target_dim=4,
                fingerprint=[0.4] * 4,
            )

        self.assertEqual((self.mm.index.d, self.mm.index.ntotal), (original_dim, original_total))
        with open(self.faiss_path, "rb") as handle:
            self.assertEqual(handle.read(), original_index_bytes)
        self.mm.cursor.execute("SELECT content, is_deleted FROM vector_metadata ORDER BY faiss_id")
        self.assertEqual(self.mm.cursor.fetchall(), [("keep me", 0), ("unavailable", 0)])
        self.mm.cursor.execute("SELECT run_id, status, skipped_count FROM vector_rebuild_runs ORDER BY rowid DESC LIMIT 1")
        run_id, status, skipped_count = self.mm.cursor.fetchone()
        self.assertEqual((status, skipped_count), ("FAILED", 1))
        self.mm.cursor.execute("SELECT content, status, new_faiss_id FROM vector_rebuild_audit WHERE run_id=?", (run_id,))
        self.assertEqual(self.mm.cursor.fetchall(), [("unavailable", "SKIPPED", None)])
        self.assertTrue(self.mm.reconcile_vector_store()["healthy"])

    def test_partial_rebuild_requires_explicit_boolean(self):
        with self.assertRaises(ValueError):
            self.mm.rebuild_vector_index_from_metadata(
                lambda content: [0.1] * 4,
                target_dim=4,
                allow_partial="false",
            )
        self.mm.cursor.execute("SELECT COUNT(*) FROM vector_rebuild_runs")
        self.assertEqual(self.mm.cursor.fetchone()[0], 0)

    def test_rebuild_rejects_out_of_range_fingerprint_before_writing(self):
        if faiss is None:
            self.skipTest("FAISS unavailable in this environment")
        with self.assertRaises(ValueError):
            self.mm.rebuild_vector_index_from_metadata(
                lambda content: [0.1] * 4,
                target_dim=4,
                fingerprint=[1e100] * 4,
            )
        self.mm.cursor.execute("SELECT COUNT(*) FROM vector_rebuild_runs")
        self.assertEqual(self.mm.cursor.fetchone()[0], 0)

    def test_batch_rollback_with_dimension_reset_keeps_disk_state(self):
        if faiss is None:
            self.skipTest("FAISS unavailable in this environment")
        self.mm.add_semantic_fact("stable detail", [0.1] * 768, {"location": "Stable"})
        self.mm.save_faiss()
        original_dim = self.mm.index.d
        original_total = self.mm.index.ntotal

        self.mm.begin_batch()
        self.mm._reset_vector_store(16)
        self.mm.end_batch(success=False)

        reloaded = MemoryManager(self.db_path, self.faiss_path)
        try:
            self.assertEqual(reloaded.index.d, original_dim)
            self.assertEqual(reloaded.index.ntotal, original_total)
        finally:
            reloaded.close()

    def test_vector_reset_moves_tombstones_before_next_vector_write(self):
        if faiss is None:
            self.skipTest("FAISS unavailable in this environment")
        self.mm.add_semantic_fact("old fact", [0.1] * 4)

        self.mm._reset_vector_store(4, preserve_metadata=True)

        self.mm.cursor.execute(
            "SELECT faiss_id, is_deleted FROM vector_metadata WHERE content='old fact'"
        )
        self.assertEqual(self.mm.cursor.fetchone(), (-1, 1))
        self.assertEqual(self.mm.index.ntotal, 0)
        self.assertTrue(self.mm.reconcile_vector_store()["healthy"])

        self.mm.add_semantic_fact("new fact", [0.2] * 4)
        self.mm.cursor.execute(
            "SELECT content, faiss_id, is_deleted FROM vector_metadata ORDER BY faiss_id"
        )
        self.assertEqual(
            self.mm.cursor.fetchall(),
            [("old fact", -1, 1), ("new fact", 0, 0)],
        )
        self.assertTrue(self.mm.reconcile_vector_store()["healthy"])

    def test_legacy_positive_tombstone_is_detected_and_remapped_on_direct_write(self):
        if faiss is None:
            self.skipTest("FAISS unavailable in this environment")
        self.mm.index = faiss.IndexFlatL2(4)
        self.mm.set_schema_meta("embedding_dim", "4")
        self.mm.cursor.execute(
            """INSERT INTO vector_metadata (faiss_id, content, metadata, is_deleted)
               VALUES (0, 'legacy deleted fact', '{}', 1)"""
        )
        self.mm.conn.commit()
        self.assertTrue(self.mm.reconcile_vector_store()["requires_rebuild"])

        self.mm.add_semantic_fact("new fact", [0.2] * 4)

        self.mm.cursor.execute(
            "SELECT content, faiss_id, is_deleted FROM vector_metadata ORDER BY faiss_id"
        )
        self.assertEqual(
            self.mm.cursor.fetchall(),
            [("legacy deleted fact", -1, 1), ("new fact", 0, 0)],
        )
        self.assertTrue(self.mm.reconcile_vector_store()["healthy"])

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
                "SELECT faiss_id, is_deleted FROM vector_metadata WHERE content='stable detail'"
            )
            self.assertEqual(self.mm.cursor.fetchone(), (0, 0))

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
                lambda text: None if text == "skip me" else [0.3] * 768,
                allow_partial=True,
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
