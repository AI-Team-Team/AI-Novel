# AI Novel Project Architecture

## Overview

This project facilitates the generation of long-form novels through a multi-agent collaborative system (Multi-Agent System), leveraging a structured memory approach to ensure narrative consistency and logical integrity over extended contexts.

## Core Concepts

### 0. Unified Discussion Logging

Discussion artifacts for `world`, `plot`, `guide`, and `chapter_text` now follow one schema and title convention:

* Title: `[phase_type] chapter={n|NA} round={r|NA} role={role}`
* Fields: `log_id`, `timestamp`, `phase_type`, `chapter_num`, `round`, `role`, `model`, `input_summary`, `output_summary`, `decision`, `needs_revision`, `artifact_paths`
* Index: every entry is also appended to `process/discussions/discussion_index.jsonl`

### 1. Three-Tier Fact System

To maintain continuity, narrative facts are categorized into three levels of "hardness," managed by a hybrid database system (SQLite + FAISS).

#### Tier 1: Core Constraints (Structural)

* **Definition:** Immutable or highly resistant facts that define the world and characters.
* **Storage:** SQLite (`characters`, `relationships`, `world_rules` tables).
* **Enforcement:** Strict filtering. The Writer Agent is explicitly forbidden from contradicting these.
* **Updates:** Supports character trait evolution and status updates.

#### Tier 2: Major Milestones (Semi-Structural)

* **Definition:** Significant events that alter the state of the world or characters.
* **Storage:** SQLite (`timeline` table).
* **Features:** Tracks `related_entities` and `location` for targeted retrieval.
* **Enforcement:** Retrieved based on timeline context to ensure cause-and-effect consistency.

#### Tier 3: Narrative Details (Atmospheric)

* **Definition:** Ephemeral or granular details that provide color but don't break the plot if slightly altered (though consistency is preferred).
* **Storage:** FAISS Vector Index + SQLite Metadata (`vector_metadata` table).
* **Enforcement:** Semantic Retrieval with optional Metadata Filtering (e.g., "visuals" at "Tavern").

## Data Schema

### Schema Versioning

* SQLite schema upgrades are now forward-only migrations managed by `MemoryManager`.
* Version state is stored in `schema_meta` (`key='schema_version'`).
* Databases without `schema_meta` are treated as unsupported legacy state and must be re-initialized.
* Fact tables include audit metadata fields: `source_commit_id`, `version`, `is_deleted`, `intent_tag`.

### SQLite Tables

**`characters`**

| Column | Type | Description |
| :--- | :--- | :--- |
| `id` | INTEGER PK | Unique ID |
| `name` | TEXT | Character Name |
| `core_traits` | JSON | MBTI, Trauma, Fears, etc. |
| `status` | TEXT | 'alive', 'dead', 'missing' |
| `attributes` | JSON | Flexible attributes |
| `last_updated` | TIMESTAMP | Last modification time |

**`timeline`** (Enhanced)

| Column | Type | Description |
| :--- | :--- | :--- |
| `event_name` | TEXT | Short title |
| `description` | TEXT | Full description |
| `timestamp_str` | TEXT | In-universe time |
| `impact_level` | INT | Significance (1-5) |
| `related_entities` | JSON List | e.g. ["Alice", "Bob"] |
| `location` | TEXT | Where it happened |
| `source_commit_id` | TEXT | Originating commit batch |
| `version` | INT | Logical row version |
| `is_deleted` | INT | Tombstone marker (0/1) |
| `intent_tag` | TEXT | Retrieval/update intent label |

**`vector_metadata`** (Linked to FAISS)

| Column | Type | Description |
| :--- | :--- | :--- |
| `faiss_id` | INT PK | Index in FAISS array |
| `content` | TEXT | The raw text chunk |
| `metadata` | JSON | Context tags (location, type) |
| `source_commit_id` | TEXT | Originating commit batch |
| `version` | INT | Logical row version |
| `is_deleted` | INT | Tombstone marker (0/1) |
| `intent_tag` | TEXT | Retrieval/update intent label |

## Agent Workflow (Closed Loop)

The system operates on a continuous Plan-Write-Scan loop where the output of one chapter directly updates the context for the next.

0. **Pre-Planning Stage (Plot Construction)**
   * **Input:** Approved World Bible.
   * **Action:** Planner produces a high-level plot outline, then a detailed plot outline.
   * **Discussion:** Critic reviews each outline; Planner revises for bounded rounds.
   * **Output:** `frame/plot/plot_outline.md` + `frame/plot/detailed_plot_outline.md` and discussion logs.

