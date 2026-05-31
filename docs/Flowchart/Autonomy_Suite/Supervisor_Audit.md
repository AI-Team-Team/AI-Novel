# Supervisor Agent Audits & Interventions Flowchart

This document details the audit channels, cost boundaries, and lexical overlap checks executed by the non-participating `SupervisorAgent`.

## 1. Broker Message Auditing Logic Flowchart

This flowchart outlines the sequence executed on every message audit step inside the Supervisor:

```mermaid
flowchart TD
    Start["Call audit_message(sender, recipient, msg_type, payload)"] --> RecordHistory["Append message details to discussion_history list"]
    
    RecordHistory --> BudgetCheck{"Is ENABLE_BUDGET_MONITORING True?"}
    
    BudgetCheck -- "Yes" --> ExtractCost["Get 'estimated_cost' from payload (default: 0.0)"]
    ExtractCost --> AddAccumulated["Accumulate cost:\nself.accumulated_cost += estimated_cost"]
    
    AddAccumulated --> BudgetLimit{"self.accumulated_cost exceeds self.budget_limit_usd?\n(default: $1.00)"}
    
    BudgetLimit -- "Yes" --> TriggerTerm["Call trigger_intervention('EARLY_TERMINATION', recipient, reason)"]
    TriggerTerm --> TerminateNode["1. Dispatch EARLY_TERMINATION to target via Broker\n2. Roll back stagings\n3. Raise RuntimeError"]
    
    BudgetLimit -- "No" --> DebateCheck
    BudgetCheck -- "No" --> DebateCheck{"Is msg_type == 'debate_round_argument'?"}
    
    DebateCheck -- "Yes" --> ExtractTurns["Filter discussion_history for 'debate_round_argument'\nSelect the last 3 debate turns (A1, A2, A3)"]
    ExtractTurns --> TurnCountCheck{"3 turns exist?"}
    
    TurnCountCheck -- "Yes" --> RunDeadlockCheck["Run Lexical Overlap Deadlock Analyzer on A1, A2, A3"]
    RunDeadlockCheck --> DeadlockFound{"Is circular deadlock detected?\n(Both overlaps exceed 75%)"}
    
    DeadlockFound -- "Yes" --> TriggerInterject["Call trigger_intervention('INTERJECT_PROMPT', recipient, reason)"]
    TriggerInterject --> DeliverInterpose["Dispatch INTERJECT_PROMPT override payload\nto force compromise synthesis on next turn"]
    
    DeadlockFound -- "No" --> End["Audit completed (No intervention needed)"]
    TurnCountCheck -- "No" --> End
    DebateCheck -- "No" --> End
```

## 2. Lexical Overlap Deadlock Analyzer Algorithm

This flowchart visualizes the exact mathematical word token check used to detect circular debates:

```mermaid
flowchart TD
    StartCheck["Analyze last 3 arguments (A1, A2, A3)"] --> Tokenize["For each argument An:\n1. Lowercase\n2. Strip punctuation\n3. Split words\n4. Discard short words (length <= 4)"]
    
    Tokenize --> Sets["Assemble token sets: Tokens_1, Tokens_2, Tokens_3"]
    
    Sets --> EmptyCheck{"Are any sets empty?"}
    EmptyCheck -- "Yes" --> ReturnFalse["No deadlock (return False)"]
    
    EmptyCheck -- "No" --> CalcOverlap12["Calculate intersection:\nIntersection_1_2 = Tokens_1 ∩ Tokens_2\nRatio_1_2 = |Intersection_1_2| / min(|Tokens_1|, |Tokens_2|)"]
    
    CalcOverlap12 --> CalcOverlap23["Calculate intersection:\nIntersection_2_3 = Tokens_2 ∩ Tokens_3\nRatio_2_3 = |Intersection_2_3| / min(|Tokens_2|, |Tokens_3|)"]
    
    CalcOverlap23 --> GateCheck{"Ratio_1_2 exceeds 0.75 AND Ratio_2_3 exceeds 0.75?"}
    
    GateCheck -- "Yes" --> ReturnTrue["Circular deadlock detected! (return True)"]
    GateCheck -- "No" --> ReturnFalse
```
