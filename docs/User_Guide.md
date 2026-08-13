# User Guide

This guide will walk you through setting up and using the AI Novel project to generate long-form, coherent stories.

## 1. Installation

### Prerequisites

* **Python 3.10+**: Ensure you have a modern Python environment.
* **LLM API Access**: You need either a **Google Gemini** API key or an **OpenAI-compatible** local/cloud endpoint.
* **Embedding Service**: The project requires an embedding model (e.g., OpenAI's `text-embedding-3-small` or a local service running `nomic-embed-text`).

### Setup

1. Clone the repository and enter the directory.
2. Install dependencies:

    ```bash
    python -m venv venv
    source ./venv/bin/activate
    pip install -r requirements.txt
    ```

## 2. Configuration

Decouple model registration and agent role assignment using two configuration files:

1. **`config.yaml`** (at the project root): Customize system behaviors, project paths, retrieval, workflow parameters, and map agent roles directly to model registration keys.
2. **`config/ai_model_config.yaml`** (inside the `config/` directory): Register LLM and embedding specifications.

### Mapping Agent Roles

In `config.yaml`, roles must be assigned to registered keys in a `models` block.

*Note: Commands that execute the workflow require every role assignment to be present and enabled. Invalid assignments produce a localized configuration message and exit code `2`, without a Python traceback. Informational commands such as `--help` use the lightweight localization bootstrap and do not load or validate the model registry.*

### Registering Models

In `config/ai_model_config.yaml`, define your model registry under named keys:

```yaml
ai:
  model_type: "llm"
  api_type: "openai"
  api_key: "${API_KEY}"
  base_url: ""
  model_name: ""
  enabled: true
```

* **Model Enable/Disable Status**: The `enabled` attribute (defaults to `true` if omitted) controls whether a model is loaded. If set to `false`, the model is excluded from the registry. Attempting to assign a disabled model to an active agent role in `config.yaml` will raise a startup validation error.
* **Environment Variables**: Use `${VAR_NAME}` or `$VAR_NAME` to pull values from system environment variables. If left blank or unset, no default fallbacks are intelligently filled, and they are passed directly as empty strings.
* **Model Name Fallback**: If `model_name` is empty or omitted, it defaults to the registration key name.
* **Workflow Parameters**: Customize paths, `WORLD_DISCUSSION_ROUNDS`, or `CHAPTER_REVISION_ROUNDS` under `config.yaml` to control how much the agents iterate.
* **Auto Mode**: Adjust `AUTO_GENERATION_MAX_RETRIES` (default: 3) to control chapter retry boundaries during `--auto` loop execution.
* **Language Guard**: `project.min_confidence` and `project.max_other_confidence` control the deterministic language check. Chinese mode defaults to `0.70` required Chinese confidence and permits up to `0.30` English confidence; English mode defaults to `0.60` and `0.10` respectively.

## 3. Getting Started

### Step 1: Initialize the Workspace

Run the initialization command to create the data structure and a template for your story:

```bash
./venv/bin/python src/main.py --init
```

This creates `novel/Novel_Overview.md`.

### Step 2: Define Your Novel

Edit `novel/Novel_Overview.md`. Describe your world, characters, and major plot points. Be as detailed as possible to give the Architect a strong foundation.

### Step 3: Build the Framework

Run the start command to invoke the Architect and Planner:

```bash
./venv/bin/python src/main.py --start
```

The system will:

1. Generate a **World Bible**.
2. Create a **High-level Plot Outline**.
3. Create a **Detailed Plot Outline**.
4. Extract initial facts to seed the memory database.

## 4. Writing Chapters

### Automatic Mode (Recommended)

Generate multiple chapters in a row:

```bash
# Generate 5 chapters starting from Chapter 1
./venv/bin/python src/main.py --auto 1 5
```

The system will automatically Plan, Write, Review, and Scan each chapter. If interrupted, simply run the command again; it will validate existing artifacts and resume where it left off.

### Manual Mode

If you want fine-grained control, you can run steps individually:

1. **Plan**: `./venv/bin/python src/main.py --plan 1` (Creates the "Writing Contract").
2. **Write & Review**: `./venv/bin/python src/main.py --write 1` (Generates prose and runs the Critic review).
3. **Scan**: `./venv/bin/python src/main.py --scan 1` (Updates the memory database with new facts from the prose).

## 5. Advanced Management

### Conflict Triage & AI Debate Panel

When the Scanner detects a semantic change in the story's state (e.g., a character's status changes or a strict rule is violated), it queues a **Conflict** in the database. The system offers multiple ways to resolve these:

