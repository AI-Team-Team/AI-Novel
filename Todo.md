# Todo

This document only contains In Progress, Known Issues, and Future Plans.

Any resolved issues should not be stored in this document.

## In Progress

1. Add bulk replay for failed commits (dry-run preview, per-commit report, retry policy).
2. Add end-to-end integration tests for write loop + conflict lifecycle + replay recovery + retrieval chain.
3. Tune query-intent classifier and cross-tier alignment thresholds using realistic chapter corpora.
4. Build consumers for `discussion_index.jsonl` and conflict triage output (analytics/audit dashboards).
5. Harden auto-mode resume with persistent run checkpoints and retry/backoff metadata (current version already performs strict runtime artifact integrity validation and discard/regenerate).
6. Add chapter-scope cleanup manifest so interrupted generations can purge generated artifacts and commit traces with stronger determinism.
7. Harden FAISS reliability paths: batch-safe vector reset persistence, index-load failure metadata reconciliation, and rebuild skipped-row audit retention.

## Known Issues

1. FAISS rollback still depends on in-memory index cloning; for very large indices this may be memory-heavy.
2. Conflict diagnostics now include diff paths and reason labels, but root-cause graphing is still basic.
3. Language guard now has confidence scoring (and excludes known character names), but still uses rewrite fallback as the final correction path.
4. Chapter planning references `prompt.planner_task`, but the i18n resources do not currently define that key; generated planning prompts may include `MISSING_RESOURCE_prompt.planner_task`.
5. `ENABLE_AUTONOMY_SUITE` is documented as the master autonomy toggle, but `initialize_autonomy()` currently initializes ATT, DMC, and gated reading regardless of that flag.
6. The Database Management Committee only audits direct SQL access through ATT tools; normal `MemoryManager` SQLite writes are not intercepted despite documentation implying full SQLite execution coverage.
7. FAISS recovery is inconsistent when the index file is missing or cannot be loaded: rebuild currently returns early when `self.index` is `None`, limiting recovery from `vector_metadata`.
8. The Chinese writer prompt contains mixed-language wording (`感官细节 and 深度人物视角`), which weakens the language consistency contract.

## Future Plans

1. Add schema-migration preflight backup/verification command before major version bumps.
2. Add explicit language-ID scoring model before rewrite fallback.
3. Introduce weighted ontology-assisted contradiction scoring for multilingual rules/events.
