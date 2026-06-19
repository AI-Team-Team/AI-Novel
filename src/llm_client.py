import os
import logging
from typing import Optional, Union, List, Dict, Any

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
                raise ValueError(f"Unknown model type: {self.model_type}")

    def _setup_gemini(self):
        if not genai:
            self.logger.error("google-genai package not installed. Please pip install google-genai")
            return
        
        api_key = self.api_key
        if not api_key:
            self.logger.warning("Gemini API Key not found in config.")
            return

        self.gemini_client = genai.Client(api_key=api_key)

    def _setup_openai(self):
        if not OpenAI:
            self.logger.error("openai package not installed. Please pip install openai")
            return
            
        try:
            self.openai_client = OpenAI(
                base_url=self.base_url,
                api_key=self.api_key
            )
        except Exception as e:
            self.logger.error(f"Failed to initialize OpenAI client: {e}")

    def _setup_embedding(self):
        """Sets up the dedicated client for embeddings."""
        if self.model_type == "openai":
            if not OpenAI:
                self.logger.error("openai package not installed. Cannot setup OpenAI embeddings.")
                return
            try:
                self.openai_embedding_client = OpenAI(
                    base_url=self.base_url,
                    api_key=self.api_key
                )
                self.logger.info(f"Initialized OpenAI Embedding Client at {self.base_url}")
            except Exception as e:
                self.logger.error(f"Failed to initialize OpenAI Embedding client: {e}")
        elif self.model_type == "gemini":
            self._setup_gemini()

    def generate(self, prompt: Union[str, List[Dict[str, Any]]], system_instruction: str = None, temperature: float = 0.7, require_json: bool = False) -> str:
        """
        Unified generation method.
        """
        if self.model_type == "gemini":
            return self._generate_gemini(prompt, system_instruction, temperature, require_json)
        elif self.model_type == "openai":
            return self._generate_openai(prompt, system_instruction, temperature, require_json)
        return ""

    def _generate_gemini(self, prompt: Union[str, List[Dict[str, Any]]], system_instruction: str, temperature: float, require_json: bool = False) -> str:
        if not self.gemini_client:
            raise LLMClientError("Gemini client not initialized.")
        
        try:
            config_args = {
                "temperature": temperature
            }
            if require_json:
                config_args["response_mime_type"] = "application/json"
            
            if isinstance(prompt, list):
                contents = []
                for msg in prompt:
                    role = msg.get("role")
                    if role == "assistant":
                        role = "model"
                    content_str = msg.get("content", "")
                    if genai and types:
                        contents.append(
                            types.Content(
                                role=role,
                                parts=[types.Part(text=content_str)]
                            )
                        )
                    else:
                        contents.append({
                            "role": role,
                            "parts": [{"text": content_str}]
                        })
            else:
                contents = prompt

            kwargs = {
                "model": self.model_name,
                "contents": contents,
                "config": config_args
            }

            if system_instruction:
                kwargs["config"]["system_instruction"] = system_instruction

            response = self.gemini_client.models.generate_content(**kwargs)
            return response.text
        except Exception as e:
            self.logger.error(f"Gemini generation error: {e}")
            raise LLMClientError(f"Gemini generation failed: {e}") from e

    def _generate_openai(self, prompt: Union[str, List[Dict[str, Any]]], system_instruction: str, temperature: float, require_json: bool = False) -> str:
        if not self.openai_client:
            raise LLMClientError("OpenAI client not initialized.")

        messages = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        
        if isinstance(prompt, list):
            messages.extend(prompt)
        else:
            messages.append({"role": "user", "content": prompt})

        kwargs = {
            "model": self.model_name,
            "messages": messages,
            "temperature": temperature
        }
        if require_json:
            kwargs["response_format"] = { "type": "json_object" }

        try:
            response = self.openai_client.chat.completions.create(**kwargs)
            return response.choices[0].message.content
        except Exception as e:
            self.logger.error(f"OpenAI generation error: {e}")
            raise LLMClientError(f"OpenAI generation failed: {e}") from e

    def get_embedding(self, text: str) -> Optional[list]:
        """
        Get embedding for vector search using the dedicated embedding configuration.
        """
        provider = self.model_type

        if provider == "gemini":
            if not self.gemini_client: 
                self._setup_gemini()
            if not self.gemini_client:
                self.logger.error("Gemini client not initialized for embeddings.")
                return None
            try:
                result = self.gemini_client.models.embed_content(
                    model=self.model_name,
                    contents=text,
                    config={"task_type": "RETRIEVAL_DOCUMENT"}
                )
                return result.embeddings[0].values
            except Exception as e:
                self.logger.error(f"Gemini embedding error: {e}")
                return None
                
        elif provider == "openai":
            if not self.openai_embedding_client:
                self._setup_embedding()
            if not self.openai_embedding_client:
                self.logger.error("OpenAI Embedding client not initialized.")
                return None
            try:
                response = self.openai_embedding_client.embeddings.create(
                    model=self.model_name,
                    input=text
                )
                return response.data[0].embedding
            except Exception as e:
                self.logger.error(f"OpenAI embedding error: {e}")
                return None
        
        self.logger.error(f"Unknown Embedding Provider: {provider}")
        return None
