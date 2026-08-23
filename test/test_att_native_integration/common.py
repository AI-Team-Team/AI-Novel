import json
import logging
import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace

CURRENT_DIR = os.path.dirname(__file__)
TEST_DIR = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
ROOT_DIR = os.path.abspath(os.path.join(TEST_DIR, ".."))
SRC_DIR = os.path.join(ROOT_DIR, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)
if TEST_DIR not in sys.path:
    sys.path.insert(0, TEST_DIR)

from ai_team_team import (
    ATTConfig,
    ATTManager,
    Agent,
    DiscussionStatus,
    LLMResponse,
    Tool,
    ToolCall,
)
from ai_team_team.core import ManagerDefaultClientAdapter
from att.runtime import ATTDiscussionPolicyError, select_designated_answer
from att_result_helpers import make_discussion_result, make_team
from llm_client import LLMClient

__all__ = [
    "json", "logging", "os", "shutil", "sys", "tempfile", "unittest",
    "SimpleNamespace", "ATTConfig", "ATTManager", "Agent",
    "DiscussionStatus", "LLMResponse", "Tool", "ToolCall",
    "ManagerDefaultClientAdapter", "ATTDiscussionPolicyError",
    "select_designated_answer", "make_discussion_result", "make_team",
    "LLMClient", "_inspect_story", "_OpenAICompletions",
]

def _inspect_story(place: str, filters: dict) -> str:
    """Inspect story facts for one place and nested filters."""

    return json.dumps({"place": place, "filters": filters}, ensure_ascii=False)

class _OpenAICompletions:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.response, list):
            return self.response.pop(0)
        return self.response
