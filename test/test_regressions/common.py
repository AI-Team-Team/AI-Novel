import json
import logging
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

CURRENT_DIR = os.path.dirname(__file__)
TEST_DIR = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
ROOT_DIR = os.path.abspath(os.path.join(TEST_DIR, ".."))
SRC_DIR = os.path.join(ROOT_DIR, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)
if TEST_DIR not in sys.path:
    sys.path.insert(0, TEST_DIR)
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import config
from llm_client import LLMClient, LLMClientError
from memory import MemoryManager
from state_manager import StoryStateManager
from workflow import WorkflowManager
from workflow_components.parsing import language_confidence
from workflow_components.resources import get_message

__all__ = [
    "json", "logging", "os", "shutil", "sys", "tempfile", "unittest",
    "mock", "ROOT_DIR", "config", "LLMClient", "LLMClientError",
    "MemoryManager", "StoryStateManager", "WorkflowManager",
    "language_confidence", "get_message", "_FakeFaissIndex",
    "_FakeFaissModule", "_FailingReadFaissModule",
]

class _FakeFaissIndex:
    def __init__(self, dimension):
        self.d = dimension
        self.ntotal = 0

    def add(self, values):
        self.ntotal += len(values)
class _FakeFaissModule:
    IndexFlatL2 = _FakeFaissIndex

    @staticmethod
    def clone_index(index):
        clone = _FakeFaissIndex(index.d)
        clone.ntotal = index.ntotal
        return clone

    @staticmethod
    def write_index(index, path):
        with open(path, "wb") as handle:
            handle.write(f"{index.d}:{index.ntotal}".encode("ascii"))

    @staticmethod
    def read_index(path):
        with open(path, "rb") as handle:
            dimension, total = handle.read().decode("ascii").split(":")
        index = _FakeFaissIndex(int(dimension))
        index.ntotal = int(total)
        return index
class _FailingReadFaissModule(_FakeFaissModule):
    @staticmethod
    def read_index(path):
        raise ValueError("simulated corrupt index")
