from typing import List, Optional

import config
from llm_client import LLMClient
from workflow_components.parsing import contains_cjk, language_confidence


from workflow_components.resources import get_ai_resource, get_message


class WorkflowLanguageMixin:
    def _language_name(self) -> str:
        return config.LANGUAGE

    def _language_rule(self) -> str:
        return get_ai_resource("prompt.language_rule")

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
        is_en = (config.LANGUAGE.lower() == "en")
        target_key = "latin_ratio" if is_en else "cjk_ratio"
        other_key = "cjk_ratio" if is_en else "latin_ratio"
        
        return confidence[target_key] >= config.MIN_CONFIDENCE and confidence[other_key] <= config.MAX_OTHER_CONFIDENCE

    def _enforce_output_language(
        self,
        client: LLMClient,
        role: str,
        text: str,
        system_instruction: str,
        chapter_num: Optional[int] = None,
        world_building: bool = False,
    ) -> str:
        current_text = text
        max_attempts = max(1, getattr(config, "LANGUAGE_REWRITE_MAX_ATTEMPTS", 2))
        for attempt in range(max_attempts):
            if self._is_expected_language(current_text):
                return current_text
            known_names = self._get_known_character_names()
            confidence = language_confidence(current_text, exclude_names=known_names)
            self.logger.warning(get_message(
                "runtime.language_guard_triggered",
                role=role,
                chinese=confidence["chinese"],
                english=confidence["english"],
                attempt=attempt + 1,
                max_attempts=max_attempts,
            ))
            if attempt == 0:
                rewrite_prompt = get_ai_resource(
                    "prompt.language_rewrite",
                    language=self._language_name(),
                    content=current_text,
                )
            else:
                rewrite_prompt = get_ai_resource(
                    "prompt.language_rewrite_strict",
                    language=self._language_name(),
                    content=current_text,
                )
            try:
                current_text = client.generate(prompt=rewrite_prompt, system_instruction=system_instruction)
            except Exception as e:
                self.logger.error(get_message("runtime.language_rewrite_failed", error=e))
                if attempt == max_attempts - 1:
                    raise
            self._log_llm_interaction(
                role=role,
                phase=get_message("runtime.language_rewrite_phase", attempt=attempt + 1),
                prompt=rewrite_prompt,
                response=current_text,
                system_instruction=system_instruction,
                chapter_num=chapter_num,
                world_building=world_building,
            )
        if not self._is_expected_language(current_text):
            raise RuntimeError(get_message(
                "runtime.language_guard_error",
                language=self._language_name(),
                attempts=max_attempts,
                role=role,
            ))
        return current_text
