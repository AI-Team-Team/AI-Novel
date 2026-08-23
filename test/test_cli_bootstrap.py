import os
import shutil
import subprocess
import sys
import tempfile
import unittest


CURRENT_DIR = os.path.dirname(__file__)
ROOT_DIR = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
SRC_DIR = os.path.join(ROOT_DIR, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

import config


class CliBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="ai_novel_cli_bootstrap_")
        src_dir = os.path.join(self.tmpdir, "src")
        components_dir = os.path.join(src_dir, "workflow_components")
        os.makedirs(components_dir)
        shutil.copy2(os.path.join(ROOT_DIR, "src", "main.py"), src_dir)
        shutil.copy2(os.path.join(ROOT_DIR, "src", "config.py"), src_dir)
        shutil.copy2(
            os.path.join(
                ROOT_DIR,
                "src",
                "workflow_components",
                "bootstrap_messages.py",
            ),
            components_dir,
        )
        with open(os.path.join(components_dir, "__init__.py"), "w", encoding="utf-8"):
            pass
        shutil.copytree(
            os.path.join(ROOT_DIR, "i18n"),
            os.path.join(self.tmpdir, "i18n"),
        )
        self.config_path = os.path.join(self.tmpdir, "config.yaml")
        with open(self.config_path, "w", encoding="utf-8") as handle:
            handle.write(
                "project:\n"
                "  language: zh-CN\n"
                "models:\n"
                "  default_model: disabled-model\n"
                "  architect_model: disabled-model\n"
                "  planner_model: disabled-model\n"
                "  writer_model: disabled-model\n"
                "  critic_model: disabled-model\n"
                "  scanner_model: disabled-model\n"
                "  embedding_model: disabled-model\n"
            )
        model_dir = os.path.join(self.tmpdir, "config")
        os.makedirs(model_dir)
        self.model_config_path = os.path.join(model_dir, "ai_model_config.yaml")
        with open(
            self.model_config_path,
            "w",
            encoding="utf-8",
        ) as handle:
            handle.write(
                "disabled-model:\n"
                "  model_type: llm\n"
                "  api_type: openai\n"
                "  model_name: disabled-model\n"
                "  enabled: false\n"
            )
        with open(os.path.join(src_dir, "workflow.py"), "w", encoding="utf-8") as handle:
            handle.write("import config\n\nclass WorkflowManager:\n    pass\n")
        self.main_path = os.path.join(src_dir, "main.py")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, self.main_path, *args],
            cwd=self.tmpdir,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_help_does_not_import_workflow_or_validate_models(self):
        result = self._run("--help")
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertIn("AI 小说创作工具", output)
        self.assertNotIn("disabled-model", output)
        self.assertNotIn("Traceback", output)

    def test_configuration_error_is_reported_without_traceback(self):
        result = self._run("--plan", "1")
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 2, output)
        self.assertIn("配置错误", output)
        self.assertIn("disabled-model", output)
        self.assertIn("config/ai_model_config.yaml", output)
        self.assertIn("明确禁用", output)
        self.assertNotIn("Traceback", output)

    def test_language_guard_defaults_are_language_aware(self):
        self.assertEqual(config.language_guard_defaults("zh-CN"), (0.70, 0.30))
        self.assertEqual(config.language_guard_defaults("en"), (0.60, 0.10))

    def test_native_tool_capability_rejects_quoted_boolean(self):
        with open(self.model_config_path, "w", encoding="utf-8") as handle:
            handle.write(
                "disabled-model:\n"
                "  model_type: llm\n"
                "  api_type: openai\n"
                "  model_name: enabled-model\n"
                "  supports_native_tool_calling: \"true\"\n"
                "  enabled: true\n"
            )
        result = self._run("--plan", "1")
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 2, output)
        self.assertIn("supports_native_tool_calling", output)
        self.assertIn("布尔值", output)
        self.assertNotIn("Traceback", output)

    def test_att_retry_counts_reject_non_integer_yaml_values(self):
        with open(self.model_config_path, "w", encoding="utf-8") as handle:
            handle.write(
                "disabled-model:\n"
                "  model_type: llm\n"
                "  api_type: openai\n"
                "  model_name: enabled-model\n"
                "  supports_native_tool_calling: false\n"
                "  enabled: true\n"
            )
        with open(self.config_path, "a", encoding="utf-8") as handle:
            handle.write(
                "autonomy:\n"
                "  max_tool_argument_retries: 1.5\n"
            )
        result = self._run("--plan", "1")
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 2, output)
        self.assertIn("非负整数", output)
        self.assertNotIn("Traceback", output)


if __name__ == "__main__":
    unittest.main()
