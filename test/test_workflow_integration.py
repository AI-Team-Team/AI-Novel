import json
import logging
import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock


CURRENT_DIR = os.path.dirname(__file__)
ROOT_DIR = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
SRC_DIR = os.path.join(ROOT_DIR, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

import config
from att.db_committee import DatabaseManagementCommittee
from memory import MemoryManager
from state_manager import StoryStateManager
from workflow import WorkflowManager
from workflow_components.parsing import extract_att_member_answer


class _EmbeddingClient:
    def get_embedding(self, text):
        return [0.25] * 16


class _TextClient:
    def __init__(self, response):
        self.response = response

    def generate(self, prompt, system_instruction=None, require_json=False, **kwargs):
        if require_json:
            return '{"approved": true, "is_healthy": true, "reason": "test approval"}'
        return self.response


class WorkflowLifecycleIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="ai_novel_e2e_")
        self.memory = MemoryManager(
            os.path.join(self.tmpdir, "story.db"),
            os.path.join(self.tmpdir, "story.faiss"),
            embedding_dim=16,
        )
        self.wf = WorkflowManager.__new__(WorkflowManager)
        self.wf.logger = logging.getLogger("workflow-lifecycle-e2e")
        self.wf.memory = self.memory
        self.wf.embedding_client = _EmbeddingClient()
        self.wf.state_manager = StoryStateManager(self.memory, self.wf.embedding_client, 5)
        self.wf.in_auto_mode = False
        self.wf.ai_resolve_conflicts = False
        self.wf.chapters_dir = os.path.join(self.tmpdir, "chapters")
        self.wf.guides_dir = os.path.join(self.tmpdir, "guides")
        self.wf.facts_dir = os.path.join(self.tmpdir, "facts")
        self.wf.archives_dir = os.path.join(self.tmpdir, "archives")
        self.wf.discussions_dir = os.path.join(self.tmpdir, "discussions")
        for path in (
            self.wf.chapters_dir,
            self.wf.guides_dir,
            self.wf.facts_dir,
            self.wf.archives_dir,
            self.wf.discussions_dir,
        ):
            os.makedirs(path, exist_ok=True)

    def tearDown(self):
        self.memory.close()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @staticmethod
    def _payload(event_name="Arrival"):
        return {
            "new_characters": [
                {"name": "Iris", "core_traits": {"role": "keeper"}, "status": "dead"}
            ],
            "updated_characters": [],
            "new_rules": [
                {"category": "Harbor", "content": "The beacon records every arrival", "strictness": 1}
            ],
            "relationships": [],
            "events": [
                {
                    "event_name": event_name,
                    "description": "A ship reaches the harbor",
                    "timestamp_str": "Day 1",
                    "impact_level": 2,
                    "related_entities": [],
                    "location": "Harbor",
                }
            ],
            "details": [
                {
                    "content": "Iris's brass lantern is stored beside the harbor gate.",
                    "metadata": {"location": "Harbor", "type": "object"},
                }
            ],
        }

    def test_write_loop_conflict_replay_and_retrieval_chain(self):
        wf = self.wf
        wf.att_manager = SimpleNamespace(dashboard=None)
        wf._validate_discussion_index_integrity = lambda: None
        wf._validate_runtime_artifacts_integrity = lambda: (set(), [])
        wf._validate_chapter_completion_integrity = lambda chapter: (False, "missing")
        wf._chapter_has_any_artifacts = lambda chapter: False
        wf._get_system_prompts = lambda: {"critic": "critic", "writer": "writer"}
        wf.generate_chapter_guide = lambda chapter, previous=None: "Chapter guide"

        def write_chapter(chapter, guide):
            path = wf.get_chapter_path(chapter)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("Chapter prose")
            return "Chapter prose"

        wf.write_chapter = write_chapter
        wf._review_and_revise_chapter = lambda chapter, guide, text, prompts: (text, "ok")

        def scan_chapter(chapter):
            payload = self._payload()
            commit_id = self.memory.begin_chapter_commit(chapter, "scan_chapter", payload)
            self.memory.begin_batch()
            try:
                conflicts = wf._apply_fact_payload(
                    payload,
                    source="scan_chapter",
                    chapter_num=chapter,
                    source_commit_id=commit_id,
                    intent_tag="scan_extract",
                )
                self.memory.finalize_chapter_commit(commit_id, "COMPLETED", conflicts)
                self.memory.end_batch(True)
            except Exception:
                self.memory.end_batch(False)
                raise
            return "Chapter 1 facts committed"

        wf.scan_chapter = scan_chapter

        with mock.patch("workflow_components.resume_mixin.time.sleep", return_value=None):
            wf.run_continuous_loop(1, 1)

        self.assertEqual(self.memory.get_chapter_commits(1)[0][4], "COMPLETED")
        self.assertIsNotNone(self.memory.get_character("Iris"))

        self.memory.upsert_character("Iris", status="alive", source="chapter_2", chapter_num=2)
        conflict = self.memory.get_pending_conflicts(limit=1)[0]
        self.assertTrue(self.memory.resolve_conflict(conflict[0], "keep_existing"))
        self.assertEqual(self.memory.get_character("Iris")[3], "dead")

        replay_payload = self._payload(event_name="Second Arrival")
        replay_payload["new_characters"] = []
        failed_id = self.memory.begin_chapter_commit(2, "scan_chapter", replay_payload)
        self.memory.finalize_chapter_commit(failed_id, "FAILED", error_message="simulated")

        preview = wf.bulk_replay_failed_commits(limit=10, dry_run=True)
        self.assertEqual(preview["total"], 1)
        self.assertTrue(preview["commits"][0]["can_replay"])
        self.assertEqual(preview["commits"][0]["outcome"], "preview_ready")
        self.assertEqual(preview["commits"][0]["attempts"], 0)
        self.assertEqual(self.memory.get_chapter_commit(failed_id)[4], "FAILED")

        report = wf.bulk_replay_failed_commits(
            limit=10, dry_run=False, max_attempts=2, retry_policy="continue"
        )
        self.assertEqual(report["succeeded"], 1)
        self.assertEqual(report["commits"][0]["outcome"], "replayed")
        self.assertEqual(self.memory.get_chapter_commit(failed_id)[4], "COMPLETED")

        stop_ids = []
        for chapter in (3, 4):
            commit_id = self.memory.begin_chapter_commit(
                chapter, "scan_chapter", replay_payload
            )
            self.memory.finalize_chapter_commit(
                commit_id, "FAILED", error_message="simulated stop-policy failure"
            )
            stop_ids.append(commit_id)
        original_replay = wf.replay_chapter_commit
        wf.replay_chapter_commit = lambda commit_id: False
        try:
            stopped = wf.bulk_replay_failed_commits(
                limit=10,
                dry_run=False,
                max_attempts=1,
                retry_policy="stop",
            )
        finally:
            wf.replay_chapter_commit = original_replay
        self.assertEqual(stopped["failed"], 1)
        self.assertEqual(stopped["skipped"], 1)
        self.assertEqual(
            {item["outcome"] for item in stopped["commits"]},
            {"failed", "not_attempted_after_stop"},
        )

        context = self.wf.state_manager.build_context_package(
            task_type="writer",
            chapter_num=2,
            previous_summary="The first ship arrived at Harbor.",
            user_request="continue at Harbor",
        )
        self.assertTrue(any(event[1] == "Second Arrival" for event in context["events"]))
        self.assertIn("brass lantern", context["semantic_summary"])


