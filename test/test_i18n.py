import os
import sys
import unittest
import unittest.mock

# Ensure src is in path
CURRENT_DIR = os.path.dirname(__file__)
ROOT_DIR = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
SRC_DIR = os.path.join(ROOT_DIR, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

import config
from workflow_components.resources import (
    LanguageResources,
    get_ai_resource,
    get_message,
    is_cjk,
)

class TestI18nLoading(unittest.TestCase):
    def setUp(self):
        self.original_language = config.LANGUAGE

    def tearDown(self):
        config.LANGUAGE = self.original_language
        from workflow_components.resources import LanguageResources
        LanguageResources._instance = None

    def test_chinese_loading(self):
        config.LANGUAGE = "zh-CN"
        # Force re-init if needed (singleton reset for test)
        from workflow_components.resources import LanguageResources
        LanguageResources._instance = None
        
        self.assertTrue(is_cjk())
        self.assertEqual(get_ai_resource("label.contract"), "章节大纲")
        self.assertIn("设计世界设定集", get_ai_resource("prompt.architect_task"))
        self.assertIn("必须全程仅使用中文输出", get_ai_resource("prompt.language_rule"))
        self.assertEqual(get_message("cli.done"), "完成。")
        
        # Test system prompts
        self.assertIn("架构师", get_ai_resource("architect"))
        self.assertIn("叙事策划", get_ai_resource("planner"))
        self.assertIn("小说正文的作者", get_ai_resource("writer"))

    def test_english_loading(self):
        config.LANGUAGE = "en"
        from workflow_components.resources import LanguageResources
        LanguageResources._instance = None
        
        self.assertFalse(is_cjk())
        self.assertEqual(get_ai_resource("label.contract"), "Writing Contract")
        self.assertIn("Design the World Bible", get_ai_resource("prompt.architect_task"))
        self.assertEqual(get_message("cli.done"), "Done.")

    def test_human_and_ai_namespaces_are_disjoint(self):
        config.LANGUAGE = "en"
        LanguageResources._instance = None
        resources = LanguageResources()
        self.assertFalse(set(resources.messages).intersection(resources.ai_resources))
        self.assertTrue(get_message("prompt.architect_task").startswith("MISSING_MESSAGE_"))
        self.assertTrue(get_ai_resource("cli.done").startswith("MISSING_AI_RESOURCE_"))


    def test_invalid_language_raises_error(self):
        config.LANGUAGE = "German"
        from workflow_components.resources import LanguageResources
        LanguageResources._instance = None
        with self.assertRaises(ValueError) as ctx:
            LanguageResources()
        self.assertIn("Available languages", str(ctx.exception))

    def test_empty_language_raises_error(self):
        config.LANGUAGE = ""
        from workflow_components.resources import LanguageResources
        LanguageResources._instance = None
        with self.assertRaises(ValueError) as ctx:
            LanguageResources()
        self.assertIn("cannot be empty", str(ctx.exception))

    @unittest.mock.patch("json.load")
    def test_incomplete_json_keys_raises_error(self, mock_json_load):
        # Must use non-en language to trigger comparison with en baseline
        config.LANGUAGE = "zh-CN"
        from workflow_components.resources import LanguageResources
        LanguageResources._instance = None
        
        # Return empty dict for zh-CN, but baseline keys for en standard
        def side_effect(f):
            path = getattr(f, "name", "")
            if "zh-CN" in path:
                return {}
            # Each JSON file owns a different namespace; use a distinct mock key
            # so this test reaches language-parity validation rather than the
            # duplicate-key guard.
            return {os.path.basename(path): "val"}
            
        mock_json_load.side_effect = side_effect
        with self.assertRaises(ValueError) as ctx:
            LanguageResources()
        self.assertIn("本地化内容错误", str(ctx.exception))

    @unittest.mock.patch("json.load")
    def test_corrupted_json_format_raises_error(self, mock_json_load):
        config.LANGUAGE = "en"
        from workflow_components.resources import LanguageResources
        LanguageResources._instance = None
        
        import json
        mock_json_load.side_effect = json.JSONDecodeError("Expecting value", "", 0)
        with self.assertRaises(ValueError) as ctx:
            LanguageResources()
        self.assertIn("JSON Format Error", str(ctx.exception))

if __name__ == "__main__":
    unittest.main()
