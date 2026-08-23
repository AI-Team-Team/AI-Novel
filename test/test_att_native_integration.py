import json
import logging
import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace


CURRENT_DIR = os.path.dirname(__file__)
ROOT_DIR = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
SRC_DIR = os.path.join(ROOT_DIR, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from ai_team_team import (
    ATTConfig,
    ATTManager,
    Agent,
    DiscussionStatus,
    LLMResponse,
    Tool,
    ToolCall,
)
from ai_team_team.core import ManagerDefaultClientAdapter
from att.runtime import ATTDiscussionPolicyError, select_designated_answer
from att_result_helpers import make_discussion_result, make_team
from llm_client import LLMClient


def _inspect_story(place: str, filters: dict) -> str:
    """Inspect story facts for one place and nested filters."""

    return json.dumps({"place": place, "filters": filters}, ensure_ascii=False)


class _OpenAICompletions:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.response, list):
            return self.response.pop(0)
        return self.response


class NativeProviderAdapterTests(unittest.TestCase):
    def _client(self, provider):
        client = LLMClient.__new__(LLMClient)
        client.model_config = {
            "api_type": provider,
            "supports_native_tool_calling": True,
        }
        client.model_type = provider
        client.model_name = "test-model"
        client.logger = logging.getLogger("native-provider-test")
        client.openai_client = None
        client.gemini_client = None
        return client

    def test_native_capability_requires_literal_boolean_true(self):
        client = self._client("openai")
        self.assertTrue(client.supports_native_tool_calling())
        client.model_config["supports_native_tool_calling"] = "true"
        self.assertFalse(client.supports_native_tool_calling())
        client.model_config.pop("supports_native_tool_calling")
        self.assertFalse(client.supports_native_tool_calling())

    def test_openai_round_trip_preserves_nested_unicode_arguments(self):
        tool = Tool(func=_inspect_story)
        function = SimpleNamespace(
            name="_inspect_story",
            arguments=json.dumps(
                {"place": "上海（旧港）", "filters": {"人物": ["林岚"]}},
                ensure_ascii=False,
            ),
        )
        call = SimpleNamespace(id="call-1", function=function)
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=None, tool_calls=[call])
                )
            ],
            usage=SimpleNamespace(prompt_tokens=12, completion_tokens=4),
        )
        completions = _OpenAICompletions(response)
        client = self._client("openai")
        client.openai_client = SimpleNamespace(
            chat=SimpleNamespace(completions=completions)
        )

        result = client.generate(
            "查找事实",
            system_instruction="你是资料员。",
            tools=[tool],
            max_output_tokens=321,
        )

        self.assertIsInstance(result, LLMResponse)
        self.assertEqual(result.tool_calls[0].call_id, "call-1")
        self.assertEqual(result.tool_calls[0].arguments["place"], "上海（旧港）")
        self.assertEqual(result.tool_calls[0].arguments["filters"]["人物"], ["林岚"])
        request = completions.calls[0]
        self.assertEqual(request["max_tokens"], 321)
        self.assertEqual(
            request["tools"][0]["function"]["parameters"], tool.json_schema
        )

        second_messages = client._openai_messages(
            [
                {
                    "role": "assistant",
                    "content": None,
                    "team_id": "AT-internal",
                    "discussion_id": "DISC-internal",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "function": {
                                "name": "_inspect_story",
                                "arguments": result.tool_calls[0].arguments,
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call-1",
                    "name": "_inspect_story",
                    "content": "港口记录（已找到）",
                    "team_id": "AT-internal",
                    "discussion_id": "DISC-internal",
                },
            ],
            None,
        )
        self.assertIsInstance(
            second_messages[0]["tool_calls"][0]["function"]["arguments"], str
        )
        self.assertEqual(second_messages[1]["tool_call_id"], "call-1")
        self.assertNotIn("name", second_messages[1])
        self.assertNotIn("team_id", second_messages[0])
        self.assertNotIn("discussion_id", second_messages[1])

    def test_gemini_schema_and_function_response_round_trip(self):
        from google.genai import types

        tool = Tool(func=_inspect_story)
        response = SimpleNamespace(
            candidates=[
                SimpleNamespace(
                    content=SimpleNamespace(
                        parts=[
                            SimpleNamespace(
                                text=None,
                                function_call=SimpleNamespace(
                                    id="gemini-call-1",
                                    name="_inspect_story",
                                    args={
                                        "place": "成都（雨夜）",
                                        "filters": {"线索": ["青铜钥匙"]},
                                    },
                                ),
                            )
                        ]
                    )
                )
            ],
            usage_metadata=None,
        )
        calls = []

        def generate_content(**kwargs):
            calls.append(kwargs)
            return response

        client = self._client("gemini")
        client.gemini_client = SimpleNamespace(
            models=SimpleNamespace(generate_content=generate_content)
        )
        result = client.generate(
            [
                {"role": "user", "content": "查找线索"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "prior-call",
                            "function": {
                                "name": "_inspect_story",
                                "arguments": {
                                    "place": "成都（雨夜）",
                                    "filters": {"线索": ["青铜钥匙"]},
                                },
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "prior-call",
                    "name": "_inspect_story",
                    "content": "已找到（第一卷）",
                },
                {
                    "role": "tool",
                    "tool_call_id": "prior-call-2",
                    "name": "_inspect_story",
                    "content": "已找到（第二卷）",
                },
            ],
            tools=[tool],
            max_output_tokens=222,
        )

        self.assertIsInstance(result, LLMResponse)
        self.assertEqual(result.tool_calls[0].call_id, "gemini-call-1")
        self.assertEqual(result.tool_calls[0].arguments["place"], "成都（雨夜）")
        request = calls[0]
        self.assertEqual(request["config"]["max_output_tokens"], 222)
        declaration = request["config"]["tools"][0].function_declarations[0]
        self.assertEqual(declaration.parameters_json_schema, tool.json_schema)
        contents = request["contents"]
        self.assertIsInstance(contents[1], types.Content)
        self.assertEqual(contents[1].parts[0].function_call.id, "prior-call")
        self.assertEqual(
            contents[2].parts[0].function_response.response["output"],
            "已找到（第一卷）",
        )
        self.assertEqual(len(contents), 3)
        self.assertEqual(len(contents[2].parts), 2)
        self.assertEqual(
            contents[2].parts[1].function_response.id, "prior-call-2"
        )


class StructuredDiscussionPolicyTests(unittest.TestCase):
    def setUp(self):
        self.team = make_team("Critic", "Designated_Arbitrator")

    def test_creative_partial_accepts_completed_designated_final_turn(self):
        result = make_discussion_result(
            self.team,
            {
                "Critic": "Critique",
                "Designated_Arbitrator": "可采用的最终稿",
            },
            status=DiscussionStatus.PARTIAL,
            incomplete=("Critic",),
        )
        self.assertEqual(
            select_designated_answer(
                result,
                self.team,
                "Designated_Arbitrator",
                "editorial",
                "accept_designated_member",
            ),
            "可采用的最终稿",
        )

    def test_governance_partial_is_rejected_even_with_designated_answer(self):
        result = make_discussion_result(
            self.team,
            {"Designated_Arbitrator": '{"approved": true}'},
            status=DiscussionStatus.PARTIAL,
            incomplete=("Critic",),
        )
        with self.assertRaises(ATTDiscussionPolicyError):
            select_designated_answer(
                result,
                self.team,
                "Designated_Arbitrator",
                "database_management",
                "reject",
            )

    def test_creative_partial_rejects_incomplete_designated_turn(self):
        result = make_discussion_result(
            self.team,
            {"Critic": "Critique"},
            status=DiscussionStatus.PARTIAL,
            incomplete=("Designated_Arbitrator",),
        )
        with self.assertRaises(ATTDiscussionPolicyError):
            select_designated_answer(
                result,
                self.team,
                "Designated_Arbitrator",
                "editorial",
                "accept_designated_member",
            )

    def test_unknown_partial_policy_fails_closed(self):
        result = make_discussion_result(
            self.team,
            {"Designated_Arbitrator": "Final"},
        )
        with self.assertRaises(ATTDiscussionPolicyError):
            select_designated_answer(
                result,
                self.team,
                "Designated_Arbitrator",
                "editorial",
                "unexpected-policy",
            )


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


if __name__ == "__main__":
    unittest.main()
