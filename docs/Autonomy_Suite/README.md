# AI Autonomy Suite Documentation

Welcome to the comprehensive technical documentation for the **High-Level AI Autonomy, Hierarchical Dynamic Delegation, and Supervisor Auditor Agent** suite in the AI-Novel generator.

This directory contains deep-dive design guides, parameters, and operation specs for each module of the autonomy system.

## Document Directory

To understand specific systems in detail, please refer to the following documents:

1. **[Hierarchical Dynamic Delegation](Dynamic_Delegation.md)**: Explains the `AgentNode` ReAct execution loop, runtime identity prompts, hard spawning depth boundaries (max depth = 2), and bidirectional parent-child JSON escalation channels.
2. **[Gated Context Protection & File Reading](Gated_Reading.md)**: Details the size-aware `GatedFileReader`, outline sampling fallbacks for files exceeding 50 KB, paginated line chunking, and streaming tail log reads.
3. **[Supervisor Auditor Agent](Supervisor_Agent.md)**: Details the asynchronous non-participating observer agent, accumulated token cost limits ($1.00 USD), sliding-window lexical overlap deadlock analysis, and intervention commands (`INTERJECT_PROMPT`, `EARLY_TERMINATION`, `PRUNE_NODE`).

## Core Architecture Overview

The Autonomy Suite transitions AI agents from passive context-consumers to active searchers and coordinators. It is built on a highly decoupled **Broker-Node Event Architecture**:

```plaintext
                    ┌──────────────────────────────┐
                    │       Supervisor Agent       │
                    └──────────────┬───────────────┘
                                   │ Audits Message Bus
                                   ▼
                    ┌──────────────────────────────┐
                    │        Message Broker        │
                    └──────────────┬───────────────┘
                                   │ Routes Messages
                     ┌─────────────┴─────────────┐
                     ▼                           ▼
        ┌────────────────────────┐   ┌────────────────────────┐
        │   Agent Node (Child)   │   │   Agent Node (Child)   │
        └────────────────────────┘   └────────────────────────┘
```