1. **Planner Agent (Narrative Planner)**
   * **Input:**
     * Static: World Bible + Plot Outline + Detailed Plot Outline.
     * **Dynamic (New):** Queries the Memory Database for "Active Characters" (Status, Traits) and "Recent History" (Last 5 Timeline Events).
     * **Semantic Context (New):** Uses intent-gated vector retrieval to inject relevant Tier-3 details from FAISS.
     * Context: Previous Chapter Summary.
   * **Output:** A structured "Writing Contract" (Chapter Outline).
   * **Discussion Gate (New):**
     * Critic reviews each chapter guide.
     * Planner revises for bounded rounds before Writer starts.

2. **Writer Agent (Novelist)**
   * **Input:** The Writing Contract.
   * **Action:** Generates the prose, focusing on "Show, Don't Tell" and strictly following the scene breakdown.
   * **Output:** Markdown-formatted Chapter Text.

3. **Critic Agent (Chapter Review)**
   * **Input:** Writing Contract + generated Chapter Text.
   * **Action:** Produces contradiction scan + patch guidance.
   * **Output:** Structured review text (`NEEDS_REVISION`, rationale, patch guidance).
   * **Discussion Artifact (New):** Chapter-level text discussion is persisted under `process/discussions/chapter_{n}_text_discussion.md`.

4. **Scanner Agent (Archivist)**
   * **Input:** The generated Chapter Text.
   * **Action:** Analyzes the text to extract new facts.
   * **Output:** **Structured JSON** containing:
     * `new_characters` / `updated_characters`
     * `new_rules` (World building updates)
     * `relationships` (Character dynamics)
     * `events` (Timeline additions)
     * `details` (Semantic chunks for Vector Store)
   * **Validation Gate:**
     * Payload must pass schema checks before review.
     * Invalid payloads are persisted for debugging and rejected from commit.

4b. **Critic Fact Review (Pre-Commit)**

* **Input:** Validated Scanner JSON + current DB state snapshot + chapter text.
* **Action:** LLM Critic examines all extracted facts against established story state in a single batch call.
* **Detection Scope:** Dead→alive contradictions, strict world rule violations, logical/causal impossibilities, character identity contradictions.
* **Output:** Issues list with severity levels:
  * `BLOCKING` — fact removed from payload; conflict queued for manual review.
  * `NON_BLOCKING` — fact kept in payload; conflict queued as advisory.
* **Fallback:** If the Critic LLM call fails, facts pass through unchanged (graceful degradation).

5. **Memory Manager (System)**
   * **Action:** Parses the Scanner's JSON.
   * **Updates:**
     * **SQLite:** Inserts/Updates rows in `characters` and `timeline`.
     * **FAISS:** Embeds semantic details and adds them to the vector index.
   * **Audit Layer (New):**
     * Writes row-level revisions into `fact_revisions`.
     * Records chapter batch state in `chapter_commits`.
     * Queues unresolved hard contradictions in `conflict_queue`.

6. **Loop**
   * The next Planner iteration queries the updated database, ensuring the story reacts to previous events (e.g., if a character died in Chapter 1, the Planner for Chapter 2 knows they are dead).
   * Chapter summaries are persisted in `chapter_{n}_facts_summary.md` and reused when resuming auto mode from later chapters.
   * Auto mode is interruption-resumable by artifact detection:
     * Runtime artifacts under `novel/` are validated before resume (`.json`, `.jsonl`, markdown/text files, and chapter commit linkage).
     * `chapter_{n}_facts.json` must remain valid JSON and pass schema checks.
     * `discussion_index.jsonl` is line-validated as JSONL.
     * A chapter is considered complete only when chapter text, facts summary, facts JSON, and latest `scan_chapter` commit payload/status are all valid.
     * If any check fails, chapter-scoped artifacts are discarded and the chapter is regenerated from planning.

## Conflict Visibility in Planning

Planner context now includes a compact list of pending conflicts (`conflict_queue`) so generated contracts can avoid compounding unresolved contradictions.

## Retrieval Pipeline (Intent -> SQLite -> FAISS -> Alignment)

Planning/writing retrieval now follows an explicit chain:

1. Query intent classification (`task_type`, strictness, required tiers, semantic gate).
2. SQLite prefilter (characters/rules/events/conflicts by intent scope).
3. FAISS semantic retrieval (Tier-3 candidates).
4. Cross-tier alignment:
   * **Future Gate Filter**: Strictly filters out any semantic hits whose metadata indicates they are from future chapters (`metadata["chapter"] > current_chapter_num`), preventing narrative foresight leaks.
   * **Dead Characters Filter**: In strict mode, filters out semantic hits mentioning dead characters. In non-strict mode, all hits pass through.
