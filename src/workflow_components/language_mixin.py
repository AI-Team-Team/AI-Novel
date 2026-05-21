from typing import List, Optional

import config
from llm_client import LLMClient
from workflow_components.parsing import contains_cjk, language_confidence


from workflow_components.resources import get_resource


class WorkflowLanguageMixin:
    def _language_name(self) -> str:
        return config.LANGUAGE

    def _language_rule(self) -> str:
        return get_resource("prompt.language_rule")

    @staticmethod
    def _contains_cjk(text: str) -> bool:
        return contains_cjk(text)

    def _get_known_character_names(self) -> List[str]:
        """Retrieve character names from DB to exclude from language confidence calculation."""
        memory = getattr(self, "memory", None)
        if memory is None:
            return []
        try:
            chars = memory.get_all_characters()
            return [name for name, _, _ in chars] if chars else []
        except Exception:
            return []

    def _is_expected_language(self, text: str) -> bool:
        known_names = self._get_known_character_names()
        confidence = language_confidence(text, exclude_names=known_names)
        if config.LANGUAGE == "Chinese":
            # Chinese mode: accept if CJK ratio is non-trivial or any CJK present
            if confidence["chinese"] >= 0.20:
                return True
            return self._contains_cjk(text)

        # English mode: after excluding known character names, check ratios.
        # Use a relaxed CJK threshold since proper nouns have been stripped.
        if confidence["english"] >= 0.60 and confidence["chinese"] <= 0.10:
            return True
        # Safety net: only fail if there is substantial CJK content remaining
        # after name exclusion (> 30% suggests real language mixing, not just names).
        if confidence["chinese"] > 0.30:
            return False
        return True

    def _enforce_output_language(
        self,
        client: LLMClient,
        role: str,
        text: str,
        system_instruction: str,
        chapter_num: Optional[int] = None,
        world_building: bool = False,
    ) -> str:
        if self._is_expected_language(text):
            return text
        known_names = self._get_known_character_names()
        confidence = language_confidence(text, exclude_names=known_names)
        self.logger.warning(
            "Language guard triggered for %s (zh=%.3f, en=%.3f, after name exclusion)",
            role,
            confidence["chinese"],
            confidence["english"],
        )
        rewrite_prompt = (
            f"Rewrite the following content in {self._language_name()} only.\n"
            "Keep all details and structure. Output only the rewritten content.\n\n"
            "--- CONTENT BEGIN ---\n"
            f"{text}\n"
            "--- CONTENT END ---"
        )
        rewritten = client.generate(prompt=rewrite_prompt, system_instruction=system_instruction)
        self._log_llm_interaction(
            role=role,
            phase="Language Rewrite",
            prompt=rewrite_prompt,
            response=rewritten,
            system_instruction=system_instruction,
            chapter_num=chapter_num,
            world_building=world_building,
        )
        return rewritten
