# Message Broker & Sibling Routing Flowchart

This document details the registration sequence and P2P routing mechanisms managed by the `MessageBroker`.

## 1. Sequence of Node Registration & Broker Lifecycle

This sequence diagram outlines the creation, registration, and subscription of nodes during the initialization of the Autonomy Suite:

```mermaid
sequenceDiagram
    autonumber
    participant Mixer as AutonomyWorkflowMixin
    participant Broker as Message Broker
    participant Node as Agent Node
    participant Supervisor as Supervisor Agent
    
    Mixer->>Broker: Instantiate MessageBroker()
    Mixer->>Supervisor: Instantiate SupervisorAgent(broker)
    Supervisor->>Broker: Register self as active observer: set_supervisor(self)
    Broker-->>Supervisor: Registered
    
    Mixer->>Node: Instantiate AgentNode(depth=0, broker)
    Node->>Broker: Auto-register: register_node(self)
    Broker->>Broker: Save Node reference in registry map
    Broker-->>Node: Registered & Addressable
```

## 2. P2P Sibling Message Routing Flowchart

This flowchart outlines the logic executed inside `MessageBroker.send` when a node attempts to transmit a message payload:

```mermaid
flowchart TD
    Start["Call send(sender, recipient, msg_type, payload)"] --> CheckSupervisor{"Supervisor exists?"}
    
    CheckSupervisor -- "Yes" --> IsSenderSupervisor{"Is sender == 'Supervisor'?"}
    
    IsSenderSupervisor -- "No (Traffic audit)" --> Audit["Call supervisor.audit_message(sender, recipient, msg_type, payload)"]
    Audit --> RouteToRecipient
    
    IsSenderSupervisor -- "Yes (Prevent recursion loop)" --> RouteToRecipient["Check recipient in registry map"]
    CheckSupervisor -- "No" --> RouteToRecipient
    
    RouteToRecipient --> RecipientExists{"Recipient exists?"}
    
    RecipientExists -- "Yes" --> Deliver["1. Call recipient.receive_message(msg_payload)\n2. Append to recipient.message_inbox"]
    Deliver --> End["Route completed successfully"]
    
    RecipientExists -- "No" --> LogWarning["Log warning: message routing failed (node unregistered)"]
    LogWarning --> End
```
