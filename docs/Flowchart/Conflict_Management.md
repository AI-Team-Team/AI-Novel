# Conflict & Integrity Management

This document details the robust mechanisms for artifact validation, interruption recovery, and conflict resolution.

## 1. Deep Interruption Recovery

When starting with `--auto`, the system performs an exhaustive integrity check rather than a simple file-exists check.

```mermaid
flowchart TD
    Start["--auto START COUNT"] --> Loop["For each Chapter N in range"]
    
    Loop --> ValPhys{"1. Physical Validation"}
    ValPhys -- "File missing OR empty" --> Discard["Mark for Regeneration"]
    
    ValPhys -- "Files OK" --> ValSchema{"2. Content Validation"}
    ValSchema -- "Fact JSON schema invalid" --> Discard
    ValSchema -- "Discussion JSONL corrupted" --> Discard
    
    ValSchema -- "Content OK" --> ValDB{"3. DB State Validation"}
    ValDB -- "Commit status != COMPLETED" --> Discard
    ValDB -- "Commit payload missing" --> Discard
    
    ValDB -- "Status COMPLETED" --> Skip["Already Done: Skip Chapter N"]
    
    Discard --> Clean["Purge DB incomplete commits + Delete partial files"]
    Clean --> Regen["Start Generation Loop for Chapter N"]
    
    Regen --> Next["Chapter N + 1"]
    Skip --> Next
```

## 2. Two-Layer Conflict Detection

Conflict detection uses a two-layer architecture to balance efficiency with accuracy.

### Layer 1: Deterministic Checks (memory.py)

Performed during data insertion. Catches exact-match contradictions:

```mermaid
flowchart TD
    Insert["Incoming Fact"] --> TypeCheck{"Fact Type?"}
    
    TypeCheck -- "Character Update" --> StatusCheck{"Status dead→alive?"}
    StatusCheck -- "Yes" --> BlockChar["BLOCKING: status_dead_to_alive"]
    StatusCheck -- "No" --> IdentityCheck{"Identity field change?"}
    IdentityCheck -- "Yes" --> BlockIdent["BLOCKING: identity_field_conflict"]
    IdentityCheck -- "No" --> DeepMerge["Deep Merge & Insert"]
    
    TypeCheck -- "Event" --> DeadRef{"References dead character?"}
    DeadRef -- "Yes" --> FlagNB["NON_BLOCKING: timeline_dead_character_involved"]
    FlagNB --> InsertEvent["Insert event (not blocked)"]
    DeadRef -- "No" --> DedupEvent{"Exact duplicate?"}
    DedupEvent -- "Yes" --> SkipEvent["Skip (return existing ID)"]
    DedupEvent -- "No" --> InsertEvent

    TypeCheck -- "Rule" --> DedupRule{"Exact duplicate?"}
    DedupRule -- "Yes" --> SkipRule["Skip (return existing ID)"]
    DedupRule -- "No" --> InsertRule["Insert rule"]

    TypeCheck -- "Relationship" --> RelCheck{"Type changed?"}
    RelCheck -- "Yes" --> FlagRelNB["NON_BLOCKING: relationship_type_change"]
    FlagRelNB --> KeepRel["Keep existing type"]
    RelCheck -- "No" --> UpsertRel["Upsert relationship"]
```

### Layer 2: LLM Critic Review (workflow.py)

Performed before DB commit in `scan_chapter()`. Catches semantic/logical contradictions:

```mermaid
flowchart TD
    Scanner["Scanner JSON (validated)"] --> Snapshot["Build DB State Snapshot"]
    Snapshot --> CriticCall["LLM Critic: batch review all facts vs state"]
    
    CriticCall -- "LLM fails" --> Passthrough["Facts pass through unchanged"]
    CriticCall -- "Issues found" --> Classify{"Classify each issue"}
    CriticCall -- "No issues" --> Passthrough
    
    Classify -- "BLOCKING" --> Remove["Remove fact from payload"]
    Remove --> QueueBlock["Queue BLOCKING conflict"]
    
    Classify -- "NON_BLOCKING" --> Keep["Keep fact in payload"]
    Keep --> QueueNB["Queue NON_BLOCKING conflict"]
    
    QueueBlock --> Commit["Proceed to DB commit with filtered payload"]
    QueueNB --> Commit
    Passthrough --> Commit
```

