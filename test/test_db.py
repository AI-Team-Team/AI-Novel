import os
import sys
import unittest

CURRENT_DIR = os.path.dirname(__file__)
ROOT_DIR = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
SRC_DIR = os.path.join(ROOT_DIR, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from memory import MemoryManager

class DatabaseTierTests(unittest.TestCase):
    def setUp(self):
        self.db_path = os.path.join(ROOT_DIR, "novel", "process", "test_facts.db")
        self.faiss_path = os.path.join(ROOT_DIR, "novel", "process", "test_index.faiss")
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        if os.path.exists(self.faiss_path):
            os.remove(self.faiss_path)
        self.mm = MemoryManager(self.db_path, self.faiss_path)

    def tearDown(self):
        self.mm.close()

    def test_tier1_character_add_update(self):
        self.mm.upsert_character("Alice", {"mbti": "INFJ"}, {"age": 20})
        char = self.mm.get_character("Alice")
        self.assertIsNotNone(char)
        self.assertIn('"mbti": "INFJ"', char[2])

        self.mm.upsert_character("Alice", {"mbti": "INFJ", "trauma": "fire"}, {"age": 21})
        updated = self.mm.get_character("Alice")
        self.assertIn('"trauma": "fire"', updated[2])
        self.assertIn('"age": 21', updated[4])

    def test_tier2_event_search(self):
        self.mm.add_event(
            "The Great Fire",
            "City burned down",
            "Year 100",
            5,
            ["Alice", "Bob"],
            "Capital",
        )
        events = self.mm.get_events("Alice")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][1], "The Great Fire")

    def test_tier3_semantic_search(self):
        mock_emb = [0.1] * 768
        self.mm.add_semantic_fact(
            "The tavern has a red door.",
            mock_emb,
            {"location": "Tavern", "type": "visual"},
        )
        self.mm.add_semantic_fact(
            "The castle walls are high.",
            mock_emb,
            {"location": "Castle", "type": "visual"},
        )
        results = self.mm.search_semantic(mock_emb, k=1, filter_metadata={"location": "Tavern"})
        self.assertEqual(len(results), 1)
        self.assertIn("tavern", results[0]["content"].lower())

    def test_audit_columns_exist_in_core_fact_tables(self):
        for table in ("world_rules", "timeline_events", "vector_metadata"):
            self.mm.cursor.execute(f"PRAGMA table_info({table})")
            cols = {row[1] for row in self.mm.cursor.fetchall()}
            self.assertIn("source_commit_id", cols)
            self.assertIn("version", cols)
            self.assertIn("is_deleted", cols)
            self.assertIn("intent_tag", cols)

if __name__ == "__main__":
    unittest.main()
