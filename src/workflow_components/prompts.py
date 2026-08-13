from typing import Dict, List
from workflow_components.resources import LanguageResources, get_message

def load_system_prompts(language: str, src_dir: str) -> Dict[str, str]:
    # We now use the centralized LanguageResources which already loaded everything from i18n
    res = LanguageResources()
    required = ["architect", "critic", "planner", "writer", "scanner"]
    prompts = res.get_ai_all(required)
    
    missing = [k for k in required if get_resource_is_missing(prompts[k], k)]
    if missing:
        raise RuntimeError(get_message("validation.prompt_sections", missing=", ".join(missing)))
    return prompts

def get_resource_is_missing(value: str, key: str) -> bool:
    return value == f"MISSING_AI_RESOURCE_{key}"
