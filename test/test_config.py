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

class ConfigTests(unittest.TestCase):
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
                import config
                with self.assertRaises(ValueError) as ctx:
                    importlib.reload(config)
        finally:
            if "AI_NOVEL_FORCE_REAL_CONFIG" in os.environ:
                del os.environ["AI_NOVEL_FORCE_REAL_CONFIG"]
                
        self.assertIn("explicitly disabled", str(ctx.exception))

if __name__ == "__main__":
    unittest.main()
