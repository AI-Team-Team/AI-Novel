# Database Management Committee

The Database Management Committee (DMC) is a configurable pre-write governance layer. It can audit both direct SQL requested by ATT agents and ordinary story-state mutations performed through `MemoryManager`.

## Decision contract

The committee contains three explicitly model-routed ATT members:

1. `Security_Officer` checks authorization, injection risk, and destructive effects.
2. `Schema_Auditor` checks SQLite schema constraints and SQLite/FAISS consistency.
3. `Transaction_Planner` is the designated decision maker.

Only the final `Transaction_Planner` response is accepted. It must be strict JSON:

```json
{"approved": true, "reason": "concise explanation"}
```

Text produced by the other members cannot accidentally approve or reject an operation. An invalid response or committee execution error is handled by `database_audit.failure_policy`; the default is `deny`.

## Configurable coverage

Configure governance in `config.yaml`:

```yaml
database_audit:
  enabled: true
  failure_policy: "deny"  # deny or allow
  failure_policies:       # optional per-scope overrides
    maintenance: "allow"
  scopes:
    att_sql: true
    chapter_fact_batches: true
    commit_replay: true
    conflict_resolution: true
    conflict_queue_writes: false
    character_writes: false
    relationship_writes: false
    world_rule_writes: false
    timeline_event_writes: false
    vector_writes: false
    revision_writes: false
    chapter_commit_metadata: false
    schema_metadata: false
    maintenance: false
```

The default profile audits high-value transaction boundaries while avoiding one model committee call per row. Users who require maximum governance can enable every scope. Users who trust a specific internal path can disable only that path without disabling the committee globally.

| Scope | Operations |
| --- | --- |
| `att_sql` | SQL submitted through the ATT `query_sqlite` tool |
| `chapter_fact_batches` | World seed and scanner fact batches before any story write |
| `commit_replay` | Replay of a persisted failed chapter commit |
| `conflict_resolution` | `keep_existing` and `apply_incoming` decisions before mutation |
| `conflict_queue_writes` | Creation or update of conflict queue rows |
| `character_writes` | Character insert/update operations |
| `relationship_writes` | Relationship insert/update operations |
| `world_rule_writes` | World-rule inserts |
| `timeline_event_writes` | Timeline-event inserts |
| `vector_writes` | FAISS plus vector-metadata inserts |
| `revision_writes` | Before/after rows written to `fact_revisions`; a rejection also rolls back the associated standalone fact mutation |
| `chapter_commit_metadata` | Chapter commit lifecycle rows |
| `schema_metadata` | `schema_meta` updates, including embedding metadata |
| `maintenance` | Vector reset and rebuild operations |

Scopes are independent. For example, a deployment may audit chapter batches and conflict resolution but trust schema metadata, or enable row scopes for character and rule writes while leaving vector writes unaudited.

Three practical profiles are supported without code changes:

* **Balanced (default)**: keep transaction-boundary scopes enabled and row scopes disabled. This gives the committee the full world-seed/scanner/replay payload once per batch.
* **Maximum story-database governance**: enable every listed scope. This audits direct ATT SQL, semantic transaction boundaries, every public fact/vector mutation, generated revision rows, conflict queue changes, commit/schema metadata, and maintenance. Startup schema bootstrap and ATT's separate state database remain trusted exclusions.
* **Selective governance**: enable only the tables or workflows that need approval. `failure_policies` can independently make critical scopes fail closed and less critical scopes fail open if the committee is unavailable.

`failure_policy` supplies the default behavior when a committee call fails or returns invalid JSON. `failure_policies` can override that behavior for individual scopes. A strict deployment can leave every scope on `deny`; a deployment may, for example, keep story writes fail-closed while allowing a non-critical maintenance audit outage.

## Enforcement order

Audits execute before the governed mutation. A rejection raises `PermissionError`; the target write has not yet run. Batch rejection occurs before `begin_batch()`. A row-level rejection inside a larger transaction propagates and causes the caller to roll back the batch. Revision audits run after an exact before/after image has been staged but before commit; rejection explicitly rolls back a standalone parent mutation.

Conflict resolution is atomic: applying the incoming entity value and changing the conflict queue row to `RESOLVED` use one SQLite/FAISS-aware batch. Enabling both `conflict_resolution` and `conflict_queue_writes` intentionally requires approval for both the semantic decision and the queue-state mutation.

Scanner and replay success metadata is also written inside the same batch as the associated facts. If a `chapter_commit_metadata` audit or SQLite commit fails, the fact rows, revision rows, vector changes, and `COMPLETED` transition roll back together.

The committee receives the complete JSON payload rather than row counts, and its ATT team is reused across operations in the same manager lifecycle. This preserves meaningful audit context without creating one persistent team per row. The committee team has no ATT tools, preventing recursive self-audit; approved non-query SQL submitted through `query_sqlite` is explicitly committed, while execution errors roll back the standalone SQL transaction.

The DMC is deliberately not recursive: its own ATT state database is managed by ATT persistence and is separate from the story `MemoryManager` database.

Initial SQLite schema bootstrap happens before ATT and the DMC exist and is therefore a trusted startup operation. After bootstrap, all application-level story mutations are represented by the selectable scopes above.
