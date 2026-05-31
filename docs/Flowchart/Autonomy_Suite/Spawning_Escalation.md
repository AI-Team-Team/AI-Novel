# Spawning & Escalation Channels Flowchart

This document details the control flow of the hierarchical subagent spawning tree and the bidirectional JSON-based escalation process.

## 1. AgentNode Spawning Gate Flowchart

This flowchart outlines the logic executed when an agent node attempts to spawn a subagent:

```mermaid
flowchart TD
    Start["Call spawn_child(name, role)"] --> GetDepth["Get current node depth (D)"]
    
    GetDepth --> DepthCheck{"Is D less than max_depth?\n(max_depth = 2)"}
    
    DepthCheck -- "Yes" --> CreateNode["1. Create child AgentNode\n2. Set depth = D + 1\n3. Set parent = current\n4. Register child in Broker"]
    CreateNode --> ReturnNode["Return child AgentNode"]
    
    DepthCheck -- "No" --> LogWarning["Log warning: Spawning depth limit hit"]
    LogWarning --> CheckParent{"Parent exists?"}
    
    CheckParent -- "Yes" --> AssembleEscalation["Assemble structured JSON payload:\n- type: spawn_subagent\n- objective: subagent task\n- target: current node name\n- depth: D"]
    AssembleEscalation --> SendBroker["Dispatch 'delegate_escalation'\nmessage to parent via Broker"]
    SendBroker --> RaiseError["Raise RuntimeError\n(Halts current grandchild spawning execution)"]
    
    CheckParent -- "No" --> RaiseError
```

---

## 2. Parent-Proxy Escalation Processing Sequence

This sequence diagram illustrates how a Grandchild (Depth 2) node escalates a task to a Child (Depth 1) node, which acts as a proxy:

```mermaid
sequenceDiagram
    autonumber
    participant Grandchild as Grandchild Node (Depth 2)
    participant Child as Child Node (Depth 1)
    participant Broker as Message Broker
    
    Grandchild->>Broker: Call spawn_child(...)
    Note over Grandchild: Depth limit reached!
    Grandchild->>Broker: Send delegate_escalation message
    Broker->>Child: Deliver escalation payload (objective, target)
    
    Note over Child: Receives escalation payload
    Child->>Broker: spawn_child(Sibling_Researcher, task)
    Note over Child, Sibling: Sibling is spawned at Depth 2 under Child
    Broker-->>Child: Sibling Node active
    Child->>Broker: Execute Sibling Node Task
    Note over Sibling: Runs ReAct tool loop
    Sibling-->>Child: Return research results
    
    Child->>Broker: Send result payload back to Grandchild
    Broker->>Grandchild: Deliver inbox payload
    Note over Grandchild: Grandchild reads results from inbox and continues!
```
