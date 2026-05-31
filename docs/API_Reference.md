# API Reference

This document provides a technical overview of the core Python modules in `src/`.

## `src.llm_client`

Handles interactions with Large Language Models. Supports cloud (Gemini) and local (OpenAI Compatible) backends, with a decoupled architecture for Embeddings.

### `LLMClient`

**Constructor:**

```python
client = LLMClient(
    model_type: str = config.PRIMARY_MODEL_TYPE,
    model_name: Optional[str] = None,
    enable_embedding: bool = True
)
```

* `model_type`: "gemini" or "openai". Defaults to config.
* `model_name`: Optional per-role override for generation model.
* `enable_embedding`: If `False`, skips embedding client setup.
* **Initialization:**
  * Sets up the **Generation Client** based on `model_type` (uses `OPENAI_BASE_URL` or `GEMINI_API_KEY`).
  * Sets up the **Embedding Client** based on `config.EMBEDDING_PROVIDER` (uses `EMBEDDING_BASE_URL`).

**Methods:**

* `generate(prompt: str, system_instruction: str = None, temperature: float = 0.7) -> str`
  * Unified method to generate text using the **Generation Client**.
  * `system_instruction`: High-level behavior guidelines.

* `get_embedding(text: str) -> Optional[list]`
  * Generates a vector embedding for the given text using the **Embedding Client**.
  * **Providers:**
    * `openai`: Uses `EMBEDDING_BASE_URL` (e.g., Llama.cpp/LM Studio) and `EMBEDDING_MODEL_NAME`.
    * `gemini`: Uses `GEMINI_EMBEDDING_MODEL` ("text-embedding-004").
  * Used for the FAISS vector store.

## `src.memory`

Manages the hybrid database system (SQLite + FAISS).

### `MemoryManager`

**Constructor:**

```python
mm = MemoryManager(db_path: str, faiss_path: str, embedding_dim: int = 768)
```

* `db_path`: Path to the SQLite file.
* `faiss_path`: Path to the FAISS index file.

**Tier 1 Operations (Structure):**

* `upsert_character(name: str, core_traits: Optional[Dict] = None, attributes: Optional[Dict] = None, status: Optional[str] = None)`
  * Inserts or partially updates a character profile without overwriting unspecified fields.
* `get_character(name: str)`
  * Retrieves character details by name.
* `add_relationship(source: str, target: str, relation_type: str, details: str = None)`
  * Adds or updates a relationship between two characters.
* `get_relationships(character_name: str)`
  * Retrieves all relationships involving the character.
* `add_rule(category: str, content: str, strictness: int = 1)`
  * Adds a world rule (e.g., Physics, Magic).
  * Supports audit metadata: `source_commit_id`, `intent_tag`.

**Audit / Control Operations:**

* `begin_chapter_commit(chapter_num: int, source: str, payload: Optional[Dict] = None) -> str`
  * Opens a chapter-level commit batch and returns `commit_id`.
* `finalize_chapter_commit(commit_id: str, status: str, conflicts_count: int = 0, error_message: str = "")`
  * Marks commit status (`COMPLETED` / `FAILED`), conflict count, and error metadata.
  * Auto-increments replay counter when a previously `FAILED` commit is finalized to `COMPLETED`.
* `get_chapter_commit(commit_id: str)`
  * Returns one commit row with payload/status/replay metadata.
* `get_failed_chapter_commits(limit: int = 20)`
  * Lists failed commit batches for replay triage.
* `queue_conflict(...) -> int`
  * Pushes unresolved contradictions to `conflict_queue`.
  * Supports severity and triage metadata via `blocking_level`, `priority`, and `suggested_action`.
* `get_pending_conflicts(limit: int = 50)`
  * Returns pending conflict rows for planner/human visibility, sorted by blocking level then priority.
  * Supports level filtering via `blocking_level`.
* `get_pending_conflict_count() -> int`
  * Returns pending conflict size for summary and metrics.
* `get_pending_blocking_conflict_count() -> int`
  * Returns only unresolved `BLOCKING` conflicts.
* `get_pending_conflict_triage(limit: int = 50) -> List[Dict]`
  * Returns machine-readable triage diagnostics with `reason_label`, `priority`, and `suggested_action`.
  * Supports level filtering via `blocking_level`.
* `resolve_conflict(conflict_id: int, action: str, resolver_note: str = "", source: str = "manual_resolve") -> bool`
  * Resolves pending conflicts.
  * Current supported actions: `keep_existing`, `apply_incoming` (for known conflict types).

**Schema control:**

