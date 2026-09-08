import unittest
import importlib
import sys
import os
import io
from unittest.mock import patch

# Setup paths
CURRENT_DIR = os.path.dirname(__file__)
ROOT_DIR = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
SRC_DIR = os.path.join(ROOT_DIR, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from workflow_components.bootstrap_messages import ConfigurationError
import config

class ConfigTests(unittest.TestCase):
    def _assert_real_config_error(self, autonomy_yaml, expected):
        config_content = f"""
models:
  default_model: "gemini"
  architect_model: "gemini"
  planner_model: "gemini"
  writer_model: "gemini"
  critic_model: "gemini"
  scanner_model: "gemini"
  embedding_model: "gemini"
project:
  language: "en"
autonomy:
{autonomy_yaml}
"""
        model_config_content = """
gemini:
  model_type: "llm"
  api_type: "gemini"
  api_key: "dummy"
  model_name: "gemini"
  supports_native_tool_calling: false
"""
        import builtins

        original_open = builtins.open
        original_exists = os.path.exists

        def custom_open(file, *args, **kwargs):
            file_str = str(file)
            if file_str.endswith("ai_model_config.yaml"):
                return io.StringIO(model_config_content)
            if file_str.endswith("config.yaml"):
                return io.StringIO(config_content)
            return original_open(file, *args, **kwargs)

        def custom_exists(path):
            path_str = str(path)
            if path_str.endswith("ai_model_config.yaml") or path_str.endswith(
                "config.yaml"
            ):
                return True
            return original_exists(path)

        os.environ["AI_NOVEL_FORCE_REAL_CONFIG"] = "1"
        try:
            with patch("builtins.open", custom_open), patch(
                "os.path.exists", custom_exists
            ):
                with self.assertRaises(ConfigurationError) as ctx:
                    importlib.reload(config)
        finally:
            os.environ.pop("AI_NOVEL_FORCE_REAL_CONFIG", None)
            importlib.reload(config)
        self.assertIn(expected, str(ctx.exception))

    def test_disabled_model_error(self):
        config_content = """
models:
  default_model: "gemini"
  architect_model: "disabled-model"
  planner_model: "gemini"
  writer_model: "gemini"
  critic_model: "gemini"
  scanner_model: "gemini"
  embedding_model: "gemini"
"""
        model_config_content = """
gemini:
  model_type: "llm"
  api_type: "gemini"
  api_key: "dummy"
  model_name: "gemini"
disabled-model:
  model_type: "llm"
  api_type: "gemini"
  api_key: "dummy"
  model_name: "disabled"
  enabled: false
"""
        
        import builtins
        original_open = builtins.open
        original_exists = os.path.exists
        
        def custom_open(file, *args, **kwargs):
            file_str = str(file)
            if file_str.endswith("ai_model_config.yaml"):
                return io.StringIO(model_config_content)
            elif file_str.endswith("config.yaml"):
                return io.StringIO(config_content)
            return original_open(file, *args, **kwargs)
            
        def custom_exists(path):
            path_str = str(path)
            if path_str.endswith("ai_model_config.yaml") or path_str.endswith("config.yaml"):
                return True
            return original_exists(path)
            
        # Force the real config logic to execute
        os.environ["AI_NOVEL_FORCE_REAL_CONFIG"] = "1"
        try:
            # Apply mocks
            with patch("builtins.open", custom_open), patch("os.path.exists", custom_exists):
                with self.assertRaises(ConfigurationError) as ctx:
                    importlib.reload(config)
        finally:
            if "AI_NOVEL_FORCE_REAL_CONFIG" in os.environ:
                del os.environ["AI_NOVEL_FORCE_REAL_CONFIG"]
            importlib.reload(config)
                
        self.assertIn("explicitly disabled", str(ctx.exception))

    def test_episodic_memory_config_is_strictly_validated(self):
        cases = (
            ("  episodic_memory: []", "must be a dictionary"),
            (
                "  episodic_memory:\n    enabled: \"yes\"",
                "episodic_memory.enabled must be a YAML boolean",
            ),
            (
                "  episodic_memory:\n    unexpected: true",
                "Unknown autonomy.episodic_memory option",
            ),
            (
                "  episodic_memory:\n    42: true",
                "Unknown autonomy.episodic_memory option",
            ),
            (
                "  episodic_memory:\n    index_worker_count: 0",
                "index_worker_count must be an integer greater than or equal to 1",
            ),
            (
                "  episodic_memory:\n    index_retry_backoff_factor: .nan",
                "index_retry_backoff_factor must be a finite non-negative number",
            ),
            (
                "  episodic_memory:\n    tool_capture:\n      query_sqlite: everything",
                "query_sqlite must be 'metadata_only' or 'content'",
            ),
            (
                "  episodic_memory:\n    tool_capture:\n      query_sqlite: []",
                "query_sqlite must be 'metadata_only' or 'content'",
            ),
            (
                "  episodic_memory:\n    tool_capture:\n      unknown_tool: content",
                "Unknown episodic-memory tool capture target",
            ),
        )
        for autonomy_yaml, expected in cases:
            with self.subTest(expected=expected):
                self._assert_real_config_error(autonomy_yaml, expected)

if __name__ == "__main__":
    unittest.main()
