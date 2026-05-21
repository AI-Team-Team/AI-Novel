# Memory & Retrieval System

This document details the multi-tier retrieval funnel and the atomic commit/rollback mechanism used to ensure narrative consistency.

## 1. Context Retrieval Funnel (The Narrowing Funnel)

The system avoids "context pollution" by filtering and ranking facts through a 5-step pipeline.

```mermaid
flowchart TD
    Start[Request Context: Task Type + State] --> Intent{1. Intent Classification}
    
    Intent -- continuity_build --> Depth[Low Recall: Focus on recent entities]
    Intent -- continuity_guard --> Depth[High Recall: Broad entity/conflict scan]
    
    Depth --> SQLite[2. SQLite Pre-filter]
    SQLite -- "Fetch T1 Rules + T2 Chars/Events" --> Semantic[3. FAISS Semantic Search]
    
    Semantic -- "Fetch T3 Lore/Atmosphere" --> Align{4. Cross-Tier Alignment}
    
    Align -- "Strict mode: drop hits mentioning dead characters" --> Rerank["5. Semantic Reranking"]
    Align -- "Non-strict mode: all hits pass through" --> Rerank
    
    Rerank -- "Score = Similarity + Token Overlap Bonus" --> Final["Final Context Package"]
```

### Reranking Heuristics

* **Entity Bonus**: +0.35 score for each focus entity token match.
* **Location Bonus**: +0.50 score for exact location match.

## 2. Atomic Chapter Commit (Scanner ↔ Memory)

Ensures that "dirty" facts from a bad scan don't corrupt the long-term memory.

```mermaid
sequenceDiagram
    autonumber
    participant W as WorkflowManager
    participant C as Critic LLM
    participant M as MemoryManager
    participant DB as SQLite
    participant V as FAISS Index
    participant CQ as Conflict Queue

    W->>W: Scanner extracts JSON + Schema validation
    W->>C: _critic_review_extracted_facts(facts, DB state)
    C-->>W: Issues list (BLOCKING/NON_BLOCKING)
    W->>W: Remove BLOCKING facts from payload
    W->>CQ: Queue all issues as conflicts

    W->>M: begin_batch()
    M->>DB: BEGIN TRANSACTION
    M->>V: faiss.clone_index(current_index)
    Note over M, V: Snapshot created for safety

    W->>M: apply_fact_payload(filtered JSON)
    
    loop Per Fact (Rule/Char/Event)
        M->>M: Deterministic checks only
        alt BLOCKING Conflict (e.g. dead→alive)
            M->>CQ: Queue conflict
        else Safe or NON_BLOCKING
            M->>DB: Staging write
        end
    end

    alt All Safe
        W->>M: end_batch(success=True)
        M->>DB: COMMIT
        Note over M, V: Discard Snapshot
        M-->>W: Status: COMPLETED
    else Error
        W->>M: end_batch(success=False)
        M->>DB: ROLLBACK
        M->>V: Restore index from Snapshot
        M-->>W: Status: FAILED
    end
```

## 3. Conflict Detection

Conflict detection uses a two-layer approach:

### Layer 1: Deterministic Checks (memory.py)

* **Character Status Guard**: Blocks dead→alive status change (`BLOCKING`). Protects immutable identity fields.
* **Timeline Dead Character Flag**: Events mentioning dead characters are inserted but flagged `NON_BLOCKING` for Critic review.
* **Exact Deduplication**: Rules and events with identical payloads return existing ID without re-insert.
* **Relationship Type Change**: Queued as `NON_BLOCKING` conflict, existing type preserved.

### Layer 2: LLM Critic Review (workflow.py)

Performed before DB commit via `_critic_review_extracted_facts()`:

* **Input**: Extracted facts + DB state snapshot (characters, strict rules, recent events) + chapter text.
* **Detection**: Semantic/logical contradictions (strict rule violations, causal impossibilities, dead character active participation vs memorial).
* **Output**: `BLOCKING` facts removed from payload; `NON_BLOCKING` facts kept; all issues queued as conflicts.