5. Context package assembly for prompts (policy + compact summaries + aligned Tier-3 details).

## Conflict Detection Architecture

Conflict detection uses a **two-layer approach**:

### Layer 1: Deterministic Checks (memory.py)

Performed at the data layer during insertion:

* `status_dead_to_alive` — character resurrection → `BLOCKING`
* `identity_field_conflict` — immutable field change → `BLOCKING`
* `timeline_dead_character_involved` — event references dead character → `NON_BLOCKING` (flagged for Critic review)
* `relationship_type_change` — type mismatch → `NON_BLOCKING`
* `relationship_dead_character_involved` → `NON_BLOCKING`
* `timeline_same_key_conflict` — duplicate event key → `BLOCKING`
* Exact deduplication (rules and events)

### Layer 2: LLM Critic Review (workflow.py)

Performed before DB commit in `scan_chapter()` via `_critic_review_extracted_facts()`:

* Reviews all extracted facts against current DB state in a single batch LLM call.
* Detects semantic/logical contradictions that keyword matching cannot: causal impossibilities, strict world rule violations, character identity conflicts.
* `BLOCKING` issues → facts removed from payload, conflicts queued.
* `NON_BLOCKING` issues → facts kept, conflicts queued as advisory.

## Conflict Levels (Blocking vs Non-Blocking)

* `conflict_queue` stores `blocking_level` (`BLOCKING` or `NON_BLOCKING`).
* `conflict_queue` also stores triage metadata: `priority` and `suggested_action`.
* Workflow gating behavior is controlled by `BLOCKING_CONFLICT_MODE`:
  * `auto_keep_existing`: auto-resolve `BLOCKING` conflicts with `keep_existing` before gate checks.
  * `manual_block`: never auto-resolve; gate blocks while any `BLOCKING` conflict remains.

### ATT Conflict Resolution Committee

When running under continuous writing mode (`--auto`) or enabled via the CLI flag (`--ai-resolve-conflicts`), the system automatically spawns a dynamically managed **Conflict Resolution Committee** Agent Team (AT) consisting of three specialized AI agents to debate and resolve any encountered blocking conflicts:

* **Historian_Critic**: Strict defender of database integrity, global rules, and past continuity (advocates for `keep_existing`).
* **Prose_Scanner**: Defends new creative prose intentions, story momentum, and character developments (advocates for `apply_incoming`).
* **Consensus_Planner**: Moderates the committee panel and makes the final executive resolution decision.

#### Debate Termination & Standoff Governance (Fail-Fast)

* **Consensus Rule**: The debate runs for exactly $N$ rounds (configured via `conflict_discussion_rounds`, default: 2) inside the dynamically generated AT.
* **Fail-Fast Boundary**: If the Consensus_Planner cannot synthesize a unanimous agreement on exactly `"apply_incoming"` or `"keep_existing"` by the final round, **the system must immediately stop and trigger a standoff** (raising `RuntimeError`). This prevents silent data corruption and queues the debate log for human inspection.
* **Deep Context Assembly**: The debate panel receives a comprehensive context window including:
  * **Multi-Chapter Prose Window**: Full text of preceding chapter Ch N-1, conflict chapter Ch N, and succeeding chapter Ch N+1.
  * **Structured Context**: SQLite character profiles/attributes, global strict world rules, and the last 10 timeline events.
* **Auditable Logs**: Every debate session transcript and reasoning is written to `novel/process/discussions/conflict_{id}_resolution_discussion.md`.

## High-Level AI Autonomy: ATT (AI Team Team) Topology

To support complex narrative tasks, background lore alignment, database transaction auditing, and timeline analysis, the system implements a unified **ATT (AI Team Team)** topology that empowers AI agents to transition from passive context consumers to active, self-governing groups.

> [!NOTE]
> For in-depth technical specifications, class models, and detailed control-flow Mermaid diagrams of each component, please refer to the dedicated **[AI Autonomy Suite Specification](ATT_Autonomy_Suite/README.md)**, **[AI Autonomy Suite Flowcharts](Flowchart/ATT_Autonomy_Suite/README.md)**, and the **[Database Management Committee Specification](Database_Management_Committee.md)**.

### 1. Recursive Spawning & Hierarchy Lineages