class ATTCurrentAPIIntegrationTests(unittest.TestCase):
    def test_discussion_close_and_restore_use_current_att_contract(self):
        tmpdir = tempfile.mkdtemp(prefix="ai_novel_att_api_")
        old_path = config.ATT_STATE_DB_PATH
        config.ATT_STATE_DB_PATH = os.path.join(tmpdir, "att_state.db")
        managers = []
        try:
            for _ in range(2):
                wf = WorkflowManager.__new__(WorkflowManager)
                wf.logger = logging.getLogger("att-current-api-e2e")
                shared_client = _TextClient("Final Answer: final guide")
                wf.architect_client = shared_client
                wf.planner_client = shared_client
                wf.writer_client = shared_client
                wf.critic_client = shared_client
                wf.scanner_client = shared_client
                wf.embedding_client = _EmbeddingClient()
                wf.memory = None
                wf.initialize_autonomy()
                wf.db_committee.enabled = False
                managers.append(wf)

                team = wf._create_att_team("planning", 1)
                transcript = wf._execute_att_discussion(team, "Refine this guide", 1)
                answer = extract_att_member_answer(
                    transcript, team, "Reviewer_Arbitrator"
                )
                self.assertEqual(answer, "final guide")
                self.assertEqual(len(team.members), 3)
                self.assertTrue(all(member.llm_client is not None for member in team.members))
                wf.close_autonomy()
                managers.pop()
        finally:
            for wf in managers:
                try:
                    wf.close_autonomy()
                except Exception:
                    pass
            config.ATT_STATE_DB_PATH = old_path
            shutil.rmtree(tmpdir, ignore_errors=True)


