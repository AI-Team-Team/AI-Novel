import asyncio
from contextlib import closing
import sqlite3
import threading

from .common import *
from att.runtime import (
    ATTEventLoopRunner,
    close_att_manager,
    run_att_async,
    run_att_sync,
)


class _EpisodicClient:
    def __init__(self):
        self.search_next = False
        self.search_issued = False
        self.search_result_prompt = None
        self.fail_labels = 0
        self.block_labels = False
        self.label_started = threading.Event()

    async def generate(
        self,
        prompt,
        system_instruction=None,
        require_json=False,
        tools=None,
        **kwargs,
    ):
        if require_json:
            if self.block_labels:
                self.label_started.set()
                await asyncio.Event().wait()
            if self.fail_labels:
                self.fail_labels -= 1
                raise RuntimeError("deliberate indexing failure")
            await asyncio.sleep(0.05)
            return LLMResponse(
                text=json.dumps(
                    {
                        "title": "Alpha continuity",
                        "summary": "The prior Agent turn retained the alpha detail.",
                        "tags": ["alpha", "continuity"],
                    }
                )
            )
        if self.search_next and not self.search_issued:
            self.search_issued = True
            return LLMResponse(
                tool_calls=[
                    ToolCall(
                        "search-alpha",
                        "search_memories",
                        {"query": "alpha"},
                    )
                ]
            )
        if self.search_next:
            self.search_result_prompt = prompt
            self.search_next = False
            return LLMResponse(text="Final Answer: prior memory found")
        return LLMResponse(text="Final Answer: retain the alpha detail")

    def supports_native_tool_calling(self):
        return True


class EpisodicMemoryRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="ai_novel_att_memory_")
        self.state_path = os.path.join(self.tmpdir, "att_state_v7.db")
        self.runner = ATTEventLoopRunner()
        self.client = _EpisodicClient()

        def create_manager():
            manager = ATTManager(
                Agent("Root", "Architect", self.client),
                ATTConfig(
                    workspace_root=self.tmpdir,
                    tool_calling_mode="native",
                    episodic_memory={
                        "enabled": True,
                        "index_retry_backoff_factor": 0.0,
                    },
                ),
                db_path=self.state_path,
            )
            manager.register_model(
                "memory-model",
                {"supports_native_tool_calling": True},
                client=self.client,
            )
            shared = Agent("Continuity", "Continuity specialist", self.client)
            manager.register_agent(shared)
            return manager, shared

        self.manager, self.shared = run_att_sync(create_manager, self.runner)

    def tearDown(self):
        close_att_manager(self.manager, self.runner)
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _create_team(self, prefix):
        return run_att_sync(
            lambda: self.manager.create_agent_team(
                self.manager.root_ai,
                member_configs={
                    f"{prefix}A": {"model": "memory-model"},
                    f"{prefix}B": {"model": "memory-model"},
                },
                existing_member_ids=[self.shared.agent_id],
                preset_name=f"{prefix.lower()}-team",
            ),
            self.runner,
        )

    def test_slow_indexer_stays_on_one_loop_and_flushes_on_later_call(self):
        team = self._create_team("First")
        result = run_att_async(
            lambda: team.execute_reasoning_step_detailed(
                self.shared,
                "Remember alpha.",
                "Preserve continuity.",
                manager=self.manager,
            ),
            self.runner,
        )
        self.assertIsNotNone(result.turn_id)

        run_att_async(lambda: self.manager.flush_memory_indexing(), self.runner)

        self.assertEqual(
            run_att_sync(
                lambda: self.manager.list_memory_index_failures(
                    self.shared.agent_id
                ),
                self.runner,
            ),
            [],
        )
        event_types = {
            event.event_type
            for event in run_att_sync(
                lambda: self.manager.list_agent_history(self.shared.agent_id),
                self.runner,
            )
        }
        self.assertIn("memory_indexed", event_types)

    def test_shared_agent_searches_memory_from_another_team(self):
        first_team = self._create_team("First")
        run_att_async(
            lambda: first_team.execute_reasoning_step_detailed(
                self.shared,
                "Remember alpha.",
                "Preserve continuity.",
                manager=self.manager,
            ),
            self.runner,
        )
        run_att_async(lambda: self.manager.flush_memory_indexing(), self.runner)

        second_team = self._create_team("Second")
        self.client.search_next = True
        result = run_att_async(
            lambda: second_team.execute_reasoning_step_detailed(
                self.shared,
                "Find the earlier alpha detail.",
                "Use your own memory catalog.",
                manager=self.manager,
            ),
            self.runner,
        )

        self.assertEqual(result.answer, "Final Answer: prior memory found")
        serialized_prompt = json.dumps(
            self.client.search_result_prompt,
            ensure_ascii=False,
            default=str,
        )
        self.assertIn("Alpha continuity", serialized_prompt)
        self.assertIn(first_team.team_id, serialized_prompt)
        self.assertNotEqual(first_team.team_id, second_team.team_id)

    def test_failed_index_can_be_retried_on_the_same_runner(self):
        run_att_sync(
            lambda: setattr(
                self.manager.config.episodic_memory,
                "index_max_retries",
                0,
            ),
            self.runner,
        )
        self.client.fail_labels = 1
        team = self._create_team("Retry")
        run_att_async(
            lambda: team.execute_reasoning_step_detailed(
                self.shared,
                "Remember a retryable detail.",
                "Preserve continuity.",
                manager=self.manager,
            ),
            self.runner,
        )
        run_att_async(lambda: self.manager.flush_memory_indexing(), self.runner)

        failures = run_att_sync(
            lambda: self.manager.list_memory_index_failures(self.shared.agent_id),
            self.runner,
        )
        self.assertEqual(len(failures), 1)
        run_att_async(
            lambda: self.manager.retry_memory_index(failures[0].segment_id),
            self.runner,
        )
        run_att_async(lambda: self.manager.flush_memory_indexing(), self.runner)
        self.assertEqual(
            run_att_sync(
                lambda: self.manager.list_memory_index_failures(
                    self.shared.agent_id
                ),
                self.runner,
            ),
            [],
        )

    def test_shutdown_persists_processing_index_as_pending_for_restart(self):
        self.client.block_labels = True
        team = self._create_team("Pending")
        run_att_async(
            lambda: team.execute_reasoning_step_detailed(
                self.shared,
                "Remember a detail across restart.",
                "Preserve continuity.",
                manager=self.manager,
            ),
            self.runner,
        )
        self.assertTrue(self.client.label_started.wait(timeout=2.0))
        original_agent_id = self.shared.agent_id

        close_att_manager(self.manager, self.runner)
        with closing(sqlite3.connect(self.state_path)) as connection:
            persisted_statuses = {
                row[0]
                for row in connection.execute(
                    "SELECT status FROM agent_memory_segments"
                ).fetchall()
            }
        self.assertEqual(persisted_statuses, {"pending"})
        self.client.block_labels = False
        self.client.label_started.clear()
        self.runner = ATTEventLoopRunner()

        def restore_manager():
            manager = ATTManager(
                Agent("Replacement Root", "Architect", self.client),
                ATTConfig(workspace_root=self.tmpdir),
                db_path=self.state_path,
            )
            # Supply the runtime binding without changing the state database
            # before load_state has restored its authoritative root/config rows.
            manager.llm_clients["memory-model"] = self.client
            return manager

        self.manager = run_att_sync(restore_manager, self.runner)
        run_att_async(
            lambda: self.manager.load_state(self.state_path),
            self.runner,
        )
        self.shared = run_att_sync(
            lambda: self.manager.agents["Continuity"],
            self.runner,
        )
        run_att_async(lambda: self.manager.flush_memory_indexing(), self.runner)

        self.assertEqual(self.shared.agent_id, original_agent_id)
        event_types = {
            event.event_type
            for event in run_att_sync(
                lambda: self.manager.list_agent_history(self.shared.agent_id),
                self.runner,
            )
        }
        self.assertIn("memory_indexed", event_types)

        restarted_team = self._create_team("Restarted")
        self.client.search_next = True
        recalled = run_att_async(
            lambda: restarted_team.execute_reasoning_step_detailed(
                self.shared,
                "Find the detail retained before restart.",
                "Use your own memory catalog.",
                manager=self.manager,
            ),
            self.runner,
        )
        self.assertEqual(recalled.answer, "Final Answer: prior memory found")
        serialized_prompt = json.dumps(
            self.client.search_result_prompt,
            ensure_ascii=False,
            default=str,
        )
        self.assertIn("Alpha continuity", serialized_prompt)
        self.assertIn(team.team_id, serialized_prompt)
        self.assertNotEqual(team.team_id, restarted_team.team_id)


if __name__ == "__main__":
    unittest.main()
