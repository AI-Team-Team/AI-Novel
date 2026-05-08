# Todo

This document only contains In Progress, Known Issues, and Future Plans.

Any resolved issues should not be stored in this document.

## In Progress

1. Strengthen conflict engine causality checks (character status <-> timeline event semantics <-> strict rules) with higher precision and fewer false positives.
2. Add bulk replay for failed commits (dry-run preview, per-commit report, retry policy).
3. Add end-to-end integration tests for write loop + conflict lifecycle + replay recovery + retrieval chain.
4. Tune query-intent classifier and cross-tier alignment thresholds using realistic chapter corpora.
5. Build consumers for `discussion_index.jsonl` and conflict triage output (analytics/audit dashboards).
6. Harden auto-mode resume with persistent run checkpoints and retry/backoff metadata (current version already performs strict runtime artifact integrity validation and discard/regenerate).
7. Add chapter-scope cleanup manifest so interrupted generations can purge generated artifacts and commit traces with stronger determinism.
8. Continue module split of monolithic orchestration/storage files (`workflow.py`, `memory.py`) into stable component layers while preserving behavior (phase 1 done: resume/io/language + schema/conflict_commit extracted; next: tier operation mixins).
9. Harden FAISS reliability paths: batch-safe vector reset persistence, index-load failure metadata reconciliation, and rebuild skipped-row audit retention.
10. **Prompt Fragmentation & Scattered Language Logic:** Move all hardcoded prompts and language-specific strings into a centralized i18n system.

## Known Issues

1. FAISS rollback still depends on in-memory index cloning; for very large indices this may be memory-heavy.
2. Conflict diagnostics now include diff paths and reason labels, but root-cause graphing is still basic.
3. Language guard now has confidence scoring, but still uses rewrite fallback as the final correction path.
4. **Heuristic Conflict Detection Limitations:** Current分词/词频 overlap score may miss logical/causal contradictions.
5. **Language Guard False Positives:** Language guard triggers expensive rewrites if English output contains too many Chinese characters (like character names), reducing efficiency.

## Future Plans

1. Add schema-migration preflight backup/verification command before major version bumps.
2. Add explicit language-ID scoring model before rewrite fallback.
3. Introduce weighted ontology-assisted contradiction scoring for multilingual rules/events.
