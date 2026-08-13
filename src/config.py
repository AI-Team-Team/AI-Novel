import os
import sys
import re
import yaml

from workflow_components.bootstrap_messages import ConfigurationError, get_bootstrap_message

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
config_yaml_path = os.path.join(project_root, "config.yaml")
model_config_dir = os.path.join(project_root, "config")
model_config_path = os.path.join(model_config_dir, "ai_model_config.yaml")


def _config_message(key: str, **kwargs) -> str:
    current_config = globals().get("_cfg", {})
    project = current_config.get("project", {}) if isinstance(current_config, dict) else {}
    language = project.get("language", "en") if isinstance(project, dict) else "en"
    return get_bootstrap_message(project_root, language, key, **kwargs)


def _load_yaml(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigurationError(
            _config_message("config.yaml_invalid", path=path, error=exc)
        ) from exc

# Helper to expand environment variables
def _resolve_config_field(val: str, field_name: str, api_type: str) -> str:
    if not val:
        return ""
    expanded = os.path.expandvars(str(val))
    if expanded.startswith("$") or (expanded.startswith("${") and expanded.endswith("}")):
        var_name = expanded.replace("$", "").replace("{", "").replace("}", "")
        env_val = os.getenv(var_name)
        if env_val is not None:
            return env_val
        return ""
    return expanded

REQUIRED_ROLES = [
    "default_model",
    "architect_model",
    "planner_model",
    "writer_model",
    "critic_model",
    "scanner_model",
    "embedding_model",
]

# Check if running in a unittest environment and test configuration is requested
is_testing = ("unittest" in sys.modules or os.getenv("AI_NOVEL_USE_TEST_CONFIG") == "1") and os.getenv("AI_NOVEL_FORCE_REAL_CONFIG") != "1"

if is_testing:
    # Use hardcoded test configuration to isolate test runs from user's local config
    _cfg = {
        "models": {
            "default_model": "gemini",
            "architect_model": "gemini",
            "planner_model": "gemini",
            "writer_model": "gemini",
            "critic_model": "gemini",
            "scanner_model": "gemini",
            "embedding_model": "gemini",
        },
        "project": {
            "db_path": "novel/process/facts/facts.db",
            "faiss_index_path": "novel/process/facts/vector_index.faiss",
            "novel_title": "Test Novel",
            "output_dir": "novel/main_text",
            "frame_dir": "novel/frame",
            "process_dir": "novel/process",
            "language": "en",
            "min_confidence": 0.60,
            "max_other_confidence": 0.10,
        }
    }
    
    resolved_models = {
        "gemini": {
            "api_type": "gemini",
            "model_type": "llm",
            "api_key": "dummy",
            "base_url": "",
            "model_name": "gemini-3.5-flash",
            "ai_note": "Mock model for testing"
        }
    }
    
    models_section = _cfg["models"]
    MODEL_REGISTRY = resolved_models
    
    ARCHITECT_CONFIG = resolved_models["gemini"]
    PLANNER_CONFIG = resolved_models["gemini"]
    WRITER_CONFIG = resolved_models["gemini"]
    CRITIC_CONFIG = resolved_models["gemini"]
    SCANNER_CONFIG = resolved_models["gemini"]
    EMBEDDING_CONFIG = resolved_models["gemini"]
    
else:
    # 1. Load config.yaml
    _cfg = _load_yaml(config_yaml_path)
    if not isinstance(_cfg, dict):
        raise ConfigurationError(_config_message("config.root_not_mapping"))

    # 2. Load config/ai_model_config.yaml. If missing, raise error directly
    if not os.path.exists(model_config_path):
        raise ConfigurationError(
            _config_message("config.model_registry_missing", path=model_config_path)
        )

    _model_registry = _load_yaml(model_config_path)
    if not isinstance(_model_registry, dict):
        raise ConfigurationError(
            _config_message("config.model_registry_not_mapping")
        )

    # 3. Validate Role Assignment in config.yaml
    models_section = _cfg.get("models", {})
    if not isinstance(models_section, dict):
        raise ConfigurationError(_config_message("config.models_not_mapping"))

    for role in REQUIRED_ROLES:
        val = models_section.get(role)
        if not val or not str(val).strip():
            raise ConfigurationError(
                _config_message("config.role_missing", role=role)
            )

    # 4. Resolve registered models from ai_model_config.yaml
    resolved_models = {}
    disabled_models = set()
    for key, model_info in _model_registry.items():
        if not isinstance(model_info, dict):
            continue
        
        # Check if the model is explicitly disabled
        enabled = model_info.get("enabled", True)
        if enabled is False or str(enabled).lower() == "false":
            disabled_models.add(key)
            continue

        api_type = str(model_info.get("api_type", "")).strip().lower()
        model_type = str(model_info.get("model_type", "llm")).strip().lower()
        raw_api_key = model_info.get("api_key", "")
        raw_base_url = model_info.get("base_url", "")
        model_name = str(model_info.get("model_name", "")).strip()
        if not model_name:
            model_name = key
        ai_note = str(model_info.get("ai_note", "No description")).strip()

        resolved_api_key = _resolve_config_field(raw_api_key, "api_key", api_type)
        resolved_base_url = _resolve_config_field(raw_base_url, "base_url", api_type)

        resolved_models[key] = {
            "api_type": api_type,
            "model_type": model_type,
            "api_key": resolved_api_key,
            "base_url": resolved_base_url,
            "model_name": model_name,
            "ai_note": ai_note,
        }

    # 5. Resolve configured roles
    def _resolve_role_config(role_name: str) -> dict:
        model_key = models_section.get(role_name)
        if model_key in disabled_models:
            raise ConfigurationError(
                _config_message(
                    "config.role_model_disabled",
                    role=role_name,
                    model=model_key,
                )
            )
        if model_key not in resolved_models:
            raise ConfigurationError(
                _config_message(
                    "config.role_model_unregistered",
                    role=role_name,
                    model=model_key,
                )
            )
        return resolved_models[model_key]

    ARCHITECT_CONFIG = _resolve_role_config("architect_model")
    PLANNER_CONFIG = _resolve_role_config("planner_model")
    WRITER_CONFIG = _resolve_role_config("writer_model")
    CRITIC_CONFIG = _resolve_role_config("critic_model")
    SCANNER_CONFIG = _resolve_role_config("scanner_model")
    EMBEDDING_CONFIG = _resolve_role_config("embedding_model")

    MODEL_REGISTRY = resolved_models

def _get(section: str, key: str, default):
    return _cfg.get(section, {}).get(key, default)


def language_guard_defaults(language: str) -> tuple[float, float]:
    """Return the target/other confidence defaults for a language profile."""

    if str(language).lower().startswith("zh"):
        return 0.70, 0.30
    return 0.60, 0.10

# Expose key variables for other parts of the application or tests
# =============================
# Paths / Project
# =============================
DB_PATH = _get("project", "db_path", "novel/process/facts/facts.db")
FAISS_INDEX_PATH = _get("project", "faiss_index_path", "novel/process/facts/vector_index.faiss")
NOVEL_TITLE = _get("project", "novel_title", "Untitled Novel")
OUTPUT_DIR = _get("project", "output_dir", "novel/main_text")
FRAME_DIR = _get("project", "frame_dir", "novel/frame")
PROCESS_DIR = _get("project", "process_dir", "novel/process")
LANGUAGE = _get("project", "language", "en")
_default_min_confidence, _default_other_confidence = language_guard_defaults(LANGUAGE)
MIN_CONFIDENCE = float(
    _get("project", "min_confidence", _default_min_confidence)
)
MAX_OTHER_CONFIDENCE = float(
    _get("project", "max_other_confidence", _default_other_confidence)
)
if not 0.0 <= MIN_CONFIDENCE <= 1.0:
    raise ConfigurationError(
        _config_message(
            "config.language_threshold_range",
            key="min_confidence",
            value=MIN_CONFIDENCE,
        )
    )
if not 0.0 <= MAX_OTHER_CONFIDENCE <= 1.0:
    raise ConfigurationError(
        _config_message(
            "config.language_threshold_range",
            key="max_other_confidence",
            value=MAX_OTHER_CONFIDENCE,
        )
    )

# =============================
# Retrieval / Constraint Controls
# =============================
TIER_1_RELEVANCE_THRESHOLD = float(_get("retrieval", "tier_1_relevance_threshold", 0.9))
TIER_3_SEARCH_LIMIT = int(_get("retrieval", "tier_3_search_limit", 5))

# =============================
# Workflow Controls
# =============================
WORLD_DISCUSSION_ROUNDS = int(_get("workflow", "world_discussion_rounds", 1))
PLOT_DISCUSSION_ROUNDS = int(_get("workflow", "plot_discussion_rounds", 1))
DETAILED_PLOT_DISCUSSION_ROUNDS = int(_get("workflow", "detailed_plot_discussion_rounds", 1))
CHAPTER_GUIDE_DISCUSSION_ROUNDS = int(_get("workflow", "chapter_guide_discussion_rounds", 1))
CHAPTER_REVISION_ROUNDS = int(_get("workflow", "chapter_revision_rounds", 1))
CHAPTER_TEXT_DISCUSSION_ROUNDS = int(_get("workflow", "chapter_text_discussion_rounds", CHAPTER_REVISION_ROUNDS))
AUTO_GENERATION_MAX_RETRIES = int(_get("workflow", "auto_generation_max_retries", 3))
LANGUAGE_REWRITE_MAX_ATTEMPTS = int(_get("workflow", "language_rewrite_max_attempts", 2))
CONFLICT_DISCUSSION_ROUNDS = int(_get("workflow", "conflict_discussion_rounds", 2))
BLOCKING_CONFLICT_MODE = str(_get("workflow", "blocking_conflict_mode", "manual_block")).lower()

# =============================
# Database Audit Controls
# =============================
DATABASE_AUDIT_ENABLED = bool(_get("database_audit", "enabled", True))
DATABASE_AUDIT_FAILURE_POLICY = str(
    _get("database_audit", "failure_policy", "deny")
).strip().lower()
if DATABASE_AUDIT_FAILURE_POLICY not in {"deny", "allow"}:
    raise ConfigurationError(_config_message("config.database_audit_policy"))

_database_audit_scopes = _get("database_audit", "scopes", {})
if not isinstance(_database_audit_scopes, dict):
    raise ConfigurationError(_config_message("config.database_audit_scopes_mapping"))
DATABASE_AUDIT_SCOPES = {
    "att_sql": True,
    "chapter_fact_batches": True,
    "commit_replay": True,
    "conflict_resolution": True,
    "conflict_queue_writes": False,
    "character_writes": False,
    "relationship_writes": False,
    "world_rule_writes": False,
    "timeline_event_writes": False,
    "vector_writes": False,
    "revision_writes": False,
    "chapter_commit_metadata": False,
    "schema_metadata": False,
    "maintenance": False,
}
for _scope_name, _scope_enabled in _database_audit_scopes.items():
    if _scope_name not in DATABASE_AUDIT_SCOPES:
        raise ConfigurationError(
            _config_message("config.database_audit_scope_unknown", scope=_scope_name)
        )
    if not isinstance(_scope_enabled, bool):
        raise ConfigurationError(
            _config_message("config.database_audit_scope_boolean", scope=_scope_name)
        )
    DATABASE_AUDIT_SCOPES[_scope_name] = _scope_enabled

_database_audit_failure_policies = _get("database_audit", "failure_policies", {})
if not isinstance(_database_audit_failure_policies, dict):
    raise ConfigurationError(_config_message("config.database_audit_failures_mapping"))
DATABASE_AUDIT_FAILURE_POLICIES = {}
for _scope_name, _failure_policy in _database_audit_failure_policies.items():
    if _scope_name not in DATABASE_AUDIT_SCOPES:
        raise ConfigurationError(
            _config_message(
                "config.database_audit_failure_scope_unknown",
                scope=_scope_name,
            )
        )
    _normalized_policy = str(_failure_policy).strip().lower()
    if _normalized_policy not in {"deny", "allow"}:
        raise ConfigurationError(
            _config_message(
                "config.database_audit_failure_policy",
                scope=_scope_name,
            )
        )
    DATABASE_AUDIT_FAILURE_POLICIES[_scope_name] = _normalized_policy

# =============================
# Autonomy / Delegation Controls
# =============================
ENABLE_AUTONOMY_SUITE = bool(_get("autonomy", "enable_autonomy_suite", True))
ATT_STATE_DB_PATH = str(
    _get("autonomy", "state_db_path", os.path.join(PROCESS_DIR, "att_state_v6.db"))
)
ENABLE_AUTONOMOUS_QUERIES = bool(_get("autonomy", "enable_autonomous_queries", False))
ENABLE_DYNAMIC_DELEGATION = bool(_get("autonomy", "enable_dynamic_delegation", False))
MAX_DELEGATION_DEPTH = int(_get("autonomy", "max_delegation_depth", 2))
MIN_SUBAGENT_TEAM_SIZE = int(_get("autonomy", "min_subagent_team_size", 3))
SUBAGENT_DISCUSSION_ROUNDS = int(_get("autonomy", "subagent_discussion_rounds", 1))
REACT_MAX_STEPS = int(_get("autonomy", "react_max_steps", 5))
INBOX_SUMMARIZE_THRESHOLD_CHARS = int(_get("autonomy", "inbox_summarize_threshold_chars", 1500))
LARGE_FILE_THRESHOLD_KB = int(_get("autonomy", "large_file_threshold_kb", 50))
MAX_CHUNK_LINES = int(_get("autonomy", "max_chunk_lines", 100))
ENABLE_BUDGET_MONITORING = bool(_get("autonomy", "enable_budget_monitoring", False))
TOTAL_TOKEN_BUDGET_USD = float(_get("autonomy", "total_token_budget_usd", 1.00))
ENABLE_MEMORY_COMPRESSION = bool(_get("autonomy", "enable_memory_compression", True))
MAX_MEMORY_TURNS = int(_get("autonomy", "max_memory_turns", 20))
FAILOVER_POLICY = str(_get("autonomy", "failover_policy", "auto"))
ENABLE_EMERGENCY_WAKEUP = bool(_get("autonomy", "enable_emergency_wakeup", True))
EMERGENCY_DISCUSSION_ROUNDS = int(_get("autonomy", "emergency_discussion_rounds", 1))
TOOL_CALLING_MODE = str(_get("autonomy", "tool_calling_mode", "auto"))
MAX_TOOL_ROUNDS = int(_get("autonomy", "max_tool_rounds", 5))
