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
from workflow_components.resources import get_resource, get_res_num, is_cjk

class TestI18nLoading(unittest.TestCase):
    def test_chinese_loading(self):
        config.LANGUAGE = "zh-CN"
        # Force re-init if needed (singleton reset for test)
        from workflow_components.resources import LanguageResources
        LanguageResources._instance = None
        
        self.assertTrue(is_cjk())
        self.assertEqual(get_resource("label.contract"), "写作契约")
        self.assertIn("设计世界设定集", get_resource("prompt.architect_task"))
        self.assertIn("必须全程仅使用中文输出", get_resource("prompt.language_rule"))
        
        # Test system prompts
        self.assertIn("架构师", get_resource("architect"))
        self.assertIn("叙事策划", get_resource("planner"))
        self.assertIn("正文的编写者", get_resource("writer"))


    def test_english_loading(self):
        config.LANGUAGE = "en"
        from workflow_components.resources import LanguageResources
        LanguageResources._instance = None
        
        self.assertFalse(is_cjk())
        self.assertEqual(get_resource("label.contract"), "Writing Contract")
        self.assertIn("Design the World Bible", get_resource("prompt.architect_task"))


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
            return {"key": "val"}
            
        mock_json_load.side_effect = side_effect
        with self.assertRaises(ValueError) as ctx:
            LanguageResources()
        self.assertIn("Content Error", str(ctx.exception))

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
