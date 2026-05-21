import json
import re
from typing import Dict, List, Optional

def contains_cjk(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))

def language_confidence(text: str, exclude_names: Optional[List[str]] = None) -> Dict[str, float]:
    sample = text or ""
    if exclude_names:
        for name in exclude_names:
            if name:
                sample = sample.replace(name, "")
    cjk_count = len(re.findall(r"[\u4e00-\u9fff]", sample))
    latin_count = len(re.findall(r"[A-Za-z]", sample))
    alpha_total = cjk_count + latin_count
    if alpha_total == 0:
        return {
            "chinese": 0.0,
            "english": 0.0,
            "cjk_ratio": 0.0,
            "latin_ratio": 0.0,
        }
    cjk_ratio = cjk_count / alpha_total
    latin_ratio = latin_count / alpha_total
    return {
        "chinese": cjk_ratio,
        "english": latin_ratio,
        "cjk_ratio": cjk_ratio,
        "latin_ratio": latin_ratio,
    }


def needs_revision(review_text: str) -> bool:
    if not review_text:
        return False
    zh = re.search(r"是否需要修订\s*:\s*(是|否)", review_text)
    if zh:
        return zh.group(1) == "是"
    m = re.search(r"needs_revision\s*:\s*(yes|no)", review_text, flags=re.IGNORECASE)
    if not m:
        return False
    return m.group(1).lower() == "yes"

def extract_json_payload(text: str, logger=None) -> Optional[Dict]:
    raw = text.strip()
    candidates: List[str] = [raw]
    if "```json" in raw:
        candidates.insert(0, raw.split("```json", 1)[1].split("```", 1)[0].strip())
    elif "```" in raw:
        candidates.insert(0, raw.split("```", 1)[1].split("```", 1)[0].strip())

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    # Fallback: find the first balanced JSON object in noisy output.
    start = raw.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escaped = False
        for i in range(start, len(raw)):
            ch = raw[i]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == "\"":
                    in_string = False
                continue
            if ch == "\"":
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    snippet = raw[start : i + 1]
                    try:
                        return json.loads(snippet)
                    except json.JSONDecodeError:
                        break
        start = raw.find("{", start + 1)

    if logger is not None:
        logger.error("Failed to decode JSON from Scanner.")
        logger.debug(f"Raw text: {raw}")
    return None

def validate_fact_payload(data: Dict) -> List[str]:
    errors: List[str] = []
    if not isinstance(data, dict):
        return ["Payload must be a JSON object."]

    list_fields = [
        "new_characters",
        "updated_characters",
        "new_rules",
        "relationships",
        "events",
        "details",
    ]
    for field in list_fields:
        value = data.get(field, [])
        if not isinstance(value, list):
            errors.append(f"Field '{field}' must be a list.")

    for idx, char in enumerate(data.get("new_characters", [])):
        if not isinstance(char, dict):
            errors.append(f"new_characters[{idx}] must be an object.")
            continue
        if not char.get("name"):
            errors.append(f"new_characters[{idx}] missing required 'name'.")

    for idx, char in enumerate(data.get("updated_characters", [])):
        if not isinstance(char, dict):
            errors.append(f"updated_characters[{idx}] must be an object.")
            continue
        if not char.get("name"):
            errors.append(f"updated_characters[{idx}] missing required 'name'.")

    for idx, rel in enumerate(data.get("relationships", [])):
        if not isinstance(rel, dict):
            errors.append(f"relationships[{idx}] must be an object.")
            continue
        if not rel.get("source") or not rel.get("target"):
            errors.append(f"relationships[{idx}] missing 'source' or 'target'.")

    for idx, ev in enumerate(data.get("events", [])):
        if not isinstance(ev, dict):
            errors.append(f"events[{idx}] must be an object.")
            continue
        if not ev.get("event_name"):
            errors.append(f"events[{idx}] missing required 'event_name'.")

    for idx, det in enumerate(data.get("details", [])):
        if not isinstance(det, dict):
            errors.append(f"details[{idx}] must be an object.")
            continue
        if not det.get("content"):
            errors.append(f"details[{idx}] missing required 'content'.")
    return errors