* `get_schema_version() -> int`
  * Returns current SQLite schema version after forward migrations.
* Internal migration flow:
  * Ensures `schema_meta`.
  * Applies versioned migrations sequentially.

**Deterministic conflict rules in `MemoryManager`:**

* Character status `dead -> alive` → `BLOCKING` (queued, not auto-overwritten).
* Identity field change → `BLOCKING`.
* Duplicate event key → `BLOCKING`.
* Event references dead character → `NON_BLOCKING` (event still inserted; flagged for Critic review).
* Relationship type change → `NON_BLOCKING` (existing type preserved).
* Relationship with dead character → `NON_BLOCKING`.
* Exact deduplication for rules and events (returns existing ID).

**Note:** Heuristic keyword-based contradiction detection (`_weighted_overlap_score`, `_rule_maybe_contradict`, etc.) has been removed. Semantic/logical contradiction detection is now handled by the LLM Critic in `workflow.py`.

**Tier 3 Operations (Semantic):**

* `add_semantic_fact(content: str, embedding: List[float], metadata: Dict = None, source: str = "unknown", chapter_num: Optional[int] = None, source_commit_id: Optional[str] = None, intent_tag: str = "")`
  * Adds a text chunk to the vector store.
  * Injects `chapter_num` directly into the SQLite metadata JSON column to enable spatiotemporal Future Gate filtering and Temporal Proximity boost reranking during retrieval.
  * Supports audit metadata: `source_commit_id`, `intent_tag`.
* `search_semantic(query_embedding: List[float], k: int = 5) -> List[Dict]`
  * Retrieves the top `k` most similar text chunks.
* `rebuild_vector_index_from_metadata(embedding_fn, include_deleted: bool = False) -> Dict[str, int]`
  * Deterministically rebuilds FAISS from `vector_metadata` rows and remaps `faiss_id`.

## `src.workflow`

Orchestrates the multi-agent process.

### `WorkflowManager`

**Methods:**

* `_append_structured_discussion(...)`
  * Internal normalized logger for `world|plot|guide|chapter_text`.
  * Writes markdown entries with consistent title/fields and appends JSON lines to `discussion_index.jsonl`.

* `load_system_prompts(language: str, src_dir: str) -> Dict[str, str]`
  * Loads core agent prompts from the `i18n/` system using `LanguageResources`.
  * Returns a dictionary of prompts for `architect`, `critic`, `planner`, `writer`, and `scanner`.

* `start_new_project(user_instruction: str)`
  * **Phase 1:** Invokes the **Architect** agent.
  * Generates the initial World Frame.
  * **New:** Runs two planning stages with Critic discussions:
    * `plot_outline.md` (high-level plot conception),
    * `detailed_plot_outline.md` (specific plot conception).
  * Seeds initial structured facts into the database.

* `generate_chapter_guide(chapter_num: int, previous_summary: str = None) -> str`
  * **Phase 4 (Plan):** Creates the "Writing Contract".
  * **Static Context:** Includes World Bible + Plot Outline + Detailed Plot Outline when available.
  * **Discussion Loop (New):** Runs optional Critic->Planner revision rounds (`CHAPTER_GUIDE_DISCUSSION_ROUNDS`) before finalizing the guide.
  * **Dynamic Context:** Uses `StoryStateManager.build_context_package(...)` retrieval chain:
    * Intent classification,
    * SQLite prefilter,
    * FAISS retrieval,
    * Cross-tier alignment,
    * Prompt context assembly.
  * Under the hood it includes:
    * **Active Characters:** Current status and traits.
    * **Recent History:** The last 5 events from the timeline.
    * **Tier-3 Semantic Details:** Runs intent-gated embedding retrieval and injects top semantic matches into planning context.
  * Injects this context into the prompt to ensure continuity.

* `write_chapter(chapter_num: int, guide_content: str) -> str`
  * **Phase 5 (Write):** Generates chapter prose based on the guide.
  * Uses the same retrieval chain as planning, with writer intent profile and higher-recall semantic gating.
* `_review_and_revise_chapter(...) -> Tuple[str, str]`
  * Runs Critic review and optional Writer revision rounds based on `CHAPTER_TEXT_DISCUSSION_ROUNDS`.
  * Persists chapter text discussion log to `process/discussions/chapter_{n}_text_discussion.md`.

