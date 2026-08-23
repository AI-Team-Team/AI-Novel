from .base import *


class FaissRecoveryTests(AutonomyTestCase):
    def test_faiss_rebuild_and_recovery(self):
        # 1. Verify rebuild_vector_index_from_metadata is non-blocking when index is None
        from memory import MemoryManager
        import io
        from contextlib import redirect_stdout

        db_path = os.path.join(self.tmpdir, "test_memory.db")
        faiss_path = os.path.join(self.tmpdir, "test_faiss.faiss")

        mem = MemoryManager(db_path=db_path, faiss_path=faiss_path, embedding_dim=4)
        try:
            mem.index = None # Simulate missing/corrupted index file

            # Seed metadata table so there is something to rebuild
            mem.cursor.execute(
                "INSERT INTO vector_metadata (faiss_id, content, metadata, source_commit_id, is_deleted) VALUES (?, ?, ?, ?, ?)",
                (0, "Lore detail", "{}", "commit_1", 0)
            )
            mem.conn.commit()

            # Dummy embedding function returning a 4-dimensional vector
            def dummy_embedding(text):
                return [0.1, 0.2, 0.3, 0.4]

            f = io.StringIO()
            with redirect_stdout(f):
                stats = mem.rebuild_vector_index_from_metadata(dummy_embedding)

            self.assertEqual(stats["rebuilt"], 1)
            self.assertIsNotNone(mem.index)
            self.assertEqual(mem.index.ntotal, 1)
            # Verify check result was outputted to stdout
            self.assertIn("Warning: FAISS index file is missing or corrupted.", f.getvalue())

            # 2. Verify automatic rebuild on WorkflowManager startup when memory index is None
            from workflow import WorkflowManager

            with unittest.mock.patch("workflow.WorkflowManager.rebuild_vector_index") as mock_rebuild:
                with unittest.mock.patch("workflow.LLMClient") as mock_client:
                    with unittest.mock.patch("workflow.config") as mock_cfg:
                        mock_cfg.DB_PATH = db_path
                        mock_cfg.FAISS_INDEX_PATH = faiss_path
                        mock_cfg.TIER_3_SEARCH_LIMIT = 5
                        mock_cfg.retrieval_section = {"tier_3_search_limit": 5}

                        with unittest.mock.patch("workflow.MemoryManager") as mock_mem_class:
                            mock_mem_inst = unittest.mock.MagicMock()
                            mock_mem_inst.index = None
                            mock_mem_inst.cursor.fetchone.return_value = (1,)
                            mock_mem_class.return_value = mock_mem_inst

                            # Initialize workflow manager
                            wf_mgr = WorkflowManager()

                            # It should have automatically triggered rebuild_vector_index on startup!
                            mock_rebuild.assert_called_once()
                            wf_mgr.close()
        finally:
            mem.close()
