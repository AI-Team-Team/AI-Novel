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