* `scan_chapter(chapter_num: int) -> str`
  * **Phase 6 (Scan):** Extracts facts from the generated text.
  * **Closed Loop:** Expects **JSON** output from the LLM.
  * Runs payload schema validation before any DB writes; invalid payloads are saved and rejected.
  * **Critic Fact Review:** After validation, all extracted facts are reviewed by the LLM Critic against current DB state via `_critic_review_extracted_facts()`. BLOCKING contradictions are removed from the payload; NON_BLOCKING issues are queued but facts are kept.
  * Automatically parses the JSON and calls `MemoryManager` to:
    * Add/Update Characters (SQLite).
    * Add Timeline Events (SQLite).
    * Add Semantic Details (FAISS).
  * Returns a text summary of the updates.
  * Saves `chapter_{n}_facts_summary.md` for chapter-to-chapter continuity.
* `review_revise_and_scan(chapter_num: int, guide_content: str, chapter_text: str) -> str`
  * Public close-loop helper for manual flow.
  * Runs Critic->Writer revision loop first, then Scanner + Memory updates.
  * Used by CLI `--write` so manual chapter generation stays consistent with auto mode.

* `_extract_json(text: str) -> Optional[Dict]`
  * Helper to robustly extract JSON objects from LLM responses, handling Markdown code blocks.
* `list_failed_chapter_commits(limit: int = 20) -> List[tuple]`
  * Lists failed commit batches.
* `replay_chapter_commit(commit_id: str) -> bool`
  * Replays one failed commit from `chapter_commits.payload_json`.
* `_critic_review_extracted_facts(chapter_num: int, facts_data: Dict, chapter_text: str, prompts: Dict) -> Dict`
  * LLM Critic batch reviews all extracted facts against current DB state before commit.
  * Builds a state snapshot (characters, strict rules, recent events) and sends to Critic with the facts payload.
  * `BLOCKING` issues: facts removed from payload, conflicts queued.
  * `NON_BLOCKING` issues: facts kept, conflicts queued as advisory.
  * Graceful degradation: if Critic call fails, returns facts unchanged.
* `batch_triage_non_blocking(limit: int = 50, note: str = ...) -> int`
  * Batch-resolves NON_BLOCKING conflicts via `keep_existing`.
* `rebuild_vector_index() -> Dict[str, int]`
  * Rebuilds vector index from metadata using current embedding backend.
* `run_continuous_loop(start_chapter: int, count: int)`
  * Executes Plan -> Write -> Review/Revise -> Scan in sequence for a chapter range.
  * Resume & Error Handling behavior:
    * Includes an automatic retry mechanism (up to `AUTO_GENERATION_MAX_RETRIES`) per chapter to recover from API or validation failures, discarding corrupted artifacts before retrying.
    * Performs strict runtime artifact integrity validation across generated files under `novel/` (JSON/JSONL/text artifacts).
    * If scanned facts for a chapter are complete and valid, that chapter is skipped.
    * Before skipping, integrity checks validate chapter text, facts summary, facts JSON schema, and latest scan commit status + payload.
    * If integrity fails, chapter-scoped artifacts are discarded and the chapter is regenerated from Plan stage.

### Blocking Conflict Governance Mode

Workflow gate behavior for unresolved `BLOCKING` conflicts is configured by `config.BLOCKING_CONFLICT_MODE`:

* `auto_keep_existing`:
  * Auto-resolves pending `BLOCKING` conflicts using `keep_existing` before gate checks.
* `manual_block`:
  * Does not auto-resolve `BLOCKING` conflicts.
  * Gate raises runtime error until conflicts are resolved explicitly.

### Multi-Agent Cooperative Debate Conflict Resolution

When continuous loops (`--auto`) or the explicit CLI flag `--ai-resolve-conflicts` are active:

* **Automatic Triage Panel**: Any encountered blocking conflicts automatically interrupt the generation workflow and launch a Multi-Agent Debate Panel (Planner, Critic, and Scanner) in the background.
* **Consensus Resolution**: If consensus is successfully negotiated in exactly $N$ rounds (configured by `config.CONFLICT_DISCUSSION_ROUNDS`), the chosen action (`apply_incoming` or `keep_existing`) is atomically committed to SQLite and documented under `novel/process/discussions/conflict_{id}_resolution_discussion.md`.
* **Fail-Fast Standoff**: If consensus cannot be reached, execution is immediately halted (raising a `RuntimeError`) to prevent database corruption.

#### Core API Methods

* `ai_debate_resolve_conflict(conflict_id: int) -> bool`
  * Orchestrates the multi-agent debate panel for the specified conflict.
  * Spawns Critic, Scanner, and Planner LLM clients to debate across $N$ rounds.
  * Parses final JSON payload, executes the safe transaction block, and records transcripts.
  * Returns `True` on successful resolution, `False` on standoff.
