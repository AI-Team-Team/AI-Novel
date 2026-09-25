from .common import *


class MemoryTestCase(unittest.TestCase):
    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory(prefix="ai_novel_memory_regressions_")
        self.addCleanup(self._temp_dir.cleanup)
        self.db_path = os.path.join(self._temp_dir.name, "test_regressions.db")
        self.faiss_path = os.path.join(self._temp_dir.name, "test_regressions.faiss")
        self.mm = MemoryManager(self.db_path, self.faiss_path)

    def tearDown(self):
        self.mm.close()
