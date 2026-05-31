import os
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

# 1. Load config.yaml
_cfg = _load_yaml(config_yaml_path)

def _get(section: str, key: str, default):
    return _cfg.get(section, {}).get(key, default)

# 2. Load config/ai_model_config.yaml. If missing, raise error directly
if not os.path.exists(model_config_path):
    raise FileNotFoundError(
        f"Configuration Error: The model registry file was not found at '{model_config_path}'. "
        f"Please create this file to register your AI models before running the application."
    )

_model_registry = _load_yaml(model_config_path)

# Helper to expand environment variables and fetch fallback environment values
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

# 3. Validate Role Assignment in config.yaml
models_section = _cfg.get("models", {})
if not isinstance(models_section, dict):
    raise ValueError("The 'models' section in config.yaml must be a dictionary.")

REQUIRED_ROLES = [
    "default_model",
    "architect_model",
    "planner_model",
    "writer_model",
    "critic_model",
    "scanner_model",
    "embedding_model",
]

for role in REQUIRED_ROLES:
    val = models_section.get(role)
    if not val or not str(val).strip():
        raise ValueError(
            f"Configuration Error: Assigned role '{role}' in config.yaml has an empty or missing value. "
            f"Please specify a valid registered model key from config/ai_model_config.yaml."
        )

# 4. Resolve registered models from ai_model_config.yaml
resolved_models = {}
for key, model_info in _model_registry.items():
    if not isinstance(model_info, dict):
        continue
    api_type = str(model_info.get("api_type", "")).strip().lower()
    model_type = str(model_info.get("model_type", "llm")).strip().lower()
    raw_api_key = model_info.get("api_key", "")
    raw_base_url = model_info.get("base_url", "")
    model_name = str(model_info.get("model_name", "")).strip()
    if not model_name:
        model_name = key

    resolved_api_key = _resolve_config_field(raw_api_key, "api_key", api_type)
    resolved_base_url = _resolve_config_field(raw_base_url, "base_url", api_type)

    resolved_models[key] = {
        "api_type": api_type,
        "model_type": model_type,
        "api_key": resolved_api_key,
        "base_url": resolved_base_url,
        "model_name": model_name,
    }

# 5. Resolve configured roles
def _resolve_role_config(role_name: str) -> dict:
    model_key = models_section.get(role_name)
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
LANGUAGE = _get("project", "language", "Chinese")

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
BLOCKING_CONFLICT_MODE = str(_get("workflow", "blocking_conflict_mode", "manual_block")).lower()