The ATT topology is built on a dynamic recursive lineage model:

* **Level 0 (Root AI)**: The primary workflow agent coordinating the project.
* **Level 1 (Child AT)**: Dynamic Agent Teams spawned by Level 0 or its components to handle specialized tasks (e.g., Chapter Planning Committee, World Bible Committee).
* **Level 2 (Grandchild AT)**: Dynamic sub-teams spawned recursively by Level 1 members to perform micro-validations (e.g., Timeline Auditor, Character status check).
* **Enforced Team Size**: Every dynamic Agent Team (AT) must contain at least 3 AI members ($N \ge 3$) to guarantee balanced debates.

### 2. P2P Negotiation & Sibling Routing

Communication permissions between dynamic ATs are strictly regulated:

* **Sibling ATs**: Communication is rule-gated by their common parent team's rules (`allow_sibling_talk`).
* **Cross-Lineage ATs**: Requires dynamic negotiation brokered by `NegotiationBroker`, running a simulated agreement loop between their respective parent teams before establishing a communication tunnel.
* **Escalation Protocol**: Dynamic parent-routing dispatches structured messages/alerts directly to the parent team's `message_inbox`. The parent team automatically processes and injects unresolved alerts into its active discussion prompts.

### 3. Supervisory Auditing Team

A non-participating **Supervisory Team** composed of exactly 3 specialized AI auditors (Integrity, Continuity, and Deadlock Auditors) audits the dialogue effectiveness of all active Agent Teams:

* **Deadlock Detection**: Audits multi-agent discussion transcripts for deadlocks, repetition, and role deviations.
* **Recursive Parent Escalation**: Escalates anomalies up the lineage tree until a healthy parent is found. If all ancestors fail (lineage collapse), reports directly to the Level 0 Root AI.

### 4. Centralized ReAct Tool Execution & Safe Argument Parser

Agents in dynamic teams execute tasks inside a robust **Reasoning & Action (ReAct)** loop:

* **Centralized Registry**: Uses a centralized factory in `tools.py` registering system-wide tools (`query_sqlite`, `search_faiss`, `read_file_chunk`, `read_file_tail`, `dispatch_subagent`, `delegate_escalation`, `set_sibling_talk`).
* **Safe Argument Parser**: Leverages Python's `ast.literal_eval` to safely evaluate quoted string arguments (e.g., search queries containing commas) without breaking or misinterpreting parameters.

### 5. Database Management Committee (DMC)

To secure the SQLite memory store, a dedicated 3-AI **Database Management Committee** audits all direct SQLite transactions and queries, guarding against malicious SQL injection, schema corruption, and logical rule contradictions.

### 6. Autonomy Toggles in Configuration

The entire autonomy framework is highly modular and can be fully enabled/disabled or tuned in `config.yaml` under `autonomy:`:

* `enable_autonomy_suite`: Master toggle to load/skip all autonomy components.
* `enable_autonomous_queries`: Toggle for ReAct tool use (SQLite, FAISS, Gated file paginators).
* `enable_dynamic_delegation`: Toggle for hierarchical subagent tree spawning.
* `enable_memory_compression`: Toggle for O(1) multi-turn memory compression/summarization.
* `max_memory_turns`: Sets the limit of turns to retain before memory compression triggers.
* `failover_policy`: Strategy for fallback LLM routing on token limit exhaustion (`"auto"` or `"parent"`).
* `enable_emergency_wakeup`: Allows waking up idle parent teams on receiving high-priority child anomalies.
* `emergency_discussion_rounds`: Number of debate rounds for emergency wakeups.
* `tool_calling_mode`: Selection of tool calling strategy (`"text_react"`, `"native"`, or `"auto"`).
* `max_tool_rounds`: Step cap for native parallel tool call executions.
* `strict_state_persistence`: Enables ORM cascading deletes for unreferenced messages/agreements.

## Commit Replay Recovery

* `chapter_commits` now tracks `error_message`, `replay_count`, and `last_replayed_at`.
* Failed scan commits can be replayed from persisted `payload_json` through CLI (`--replay-commit`).
* Replay runs the same DB mutation path (`apply_fact_payload`) under a fresh transaction and promotes commit status back to `COMPLETED` on success.
* FAISS can be deterministically rebuilt from `vector_metadata` (`--rebuild-vectors`) to recover DB/vector alignment.

## Initialization Seeding

After World Bible + Plot discussions finish, the system asks the Scanner role to extract initial structured facts (JSON schema identical to chapter scanning) and seeds SQLite/FAISS before Chapter 1 planning. This reduces empty-context starts and improves early chapter continuity.

