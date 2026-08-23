from .common import *


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
