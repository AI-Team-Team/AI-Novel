import json
import os
import re
from typing import Dict, Any, List

import config
from workflow_components.bootstrap_messages import get_bootstrap_message

class LanguageResources:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            instance = super(LanguageResources, cls).__new__(cls)
            instance._init_resources()
            cls._instance = instance
        return cls._instance

    def _init_resources(self):
        self.messages: Dict[str, Any] = {}
        self.ai_resources: Dict[str, Any] = {}
        
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        i18n_root = os.path.join(project_root, "i18n")

        def startup_message(key: str, **kwargs) -> str:
            return get_bootstrap_message(
                project_root,
                getattr(config, "LANGUAGE", "en"),
                key,
                **kwargs,
            )
        
        ai_dir = os.path.join(i18n_root, "AI")
        messages_dir = os.path.join(i18n_root, "messages")
        
        if not os.path.exists(ai_dir) or not os.path.exists(messages_dir):
            raise FileNotFoundError(
                startup_message(
                    "localization.roots_missing",
                    ai_dir=ai_dir,
                    messages_dir=messages_dir,
                )
            )
            
        ai_langs = [d for d in os.listdir(ai_dir) if os.path.isdir(os.path.join(ai_dir, d))]
        messages_langs = [d for d in os.listdir(messages_dir) if os.path.isdir(os.path.join(messages_dir, d))]
        
        available_langs = set(ai_langs).intersection(set(messages_langs))
        
        lang_input = str(config.LANGUAGE).strip()
        if not lang_input:
            raise ValueError(startup_message("localization.language_empty"))
            
        # Match directories case-insensitively
        matched_lang = None
        for lang in available_langs:
            if lang.lower() == lang_input.lower():
                matched_lang = lang
                break
                        
        if not matched_lang:
            raise ValueError(
                startup_message(
                    "localization.language_unknown",
                    language=lang_input,
                    available=sorted(available_langs),
                )
            )
            
        self.language_code = matched_lang
        self.is_cjk = (matched_lang.lower() != "en")
        
        # Load & validate formats of active language
        active_messages_dir = os.path.join(i18n_root, "messages", self.language_code)
        active_ai_dir = os.path.join(i18n_root, "AI", self.language_code)
        active_templates_path = os.path.join(i18n_root, "AI", self.language_code, "templates.md")
        
        active_messages = self._load_json_directory(active_messages_dir)
        active_ai_json = self._load_json_directory(active_ai_dir)
        active_templates = self._parse_markdown(active_templates_path)
        
        # If not en, compare keys/sections against en baseline standard
        if self.language_code.lower() != "en":
            en_messages_dir = os.path.join(i18n_root, "messages", "en")
            en_ai_dir = os.path.join(i18n_root, "AI", "en")
            en_templates_path = os.path.join(i18n_root, "AI", "en", "templates.md")
            
            en_messages = self._load_json_directory(en_messages_dir)
            en_ai_json = self._load_json_directory(en_ai_dir)
            en_templates = self._parse_markdown(en_templates_path)
            
            def compare_keys(active_keys: set, baseline_keys: set, filename: str):
                if active_keys != baseline_keys:
                    missing = baseline_keys - active_keys
                    extra = active_keys - baseline_keys
                    errors = []
                    if missing:
                        errors.append(f"missing: {', '.join(sorted(missing))}")
                    if extra:
                        errors.append(f"extra: {', '.join(sorted(extra))}")
                    raise ValueError(
                        startup_message(
                            "localization.content_error",
                            filename=filename,
                            errors="; ".join(errors),
                        )
                    )
                    
            compare_keys(set(active_messages.keys()), set(en_messages.keys()), active_messages_dir)
            compare_keys(set(active_ai_json.keys()), set(en_ai_json.keys()), active_ai_dir)
            compare_keys(set(active_templates.keys()), set(en_templates.keys()), active_templates_path)

        active_ai = dict(active_ai_json)
        ai_overlap = set(active_ai).intersection(active_templates)
        if ai_overlap:
            raise ValueError(
                startup_message(
                    "localization.ai_collision",
                    keys=", ".join(sorted(ai_overlap)),
                )
            )
        active_ai.update(active_templates)
        overlap = set(active_messages).intersection(active_ai)
        if overlap:
            raise ValueError(
                startup_message(
                    "localization.audience_collision",
                    keys=", ".join(sorted(overlap)),
                )
            )

        self.messages.update(active_messages)
        self.ai_resources.update(active_ai)

    def _load_json_directory(self, path: str) -> Dict[str, Any]:
        if not os.path.isdir(path):
            raise FileNotFoundError(self._startup_error("localization.directory_missing", path=path))
        merged: Dict[str, Any] = {}
        for filename in sorted(os.listdir(path)):
            if not filename.endswith(".json"):
                continue
            values = self._parse_json(os.path.join(path, filename))
            overlap = set(merged).intersection(values)
            if overlap:
                raise ValueError(
                    self._startup_error(
                        "localization.duplicate_keys",
                        path=path,
                        keys=", ".join(sorted(overlap)),
                    )
                )
            merged.update(values)
        return merged

    def _parse_json(self, path: str) -> Dict[str, Any]:
        if not os.path.exists(path):
            raise FileNotFoundError(self._startup_error("localization.file_missing", path=path))
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(
                self._startup_error("localization.json_error", path=path, error=e)
            )
        except Exception as e:
            raise RuntimeError(
                self._startup_error("localization.file_read_error", path=path, error=e)
            )

    def _parse_markdown(self, path: str) -> Dict[str, str]:
        if not os.path.exists(path):
            raise FileNotFoundError(
                self._startup_error("localization.template_missing", path=path)
            )
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            raise RuntimeError(
                self._startup_error(
                    "localization.template_read_error",
                    path=path,
                    error=e,
                )
            )
            
        parsed = {}
        sections = re.split(r"^##\s+", content, flags=re.MULTILINE)
        for section in sections:
            lines = section.strip().split("\n")
            if not lines:
                continue
            header = lines[0].strip()
            body = "\n".join(lines[1:]).strip()
            if header:
                parsed[header] = body
        return parsed

    def _startup_error(self, key: str, **kwargs) -> str:
        project_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..")
        )
        return get_bootstrap_message(
            project_root,
            getattr(config, "LANGUAGE", "en"),
            key,
            **kwargs,
        )

    @staticmethod
    def _format(text: str, key: str, kwargs: Dict[str, Any]) -> str:
        if kwargs:
            try:
                return text.format(**kwargs)
            except KeyError as e:
                return f"RESOURCE_FORMAT_ERROR_{key}_{e}"
        return text

    def get_message(self, key: str, **kwargs) -> str:
        text = self.messages.get(key, f"MISSING_MESSAGE_{key}")
        return self._format(text, key, kwargs)

    def get_ai(self, key: str, **kwargs) -> str:
        text = self.ai_resources.get(key, f"MISSING_AI_RESOURCE_{key}")
        return self._format(text, key, kwargs)

    @staticmethod
    def _get_num(resources: Dict[str, Any], key: str) -> float:
        val = resources.get(key, 0.0)
        try:
            return float(val)
        except (ValueError, TypeError):
            return 0.0

    def get_message_num(self, key: str) -> float:
        return self._get_num(self.messages, key)

    def get_ai_num(self, key: str) -> float:
        return self._get_num(self.ai_resources, key)

    def get_ai_all(self, keys: List[str]) -> Dict[str, str]:
        return {key: self.get_ai(key) for key in keys}

def get_message(key: str, **kwargs) -> str:
    return LanguageResources().get_message(key, **kwargs)

def get_ai_resource(key: str, **kwargs) -> str:
    return LanguageResources().get_ai(key, **kwargs)

def get_message_num(key: str) -> float:
    return LanguageResources().get_message_num(key)

def get_ai_num(key: str) -> float:
    return LanguageResources().get_ai_num(key)

def is_cjk() -> bool:
    return LanguageResources().is_cjk
