# Chapter Generation Workflow

This document details the iterative process of planning, writing, and scanning, including the automated "Guard" systems.

## 1. Chapter Planning Loop (Planner ↔ Critic ↔ Memory)

```mermaid
sequenceDiagram
    autonumber
    participant W as WorkflowManager
    participant M as Memory (Funnel)
    participant P as Planner
    participant C as Critic

    W->>M: build_context_package(chapter_n)
    M-->>W: Aligned Context (Rules + Chars + History)

    W->>P: generate_chapter_guide(Context + Plot Frames)
    P-->>W: Draft (Markdown)

    loop Guide Discussion (config.CHAPTER_GUIDE_DISCUSSION_ROUNDS)
        W->>C: review_guide(draft)
        C-->>W: Critique
        W->>P: revise_guide(draft, critique)
        P-->>W: Revised Guide
    end
    W->>W: Save chapter_n_guide.md
```

## 2. Chapter Writing & Language Guard

Every output from an LLM agent is passed through a language validator before being accepted.

```mermaid
flowchart TD
    Write["Writer: Generate Prose"] --> Names["Get known character names from DB"]
    Names --> Guard{"_enforce_output_language"}
    
    Guard -- "Exclude names → Compute CJK/Latin ratio" --> Check{"Confidence is greater than or equal to Threshold?"}
    
    Check -- "Yes" --> Accept["Accept Prose"]
    
    Check -- "No (CJK greater than 30% after name exclusion)" --> Rewrite["Log Warning: Language Guard Triggered"]
    Rewrite --> LLM_Rewrite["LLM: Specialized Rewrite Task"]
    LLM_Rewrite -- "Keep structure, translate to Target Language" --> Accept
    
    Accept --> Save["Save chapter_n.md"]
    Save --> Review["Critic: Review and Revise Chapter"]
```

## 3. Review, Scan & Commit

The final stage ensures the written text is converted back into facts and committed to memory.

```mermaid
flowchart TD
    Review["Critic Review"] --> NeedsRev{"Needs Revision?"}
    NeedsRev -- "Yes" --> Writer["Writer: Apply Patch"]
    Writer --> Review
    
    NeedsRev -- "No" --> Scan["Scanner: Extract Facts (JSON)"]
    Scan --> Validate{"Schema Validation"}
    Validate -- "Invalid" --> SaveRaw["Persist invalid payload for debug"]
    SaveRaw --> Error["RuntimeError"]
    
    Validate -- "Valid" --> CriticReview["Critic: Batch Fact Review vs DB State"]
    CriticReview --> Filter{"Issues found?"}
    Filter -- "BLOCKING issues" --> Remove["Remove BLOCKING facts from payload"]
    Remove --> Queue1["Queue BLOCKING conflicts"]
    Filter -- "NON_BLOCKING issues" --> Queue2["Queue NON_BLOCKING conflicts (facts kept)"]
    Filter -- "No issues / LLM failure" --> Commit
    Queue1 --> Commit{"Memory: Chapter Commit"}
    Queue2 --> Commit
    
    Commit -- "COMPLETED" --> End(("Next Chapter"))
    
    Commit -- "FAILED" --> FailedCommit["Record in failed_commits table"]
    FailedCommit --> User["User Resolution Required"]
    
    User -- "--replay-commit ID" --> Replay["WorkflowManager: Replay Logic"]
    Replay --> Commit
```
