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

*Note: All role assignments must be provided and non-empty; otherwise, the system will raise an error and exit immediately on startup. Similarly, assigning a role to a model key that has been disabled in `config/ai_model_config.yaml` will trigger a validation error on startup.*

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

## 3. Getting Started

### Step 1: Initialize the Workspace

Run the initialization command to create the data structure and a template for your story:

```bash
python src/main.py --init
```

This creates `novel/Novel_Overview.md`.

### Step 2: Define Your Novel

Edit `novel/Novel_Overview.md`. Describe your world, characters, and major plot points. Be as detailed as possible to give the Architect a strong foundation.

### Step 3: Build the Framework

Run the start command to invoke the Architect and Planner:

```bash
python src/main.py --start
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
python src/main.py --auto 1 5
```

The system will automatically Plan, Write, Review, and Scan each chapter. If interrupted, simply run the command again; it will validate existing artifacts and resume where it left off.

### Manual Mode

If you want fine-grained control, you can run steps individually:

1. **Plan**: `python src/main.py --plan 1` (Creates the "Writing Contract").
2. **Write & Review**: `python src/main.py --write 1` (Generates prose and runs the Critic review).
3. **Scan**: `python src/main.py --scan 1` (Updates the memory database with new facts from the prose).

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
   * **List Conflicts**: `python src/main.py --conflicts-triage`
   * **Resolve a Conflict**:

     ```bash
     python src/main.py --resolve-conflict <ID> <keep_existing|apply_incoming>
     ```

### High-Level AI Autonomy & ATT Topology

For complex background research, timeline auditing, and multi-tier logical analysis, the system supports dynamic AI team delegation and autonomous tool use. This suite is highly modular and is fully customized under the `autonomy` section in `config.yaml`:

* **`enable_autonomy_suite: false`**
  * *Master Switch*: Toggle to enable or disable the entire autonomy suite. If `false`, all other autonomy processes are bypassed and no dynamic teams, brokers, or supervisors are instantiated.
* **`enable_autonomous_queries: false`**
  * *Tool Loop Toggle*: Allows AI agents to run bounded ReAct (Reasoning & Action) loops, autonomously executing SQLite queries, FAISS vector searches, and paginated gated file lookups in the background.
* **`enable_dynamic_delegation: false`**
  * *Delegation Toggle*: Enables agents to recursively spawn specialized child and grandchild Agent Teams (ATs) to offload research and outline consistency tasks.
* **`large_file_threshold_kb: 50`**
  * *Context Protection Limit*: Files larger than this threshold (in KB) will block direct full reads by agents. The system will instead return a structured **File Outline** sample, forcing the agent to paginate.
* **`max_chunk_lines: 100`**
  * *Pagination Chunk Cap*: The maximum number of lines returned in a single paginated chunk read. Helps protect the LLM context from log/draft dumps.

### SQLite Auditing: Database Management Committee

Direct transactions on the SQLite memory database are guarded by the 3-AI Database Management Committee. Every SQLite execution is intercepted, audited, and safety-verified automatically before being committed.

### Recovery from Failures

If an API error or logic crash happens during a database commit:

1. Check failed commits: `python src/main.py --failed-commits`
2. Replay a commit: `python src/main.py --replay-commit <COMMIT_ID>`

### Rebuilding Search Index

If you change your Embedding model or need to refresh the vector store:

```bash
python src/main.py --rebuild-vectors
```

## 6. Real-Time Terminal Dashboard & Logging

To provide an elite, premium user experience, the system replaces cluttered raw console outputs with a gorgeous real-time visual interface and an advanced multi-gated logging system.

### Full-Screen Alternate Buffer Dashboard (`rich.live`)

When running workflow commands (`--start`, `--plan`, `--write`, `--scan`, `--auto`), the system launches a gorgeous, interactive full-screen application inside your terminal's alternate screen buffer (similar to `top` or `vim`):

* **Workspace Alternate Buffer**: Runs full-screen and perfectly restores your previous screen output upon completion, protecting your terminal scroll history.
* **Header Bar**: Tracks current high-level operations (e.g. World Building, Chapter Planning, Prose Writing, Reviewing) and overall chapter auto-progress.
* **Lineage Tree of Active ATTs (Left Pane)**: Visualizes the active parent and child Agent Teams (ATs) spawned dynamically, highlighting active agents and their real-time execution states (e.g., `Idle`, `Thinking...`, `Executing Tool: tool_name`).
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
* `templates.md`: The core system prompts for each agent.

---

For more technical details, refer to the [Project Architecture](Architecture.md) and [System Flowcharts](Flowchart/README.md).