## System Robustness & Integrity

The system employs several layers of engineering to maintain stability and consistency during long-form generation.

### 1. Multi-tier Retrieval Funnel

To avoid "context pollution," the `StoryStateManager` filters and ranks facts:

* **Intent-based Gating**: Determines if semantic retrieval is needed.
* **SQLite Pre-filtering**: Limits character and event scope based on focus entities.
* **Cross-Tier Alignment**:
  * **Spatiotemporal Future Gate**: Discards any retrieved details that belong to future chapters to prevent timeline leaks.
  * **Dead Character Gate**: Filters out semantic hallucinations (T3) that contradict hard facts (T1/T2), such as a dead character performing active actions.
* **Weighted Reranking**: Boosts details that match focus entities (+0.35) or locations (+0.50), and adds a **Temporal Proximity Boost** ($+0.4 / (1.0 + |DetailChapter - TargetChapter|)$) for active chapter details to prioritize chronologically closer events. Initial seed facts/lore are kept stable without decay.

### 2. Atomic Memory Transactions

`MemoryManager` ensures that a single bad scan doesn't corrupt the long-term memory:

* **FAISS Index Cloning**: At the start of a chapter commit, the system creates a full snapshot of the vector index.
* **Synchronized Rollback**: If a `BLOCKING` conflict occurs, the system rollbacks the SQLite transaction and restores the FAISS index from the snapshot.

### 3. Exhaustive Interruption Recovery

The `WorkflowResumeMixin` performs a three-stage integrity check during resume:

1. **Physical**: Validates file existence and non-zero size.
2. **Schema**: Validates JSON/JSONL against required schemas.
3. **Database**: Validates that the chapter commit status is marked as `COMPLETED` in the SQLite `chapter_commits` table.
*Any failure triggers a clean purge of all partial chapter artifacts.*

### 4. Language Guard

Ensures output consistency through a multi-step process with robust retry boundaries:

1. **Name Exclusion**: Known character names from the DB are excluded from text before computing CJK/Latin ratios, preventing false positives when Chinese names appear in English text (or vice versa).
2. **Confidence Ratio**: Calculates CJK/Latin character ratio on the name-excluded text.
3. **Threshold Check**: Uses direct thresholds (Chinese ≥ 20% CJK; English ≥ 60% Latin with ≤ 10% CJK) to determine if text is in the expected language.
4. **Safety Net**: For English mode, only triggers rewrite if CJK content exceeds 30% after name exclusion.
5. **Bounded LLM Rewrite Loop**: If the language check fails, the system executes an automatic LLM-driven rewrite loop. The maximum number of attempts is fully configurable via `language_rewrite_max_attempts` inside `config.yaml` (default: 2):
   * The first attempt uses a standard translation/rewrite instruction prompt.
   * Any subsequent attempts (up to the configured limit) use a highly strict **escalation prompt** explicitly demanding the pure expected language and the elimination of foreign mixed characters.
   * If all attempts fail to meet the confidence thresholds, the system raises a `RuntimeError` (fail-fast boundary) to prevent corrupted text from entering the draft.
6. **Critic Review**: Language consistency is also part of the Critic's system prompt responsibilities.

## Customization

### Model Configuration

Agent roles are completely decoupled from model specifications:

* **`config.yaml`**: Assigns agent roles (`architect_model`, `planner_model`, `writer_model`, `critic_model`, `scanner_model`, `embedding_model`, and `default_model`) to named model registration keys. Any missing or empty role assignment, or assignment to a disabled model key, triggers a validation error immediately.
* **`config/ai_model_config.yaml`**: Registers credentials, providers, endpoints, and identifiers for LLM and embedding models under named keys. Unset API keys are left blank and do not trigger automatic defaults. Missing `model_name` attributes automatically fall back to the YAML key. Additionally, supports an optional `enabled` flag (boolean, defaults to `true`). If set to `false`, the model registry will skip registration for that key, deactivating the model.

### i18n & Prompts

All system prompts and user-facing strings are managed in the `i18n/` directory:

* **AI Fragments & Templates**: `i18n/AI/{language_code}/`
  * `fragments.json`: Short instructions and labels used in Context.
  * `templates.md`: Multi-line system prompts and task instructions (organized by `## Key`).
* **User Messages**: `i18n/messages/{language_code}/ui.json` (UI strings, summaries, and reports).

You can add new languages or modify behavior by creating/editing files in these directories and updating `config.LANGUAGE`.
