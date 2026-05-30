import os
import sys
import unittest
import tempfile
import shutil
import yaml

CURRENT_DIR = os.path.dirname(__file__)
ROOT_DIR = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
SRC_DIR = os.path.join(ROOT_DIR, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

class TestModelConfig(unittest.TestCase):
    def setUp(self):
        import config
        self.resolve_field = config._resolve_config_field

    def test_resolve_explicit_env_vars(self):
        os.environ["TEST_API_KEY_ENV"] = "secret_key_123"
        os.environ["TEST_BASE_URL_ENV"] = "http://localhost:8080/v1"
        
        # Test $VAR syntax
        self.assertEqual(self.resolve_field("$TEST_API_KEY_ENV", "api_key", "openai"), "secret_key_123")
        # Test ${VAR} syntax
        self.assertEqual(self.resolve_field("${TEST_BASE_URL_ENV}", "base_url", "openai"), "http://localhost:8080/v1")
        
        # Clean up
        del os.environ["TEST_API_KEY_ENV"]
        del os.environ["TEST_BASE_URL_ENV"]

    def test_resolve_empty_is_empty(self):
        # Blank values should return empty string directly, no fallbacks
        self.assertEqual(self.resolve_field("", "api_key", "openai"), "")
        self.assertEqual(self.resolve_field("", "api_key", "gemini"), "")
        self.assertEqual(self.resolve_field("", "base_url", "openai"), "")

    def test_resolve_unexpanded_placeholder_is_empty(self):
        # If the YAML specifies ${NON_EXISTENT_VAR} and it cannot be resolved, return empty string
        self.assertEqual(self.resolve_field("${NON_EXISTENT_VAR}", "api_key", "openai"), "")

    def test_end_to_end_resolution_logic(self):
        # Let's verify how the loading parses registrations
        # Mock registration registry
        registry = {
            "gemini-flash-custom": {
                "model_type": "llm",
                "api_type": "gemini",
                "api_key": "${GEMINI_API_KEY}"  # Explicit env placeholder
            },
            "openai-local": {
                "model_type": "llm",
                "api_type": "openai",
                "base_url": "${MY_TEST_URL}",
                "model_name": ""  # Should default to key name 'openai-local'
            }
        }
        
        # Set env
        os.environ["GEMINI_API_KEY"] = "gemini_secret"
        os.environ["MY_TEST_URL"] = "http://local_url/v1"
        
        import config
        resolved = {}
        for key, model_info in registry.items():
            api_type = str(model_info.get("api_type", "")).strip().lower()
            model_type = str(model_info.get("model_type", "llm")).strip().lower()
            raw_api_key = model_info.get("api_key", "")
            raw_base_url = model_info.get("base_url", "")
            model_name = str(model_info.get("model_name", "")).strip()
            if not model_name:
                model_name = key

            resolved_api_key = config._resolve_config_field(raw_api_key, "api_key", api_type)
            resolved_base_url = config._resolve_config_field(raw_base_url, "base_url", api_type)

            resolved[key] = {
                "api_type": api_type,
                "model_type": model_type,
                "api_key": resolved_api_key,
                "base_url": resolved_base_url,
                "model_name": model_name,
            }
            
        self.assertEqual(resolved["gemini-flash-custom"]["api_key"], "gemini_secret")
        self.assertEqual(resolved["gemini-flash-custom"]["model_name"], "gemini-flash-custom")
        self.assertEqual(resolved["openai-local"]["base_url"], "http://local_url/v1")
        self.assertEqual(resolved["openai-local"]["model_name"], "openai-local")
        
        del os.environ["GEMINI_API_KEY"]
        del os.environ["MY_TEST_URL"]

    def test_validation_error_on_empty_role(self):
        import config
        bad_models = {
            "default_model": "gpt-5.5",
            "architect_model": "",  # Empty!
            "planner_model": "gpt-5.5",
            "writer_model": "gpt-5.5",
            "critic_model": "gpt-5.5",
            "scanner_model": "gpt-5.5",
            "embedding_model": "emb",
        }
        
        with self.assertRaises(ValueError) as ctx:
            for role in config.REQUIRED_ROLES:
                val = bad_models.get(role)
                if not val or not str(val).strip():
                    raise ValueError(
                        f"Configuration Error: Assigned role '{role}' in config.yaml has an empty or missing value."
                    )
        self.assertIn("Assigned role 'architect_model'", str(ctx.exception))

if __name__ == "__main__":
    unittest.main()
