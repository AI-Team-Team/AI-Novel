import json
import logging
import uuid
from typing import Optional, Union, List, Dict, Any

from att.compat import LLMResponse, ToolCall
from workflow_components.resources import get_message

# Conditional import for the new Google GenAI SDK
try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

class LLMClientError(RuntimeError):
    """Raised when generation client calls fail."""

class LLMClient:
    def __init__(
        self,
        model_config: dict,
        enable_embedding: bool = False
    ):
        self.model_config = model_config
        self.model_type = model_config.get("api_type")   # "gemini" or "openai"
        self.model_name = model_config.get("model_name") # Model name/identifier
        self.api_key = model_config.get("api_key")
        self.base_url = model_config.get("base_url")
        self.enable_embedding = enable_embedding
        
        # Generation Clients
        self.gemini_client = None
        self.openai_client = None
        
        # Embedding Client (Separate)
        self.openai_embedding_client = None
        
        self.logger = logging.getLogger("LLMClient")
        
        # Setup Client depending on role/type
        if self.enable_embedding:
            self._setup_embedding()
        else:
            if self.model_type == "gemini":
                self._setup_gemini()
            elif self.model_type == "openai":
                self._setup_openai()
            else:
                raise ValueError(get_message("llm.unknown_model_type", model_type=self.model_type))

    def _setup_gemini(self):
        if not genai:
            self.logger.error(get_message("llm.google_package_missing"))
            return
        
        api_key = self.api_key
        if not api_key:
            self.logger.warning(get_message("llm.gemini_key_missing"))
            return

        self.gemini_client = genai.Client(api_key=api_key)

    def _setup_openai(self):
        if not OpenAI:
            self.logger.error(get_message("llm.openai_package_missing"))
            return
            
        try:
            self.openai_client = OpenAI(
                base_url=self.base_url,
                api_key=self.api_key
            )
        except Exception as e:
            self.logger.error(get_message("llm.openai_init_failed", error=e))

    def _setup_embedding(self):
        """Sets up the dedicated client for embeddings."""
        if self.model_type == "openai":
            if not OpenAI:
                self.logger.error(get_message("llm.openai_embedding_package_missing"))
                return
            try:
                self.openai_embedding_client = OpenAI(
                    base_url=self.base_url,
                    api_key=self.api_key
                )
                self.logger.info(get_message("llm.openai_embedding_ready", base_url=self.base_url))
            except Exception as e:
                self.logger.error(get_message("llm.openai_embedding_init_failed", error=e))
        elif self.model_type == "gemini":
            self._setup_gemini()

    def generate(
        self,
        prompt: Union[str, List[Dict[str, Any]]],
        system_instruction: str = None,
        tools: Optional[List[Any]] = None,
        max_output_tokens: Optional[int] = None,
        temperature: float = 0.7,
        require_json: bool = False,
        **kwargs,
    ) -> Union[str, LLMResponse]:
        """
        Unified generation method.
        """
        if self.model_type == "gemini":
            return self._generate_gemini(
                prompt,
                system_instruction,
                tools,
                max_output_tokens,
                temperature,
                require_json,
            )
        elif self.model_type == "openai":
            return self._generate_openai(
                prompt,
                system_instruction,
                tools,
                max_output_tokens,
                temperature,
                require_json,
            )
        return ""

    def supports_native_tool_calling(self) -> bool:
        """
        Returns True if the client configuration natively supports structured function calling.
        """
        return (
            isinstance(self.model_config, dict)
            and self.model_config.get("supports_native_tool_calling") is True
        )

    def supports_output_token_limit(self) -> str:
        """Report the provider-neutral output-token parameter accepted here."""

        return "max_output_tokens"

    @staticmethod
    def _tool_contract(tool: Any) -> Dict[str, Any]:
        schema = getattr(tool, "json_schema", None) or {
            "type": "object",
            "properties": {},
        }
        return {
            "name": str(getattr(tool, "name", "")),
            "description": str(getattr(tool, "description", "")),
            "parameters": schema,
        }

    @staticmethod
    def _usage_payload(usage: Any) -> Any:
        if usage is None:
            return None
        if hasattr(usage, "model_dump"):
            return usage.model_dump(mode="json")
        return usage

    @staticmethod
    def _openai_messages(
        prompt: Union[str, List[Dict[str, Any]]],
        system_instruction: Optional[str],
    ) -> List[Dict[str, Any]]:
        """Translate ATT history to the strict OpenAI chat message schema."""

        messages: List[Dict[str, Any]] = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        source = prompt if isinstance(prompt, list) else [
            {"role": "user", "content": prompt}
        ]
        for original in source:
            role = str(original.get("role") or "user")
            content = original.get("content")
            if role == "assistant":
                message: Dict[str, Any] = {
                    "role": "assistant",
                    "content": content,
                }
                normalized_calls = []
                for call in original.get("tool_calls") or []:
                    function = dict(call.get("function") or {})
                    arguments = function.get("arguments", {})
                    if not isinstance(arguments, str):
                        arguments = json.dumps(arguments, ensure_ascii=False)
                    normalized_calls.append(
                        {
                            "id": str(call.get("id") or ""),
                            "type": "function",
                            "function": {
                                "name": str(function.get("name") or ""),
                                "arguments": arguments,
                            },
                        }
                    )
                if normalized_calls:
                    message["tool_calls"] = normalized_calls
            elif role == "tool":
                message = {
                    "role": "tool",
                    "content": "" if content is None else content,
                    "tool_call_id": str(original.get("tool_call_id") or ""),
                }
            elif role in {"system", "developer", "user"}:
                message = {
                    "role": role,
                    "content": "" if content is None else content,
                }
                if original.get("name"):
                    message["name"] = str(original["name"])
            else:
                message = {
                    "role": "user",
                    "content": "" if content is None else content,
                }
            messages.append(message)
        return messages

    @staticmethod
    def _gemini_contents(
        prompt: Union[str, List[Dict[str, Any]]]
    ) -> Union[str, List[Any]]:
        if not isinstance(prompt, list):
            return prompt
        contents = []
        pending_tool_parts = []

        def flush_tool_parts() -> None:
            if pending_tool_parts:
                contents.append(
                    types.Content(role="user", parts=list(pending_tool_parts))
                )
                pending_tool_parts.clear()

        for message in prompt:
            role = str(message.get("role") or "user")
            content = str(message.get("content") or "")
            if role == "assistant":
                flush_tool_parts()
                parts = []
                if content:
                    parts.append(types.Part(text=content))
                for call in message.get("tool_calls") or []:
                    function = call.get("function") or {}
                    arguments = function.get("arguments", {})
                    if isinstance(arguments, str):
                        try:
                            arguments = json.loads(arguments)
                        except json.JSONDecodeError:
                            arguments = {}
                    parts.append(
                        types.Part(
                            function_call=types.FunctionCall(
                                id=call.get("id"),
                                name=function.get("name"),
                                args=arguments,
                            )
                        )
                    )
                contents.append(types.Content(role="model", parts=parts))
            elif role == "tool":
                pending_tool_parts.append(
                    types.Part(
                        function_response=types.FunctionResponse(
                            id=message.get("tool_call_id"),
                            name=message.get("name"),
                            response={"output": content},
                        )
                    )
                )
            else:
                flush_tool_parts()
                contents.append(
                    types.Content(role="user", parts=[types.Part(text=content)])
                )
        flush_tool_parts()
        return contents

    def _generate_gemini(
        self,
        prompt: Union[str, List[Dict[str, Any]]],
        system_instruction: str,
        tools: Optional[List[Any]],
        max_output_tokens: Optional[int],
        temperature: float,
        require_json: bool = False,
    ) -> Union[str, LLMResponse]:
        if not self.gemini_client:
            raise LLMClientError(get_message("llm.gemini_uninitialized"))
        
        try:
            config_args = {
                "temperature": temperature
            }
            if require_json:
                config_args["response_mime_type"] = "application/json"
            if max_output_tokens is not None:
                config_args["max_output_tokens"] = max_output_tokens
            if tools:
                declarations = []
                for tool in tools:
                    contract = self._tool_contract(tool)
                    declarations.append(
                        types.FunctionDeclaration(
                            name=contract["name"],
                            description=contract["description"],
                            parameters_json_schema=contract["parameters"],
                        )
                    )
                config_args["tools"] = [
                    types.Tool(function_declarations=declarations)
                ]

            contents = self._gemini_contents(prompt)

            kwargs = {
                "model": self.model_name,
                "contents": contents,
                "config": config_args
            }

            if system_instruction:
                kwargs["config"]["system_instruction"] = system_instruction

            response = self.gemini_client.models.generate_content(**kwargs)
            if not tools:
                return response.text or ""

            text_parts = []
            tool_calls = []
            candidates = list(getattr(response, "candidates", None) or [])
            response_parts = (
                list(
                    getattr(
                        getattr(candidates[0], "content", None), "parts", None
                    )
                    or []
                )
                if candidates
                else []
            )
            for index, part in enumerate(response_parts):
                part_text = getattr(part, "text", None)
                if part_text:
                    text_parts.append(str(part_text))
                function_call = getattr(part, "function_call", None)
                if function_call is not None:
                    raw_arguments = getattr(function_call, "args", None) or {}
                    arguments = dict(raw_arguments)
                    tool_calls.append(
                        ToolCall(
                            call_id=(
                                getattr(function_call, "id", None)
                                or f"gemini-{uuid.uuid4().hex}-{index}"
                            ),
                            name=str(getattr(function_call, "name", "")),
                            arguments=arguments,
                        )
                    )
            return LLMResponse(
                text="\n".join(text_parts) or None,
                tool_calls=tool_calls,
                usage=self._usage_payload(getattr(response, "usage_metadata", None)),
            )
        except Exception as e:
            self.logger.error(get_message("llm.gemini_generation_error", error=e))
            raise LLMClientError(get_message("llm.gemini_generation_failed", error=e)) from e

    def _generate_openai(
        self,
        prompt: Union[str, List[Dict[str, Any]]],
        system_instruction: str,
        tools: Optional[List[Any]],
        max_output_tokens: Optional[int],
        temperature: float,
        require_json: bool = False,
    ) -> Union[str, LLMResponse]:
        if not self.openai_client:
            raise LLMClientError(get_message("llm.openai_uninitialized"))

        messages = self._openai_messages(prompt, system_instruction)

        kwargs = {
            "model": self.model_name,
            "messages": messages,
            "temperature": temperature
        }
        if require_json:
            kwargs["response_format"] = { "type": "json_object" }
        if max_output_tokens is not None:
            kwargs["max_tokens"] = max_output_tokens
        if tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": self._tool_contract(tool),
                }
                for tool in tools
            ]

        try:
            response = self.openai_client.chat.completions.create(**kwargs)
            message = response.choices[0].message
            if not tools:
                return message.content or ""

            tool_calls = []
            for index, call in enumerate(message.tool_calls or []):
                arguments = call.function.arguments
                try:
                    arguments = json.loads(arguments)
                except (json.JSONDecodeError, TypeError):
                    pass
                tool_calls.append(
                    ToolCall(
                        call_id=call.id or f"openai-{uuid.uuid4().hex}-{index}",
                        name=call.function.name,
                        arguments=arguments,
                        raw=(call.model_dump(mode="json") if hasattr(call, "model_dump") else None),
                    )
                )
            return LLMResponse(
                text=message.content,
                tool_calls=tool_calls,
                usage=self._usage_payload(getattr(response, "usage", None)),
            )
        except Exception as e:
            self.logger.error(get_message("llm.openai_generation_error", error=e))
            raise LLMClientError(get_message("llm.openai_generation_failed", error=e)) from e

    def get_embedding(self, text: str) -> Optional[list]:
        """
        Get embedding for vector search using the dedicated embedding configuration.
        """
        provider = self.model_type

        if provider == "gemini":
            if not self.gemini_client: 
                self._setup_gemini()
            if not self.gemini_client:
                self.logger.error(get_message("llm.gemini_embedding_uninitialized"))
                return None
            try:
                result = self.gemini_client.models.embed_content(
                    model=self.model_name,
                    contents=text,
                    config={"task_type": "RETRIEVAL_DOCUMENT"}
                )
                return result.embeddings[0].values
            except Exception as e:
                self.logger.error(get_message("llm.gemini_embedding_error", error=e))
                return None
                
        elif provider == "openai":
            if not self.openai_embedding_client:
                self._setup_embedding()
            if not self.openai_embedding_client:
                self.logger.error(get_message("llm.openai_embedding_uninitialized"))
                return None
            try:
                response = self.openai_embedding_client.embeddings.create(
                    model=self.model_name,
                    input=text
                )
                return response.data[0].embedding
            except Exception as e:
                self.logger.error(get_message("llm.openai_embedding_error", error=e))
                return None
        
        self.logger.error(get_message("llm.unknown_embedding_provider", provider=provider))
        return None
