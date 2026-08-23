from .common import *


class ATTNativeToolLoopIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_manager_adapter_executes_native_tool_and_returns_result(self):
        tmpdir = tempfile.mkdtemp(prefix="ai_novel_native_att_")
        manager = None
        try:
            root = Agent(name="Root", role="Architect")
            manager = ATTManager(
                root_ai=root,
                config=ATTConfig(
                    tool_calling_mode="native",
                    max_tool_rounds=2,
                    enable_dynamic_delegation=False,
                    model_registry={"default": "native"},
                    workspace_root=tmpdir,
                ),
                db_path=os.path.join(tmpdir, "att.db"),
            )
            native_config = {"supports_native_tool_calling": True}
            self.assertEqual(manager.config.workspace_root, tmpdir)
            manager.register_model("native", native_config)
            manager.register_model("default", native_config)
            requests = []

            async def handler(
                model_name,
                prompt,
                system_instruction=None,
                tools=None,
                max_output_tokens=None,
                **kwargs,
            ):
                requests.append(
                    {
                        "model_name": model_name,
                        "prompt": prompt,
                        "tools": tools,
                        "max_output_tokens": max_output_tokens,
                    }
                )
                if isinstance(prompt, list) and any(
                    message.get("role") == "tool" for message in prompt
                ):
                    return LLMResponse(text="原生工具链完成")
                return LLMResponse(
                    tool_calls=[
                        ToolCall(
                            call_id="native-call-1",
                            name="echo_unicode",
                            arguments={"text": "上海（旧港）"},
                        )
                    ]
                )

            manager.register_generator_handler(handler)
            root.llm_client = ManagerDefaultClientAdapter(manager)
            root._model_alias = "default"
            manager.register_tool(
                "echo_unicode", "Echo Unicode text", lambda text: f"已找到：{text}"
            )
            team = manager.create_agent_team(
                creator=root,
                member_count=3,
                roles_and_presets=[("A", "A"), ("B", "B"), ("C", "C")],
                roles_and_models={"A": "native", "B": "native", "C": "native"},
            )

            answer = await team.execute_reasoning_step(
                team.members[0], "查找港口", "Use available tools.", manager=manager
            )

            self.assertEqual(answer, "原生工具链完成")
            self.assertEqual(len(requests), 2)
            self.assertTrue(
                any(tool.name == "echo_unicode" for tool in requests[0]["tools"])
            )
            tool_messages = [
                message
                for message in requests[1]["prompt"]
                if message.get("role") == "tool"
            ]
            self.assertEqual(tool_messages[0]["tool_call_id"], "native-call-1")
            self.assertIn("已找到：上海（旧港）", tool_messages[0]["content"])
        finally:
            if manager is not None:
                await manager.close()
            shutil.rmtree(tmpdir, ignore_errors=True)

    async def test_att_to_openai_adapter_round_trip_strips_internal_metadata(self):
        tmpdir = tempfile.mkdtemp(prefix="ai_novel_openai_att_")
        manager = None
        try:
            first_call = SimpleNamespace(
                id="openai-native-call",
                function=SimpleNamespace(
                    name="echo_unicode",
                    arguments='{"text": "杭州（西湖）"}',
                ),
            )
            responses = [
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(
                                content=None, tool_calls=[first_call]
                            )
                        )
                    ],
                    usage=None,
                ),
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(
                                content="OpenAI adapter completed.", tool_calls=None
                            )
                        )
                    ],
                    usage=None,
                ),
            ]
            completions = _OpenAICompletions(responses)
            client = LLMClient.__new__(LLMClient)
            client.model_config = {
                "api_type": "openai",
                "supports_native_tool_calling": True,
            }
            client.model_type = "openai"
            client.model_name = "test-model"
            client.logger = logging.getLogger("att-openai-round-trip")
            client.openai_client = SimpleNamespace(
                chat=SimpleNamespace(completions=completions)
            )
            client.gemini_client = None

            root = Agent(name="Root", role="Architect")
            manager = ATTManager(
                root_ai=root,
                config=ATTConfig(
                    tool_calling_mode="native",
                    max_tool_rounds=2,
                    enable_dynamic_delegation=False,
                    model_registry={"default": "native"},
                    workspace_root=tmpdir,
                ),
                db_path=os.path.join(tmpdir, "att.db"),
            )
            native_config = {"supports_native_tool_calling": True}
            self.assertEqual(manager.config.workspace_root, tmpdir)
            manager.register_model("native", native_config)
            manager.register_model("default", native_config)

            async def handler(
                model_name,
                prompt,
                system_instruction=None,
                tools=None,
                max_output_tokens=None,
                temperature=0.3,
                require_json=False,
                **kwargs,
            ):
                return client.generate(
                    prompt,
                    system_instruction=system_instruction,
                    tools=tools,
                    max_output_tokens=max_output_tokens,
                    temperature=temperature,
                    require_json=require_json,
                )

            manager.register_generator_handler(handler)
            root.llm_client = ManagerDefaultClientAdapter(manager)
            root._model_alias = "default"
            manager.register_tool(
                "echo_unicode", "Echo Unicode text", lambda text: f"找到：{text}"
            )
            team = manager.create_agent_team(
                creator=root,
                member_count=3,
                roles_and_presets=[("A", "A"), ("B", "B"), ("C", "C")],
                roles_and_models={"A": "native", "B": "native", "C": "native"},
            )

            answer = await team.execute_reasoning_step(
                team.members[0], "查找地点", "Use available tools.", manager=manager
            )

            self.assertEqual(answer, "OpenAI adapter completed.")
            second_messages = completions.calls[1]["messages"]
            self.assertTrue(any(message["role"] == "tool" for message in second_messages))
            self.assertTrue(
                all(
                    "team_id" not in message and "discussion_id" not in message
                    for message in second_messages
                )
            )
            tool_message = next(
                message for message in second_messages if message["role"] == "tool"
            )
            self.assertEqual(tool_message["tool_call_id"], "openai-native-call")
            self.assertNotIn("name", tool_message)
            self.assertIn("找到：杭州（西湖）", tool_message["content"])
        finally:
            if manager is not None:
                await manager.close()
            shutil.rmtree(tmpdir, ignore_errors=True)
