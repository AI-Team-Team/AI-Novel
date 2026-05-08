# AI Novel Writer

A multi-agent system designed to write long-form coherent novels by maintaining a structured, multi-tier memory of the world, characters, and plot.

Many thanks to Gemini and GPT for their help!

> [!NOTE]
> The project is currently in a very early stage.

[👉 Project Architecture](docs/Architecture.md) | [👉 Flowchart](docs/Flowchart/README.md) | [👉 User Guide](docs/User_Guide.md) | [👉 Documents](docs/)

## Key Features

* **Long-Term Consistency:** Uses a hybrid database (SQLite + FAISS) to track "hard" facts (rules, relationships) vs. "soft" details (descriptions, atmosphere).
* **Multi-Agent Collaboration:** Specialized AI roles (Architect, Writer, Critic, Scanner) work together to plan, write, review, and archive the story.
* **Dual Model Support:** Supports **Google Gemini** and **OpenAI Compatible APIs**.
* **Dedicated Embedding Service:** Decoupled architecture allowing separate endpoints for Generation (LLM) and Embeddings (Vector DB).
* **Continuous Writing:** Automated loop to generate multiple chapters in sequence.
* **Two-Stage Plot Planning:** Builds both a high-level Novel Plot Outline and a Detailed Plot Outline before chapter planning.
* **Unified Discussion Template:** World/plot/guide/chapter-text discussions now share one normalized template and title format.
* **Centralized i18n & Prompt Management:** All AI instructions and UI strings are strictly separated into the `i18n/` directory, supporting `zh-CN` and `en` natively.
* **Intent-Gated Retrieval Chain:** Planning/writing now enforce `Intent Classifier -> SQLite Prefilter -> FAISS Retrieval -> Cross-Tier Alignment`.
* **Atomic Memory Transactions:** Synchronized SQLite and FAISS commits with index cloning and automated rollback on `BLOCKING` conflicts.
* **Deep Interruption Recovery:** Exhaustive integrity validation of all runtime artifacts before resuming.
* **Language Guard:** Automatic confidence-based language detection and rewrite loop.
* **Conflict Severity Levels:** Conflicts are classified as `BLOCKING` or `NON_BLOCKING` for safer automation.

## Installation

1. **Prerequisites:**
   * Python 3.10+
   * (Optional) A supported local LLM model (GGUF format) for Llama.cpp or an OpenAI-compatible server.
   * (Optional) A supported local Embedding model for Llama.cpp or an OpenAI-compatible server.

2. **Install Dependencies:**

   ```bash
   pip install -r requirements.txt
   ```

3. **Configuration:**
   * Open `config.yaml`.
   * Set `LANGUAGE = "Chinese"` or `"English"`.
   * Configure role model routing and workflow controls.

### 1. Initialize Workspace

```bash
python src/main.py --init
```

Fill `novel/Novel_Overview.md` and start:

```bash
python src/main.py --start
```

### 2. Manual Workflow

* **Plan:** `python src/main.py --plan 1`
* **Write:** `python src/main.py --write 1`
* **Scan:** `python src/main.py --scan 1`
* **Conflicts:** `python src/main.py --conflicts-triage`

### 3. Continuous Writing (Auto Mode)

```bash
# Generate 5 chapters starting from Chapter 1
python src/main.py --auto 1 5
```

## GPU Acceleration & Model Configuration

The system now supports separating the **Generation Model** from the **Embedding Model** for optimal local performance.
