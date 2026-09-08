from .base import *


class GeneratorAndToolTests(AutonomyTestCase):
    @unittest.mock.patch("ai_team_team.ATTManager.register_generator_handler")
    def test_generator_handler_routing(self, mock_register):
        import asyncio
        from workflow_components.autonomy_mixin import AutonomyWorkflowMixin
        self.wf.initialize_autonomy = AutonomyWorkflowMixin.initialize_autonomy.__get__(self.wf)

        # Setup dummy clients
        self.wf.architect_client = unittest.mock.MagicMock()
        self.wf.planner_client = unittest.mock.MagicMock()
        self.wf.critic_client = unittest.mock.MagicMock()

        # Run init
        self.wf.initialize_autonomy()
        self.assertEqual(
            self.wf.att_manager.config.max_tool_argument_retries,
            config.MAX_TOOL_ARGUMENT_RETRIES,
        )
        self.assertEqual(
            self.wf.att_manager.config.max_tool_execution_retries,
            config.MAX_TOOL_EXECUTION_RETRIES,
        )
        self.assertEqual(
            self.wf.att_manager.config.tool_execution_retry_policy,
            config.TOOL_EXECUTION_RETRY_POLICY,
        )
        self.assertEqual(
            self.wf.att_manager.config.turn_failure_policy.tool,
            config.TURN_FAILURE_TOOL_POLICY,
        )
        self.assertFalse(self.wf.att_manager.config.episodic_memory.enabled)
        self.assertIn("default", self.wf.att_manager.model_configs)
        self.assertIs(
            self.wf.att_manager.model_configs["default"][
                "supports_native_tool_calling"
            ],
            False,
        )

        # Force collision: map "gemma4-26b" (or whatever the model name is) to critic_client
        model_key = config.models_section.get("critic_model")
        self.wf.llm_clients[model_key] = self.wf.critic_client

        # Capture generator_handler
        handler = mock_register.call_args[0][0]

        # 1. Test routing with "planner" keyword in system_instruction
        tool_marker = object()
        asyncio.run(
            handler(
                model_name=model_key,
                prompt="test",
                system_instruction="You are a Consensus_Planner.",
                tools=[tool_marker],
                max_output_tokens=456,
            )
        )
        self.wf.planner_client.generate.assert_called_once_with(
            "test",
            system_instruction="You are a Consensus_Planner.",
            temperature=0.3,
            require_json=False,
            tools=[tool_marker],
            max_output_tokens=456,
        )
        self.wf.critic_client.generate.assert_not_called()

        self.wf.planner_client.generate.reset_mock()

        # 2. Test routing with "architect" keyword in system_instruction
        asyncio.run(handler(model_name=model_key, prompt="test", system_instruction="You are the Lore_Architect."))
        self.wf.architect_client.generate.assert_called_once()
        self.wf.critic_client.generate.assert_not_called()

        self.wf.architect_client.generate.reset_mock()

        # 3. Test routing with no keywords (should fall back to model_name, which is critic_client due to collision)
        asyncio.run(handler(model_name=model_key, prompt="test", system_instruction="Hello World."))
        self.wf.critic_client.generate.assert_called_once()

        # 4. Provider TypeError is propagated once; it is not retried without
        # generation options (which used to silently remove native tools).
        self.wf.critic_client.generate.reset_mock()
        self.wf.critic_client.generate.side_effect = TypeError("provider rejected request")
        with self.assertRaises(TypeError):
            asyncio.run(
                handler(
                    model_name=model_key,
                    prompt="test",
                    system_instruction="Hello World.",
                    tools=[tool_marker],
                )
            )
        self.wf.critic_client.generate.assert_called_once()
    def test_autonomous_query_toggle_controls_custom_att_tools(self):
        from workflow_components.autonomy_mixin import AutonomyWorkflowMixin

        self.wf.initialize_autonomy = AutonomyWorkflowMixin.initialize_autonomy.__get__(self.wf)
        self.wf.architect_client = unittest.mock.MagicMock()
        self.wf.planner_client = unittest.mock.MagicMock()
        self.wf.writer_client = unittest.mock.MagicMock()
        self.wf.critic_client = unittest.mock.MagicMock()
        self.wf.scanner_client = unittest.mock.MagicMock()
        config.ENABLE_AUTONOMY_SUITE = True
        config.ENABLE_AUTONOMOUS_QUERIES = False

        self.wf.initialize_autonomy()

        custom_tools = {"query_sqlite", "search_faiss", "read_file_chunk", "read_file_tail"}
        self.assertTrue(custom_tools.isdisjoint(self.wf.att_manager.global_tools))
        self.assertNotIn("query_sqlite", self.wf.att_manager.tool_auditors)
        for preset_name in (
            "conflict_resolution",
            "database_management",
            "planning",
            "editorial",
            "world_bible",
            "plot_outline",
        ):
            self.assertEqual(len(self.wf.att_manager.get_preset(preset_name)["roles"]), 3)
    def test_att_sql_tool_commits_with_thread_safe_connection_and_dmc_has_no_tools(self):
        import asyncio
        import sqlite3
        from memory import MemoryManager
        from workflow_components.autonomy_mixin import AutonomyWorkflowMixin

        memory = MemoryManager(
            os.path.join(self.tmpdir, "att_tool.db"),
            os.path.join(self.tmpdir, "att_tool.faiss"),
        )
        self.wf.memory = memory
        self.wf.initialize_autonomy = AutonomyWorkflowMixin.initialize_autonomy.__get__(self.wf)
        self.wf.architect_client = unittest.mock.MagicMock()
        self.wf.planner_client = unittest.mock.MagicMock()
        self.wf.writer_client = unittest.mock.MagicMock()
        self.wf.critic_client = unittest.mock.MagicMock()
        self.wf.scanner_client = unittest.mock.MagicMock()
        self.wf.embedding_client = unittest.mock.MagicMock()
        config.ENABLE_AUTONOMY_SUITE = True
        config.ENABLE_AUTONOMOUS_QUERIES = True

        try:
            self.wf.initialize_autonomy()
            tool = self.wf.att_manager.global_tools["query_sqlite"]
            opened_connections = []
            real_connect = sqlite3.connect

            class TrackingConnection(sqlite3.Connection):
                was_closed = False

                def close(self):
                    self.was_closed = True
                    return super().close()

            def tracking_connect(*args, **kwargs):
                kwargs["factory"] = TrackingConnection
                connection = real_connect(*args, **kwargs)
                opened_connections.append(connection)
                return connection

            with unittest.mock.patch(
                "workflow_components.autonomy_mixin.sqlite3.connect",
                side_effect=tracking_connect,
            ):
                result = asyncio.run(
                    tool(
                        "INSERT INTO schema_meta (key, value) "
                        "VALUES ('att_tool_commit', 'yes')"
                    )
                )
            self.assertIn("Rows affected: 1", result)
            self.assertEqual(len(opened_connections), 1)
            self.assertTrue(opened_connections[0].was_closed)
            self.assertEqual(memory.get_schema_meta("att_tool_commit"), "yes")

            committee_team = self.wf.db_committee._create_team()
            self.assertEqual(committee_team.tools, {})
        finally:
            memory.close()
            self.wf.memory = None

    def test_custom_tool_memory_capture_policies_are_explicit(self):
        from workflow_components.autonomy_mixin import AutonomyWorkflowMixin

        self.wf.initialize_autonomy = AutonomyWorkflowMixin.initialize_autonomy.__get__(
            self.wf
        )
        for role in ("architect", "planner", "writer", "critic", "scanner"):
            setattr(self.wf, f"{role}_client", unittest.mock.MagicMock())
        config.ENABLE_AUTONOMY_SUITE = True
        config.ENABLE_AUTONOMOUS_QUERIES = True
        config.TOOL_MEMORY_CAPTURE_POLICIES = {
            "query_sqlite": "metadata_only",
            "search_faiss": "content",
            "read_file_chunk": "metadata_only",
            "read_file_tail": "content",
        }

        self.wf.initialize_autonomy()

        self.assertEqual(
            self.wf.att_manager.global_tools["query_sqlite"].memory_capture,
            "metadata_only",
        )
        self.assertEqual(
            self.wf.att_manager.global_tools["search_faiss"].memory_capture,
            "content",
        )
        self.assertEqual(
            self.wf.att_manager.global_tools["read_file_tail"].memory_capture,
            "content",
        )

    def test_enabled_episodic_memory_reuses_role_agents_across_teams_and_restore(self):
        from workflow_components.autonomy_mixin import AutonomyWorkflowMixin

        def new_workflow():
            workflow = WorkflowManager.__new__(WorkflowManager)
            workflow.logger = unittest.mock.MagicMock()
            for role in ("architect", "planner", "writer", "critic", "scanner"):
                setattr(workflow, f"{role}_client", unittest.mock.MagicMock())
            workflow.initialize_autonomy = (
                AutonomyWorkflowMixin.initialize_autonomy.__get__(workflow)
            )
            return workflow

        config.EPISODIC_MEMORY_ENABLED = True
        config.EPISODIC_MEMORY_SETTINGS = {
            **config.EPISODIC_MEMORY_SETTINGS,
            "enabled": True,
            "index_retry_backoff_factor": 0.0,
        }
        config.ENABLE_AUTONOMOUS_QUERIES = False

        first = new_workflow()
        first.initialize_autonomy()
        team_one = first._create_att_team("planning", 1)
        team_two = first._create_att_team("planning", 2)
        first_ids = [member.agent_id for member in team_one.members]
        self.assertEqual(first_ids, [member.agent_id for member in team_two.members])
        database_team = first.db_committee._create_team()
        self.assertEqual(
            [member.agent_id for member in database_team.members],
            [
                first._att_stable_agent_ids[name]
                for name in (
                    "Security_Officer",
                    "Schema_Auditor",
                    "Transaction_Planner",
                )
            ],
        )
        self.assertNotEqual(team_one.team_id, team_two.team_id)
        self.assertEqual(team_one.chapter_num, 1)
        self.assertEqual(team_two.chapter_num, 2)
        first.close_autonomy()

        second = new_workflow()
        try:
            second.initialize_autonomy()
            restored = second._create_att_team("planning", 3)
            self.assertEqual(first_ids, [member.agent_id for member in restored.members])
            restored_database_team = second.db_committee._create_team()
            self.assertEqual(
                {member.agent_id for member in restored_database_team.members},
                {
                    second._att_stable_agent_ids[name]
                    for name in (
                        "Security_Officer",
                        "Schema_Auditor",
                        "Transaction_Planner",
                    )
                },
            )
            self.assertTrue(second.att_manager.config.episodic_memory.enabled)
        finally:
            second.close_autonomy()

    def test_disabled_episodic_memory_keeps_ephemeral_committee_members(self):
        from workflow_components.autonomy_mixin import AutonomyWorkflowMixin

        self.wf.initialize_autonomy = AutonomyWorkflowMixin.initialize_autonomy.__get__(
            self.wf
        )
        for role in ("architect", "planner", "writer", "critic", "scanner"):
            client = unittest.mock.MagicMock()
            client.generate.return_value = "Final Answer: memory remains disabled"
            setattr(self.wf, f"{role}_client", client)
        config.EPISODIC_MEMORY_ENABLED = False
        config.EPISODIC_MEMORY_SETTINGS = {
            **config.EPISODIC_MEMORY_SETTINGS,
            "enabled": False,
        }

        self.wf.initialize_autonomy()
        first = self.wf._create_att_team("planning", 1)
        second = self.wf._create_att_team("planning", 2)

        self.assertTrue(
            {member.agent_id for member in first.members}.isdisjoint(
                {member.agent_id for member in second.members}
            )
        )
        self.assertNotIn("search_memories", first.tools)

        from att.runtime import run_att_async, run_att_sync

        turn = run_att_async(
            lambda: first.execute_reasoning_step_detailed(
                first.members[0],
                "Do not retain this turn.",
                "Answer once.",
                manager=self.wf.att_manager,
            ),
            self.wf._att_runner,
        )
        self.assertEqual(turn.answer, "memory remains disabled")
        memory_counts = run_att_sync(
            lambda: (
                len(self.wf.att_manager._memory.segments),
                len(self.wf.att_manager._memory.cards),
            ),
            self.wf._att_runner,
        )
        self.assertEqual(memory_counts, (0, 0))
        self.assertEqual(
            sum(
                getattr(self.wf, f"{role}_client").generate.call_count
                for role in ("architect", "planner", "writer", "critic", "scanner")
            ),
            1,
        )

        from att.db_committee import DatabaseManagementCommittee

        first_database_team = self.wf.db_committee._create_team()
        another_committee = DatabaseManagementCommittee(
            self.wf.att_manager,
            runner=self.wf._att_runner,
        )
        second_database_team = another_committee._create_team()
        self.assertTrue(
            {member.agent_id for member in first_database_team.members}.isdisjoint(
                {member.agent_id for member in second_database_team.members}
            )
        )

    def test_current_episodic_config_overrides_restored_att_config(self):
        from workflow_components.autonomy_mixin import AutonomyWorkflowMixin

        def new_workflow():
            workflow = WorkflowManager.__new__(WorkflowManager)
            workflow.logger = unittest.mock.MagicMock()
            for role in ("architect", "planner", "writer", "critic", "scanner"):
                setattr(workflow, f"{role}_client", unittest.mock.MagicMock())
            workflow.initialize_autonomy = (
                AutonomyWorkflowMixin.initialize_autonomy.__get__(workflow)
            )
            return workflow

        config.EPISODIC_MEMORY_ENABLED = False
        config.EPISODIC_MEMORY_SETTINGS = {
            **config.EPISODIC_MEMORY_SETTINGS,
            "enabled": False,
        }
        first = new_workflow()
        first.initialize_autonomy()
        first.close_autonomy()

        config.EPISODIC_MEMORY_ENABLED = True
        config.EPISODIC_MEMORY_SETTINGS = {
            **config.EPISODIC_MEMORY_SETTINGS,
            "enabled": True,
        }
        second = new_workflow()
        try:
            second.initialize_autonomy()
            self.assertTrue(second.att_manager.config.episodic_memory.enabled)
            self.assertEqual(
                len(second._att_stable_agent_ids),
                18,
            )
        finally:
            second.close_autonomy()

        config.EPISODIC_MEMORY_ENABLED = False
        config.EPISODIC_MEMORY_SETTINGS = {
            **config.EPISODIC_MEMORY_SETTINGS,
            "enabled": False,
        }
        third = new_workflow()
        try:
            third.initialize_autonomy()
            self.assertFalse(third.att_manager.config.episodic_memory.enabled)
            self.assertEqual(third._att_stable_agent_ids, {})
            ephemeral = third._create_att_team("planning", 4)
            self.assertNotIn("search_memories", ephemeral.tools)
            for role in ("architect", "planner", "writer", "critic", "scanner"):
                getattr(third, f"{role}_client").generate.assert_not_called()
        finally:
            third.close_autonomy()

    def test_restored_stable_agents_rebind_to_current_model_aliases(self):
        from workflow_components.autonomy_mixin import AutonomyWorkflowMixin

        def new_workflow():
            workflow = WorkflowManager.__new__(WorkflowManager)
            workflow.logger = unittest.mock.MagicMock()
            for role in ("architect", "planner", "writer", "critic", "scanner"):
                setattr(workflow, f"{role}_client", unittest.mock.MagicMock())
            workflow.initialize_autonomy = (
                AutonomyWorkflowMixin.initialize_autonomy.__get__(workflow)
            )
            return workflow

        config.EPISODIC_MEMORY_ENABLED = True
        config.EPISODIC_MEMORY_SETTINGS = {
            **config.EPISODIC_MEMORY_SETTINGS,
            "enabled": True,
        }
        first = new_workflow()
        first.initialize_autonomy()
        first.close_autonomy()

        original_planner_alias = config.models_section["planner_model"]
        prior_alternate = config.MODEL_REGISTRY.get("alternate-planner")
        try:
            config.MODEL_REGISTRY["alternate-planner"] = {
                **config.MODEL_REGISTRY[original_planner_alias],
                "model_name": "alternate-planner",
            }
            config.models_section["planner_model"] = "alternate-planner"
            second = new_workflow()
            try:
                second.initialize_autonomy()
                team = second._create_att_team("planning", 2)
                rebound = {
                    member.name: member.llm_client.model_name
                    for member in team.members
                }
                self.assertEqual(
                    rebound["Structural_Planner"],
                    "alternate-planner",
                )
                self.assertEqual(
                    rebound["Reviewer_Arbitrator"],
                    "alternate-planner",
                )
            finally:
                second.close_autonomy()
        finally:
            config.models_section["planner_model"] = original_planner_alias
            if prior_alternate is None:
                config.MODEL_REGISTRY.pop("alternate-planner", None)
            else:
                config.MODEL_REGISTRY["alternate-planner"] = prior_alternate

    def test_missing_fts5_fails_cleanly_and_stops_runner(self):
        import sqlite3
        from workflow_components.bootstrap_messages import ConfigurationError
        from workflow_components.autonomy_mixin import AutonomyWorkflowMixin

        self.wf.initialize_autonomy = AutonomyWorkflowMixin.initialize_autonomy.__get__(
            self.wf
        )
        config.EPISODIC_MEMORY_ENABLED = True
        config.EPISODIC_MEMORY_SETTINGS = {
            **config.EPISODIC_MEMORY_SETTINGS,
            "enabled": True,
        }

        with unittest.mock.patch(
            "workflow_components.autonomy_mixin.sqlite3.connect",
            side_effect=sqlite3.OperationalError("no fts5"),
        ):
            with self.assertRaises(ConfigurationError) as ctx:
                self.wf.initialize_autonomy()

        self.assertIn("FTS5", str(ctx.exception))
        self.assertIsNone(self.wf.att_manager)
        self.assertIsNone(self.wf._att_runner)

    def test_schema_six_state_fails_cleanly_and_stops_runner(self):
        from contextlib import closing
        import sqlite3
        from workflow_components.bootstrap_messages import ConfigurationError
        from workflow_components.autonomy_mixin import AutonomyWorkflowMixin

        with closing(sqlite3.connect(config.ATT_STATE_DB_PATH)) as connection, connection:
            connection.execute(
                "CREATE TABLE manager_config ("
                "config_key TEXT PRIMARY KEY, config_value TEXT)"
            )
            connection.execute(
                "INSERT INTO manager_config (config_key, config_value) "
                "VALUES ('schema_version', '6')"
            )

        self.wf.initialize_autonomy = AutonomyWorkflowMixin.initialize_autonomy.__get__(
            self.wf
        )
        config.EPISODIC_MEMORY_ENABLED = False
        config.EPISODIC_MEMORY_SETTINGS = {
            **config.EPISODIC_MEMORY_SETTINGS,
            "enabled": False,
        }

        for _ in range(2):
            with self.assertRaises(ConfigurationError) as ctx:
                self.wf.initialize_autonomy()

            self.assertIn("schema 7", str(ctx.exception))
            self.assertIn(config.ATT_STATE_DB_PATH, str(ctx.exception))
            self.assertIsNone(self.wf.att_manager)
            self.assertIsNone(self.wf._att_runner)

        with closing(sqlite3.connect(config.ATT_STATE_DB_PATH)) as connection:
            version = connection.execute(
                "SELECT config_value FROM manager_config "
                "WHERE config_key='schema_version'"
            ).fetchone()[0]
        self.assertEqual(version, "6")
