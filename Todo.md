# Todo

This document only contains In Progress, Known Issues, and Future Plans.

Any resolved issues should not be stored in this document.

## In Progress

1. Tune query-intent classifier and cross-tier alignment thresholds using realistic chapter corpora.
2. Build consumers for `discussion_index.jsonl` and conflict triage output (analytics/audit dashboards).
3. Harden auto-mode resume with persistent run checkpoints and retry/backoff metadata (current version already performs strict runtime artifact integrity validation and discard/regenerate).
4. Add chapter-scope cleanup manifest so interrupted generations can purge generated artifacts and commit traces with stronger determinism.

## Known Issues

1. FAISS rollback still depends on in-memory index cloning; for very large indices this may be memory-heavy.
2. Conflict diagnostics now include diff paths and reason labels, but root-cause graphing is still basic.
3. Language guard now has confidence scoring (and excludes known character names), but still uses rewrite fallback as the final correction path.
4. `ENABLE_BUDGET_MONITORING` and related token limits are defined in config but lack actual implementation in LLM clients, offering no cost circuit breakers.
5. Critic fact-review failures currently pass the extracted payload through unchanged even though most semantic contradiction checks were removed from the deterministic memory layer; this needs an explicit user-selectable fail-closed, queue-for-review, or fail-open policy.
6. The full unittest process intermittently emits `ResourceWarning` for late-collected SQLite connections around ATT persistence tests even though every AI-Novel manager follows `close_autonomy()`; isolate whether an AI-Novel test fixture or ATT's persistence teardown retains the final reference.

## Future Plans

1. Add schema-migration preflight backup/verification command before major version bumps.
2. Add an optional AI-assisted language guard that can judge mixed-language prose contextually before deterministic rewrite fallback.
3. Introduce weighted ontology-assisted contradiction scoring for multilingual rules/events.
