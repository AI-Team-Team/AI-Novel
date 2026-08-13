"""Read human messages before the validated i18n singleton is available."""

from __future__ import annotations

import json
import os
from typing import Any, Dict

import yaml


class ConfigurationError(RuntimeError):
    """A user-correctable application configuration error."""


def load_project_language(project_root: str, default: str = "en") -> str:
    """Read only the UI language without importing or validating models."""

    path = os.path.join(project_root, "config.yaml")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        project = data.get("project", {}) if isinstance(data, dict) else {}
        language = project.get("language", default) if isinstance(project, dict) else default
        return str(language or default).strip() or default
    except (OSError, yaml.YAMLError, TypeError, ValueError):
        return default


def get_bootstrap_message(
    project_root: str,
    locale: str,
    key: str,
    **kwargs: Any,
) -> str:
    """Return one human message without importing ``config``.

    Configuration and localization validation run before ``LanguageResources``
    can safely exist. This deliberately small reader provides localized startup
    errors while leaving full namespace/parity validation to the main loader.
    """

    normalized = str(locale or "en").strip()
    candidates = [normalized]
    if normalized.lower() != "en":
        candidates.append("en")
    messages_root = os.path.join(project_root, "i18n", "messages")
    available = {}
    if os.path.isdir(messages_root):
        available = {
            name.lower(): name
            for name in os.listdir(messages_root)
            if os.path.isdir(os.path.join(messages_root, name))
        }
    for candidate in candidates:
        resolved = available.get(candidate.lower(), candidate)
        directory = os.path.join(messages_root, resolved)
        if not os.path.isdir(directory):
            continue
        for filename in sorted(os.listdir(directory)):
            if not filename.endswith(".json"):
                continue
            try:
                path = os.path.join(directory, filename)
                with open(path, "r", encoding="utf-8") as handle:
                    values: Dict[str, Any] = json.loads(handle.read())
            except (OSError, json.JSONDecodeError, TypeError):
                continue
            text = values.get(key)
            if isinstance(text, str):
                try:
                    return text.format(**kwargs)
                except KeyError as exc:
                    return f"RESOURCE_FORMAT_ERROR_{key}_{exc}"
    return f"MISSING_MESSAGE_{key}"