## 3. Conflict Triage State Machine

Conflicts are classified to balance automation with narrative safety.

```mermaid
stateDiagram-v2
    [*] --> BLOCKING: Deterministic or Critic BLOCKING
    [*] --> NON_BLOCKING: Deterministic or Critic NON_BLOCKING
    
    state BLOCKING {
        direction TB
        B_Queued: Queued in DB
        B_Gate: Workflow gate check
        B_Queued --> B_Gate
    }

    state NON_BLOCKING {
        direction TB
        NB_Queued: Queued in DB
        NB_Proceed: Commit Proceeds
        NB_Queued --> NB_Proceed
    }

    BLOCKING --> RESOLVED: Manual --resolve-conflict
    BLOCKING --> RESOLVED: auto_resolve (Strict Mode)
    BLOCKING --> RESOLVED: Multi-Agent Debate Panel Consensus
    BLOCKING --> STANDOFF: Multi-Agent Debate Panel Consensus Failure (Fail-Fast)
    
    NON_BLOCKING --> RESOLVED: batch_triage
    
    state RESOLVED {
        direction LR
        keep_existing: Discard incoming
        apply_incoming: Overwrite DB
    }
```

## 4. Multi-Agent Cooperative Debate Conflict Resolution Workflow

When continuous loops (`--auto`) or the CLI flag `--ai-resolve-conflicts` are active, the system automatically spawns a Multi-Agent Debate Panel to resolve blocking conflicts rather than halting immediately or applying simple heuristics.

```mermaid
flowchart TD
    Start["Blocking Conflict Encountered"] --> CheckMode{"AI Debate Enabled?\n(in --auto mode OR --ai-resolve-conflicts)"}
    
    CheckMode -- "No" --> StandardGate{"BLOCKING_CONFLICT_MODE?"}
    StandardGate -- "auto_keep_existing" --> AutoKeep["Auto-resolve via keep_existing"]
    StandardGate -- "manual_block" --> FailImmediate["Raise RuntimeError & Pause"]
    
    CheckMode -- "Yes" --> ContextAssembly["1. Deep Context Window Assembly"]
    
    subgraph Context ["Deep Context Window"]
        C1["Multi-Chapter Prose\n(Ch N-1, Ch N, Ch N+1)"]
        C2["SQLite Character Profiles"]
        C3["SQLite Global Rules"]
        C4["SQLite Last 10 Timeline Events"]
    end
    
    ContextAssembly --> Context
    Context --> DebateLoop["2. Bounded Debate Loop (1 to N Rounds)"]
    
    subgraph Panel ["Debate Panel"]
        Critic["Critic (Historian)\nDefends continuity & rules"]
        Scanner["Scanner (Prose Advocate)\nDefends writer's creative choices"]
        Planner["Planner (Arbitrator)\nModerates debate & summarizes"]
        
        Critic --> Scanner --> Planner
    end
    
    DebateLoop --> Panel
    Panel --> FinalRound{"Round N (Final)?"}
    
    FinalRound -- "Planner decides action" --> ExtractJSON["Parse JSON payload"]
    ExtractJSON --> ValidateConsensus{"Valid Consensus Action?\n(apply_incoming OR keep_existing)"}
    
    ValidateConsensus -- "Yes" --> CommitDB["3. Commit SQLite Transaction & Mark RESOLVED"]
    CommitDB --> LogMD["Write novel/process/discussions/...md transcript"]
    LogMD --> Resume["Resume workflow execution"]
    
    ValidateConsensus -- "No (Standoff)" --> LogStandoff["Write standoff transcript to ...md"]
    LogStandoff --> FailFast["Fail-Fast: Raise RuntimeError & Halt"]
```

## 5. Runtime Integrity Rules

* **Critical Globals**: `world_bible.md`, `plot_outline.md`, and `detailed_plot_outline.md` must be valid. If missing or corrupted, the system fails fast and requests `--start` again.
* **Chapter Artifacts**: If *any* artifact of a chapter (Guide, Text, Facts, Review) is missing or invalid, the entire chapter is treated as incomplete.
