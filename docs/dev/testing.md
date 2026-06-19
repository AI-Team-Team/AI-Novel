# AI-Novel testing Guide

This guide provides a comprehensive overview of the testing system in the AI-Novel project, detail on the available test suites, and instructions on how to execute, write, and mock tests properly under the new **Agent Tournament Topology (ATT)** architecture.

## 1. Testing Philosophy & Constraints

* **Standard Python `unittest` Only**: The project strictly standardized on the standard library's `unittest` framework. **Do not use `pytest`** or introduce pytest-specific fixtures or configurations.
* **Virtual Environment Context**: All tests must be executed using the python interpreter from the local virtual environment (`./venv/bin/python`).
* **No Leftover Test Artifacts**: Tests that mutate storage, databases, or file systems must operate inside isolated temporary directories (e.g., using `tempfile.mkdtemp`), and clean up all resources in `tearDown()`.
* **Config Isolation**: To prevent local/user configurations and API keys from interfering with or being charged during test execution, the configuration loader detects when `unittest` is in `sys.modules`. It automatically falls back to a static, mock-friendly dummy configuration rather than loading user-defined `config.yaml` or `config/ai_model_config.yaml` files. Developers can force the real config using the `AI_NOVEL_FORCE_REAL_CONFIG=1` environment variable if integration tests require actual endpoints.

## 2. Running Tests

### Running a Single Test Suite

To run any specific test suite file, execute it directly with python:

```bash
./venv/bin/python test/test_db.py
```

### Running All Tests (Test Discovery)

To discover and run all tests under the `test/` directory, use standard python test discovery:

```bash
./venv/bin/python -m unittest discover -s test
```

## 3. Test Suites Directory & Architecture

The `test/` directory contains standard test suites covering every layer of the system:

| Test File | Target Coverage | Key Validations |
| :--- | :--- | :--- |
| `test_db.py` | Database Layer | SQLite database initialization, upserts, schema logic, and basic FAISS semantic index setup. |
| `test_i18n.py` | Localization Layer | Language configuration, key resolution, and translation fallbacks. |
| `test_project_logic.py` | Core Workspace Layer | Validation of chapter files, overview directories, paths, and templates. |
| `test_model_config.py` | Model Configurations | Parsing of `config.yaml`, mapping of provider credentials, and context parameters. |
| `test_init_seed.py` | System Seeding | Setup and generation of early seed plot outlines, world bibles, and compact archives. |
| `test_full_system.py` | System Simulation | End-to-end workspace flow and integration validations. |
| `test_embedding_validation.py`| Vector Embeddings | Vector boundaries, dimension constraints, FAISS indexing, and semantic search. |
| `test_ai_debate_conflict_resolver.py` | Dynamic Debate Gating | Resolving narrative character contradictions and rule collisions in bounded debates. |
| `test_regressions.py` | Comprehensive Integration | Intent gates, rollback, database commits, language security, and structural rollbacks. |

## 4. Best Practices for Writing Tests

### Prepending `src/` to `sys.path`

Every test file must correctly configure the path structure at the very top of the file before importing source code to prevent `ModuleNotFoundError` when run in standalone modes:

```python
import os
import sys

CURRENT_DIR = os.path.dirname(__file__)
ROOT_DIR = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
SRC_DIR = os.path.join(ROOT_DIR, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)
```

### Handling Lightweight Stub Instances

In several tests, `WorkflowManager` is instantiated using `WorkflowManager.__new__(WorkflowManager)` to bypass the heavyweight normal initialization.
Always ensure that in system code, dependencies such as `self.memory` are guarded before accessing them (e.g., using `getattr(self, "memory", None)`).

## 5. Mocking in the ATT Architecture

Under the ATT topology, multiple agents (such as `Historian_Critic`, `Prose_Scanner`, `Consensus_Planner`) and the auditing `SupervisoryTeam` share the parent workflow's **`critic_client`** for generation. The older structure of mocking individual LLM clients (`planner_client`, `scanner_client`) separately does not apply.

When mocking the shared `critic_client.generate`, observe the following guidelines:

### A. Bound Debate Loop Rounds

By default, the debate loops run multiple rounds (e.g., `CONFLICT_DISCUSSION_ROUNDS = 2`). For predictable unit testing, always bound this value to `1` round:

```python
import config
old_rounds = getattr(config, "CONFLICT_DISCUSSION_ROUNDS", 2)
config.CONFLICT_DISCUSSION_ROUNDS = 1
try:
    # Trigger resolution
    resolved = self.workflow.ai_debate_resolve_conflict(conflict_id)
finally:
    config.CONFLICT_DISCUSSION_ROUNDS = old_rounds
```

### B. Use Sequential Mock Responses

Mock the `critic_client.generate` using a `side_effect` function that yields the turns sequentially:

* **Turn 1 (Agent 1)**: `"Final Answer: Argument 1"`
* **Turn 2 (Agent 2)**: `"Final Answer: Argument 2"`
* **Turn 3 (Agent 3)**: `"Final Answer: Decision Payload"`
* **Turn 4 (Supervisor Audit)**: `'{"is_healthy": true, "reason": "ok"}'`

### C. Prefix Outputs with `"Final Answer: "`

The `execute_react_step` loop includes a ReAct fallback that loops up to 5 steps if it does not receive a final reply. To terminate immediately and run deterministic 1-step calls, **always prefix agent mock outputs with `"Final Answer: "`**:

```python
outputs = [
    "Final Answer: Keep Iris dead for tragic impact!",
    "Final Answer: Iris must live because she has an ongoing harbor arc.",
    "Final Answer: " + json.dumps({
        "action": "apply_incoming",
        "reasoning": "...",
        "narrative_compromise": "..."
    })
]
```

### D. Return Valid JSON for Supervisory Audits

The Supervisory Team performs an automated audit after every debate, calling `critic_client.generate` with `require_json=True`. Ensure the mock handler returns a valid JSON string when `require_json` is requested:

```python
def generate_mock(prompt, system_instruction=None, require_json=False, **kwargs):
    if require_json:
        return '{"is_healthy": true, "reason": "Dialogue approved."}'
    return outputs.pop(0) if outputs else "Final Answer: ok"

self.workflow.critic_client.generate.side_effect = generate_mock
```

### E. Extract JSON robustly

Do not use `json.loads(transcript_text)` to parse the outcome directly from the debate transcript. Always use `extract_json_payload` from `workflow_components.parsing` to isolate the final decision block from speaker tags:

```python
from workflow_components.parsing import extract_json_payload
planner_decision = extract_json_payload(transcript_text)
```