class _CommitteeProbe:
    def __init__(self, enabled_scopes, approved=True):
        self.enabled_scopes = set(enabled_scopes)
        self.approved = approved
        self.calls = []

    def should_audit(self, scope):
        return scope in self.enabled_scopes

    def audit_operation(self, scope, operation, payload, chapter_num=None):
        self.calls.append((scope, operation, payload, chapter_num))
        return self.approved, "probe decision"


class DatabaseAuditScopeIntegrationTests(unittest.TestCase):
    def test_memory_write_scopes_are_independent_and_deny_before_write(self):
        tmpdir = tempfile.mkdtemp(prefix="ai_novel_dmc_scope_")
        memory = MemoryManager(
            os.path.join(tmpdir, "story.db"), os.path.join(tmpdir, "story.faiss")
        )
        try:
            probe = _CommitteeProbe({"character_writes"}, approved=False)
            memory.set_db_committee(probe)
            with self.assertRaises(PermissionError):
                memory.upsert_character("Denied", status="alive", chapter_num=1)
            self.assertIsNone(memory.get_character("Denied"))
            self.assertEqual(probe.calls[0][0], "character_writes")

            probe.approved = True
            memory.add_event("Allowed", "event", "Day 1")
            self.assertEqual(len(probe.calls), 1)
            memory.upsert_character("Allowed", status="alive", chapter_num=1)
            self.assertIsNotNone(memory.get_character("Allowed"))
            self.assertEqual(len(probe.calls), 2)

            revision_probe = _CommitteeProbe({"revision_writes"}, approved=False)
            memory.set_db_committee(revision_probe)
            with self.assertRaises(PermissionError):
                memory.upsert_character(
                    "Revision Denied",
                    status="alive",
                    chapter_num=1,
                )
            self.assertIsNone(memory.get_character("Revision Denied"))
            self.assertEqual(revision_probe.calls[0][0], "revision_writes")

            memory.set_db_committee(None)
            memory.upsert_character("Queue Guard", status="dead", chapter_num=1)
            memory.upsert_character("Queue Guard", status="alive", chapter_num=2)
            conflict_id = memory.get_pending_conflicts(limit=1)[0][0]
            queue_probe = _CommitteeProbe({"conflict_queue_writes"}, approved=False)
            memory.set_db_committee(queue_probe)
            with self.assertRaises(PermissionError):
                memory.resolve_conflict(conflict_id, "keep_existing")
            self.assertEqual(memory.get_conflict_by_id(conflict_id)[8], "PENDING")
            self.assertEqual(queue_probe.calls[0][0], "conflict_queue_writes")
        finally:
            memory.close()
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_database_committee_receives_full_batch_payload(self):
        roles = [
            ("Security_Officer", "security"),
            ("Schema_Auditor", "schema"),
            ("Transaction_Planner", "transaction"),
        ]
        team = SimpleNamespace(
            members=[SimpleNamespace(name=name) for name, _ in roles],
            tools={"query_sqlite": object()},
        )
        create_calls = []

        def create_team(**kwargs):
            create_calls.append(kwargs)
            return team

        manager = SimpleNamespace(
            root_ai=SimpleNamespace(),
            teams={},
            _closing=False,
            config=SimpleNamespace(
                model_registry={name: "model" for name, _ in roles}
            ),
            get_preset=lambda name: {
                "roles": roles,
                "system_instructions": "audit",
            },
            create_agent_team=create_team,
        )
        committee = DatabaseManagementCommittee(
            manager,
            enabled=True,
            scopes={"chapter_fact_batches": True},
            failure_policy="deny",
        )
        captured = {}

        def discussion(_manager, _team, prompt, rounds):
            captured["prompt"] = prompt
            return 'Transaction_Planner: {"approved": true, "reason": "ok"}'

        payload = {
            "events": [
                {
                    "event_name": "Full Payload Marker",
                    "description": "The committee must inspect this content.",
                }
            ]
        }
        with mock.patch("att.db_committee.run_team_discussion", side_effect=discussion):
            approved, reason = committee.audit_batch_transaction(payload, 3)

        self.assertTrue(approved)
        self.assertEqual(reason, "ok")
        self.assertIn("Full Payload Marker", captured["prompt"])
        self.assertIn("The committee must inspect this content.", captured["prompt"])
        self.assertEqual(team.tools, {})
        with mock.patch("att.db_committee.run_team_discussion", side_effect=discussion):
            committee.audit_batch_transaction(payload, 4)
        self.assertEqual(len(create_calls), 1)


if __name__ == "__main__":
    unittest.main()