1. **Multi-Agent Cooperative Debate Panel (Recommended)**:
   Enabled automatically in `--auto` mode or by passing the `--ai-resolve-conflicts` CLI flag. The system spawns a background discussion panel composed of:
   * **Critic (Historian)**: Argues for world-bible consistency and database integrity (`keep_existing`).
   * **Scanner (Prose Advocate)**: Argues in favor of new creative directions in the prose (`apply_incoming`).
   * **Planner (Arbitrator)**: Moderates the panel over a set number of rounds and makes the final executive decision.

   If they fail to reach a unanimous decision in exactly $N$ rounds (configured via `conflict_discussion_rounds`), a **Fail-Fast Standoff** is triggered, raising a `RuntimeError` to halt writing and keep your database pristine. Full transcripts are documented under `novel/process/discussions/conflict_{id}_resolution_discussion.md`.

2. **Standard Automatic Triage**:
   Controlled by `blocking_conflict_mode` in `config.yaml`:
   * `auto_keep_existing`: Automatically resolves blocking conflicts via `keep_existing` during scan gates.
   * `manual_block`: Halts continuous loops, forcing manual resolution.

3. **Manual Override Triage**:
   * **List Conflicts**: `./venv/bin/python src/main.py --conflicts-triage`
   * **Resolve a Conflict**:

     ```bash
     ./venv/bin/python src/main.py --resolve-conflict <ID> <keep_existing|apply_incoming>
     ```

### High-Level AI Autonomy & ATT Topology

For complex background research, timeline auditing, and multi-tier logical analysis, the system supports dynamic AI team delegation and autonomous tool use. This suite is highly modular and is fully customized under the `autonomy` section in `config.yaml`:

* **`enable_autonomy_suite: false`**
  * *Narrative Autonomy Switch*: Disables narrative discussion teams, delegation, supervisors, and custom ATT query tools. The ATT manager may still initialize when the independently configured Database Management Committee is enabled, because database governance uses an ATT committee.
* **`enable_autonomous_queries: false`**
  * *Tool Loop Toggle*: Allows AI agents to run bounded ReAct (Reasoning & Action) loops, autonomously executing SQLite queries, FAISS vector searches, and paginated gated file lookups in the background.
* **`enable_dynamic_delegation: false`**
  * *Delegation Toggle*: Enables agents to recursively spawn specialized child and grandchild Agent Teams (ATs) to offload research and outline consistency tasks.
* **`large_file_threshold_kb: 50`**
  * *Context Protection Limit*: Files larger than this threshold (in KB) will block direct full reads by agents. The system will instead return a structured **File Outline** sample, forcing the agent to paginate.
* **`max_chunk_lines: 100`**
  * *Pagination Chunk Cap*: The maximum number of lines returned in a single paginated chunk read. Helps protect the LLM context from log/draft dumps.
* **`enable_memory_compression: true`**
  * *Memory Compression*: Automatically summarizes early parts of dialogue histories when the turn count exceeds `max_memory_turns` to prevent Out-Of-Memory (OOM) failures and token window pollution.
* **`max_memory_turns: 20`**
  * *High-fidelity Context Window*: The number of raw turns kept as high-fidelity context before older dialogue turns are compressed.
* **`failover_policy: "auto"`**
  * *Failover Routing Strategy*: Defines how the system behaves on model API failure or token limit exhaustion. Options: `"auto"` (automatically swap to another available model) or `"parent"` (escalate model selection decisions dynamically to the parent team representatives).
* **`enable_emergency_wakeup: true`**
  * *Emergency Wakeup*: Allows idle parent teams to be automatically awoken for rapid emergency debates when high-priority warnings/anomalies are escalated from child teams.
* **`emergency_discussion_rounds: 1`**
  * *Emergency Rounds*: Number of discussion rounds run by the parent team when triggered by an emergency wakeup.
* **`tool_calling_mode: "auto"`**
  * *Tool Gating Mode*: Strategy for executing tools. Options: `"text_react"` (sequential ReAct loops parsing XML/Thought blocks), `"native"` (parallel structured function calling), or `"auto"` (automatically use native structured calling if the LLM adapter supports it, else fall back to text ReAct).
* **`max_tool_rounds: 5`**
  * *Native Parallel Rounds*: The maximum reasoning rounds allowed during native parallel structured tool calls.
* **`state_db_path: "novel/process/att_state_v6.db"`**
  * *ATT State Store*: Restores the current ATT agent/team state on startup and writes a full, internally consistent snapshot during orderly shutdown.

### SQLite Auditing: Database Management Committee

