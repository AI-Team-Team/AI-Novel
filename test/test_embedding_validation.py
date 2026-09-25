import os
import sys
import json
import unittest
import tempfile
import shutil
from unittest.mock import patch, MagicMock

CURRENT_DIR = os.path.dirname(__file__)
ROOT_DIR = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
SRC_DIR = os.path.join(ROOT_DIR, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

import config
from workflow_components.resources import get_message

class TestEmbeddingValidation(unittest.TestCase):
    def setUp(self):
        # Create temp directory for database and faiss index
        self.test_dir = tempfile.mkdtemp()
        self.original_cwd = os.getcwd()
        os.chdir(self.test_dir)
        self.db_path = os.path.join(self.test_dir, "test_facts.db")
        self.faiss_path = os.path.join(self.test_dir, "test_index.faiss")
        
        # Save original config parameters
        self.orig_db_path = config.DB_PATH
        self.orig_faiss_path = config.FAISS_INDEX_PATH
        self.orig_att_state_path = config.ATT_STATE_DB_PATH
        
        # Set config to temp paths
        config.DB_PATH = self.db_path
        config.FAISS_INDEX_PATH = self.faiss_path
        config.ATT_STATE_DB_PATH = os.path.join(self.test_dir, "att_state.db")

    def tearDown(self):
        # Restore original config
        config.DB_PATH = self.orig_db_path
        config.FAISS_INDEX_PATH = self.orig_faiss_path
        config.ATT_STATE_DB_PATH = self.orig_att_state_path
        os.chdir(self.original_cwd)
        
        # Remove temp directory
        shutil.rmtree(self.test_dir)

    @patch("workflow.LLMClient")
    def test_fingerprint_check_is_lazy_and_runs_once(self, mock_llm_client_class):
        # Setup mock client
        mock_embedding_client = MagicMock()
        
        # Mock get_embedding to return a simple vector
        default_vector = [0.1] * 128
        hw_vector = [0.2] * 128
        
        # Counter for Hello World calls
        self.hello_world_calls = 0
        
        def mock_get_embedding(text):
            if text == "Hello World!":
                self.hello_world_calls += 1
                return hw_vector
            return default_vector

        mock_embedding_client.get_embedding = mock_get_embedding
        
        # Configure the patch to return our mock when enable_embedding is True
        def get_mock_client(model_config, enable_embedding=False):
            if enable_embedding:
                return mock_embedding_client
            return MagicMock()

        mock_llm_client_class.side_effect = get_mock_client

        from workflow import WorkflowManager
        wm = WorkflowManager()
        
        # Verify that fingerprint has NOT been verified yet on startup
        self.assertFalse(wm.embedding_client._fingerprint_verified)
        self.assertEqual(self.hello_world_calls, 0)
        
        # Call get_embedding for a generic query
        v1 = wm.embedding_client.get_embedding("query 1")
        self.assertEqual(v1, default_vector)
        
        # Verify that "Hello World!" was called exactly ONCE to verify the fingerprint
        self.assertEqual(self.hello_world_calls, 1)
        self.assertTrue(wm.embedding_client._fingerprint_verified)
        
        # Verify SQLite schema_meta now has the fingerprint and dimension saved
        fp_json = wm.memory.get_schema_meta("embedding_fingerprint")
        dim_str = wm.memory.get_schema_meta("embedding_dim")
        self.assertIsNotNone(fp_json)
        self.assertEqual(dim_str, "128")
        self.assertEqual(json.loads(fp_json), hw_vector)
        
        # Call get_embedding again
        v2 = wm.embedding_client.get_embedding("query 2")
        self.assertEqual(v2, default_vector)
        
        # Verify that "Hello World!" was NOT called a second time (lazy validation!)
        self.assertEqual(self.hello_world_calls, 1)
        wm.close()

    @patch("workflow.LLMClient")
    def test_fingerprint_mismatch_raises_error(self, mock_llm_client_class):
        # 1. First run: establish a fingerprint in the DB
        mock_embedding_client_1 = MagicMock()
        mock_embedding_client_1.get_embedding = lambda text: [0.1] * 128 if text == "Hello World!" else [0.5] * 128
        
        def get_mock_client_1(model_config, enable_embedding=False):
            if enable_embedding:
                return mock_embedding_client_1
            return MagicMock()
            
        mock_llm_client_class.side_effect = get_mock_client_1
        
        from workflow import WorkflowManager
        wm1 = WorkflowManager()
        wm1.embedding_client.get_embedding("init query") # Initialize fingerprint
        
        # Verify it was saved
        fp1 = wm1.memory.get_schema_meta("embedding_fingerprint")
        self.assertEqual(json.loads(fp1), [0.1] * 128)
        wm1.close()
        
        # 2. Second run: Mock a different embedding client returning a different fingerprint
        mock_embedding_client_2 = MagicMock()
        mock_embedding_client_2.get_embedding = lambda text: [0.9] * 128 if text == "Hello World!" else [0.5] * 128
        
        def get_mock_client_2(model_config, enable_embedding=False):
            if enable_embedding:
                return mock_embedding_client_2
            return MagicMock()
            
        mock_llm_client_class.side_effect = get_mock_client_2
        
        wm2 = WorkflowManager()
        # The first embedding call should fail because fingerprint [0.9] != [0.1]
        with self.assertRaises(RuntimeError) as ctx:
            wm2.embedding_client.get_embedding("another query")
            
        self.assertEqual(get_message("runtime.vector_model_mismatch"), str(ctx.exception))
        self.assertFalse(wm2.embedding_client._fingerprint_verified)
        with self.assertRaises(RuntimeError) as retry_error:
            wm2.embedding_client.get_embedding("retry query")
        self.assertEqual(str(retry_error.exception), get_message("runtime.vector_model_mismatch"))
        self.assertFalse(wm2.embedding_client._fingerprint_verified)
        wm2.close()

    @patch("workflow.LLMClient")
    def test_missing_probe_is_retried_without_saving_metadata(self, mock_llm_client_class):
        embedding_client = MagicMock()
        probe_calls = 0

        def embed(text):
            nonlocal probe_calls
            if text == "Hello World!":
                probe_calls += 1
                return None if probe_calls == 1 else [0.2] * 4
            return [0.3] * 4

        embedding_client.get_embedding = embed
        mock_llm_client_class.side_effect = lambda model_config, enable_embedding=False: (
            embedding_client if enable_embedding else MagicMock()
        )
        from workflow import WorkflowManager
        wm = WorkflowManager()
        try:
            with self.assertRaises(RuntimeError) as probe_error:
                wm.embedding_client.get_embedding("first query")
            self.assertEqual(str(probe_error.exception), get_message("runtime.embedding_probe_unavailable"))
            self.assertFalse(wm.embedding_client._fingerprint_verified)
            self.assertIsNone(wm.memory.get_schema_meta("embedding_fingerprint"))
            self.assertEqual(wm.embedding_client.get_embedding("second query"), [0.3] * 4)
            self.assertTrue(wm.embedding_client._fingerprint_verified)
            self.assertEqual(probe_calls, 2)
        finally:
            wm.close()

    @patch("workflow.LLMClient")
    def test_out_of_range_stored_fingerprint_is_reported_as_invalid(self, mock_llm_client_class):
        from memory import MemoryManager
        from workflow import WorkflowManager

        memory = MemoryManager(self.db_path, self.faiss_path)
        memory.set_schema_meta("embedding_fingerprint", json.dumps([1e100] * 4))
        memory.set_schema_meta("embedding_dim", "4")
        memory.close()

        embedding_client = MagicMock()
        embedding_client.get_embedding = lambda text: [0.2] * 4
        mock_llm_client_class.side_effect = lambda model_config, enable_embedding=False: (
            embedding_client if enable_embedding else MagicMock()
        )
        wm = WorkflowManager()
        try:
            with self.assertRaises(RuntimeError) as fingerprint_error:
                wm.embedding_client.get_embedding("query")
            self.assertEqual(
                str(fingerprint_error.exception),
                get_message("runtime.embedding_fingerprint_invalid"),
            )
            self.assertFalse(wm.embedding_client._fingerprint_verified)
        finally:
            wm.close()

    @patch("workflow.LLMClient")
    def test_ordinary_embeddings_reject_malformed_and_nonfinite_vectors(self, mock_llm_client_class):
        current_vector = [0.2] * 4
        embedding_client = MagicMock()

        def embed(text):
            return [0.1] * 4 if text == "Hello World!" else current_vector

        embedding_client.get_embedding = embed
        mock_llm_client_class.side_effect = lambda model_config, enable_embedding=False: (
            embedding_client if enable_embedding else MagicMock()
        )
        from workflow import WorkflowManager
        wm = WorkflowManager()
        try:
            self.assertEqual(wm.embedding_client.get_embedding("warmup"), [0.2] * 4)
            for current_vector in (
                [],
                [[0.2] * 4],
                [float("nan")] * 4,
                [float("inf")] * 4,
                [1e100] * 4,
                "not a vector",
            ):
                with self.subTest(vector=current_vector):
                    with self.assertRaises(RuntimeError) as vector_error:
                        wm.embedding_client.get_embedding("ordinary query")
                    self.assertEqual(str(vector_error.exception), get_message("runtime.embedding_vector_invalid"))
            current_vector = [0.3] * 4
            self.assertEqual(wm.embedding_client.get_embedding("recovered query"), [0.3] * 4)
        finally:
            wm.close()

    @patch("workflow.LLMClient")
    def test_invalid_probe_is_retried_without_marking_model_verified(self, mock_llm_client_class):
        probe_vector = [float("nan")] * 4
        embedding_client = MagicMock()

        def embed(text):
            return probe_vector if text == "Hello World!" else [0.2] * 4

        embedding_client.get_embedding = embed
        mock_llm_client_class.side_effect = lambda model_config, enable_embedding=False: (
            embedding_client if enable_embedding else MagicMock()
        )
        from workflow import WorkflowManager
        wm = WorkflowManager()
        try:
            for probe_vector in ([float("nan")] * 4, [[0.1] * 4], [1e100] * 4, []):
                with self.subTest(probe=probe_vector):
                    with self.assertRaises(RuntimeError) as probe_error:
                        wm.embedding_client.get_embedding("query")
                    self.assertEqual(
                        str(probe_error.exception), get_message("runtime.embedding_probe_invalid")
                    )
                    self.assertFalse(wm.embedding_client._fingerprint_verified)
                    self.assertIsNone(wm.memory.get_schema_meta("embedding_fingerprint"))
            probe_vector = [0.1] * 4
            self.assertEqual(wm.embedding_client.get_embedding("query"), [0.2] * 4)
        finally:
            wm.close()

    @patch("workflow.LLMClient")
    def test_empty_rebuild_uses_probed_dimension_and_survives_restart(self, mock_llm_client_class):
        def new_client(model_config, enable_embedding=False):
            client = MagicMock()
            if enable_embedding:
                client.get_embedding = lambda text: [0.2] * 4
            return client

        mock_llm_client_class.side_effect = new_client
        from workflow import WorkflowManager
        wm = WorkflowManager()
        try:
            stats = wm.rebuild_vector_index()
            self.assertEqual(stats["rebuilt"], 0)
            self.assertEqual(wm.memory.index.d, 4)
            self.assertEqual(wm.memory.get_schema_meta("embedding_dim"), "4")
            self.assertTrue(wm.memory.reconcile_vector_store()["healthy"])
            self.assertEqual(wm.embedding_client.get_embedding("query"), [0.2] * 4)
        finally:
            wm.close()

        reopened = WorkflowManager()
        try:
            self.assertEqual(reopened.memory.index.d, 4)
            self.assertTrue(reopened.memory.reconcile_vector_store()["healthy"])
            self.assertEqual(reopened.embedding_client.get_embedding("query"), [0.2] * 4)
        finally:
            reopened.close()

    @patch("workflow.LLMClient")
    def test_auto_rebuild_provider_failure_preserves_source_metadata(self, mock_llm_client_class):
        from memory import MemoryManager
        from workflow import WorkflowManager

        memory = MemoryManager(self.db_path, self.faiss_path)
        memory.add_semantic_fact("preserve this fact", [0.1] * 4, {"source": "test"})
        memory.close()
        os.unlink(self.faiss_path)

        embedding_client = MagicMock()
        embedding_client.get_embedding = lambda text: None
        mock_llm_client_class.side_effect = lambda model_config, enable_embedding=False: (
            embedding_client if enable_embedding else MagicMock()
        )
        with self.assertRaises(RuntimeError) as rebuild_error:
            WorkflowManager()
        self.assertIn(get_message("runtime.embedding_probe_unavailable"), str(rebuild_error.exception))
        reopened = MemoryManager(self.db_path, self.faiss_path)
        try:
            self.assertTrue(reopened.reconcile_vector_store()["requires_rebuild"])
            reopened.cursor.execute("SELECT content, is_deleted FROM vector_metadata")
            self.assertEqual(reopened.cursor.fetchall(), [("preserve this fact", 0)])
            self.assertIsNone(reopened.index)
            self.assertFalse(os.path.exists(self.faiss_path))
        finally:
            reopened.close()

    @patch("workflow.LLMClient")
    def test_startup_reconciles_wrong_dimension_empty_index(self, mock_llm_client_class):
        import faiss
        from memory import MemoryManager
        from workflow import WorkflowManager

        faiss.write_index(faiss.IndexFlatL2(768), self.faiss_path)
        memory = MemoryManager(self.db_path, self.faiss_path)
        memory.set_schema_meta("embedding_dim", "4")
        memory.close()

        def new_client(model_config, enable_embedding=False):
            client = MagicMock()
            if enable_embedding:
                client.get_embedding = lambda text: [0.2] * 4
            return client

        mock_llm_client_class.side_effect = new_client
        wm = WorkflowManager()
        try:
            self.assertEqual(wm.memory.index.d, 4)
            self.assertTrue(wm.vector_health["healthy"])
            self.assertEqual(wm.embedding_client.get_embedding("query"), [0.2] * 4)
        finally:
            wm.close()

    @patch("workflow.LLMClient")
    def test_startup_rebuild_preserves_legacy_positive_tombstone(self, mock_llm_client_class):
        from memory import MemoryManager
        from workflow import WorkflowManager

        memory = MemoryManager(self.db_path, self.faiss_path)
        memory.cursor.execute(
            """INSERT INTO vector_metadata (faiss_id, content, metadata, is_deleted)
               VALUES (0, 'deleted secret', '{}', 1)"""
        )
        memory.conn.commit()
        memory.close()

        def new_client(model_config, enable_embedding=False):
            client = MagicMock()
            if enable_embedding:
                def embed(text):
                    self.assertEqual(text, "Hello World!")
                    return [0.2] * 4
                client.get_embedding = embed
            return client

        mock_llm_client_class.side_effect = new_client
        wm = WorkflowManager()
        try:
            self.assertEqual((wm.memory.index.d, wm.memory.index.ntotal), (4, 0))
            self.assertTrue(wm.vector_health["healthy"])
            wm.memory.cursor.execute(
                "SELECT faiss_id, is_deleted FROM vector_metadata WHERE content='deleted secret'"
            )
            self.assertEqual(wm.memory.cursor.fetchone(), (-1, 1))
        finally:
            wm.close()

    @patch("workflow.LLMClient")
    def test_failed_rebuild_keeps_previous_fingerprint_and_searchable_index(self, mock_llm_client_class):
        fail_fact_embedding = False

        def embed(text):
            if fail_fact_embedding and text == "existing fact":
                return None
            return [0.2] * 4

        def new_client(model_config, enable_embedding=False):
            client = MagicMock()
            if enable_embedding:
                client.get_embedding = embed
            return client

        mock_llm_client_class.side_effect = new_client
        from workflow import WorkflowManager
        wm = WorkflowManager()
        try:
            wm.embedding_client.get_embedding("warmup")
            previous_fingerprint = wm.memory.get_schema_meta("embedding_fingerprint")
            wm.memory.add_semantic_fact("existing fact", [0.2] * 4, {"source": "test"})
            fail_fact_embedding = True

            with self.assertRaises(RuntimeError):
                wm.rebuild_vector_index()

            self.assertFalse(wm.embedding_client._fingerprint_verified)
            self.assertEqual(wm.memory.get_schema_meta("embedding_fingerprint"), previous_fingerprint)
            self.assertEqual(wm.memory.index.ntotal, 1)
            self.assertTrue(wm.memory.reconcile_vector_store()["healthy"])
            self.assertEqual(wm.embedding_client.get_embedding("ordinary query"), [0.2] * 4)
        finally:
            wm.close()

    @patch("workflow.LLMClient")
    def test_dimension_validation_on_every_call(self, mock_llm_client_class):
        mock_embedding_client = MagicMock()
        
        # "Hello World!" fingerprint is of length 128, but actual query returns vector of different length 64
        mock_embedding_client.get_embedding = lambda text: [0.1] * 128 if text == "Hello World!" else [0.5] * 64
        
        def get_mock_client(model_config, enable_embedding=False):
            if enable_embedding:
                return mock_embedding_client
            return MagicMock()
            
        mock_llm_client_class.side_effect = get_mock_client
        
        from workflow import WorkflowManager
        wm = WorkflowManager()
        
        # First call will initialize fingerprint of size 128 in DB, but then return vector of size 64 for "test"
        # and should fail the dimension check immediately!
        with self.assertRaises(RuntimeError) as ctx:
            wm.embedding_client.get_embedding("test")
            
        self.assertEqual(
            str(ctx.exception),
            get_message("runtime.vector_dim_mismatch", expected=128, actual=64),
        )
        wm.close()

    @patch("workflow.LLMClient")
    def test_rebuild_vectors_updates_metadata_after_complete_rebuild(self, mock_llm_client_class):
        # 1. Initialize with 128-dim fingerprint
        mock_embedding_client = MagicMock()
        
        self.vector_dim = 128
        
        def mock_get_embedding(text):
            if text == "Hello World!":
                return [0.1] * self.vector_dim
            return [0.5] * self.vector_dim
            
        mock_embedding_client.get_embedding = mock_get_embedding
        
        def get_mock_client(model_config, enable_embedding=False):
            if enable_embedding:
                return mock_embedding_client
            return MagicMock()
            
        mock_llm_client_class.side_effect = get_mock_client
        
        from workflow import WorkflowManager
        wm = WorkflowManager()
        
        # Warm up & initialize DB
        wm.embedding_client.get_embedding("warmup")
        
        # Ensure SQLite has dim = 128
        self.assertEqual(wm.memory.get_schema_meta("embedding_dim"), "128")
        
        # Add some mock vector metadata to memory
        wm.memory.cursor.execute(
            "INSERT INTO vector_metadata (faiss_id, content, metadata, source_commit_id) VALUES (?, ?, ?, ?)",
            (0, "Tavern", "{}", "commit_1")
        )
        wm.memory.conn.commit()
        wm.memory._init_faiss() # Setup self.index
        
        # 2. Change vector_dim to 256 (simulating a model switch)
        self.vector_dim = 256
        
        # Running generic call should crash because dim is now 256 but DB expects 128
        with self.assertRaises(RuntimeError):
            wm.embedding_client.get_embedding("generic call")
            
        # Rebuild uses the original provider without the old dimension guard.
        import faiss
        wm.memory.index = faiss.IndexFlatL2(128) # Initialize faiss index
        
        stats = wm.rebuild_vector_index()
        self.assertEqual(stats["rebuilt"], 1)
        
        # Verify SQLite has now updated the dimension to 256 and stored the new fingerprint of size 256
        self.assertEqual(wm.memory.get_schema_meta("embedding_dim"), "256")
        
        new_fp_json = wm.memory.get_schema_meta("embedding_fingerprint")
        new_fp = json.loads(new_fp_json)
        self.assertEqual(len(new_fp), 256)
        
        # Generic calls should now succeed without error since fingerprint and dimension were updated to 256!
        v = wm.embedding_client.get_embedding("successful call")
        self.assertEqual(len(v), 256)
        
        wm.close()

if __name__ == "__main__":
    unittest.main()
