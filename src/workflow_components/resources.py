import json
import os
import re
from typing import Dict, Any, List

import config

class LanguageResources:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(LanguageResources, cls).__new__(cls)
            cls._instance._init_resources()
        return cls._instance

    def _init_resources(self):
        self.resources: Dict[str, Any] = {}
        
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        i18n_root = os.path.join(project_root, "i18n")
        
        ai_dir = os.path.join(i18n_root, "AI")
        messages_dir = os.path.join(i18n_root, "messages")
        
        if not os.path.exists(ai_dir) or not os.path.exists(messages_dir):
            raise FileNotFoundError(f"Localization root directories missing. AI: {ai_dir}, messages: {messages_dir}")
            
        ai_langs = [d for d in os.listdir(ai_dir) if os.path.isdir(os.path.join(ai_dir, d))]
        messages_langs = [d for d in os.listdir(messages_dir) if os.path.isdir(os.path.join(messages_dir, d))]
        
        available_langs = set(ai_langs).intersection(set(messages_langs))
        
        lang_input = str(config.LANGUAGE).strip()
        if not lang_input:
            raise ValueError("The 'project.language' field in config.yaml cannot be empty.")
            
        # Match directories case-insensitively
        matched_lang = None
        for lang in available_langs:
            if lang.lower() == lang_input.lower():
                matched_lang = lang
                break
                        
        if not matched_lang:
            raise ValueError(
                f"Language Configuration Error: Could not match language '{lang_input}' in config.yaml "
                f"to any directory in i18n/AI/ or i18n/messages/. "
                f"Available languages: {sorted(list(available_langs))}."
            )
            
        self.language_code = matched_lang
        self.is_cjk = (matched_lang.lower() != "en")
        
        # Load & validate formats of active language
        active_ui_path = os.path.join(i18n_root, "messages", self.language_code, "ui.json")
        active_fragments_path = os.path.join(i18n_root, "AI", self.language_code, "fragments.json")
        active_templates_path = os.path.join(i18n_root, "AI", self.language_code, "templates.md")
        
        active_ui = self._parse_json(active_ui_path)
        active_fragments = self._parse_json(active_fragments_path)
        active_templates = self._parse_markdown(active_templates_path)
        
        # If not en, compare keys/sections against en baseline standard
        if self.language_code.lower() != "en":
            en_ui_path = os.path.join(i18n_root, "messages", "en", "ui.json")
            en_fragments_path = os.path.join(i18n_root, "AI", "en", "fragments.json")
            en_templates_path = os.path.join(i18n_root, "AI", "en", "templates.md")
            
            en_ui = self._parse_json(en_ui_path)
            en_fragments = self._parse_json(en_fragments_path)
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
                    raise ValueError(f"Localization Content Error in {filename}: {'; '.join(errors)}")
                    
            compare_keys(set(active_ui.keys()), set(en_ui.keys()), active_ui_path)
            compare_keys(set(active_fragments.keys()), set(en_fragments.keys()), active_fragments_path)
            compare_keys(set(active_templates.keys()), set(en_templates.keys()), active_templates_path)
            
        self.resources.update(active_ui)
        self.resources.update(active_fragments)
        self.resources.update(active_templates)

    def _parse_json(self, path: str) -> Dict[str, Any]:
        if not os.path.exists(path):
            raise FileNotFoundError(f"Required localization file missing: {path}")
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"Localization JSON Format Error in {path}: {e}")
        except Exception as e:
            raise RuntimeError(f"Failed to read localization file {path}: {e}")

    def _parse_markdown(self, path: str) -> Dict[str, str]:
        if not os.path.exists(path):
            raise FileNotFoundError(f"Required localization template file missing: {path}")
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            raise RuntimeError(f"Failed to read localization template file {path}: {e}")
            
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

    def get(self, key: str, **kwargs) -> str:
        text = self.resources.get(key, f"MISSING_RESOURCE_{key}")
        if kwargs:
            try:
                return text.format(**kwargs)
            except KeyError as e:
                return f"RESOURCE_FORMAT_ERROR_{key}_{e}"
        return text

    def get_num(self, key: str) -> float:
        val = self.resources.get(key, 0.0)
        try:
            return float(val)
        except (ValueError, TypeError):
            return 0.0

    def get_all(self, keys: List[str]) -> Dict[str, str]:
        return {k: self.get(k) for k in keys}

def get_resource(key: str, **kwargs) -> str:
    return LanguageResources().get(key, **kwargs)

def get_res_num(key: str) -> float:
    return LanguageResources().get_num(key)

def is_cjk() -> bool:
    return LanguageResources().is_cjk
