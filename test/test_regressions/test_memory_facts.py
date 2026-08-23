from .common import *
from .memory_base import MemoryTestCase


class MemoryFactConflictTests(MemoryTestCase):
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

    def test_resolve_conflict_rolls_back_entity_when_queue_update_fails(self):
        self.mm.upsert_character(name="Eve", status="dead", source="test", chapter_num=1)
        self.mm.upsert_character(name="Eve", status="alive", source="test", chapter_num=2)
        conflict_id = self.mm.get_pending_conflicts(limit=10)[0][0]
        self.mm.cursor.execute(
            """CREATE TRIGGER reject_conflict_resolution
               BEFORE UPDATE OF status ON conflict_queue
               WHEN NEW.status = 'RESOLVED'
               BEGIN
                   SELECT RAISE(ABORT, 'simulated queue update failure');
               END"""
        )
        self.mm.conn.commit()

        with self.assertRaisesRegex(Exception, "simulated queue update failure"):
            self.mm.resolve_conflict(
                conflict_id,
                "apply_incoming",
                resolver_note="must roll back",
            )

        self.assertEqual(self.mm.get_character("Eve")[3], "dead")
        self.assertEqual(self.mm.get_conflict_by_id(conflict_id)[8], "PENDING")

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