Database Management Committee coverage is explicitly configurable. The default profile audits ATT SQL, chapter fact batches, failed-commit replay, and conflict resolution. Enable individual scopes in `database_audit.scopes` when character, relationship, rule, event, vector, revision, conflict-queue, commit/schema metadata, or maintenance operations also require review. `failure_policy: deny` is the recommended fail-closed setting. See [Database_Management_Committee.md](Database_Management_Committee.md).

### Recovery from Failures

If an API error or logic crash happens during a database commit:

1. Check failed commits: `./venv/bin/python src/main.py --failed-commits`
2. Replay a commit: `./venv/bin/python src/main.py --replay-commit <COMMIT_ID>`
3. Preview all eligible failed commits without writing: `./venv/bin/python src/main.py --replay-failed-bulk --replay-dry-run`
4. Replay in bulk with bounded retries: `./venv/bin/python src/main.py --replay-failed-bulk --replay-max-attempts 3 --replay-policy continue`

The bulk report includes validation eligibility, attempts, outcome, final status, and error details for every processed commit. Use `--replay-policy stop` to stop after the first invalid or exhausted commit.

### Rebuilding & Recovering Search Index

If you change your Embedding model or need to refresh the vector store:

```bash
./venv/bin/python src/main.py --rebuild-vectors
```

**Automatic Self-Healing**:
At startup, the system reconciles the loaded FAISS count and contiguous IDs against active SQLite `vector_metadata`. Missing/corrupt indexes and metadata/index mismatches trigger reconstruction without deleting source metadata. Each rebuild is recorded in `vector_rebuild_runs`; skipped source rows and their reasons remain available in `vector_rebuild_audit` and as soft-deleted metadata tombstones.

## 6. Real-Time Terminal Dashboard & Logging

To provide an elite, premium user experience, the system replaces cluttered raw console outputs with a gorgeous real-time visual interface and an advanced multi-gated logging system.

### Full-Screen Alternate Buffer Dashboard (`rich.live`)

When running workflow commands (`--start`, `--plan`, `--write`, `--scan`, `--auto`), the system launches a gorgeous, interactive full-screen application inside your terminal's alternate screen buffer (similar to `top` or `vim`):

* **Workspace Alternate Buffer**: Runs full-screen and perfectly restores your previous screen output upon completion, protecting your terminal scroll history.
* **Header Bar**: Tracks current high-level operations (e.g. World Building, Chapter Planning, Prose Writing, Reviewing) and overall chapter auto-progress.
* **Lineage Tree of Active ATT (Left Pane)**: Visualizes the active parent and child Agent Teams (ATs) spawned dynamically, highlighting active agents and their real-time execution states (e.g., `Idle`, `Thinking...`, `Executing Tool: tool_name`).
* **Real-Time Agent Terminal (Right Top Pane)**: Displays scrolling agent thoughts (`🧠`), actions (`⚙️`), observations (`👁️`), and final answers (`✨`) in real time. **All core workflow AI agents** (Architect, Planner, Writer, Critic, Scanner) are fully integrated to display their dynamic activities in real-time alongside dynamic ATT debate agents!
* **System & Memory Log (Right Bottom Pane)**: Captures and scrolls general runtime messages cleanly in a designated panel, dynamically silencing background outputs to prevent screen garbling.

### Divided Dynamic ATT Logging

While keeping the terminal output clean and visually stunning, the system records complete, un-truncated traces on disk for absolute transparency and debugging:

1. **Isolated Team Logs**: Every dynamically created Agent Team records its entire debate history and intermediate ReAct steps under its own dedicated file at `novel/Discussion_Log/att/{team_id}.log`.
2. **Chapter-Specific Logs**: Debates are automatically grouped and appended to `novel/Discussion_Log/chapter_{chapter_num}_Discussion.log` based on the active chapter.
3. **Global Trace**: All activities are logged chronologically inside `novel/Discussion_Log/All_Discussion.log`.

## 7. Customizing Prompts

You can refine the AI's behavior without changing Python code by editing the files in `i18n/AI/`:

* `fragments.json`: Short instructions used within the context funnel.
* `context.json`: Story-state, retrieval, conflict, and archive context shown to models.
* `runtime.json`: ATT committee instructions, tool text, audit prompts, and model-visible runtime text.
* `templates.md`: The core system prompts for each agent.

Human-visible CLI, dashboard, log, error, and report text is stored separately in `i18n/messages/{language}/runtime.json`. AI-visible content must not be placed there.

---

For more technical details, refer to the [Project Architecture](Architecture.md) and [System Flowcharts](Flowchart/README.md).
