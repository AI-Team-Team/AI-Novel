import os
import yaml

ROLE_NAMES = ("ARCHITECT", "PLANNER", "WRITER", "CRITIC", "SCANNER")

def _load_config():
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    yaml_path = os.path.join(project_root, "config.yaml")
    if os.path.exists(yaml_path):
        with open(yaml_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}

_cfg = _load_config()

# Helper to safely get nested yaml keys
def _get(section: str, key: str, default):
    return _cfg.get(section, {}).get(key, default)

# =============================
# Generation Model Configuration
# =============================
PRIMARY_MODEL_TYPE = _get("models", "primary_type", "openai")

ARCHITECT_MODEL_TYPE = _get("models", "architect_type", PRIMARY_MODEL_TYPE)
PLANNER_MODEL_TYPE = _get("models", "planner_type", PRIMARY_MODEL_TYPE)
WRITER_MODEL_TYPE = _get("models", "writer_type", PRIMARY_MODEL_TYPE)
CRITIC_MODEL_TYPE = _get("models", "critic_type", PRIMARY_MODEL_TYPE)
SCANNER_MODEL_TYPE = _get("models", "scanner_type", PRIMARY_MODEL_TYPE)

# Gemini settings (generation)
# Privacy fields fallback to environment variables
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or _get("gemini", "api_key", "YOUR_API_KEY_HERE")
GEMINI_MODEL_NAME = _get("gemini", "default_model", "gemini-3-flash")

ARCHITECT_GEMINI_MODEL_NAME = _get("gemini", "architect_model", GEMINI_MODEL_NAME)
PLANNER_GEMINI_MODEL_NAME = _get("gemini", "planner_model", GEMINI_MODEL_NAME)
WRITER_GEMINI_MODEL_NAME = _get("gemini", "writer_model", GEMINI_MODEL_NAME)
CRITIC_GEMINI_MODEL_NAME = _get("gemini", "critic_model", GEMINI_MODEL_NAME)
SCANNER_GEMINI_MODEL_NAME = _get("gemini", "scanner_model", GEMINI_MODEL_NAME)

# OpenAI-compatible settings
# Privacy fields fallback to environment variables
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") or _get("openai", "api_key", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL") or _get("openai", "base_url", "")
OPENAI_MODEL_NAME = _get("openai", "default_model", "local-model")

ARCHITECT_OPENAI_MODEL_NAME = _get("openai", "architect_model", OPENAI_MODEL_NAME)
PLANNER_OPENAI_MODEL_NAME = _get("openai", "planner_model", OPENAI_MODEL_NAME)
WRITER_OPENAI_MODEL_NAME = _get("openai", "writer_model", OPENAI_MODEL_NAME)
CRITIC_OPENAI_MODEL_NAME = _get("openai", "critic_model", OPENAI_MODEL_NAME)
SCANNER_OPENAI_MODEL_NAME = _get("openai", "scanner_model", OPENAI_MODEL_NAME)

# =============================
# Embedding Configuration
# =============================
EMBEDDING_PROVIDER = _get("embedding", "provider", "openai")
EMBEDDING_BASE_URL = os.getenv("EMBEDDING_BASE_URL") or _get("embedding", "base_url", "")
EMBEDDING_API_KEY = os.getenv("EMBEDDING_API_KEY") or _get("embedding", "api_key", "")
EMBEDDING_MODEL_NAME = _get("embedding", "model_name", "local-embedding-model")
EMBEDDING_DIM = int(_get("embedding", "dim", 768))
GEMINI_EMBEDDING_MODEL = _get("embedding", "gemini_model", "text-embedding-004")

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
BLOCKING_CONFLICT_MODE = str(_get("workflow", "blocking_conflict_mode", "auto_keep_existing")).lower()
