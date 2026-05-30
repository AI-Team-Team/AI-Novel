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

In `config.yaml`, roles must be assigned to registered keys in a `models` block

*Note: All role assignments must be provided and non-empty; otherwise, the system will raise an error and exit immediately on startup.*

### Registering Models

In `config/ai_model_config.yaml`, define your model registry under named keys:

```yaml
ai:
  model_type: "llm"
  api_type: "openai"
  api_key: "${API_KEY}"
  base_url: ""
  model_name: ""
```

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

### Conflict Triage

When the Scanner detects a change in the story's state (e.g., a character's relationship changes or a dead character appears), it evaluates the change:

1. **LLM Arbiter (Auto-Resolution):** For plot-dependent changes like `relationship_type_change` or `status_dead_to_alive`, the system sends the full chapter text and the conflicting states to an LLM Arbiter. If the Arbiter determines this is a "reasonable plot progression" (e.g., enemies becoming friends), the conflict is automatically resolved and the database is updated.
2. **Manual Triage:** If the Arbiter determines it's a true contradiction (e.g., a character resurrecting with no plot explanation), or for other strict rule violations, it creates a **Conflict** that requires your intervention.

* **List Conflicts**: `python src/main.py --conflicts-triage`
* **Resolve a Conflict**:

  ```bash
  python src/main.py --resolve-conflict <ID> keep_existing
  ```

  Use `keep_existing` to ignore the new fact or `apply_incoming` to overwrite the memory.

### Recovery from Failures

If an API error or logic crash happens during a database commit:

1. Check failed commits: `python src/main.py --failed-commits`
2. Replay a commit: `python src/main.py --replay-commit <COMMIT_ID>`

### Rebuilding Search Index

If you change your Embedding model or need to refresh the vector store:

```bash
python src/main.py --rebuild-vectors
```

## 6. Customizing Prompts

You can refine the AI's behavior without changing Python code by editing the files in `i18n/AI/`:

* `fragments.json`: Short instructions used within the context funnel.
* `templates.md`: The core system prompts for each agent.

---

For more technical details, refer to the [Project Architecture](Architecture.md) and [System Flowcharts](Flowchart/README.md).
