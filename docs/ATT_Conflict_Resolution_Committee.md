# Multi-Agent Cooperative Debate Conflict Resolver

This document provides a technical reference for the **Multi-Agent Cooperative Debate Conflict Resolver**, a conflict resolution mechanism implemented using the AI Team Team (ATT) framework to resolve semantic and logical contradictions in the novel database.

## 1. Design Paradigm: Dynamic Committee Teams

When a blocking conflict is encountered during chapter scanning (such as character status contradictions or spatiotemporal rule violations), instead of relying on static pre-configured panels or static defaults, the system dynamically convenes a **Conflict Resolution Committee** composed of 3 specialized AI members running as a team under the ATT framework:

1. **Historian_Critic**: The guardian of past continuity. Highly skeptical of changes that contradict existing database facts or established world-building rules (advocates for `keep_existing`).
2. **Prose_Scanner**: The champion of creative directions. Defends the writer's creative prose choices, narrative pacing, and newly scanned character statuses or details (advocates for `apply_incoming`).
3. **Consensus_Planner**: The moderator and arbitrator of the committee. Facilitates discussion over multiple rounds and is responsible for making the final executive resolution in the last round.

## 2. Deep Context Assembly (Multi-Chapter Window)

To enable highly coherent decision-making, the dynamic committee is supplied with a comprehensive, context package assembled by the State Manager:

```plaintext
                  ┌──────────────────────────────┐
                  │   Preceding Chapter Ch N-1   │
                  └──────────────┬───────────────┘
                                 ▼
                  ┌──────────────────────────────┐
                  │    Conflict Chapter Ch N     │
                  └──────────────┬───────────────┘
                                 ▼
                  ┌──────────────────────────────┐
                  │   Succeeding Chapter Ch N+1  │
                  └──────────────────────────────┘
                                 +
  ┌──────────────────────┬──────────────────────┬──────────────────────┐
  │  SQLite Char Profile │  SQLite World Rules  │  SQLite Last 10 Evts │
  └──────────────────────┴──────────────────────┴──────────────────────┘
```

* **Preceding Chapter Prose**: The full markdown text of the chapter *immediately before* the conflict (Chapter $N-1$).
* **Conflict Chapter Prose**: The full text of the chapter containing the scanned conflict (Chapter $N$).
* **Succeeding Chapter Prose**: The full text of the succeeding chapter (Chapter $N+1$) if available.
* **Structured Database Context**:
  * **Focus Character Profile**: MBTI traits, status, and attributes (if a character conflict).
  * **Active Characters Directory**: The status and key traits of all characters in the narrative.
  * **Strict Category Rules**: The active rules in the World Bible.
  * **Last 10 Timeline Events**: The latest timeline logs to trace story progression.

## 3. The Bounded Debate Loop

The discussion runs for exactly $N$ rounds (configured via `conflict_discussion_rounds`, default: 2) inside the dynamically generated AT.

### Round-by-Round Flow ($R < N$)

In each intermediate round, the panel exchanges views sequentially:

1. **Historian_Critic** reviews the context and previous debate history, formulating structural continuity arguments in favor of keeping the existing facts (`keep_existing`).
2. **Prose_Scanner** reviews the context, debate history, and the Critic's newly added argument, defending the creative choices that justify the incoming facts (`apply_incoming`).
3. **Consensus_Planner** reviews the round arguments, synthesizes a summary of the debate, and highlights potential paths towards compromise.

### The Final Round ($R = N$)

In the final round, after concluding arguments, the **Consensus_Planner** is commanded to make the final executive decision. It must output exactly a JSON payload:

```json
{
  "action": "keep_existing" | "apply_incoming",
  "reasoning": "Narrative and logical justification for the consensus...",
  "narrative_compromise": "Suggested prose adjustments or bridge explanation to resolve the narrative mismatch..."
}
```

## 4. Consensus Gating & Fail-Fast Integrity

Narrative safety is strictly prioritized. The system executes a **Fail-Fast Standoff Governance**:

```plaintext
                       ┌──────────────────────┐
                       │  Planner Decision?   │
                       └──────────┬───────────┘
                                  │
                  ┌───────────────┴───────────────┐
                  ▼                               ▼
            [Parsed JSON]                 [Parse Fails/Standoff]
                  │                               │
        ┌─────────┴─────────┐                     ▼
        ▼                   ▼             ┌──────────────┐
["apply_incoming"]  ["keep_existing"]     │  Fail-Fast:  │
        │                   │             │  Stop Loop   │
        ▼                   ▼             │  Raise Error │
  ┌───────────┐       ┌───────────┐       └──────────────┘
  │ DB Update │       │ DB Stays  │
  │  Atomic   │       │ Pristine  │
  └───────────┘       └───────────┘
```

* **Consensus Validation**: The Consensus_Planner's output is strictly validated. The action must be exactly `"apply_incoming"` or `"keep_existing"`.
* **Atomic SQLite Mutative Commit**:
  * On `"apply_incoming"`: The incoming changes are safely applied to the SQLite database under a single transaction.
  * On `"keep_existing"`: The incoming changes are discarded, keeping the existing database state pristine.
* **Fail-Fast Standoff Gating**: If the Planner fails to output a parseable JSON block, selects an invalid option, or cannot reconcile the agents (a standoff), **the conflict remains `PENDING` in the SQLite database and a `RuntimeError` is immediately thrown**, stopping continuous generation. This guarantees that no silent data corruption can slip through.

## 5. Auditable Discussion Logs

Every debate session (regardless of whether it ended in `RESOLVED` or `STANDOFF`) writes its full transcript, context package, reasoning, and committed mutations to a persistent Markdown log:

`novel/process/discussions/conflict_{id}_resolution_discussion.md`

This transcript provides a rich, readable trace for developers and writers to audit the AI panel's logic and compromises.

## 6. How to Configure & Use

### Configuration

Set the discussion round limit in `config.yaml`:

```yaml
workflow:
  conflict_discussion_rounds: 2   # Number of debate rounds (N >= 1)
```

### Usage Modes

1. **Continuous Writing Loop (`--auto`)**:
   Under `--auto`, the debate resolver is **enabled automatically**. Any blocking conflict encountered during scanning or writing will immediately spawn the panel in the background, showing real-time terminal progress indicators.
2. **CLI Flag (`--ai-resolve-conflicts`)**:
   Pass this flag with planning, writing, or continuous tasks to automatically resolve blocking conflicts using AI debate:

   ```bash
   python src/main.py --write 3 --ai-resolve-conflicts
   ```

3. **Manual Override Fallback**:
   If the loop is not continuous, the workflow will block on the conflict until manually resolved via:

   ```bash
   python src/main.py --resolve-conflict <CONFLICT_ID> <keep_existing|apply_incoming>
   ```
