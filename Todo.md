# Todo

This document only contains In Progress, Known Issues, and Future Plans.

Any resolved issues should not be stored in this document.

## In Progress

1. Tune query-intent classifier and cross-tier alignment thresholds using realistic chapter corpora.
2. Build consumers for `discussion_index.jsonl` and conflict triage output (analytics/audit dashboards).
3. Harden auto-mode resume with persistent run checkpoints and retry/backoff metadata (current version already performs strict runtime artifact integrity validation and discard/regenerate).
4. Add chapter-scope cleanup manifest so interrupted generations can purge generated artifacts and commit traces with stronger determinism.
5. Define a least-privilege path policy for the AI-facing `read_file_chunk` tool, including approved roots and symlink-escape handling (design is still under consideration).

## Known Issues

1. FAISS rollback still depends on in-memory index cloning; for very large indices this may be memory-heavy.
2. Conflict diagnostics now include diff paths and reason labels, but root-cause graphing is still basic.
3. Language guard now has confidence scoring (and excludes known character names), but still uses rewrite fallback as the final correction path.
4. `ENABLE_BUDGET_MONITORING` and related token limits are defined in config but lack actual implementation in LLM clients, offering no cost circuit breakers.
5. Critic fact-review failures currently pass the extracted payload through unchanged even though most semantic contradiction checks were removed from the deterministic memory layer; this needs an explicit user-selectable fail-closed, queue-for-review, or fail-open policy.
6. Empty vector rebuilds can create a FAISS index with the hardcoded 768-dimensional fallback while the current embedding probe saves a different dimension. FAISS/SQLite reconciliation checks counts and IDs but not index dimension versus saved dimension, so it can report healthy and then incorrectly instruct a user of the same model to rebuild again.
7. Embedding fingerprint validation marks the session verified before the probe and comparison succeed. A failed probe or mismatch can therefore be skipped on the next call in the same process; the current fixed-text vector comparison also reports a changed model without distinguishing backend output drift from an actual model change.
8. Automatic vector rebuild can commit an empty index when embedding calls temporarily fail: previously active metadata rows become soft-deleted skipped rows, and reconciliation then reports healthy. Preserve the existing searchable index and active rows until a complete or explicitly approved partial rebuild succeeds.

## Future Plans

1. Add schema-migration preflight backup/verification command before major version bumps.
2. Add an optional AI-assisted language guard that can judge mixed-language prose contextually before deterministic rewrite fallback.
3. Introduce weighted ontology-assisted contradiction scoring for multilingual rules/events.
4. Add a localized human-management CLI for ATT schema 9 identities and episodic memory:
   * List durable Agent identities with memory and indexing statistics.
   * List index failures in human-readable or JSON form, with Agent/status filters.
   * Retry one failed or pending segment, or retry all eligible segments, with an explicit per-segment report.
   * Page through the system history for a selected Agent without truncating provenance.
   * Restore a selected forgotten memory only after explicit confirmation; never offer an implicit bulk restore.
   * Define stable filter arguments and exit codes for empty results, invalid input, partial failure, and complete success.
   * Keep all human output in `i18n/messages/`; read-only commands must not initialize workflow models or make model calls.
