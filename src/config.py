import os
import sys
import re
import yaml

def _load_yaml(path: str) -> dict:
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
config_yaml_path = os.path.join(project_root, "config.yaml")
model_config_dir = os.path.join(project_root, "config")
model_config_path = os.path.join(model_config_dir, "ai_model_config.yaml")

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

    # 2. Load config/ai_model_config.yaml. If missing, raise error directly
    if not os.path.exists(model_config_path):
        raise FileNotFoundError(
            f"Configuration Error: The model registry file was not found at '{model_config_path}'. "
            f"Please create this file to register your AI models before running the application."
        )

    _model_registry = _load_yaml(model_config_path)

    # 3. Validate Role Assignment in config.yaml
    models_section = _cfg.get("models", {})
    if not isinstance(models_section, dict):
        raise ValueError("The 'models' section in config.yaml must be a dictionary.")

    for role in REQUIRED_ROLES:
        val = models_section.get(role)
        if not val or not str(val).strip():
            raise ValueError(
                f"Configuration Error: Assigned role '{role}' in config.yaml has an empty or missing value. "
                f"Please specify a valid registered model key from config/ai_model_config.yaml."
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
            raise ValueError(
                f"Configuration Error: Role '{role_name}' is assigned to model key '{model_key}', "
                f"which is explicitly disabled in config/ai_model_config.yaml."
            )
        if model_key not in resolved_models:
            raise ValueError(
                f"Configuration Error: Role '{role_name}' is assigned to model key '{model_key}', "
                f"which is not registered in config/ai_model_config.yaml."
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
MIN_CONFIDENCE = float(_get("project", "min_confidence", 0.60))
MAX_OTHER_CONFIDENCE = float(_get("project", "max_other_confidence", 0.10))

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
# Autonomy / Delegation Controls
# =============================
ENABLE_AUTONOMY_SUITE = bool(_get("autonomy", "enable_autonomy_suite", False))
ENABLE_AUTONOMOUS_QUERIES = bool(_get("autonomy", "enable_autonomous_queries", False))
ENABLE_DYNAMIC_DELEGATION = bool(_get("autonomy", "enable_dynamic_delegation", False))
MAX_DELEGATION_DEPTH = int(_get("autonomy", "max_delegation_depth", 2))
MIN_SUBAGENT_TEAM_SIZE = int(_get("autonomy", "min_subagent_team_size", 3))
MAX_SUBAGENT_TEAM_SIZE = int(_get("autonomy", "max_subagent_team_size", 3))
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
STRICT_STATE_PERSISTENCE = bool(_get("autonomy", "strict_state_persistence", True))
