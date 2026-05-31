# Supervisor Auditor Agent Specification

This document details the operational behavior, decision rules, and intervention logic of the **Supervisor Agent (Auditor & Observer)**.

## 1. Design Paradigm: The Non-Participating Observer

Multi-agent collaborative loops run the risk of narrative drift, circular deadlock debates, and high API token inflation. The **Supervisor Agent** operates as an asynchronous, non-participating auditor registered on the `MessageBroker`.

* It **subscribes** to and intercepts all broker message traffic.
* It **never** writes story prose or directly debates lore details.
* It **polices** system costs, limits thread spawning, and breaks logical deadlocks through control interventions.

## 2. Resource Cost Budgeting & Gating

Every ReAct step or debate turn routed through the broker reports an estimated cost:

* When `enable_budget_monitoring` is active, the Supervisor accumulates these estimates:
  $$\text{AccumulatedCost} = \sum \text{TurnEstimatedCost}$$
* **Threshold Violation**: If the accumulated cost exceeds `total_token_budget_usd` (default: `$1.00`), the Supervisor fires an immediate `EARLY_TERMINATION` override signal, rolls back the staged SQLite transaction, and raises a `RuntimeError` to prevent runaway charges.

## 3. Circular Debate Deadlock Analysis

If sibling agents get locked in repetitive arguments (e.g. arguing character status or timeline points over and over), the Supervisor intercedes using **Lexical Overlap Analysis** across a sliding 3-turn debate window:

### Step 1: Text Tokenization & Noise Filtering

For the last three debate arguments ($A_1, A_2, A_3$), the text is lowercased, punctuation is stripped, and words are split into token sets. Short noise words (length $\le 4$ characters) are discarded:
$$Tokens_{n} = \{ w \in \text{Words}(A_n) \mid \text{len}(w) > 4 \}$$

### Step 2: Lexical Overlap Score Computation

The intersection ratio between consecutive turns is calculated as:
$$\text{OverlapRatio}_{1,2} = \frac{|Tokens_1 \cap Tokens_2|}{\min(|Tokens_1|, |Tokens_2|)}$$
$$\text{OverlapRatio}_{2,3} = \frac{|Tokens_2 \cap Tokens_3|}{\min(|Tokens_2|, |Tokens_3|)}$$

### Step 3: Deadlock Gating

* **Threshold**: If both $\text{OverlapRatio}_{1,2} > 0.75$ and $\text{OverlapRatio}_{2,3} > 0.75$ (indicating that $>75\%$ of the key narrative points are being repeated across 3 turns), the Supervisor flags a **Circular Debate Deadlock**.
* **Intervention**: The Supervisor intercepts the broker traffic and issues an `INTERJECT_PROMPT` command.

## 4. Supervisor Intervention Commands

| Command | Action and Purpose | Output Effect |
| :--- | :--- | :--- |
| **`INTERJECT_PROMPT`** | Injects an overriding, high-priority prompt into the next round of discussions to correct agent drift or circular deadlocks. | Forces the Planner to synthesize a creative compromise in the very next turn and close the session. |
| **`EARLY_TERMINATION`** | Forces immediate shutdown of the active agent team due to budget exhaustion or stack violations. | Aborts staged changes, rolls back database transactions, and raises a `RuntimeError` to halt loops. |
| **`PRUNE_NODE`** | Shuts down redundant child subagents that have finished their assigned tasks. | Safely frees system memory, garbage-collects node scopes, and closes database stage handles. |

## 5. Public Python Class Interface: `SupervisorAgent`

```python
class SupervisorAgent:
    def __init__(self, broker: Any, budget_limit_usd: float = 1.00):
        """
        Initializes the Supervisor and registers itself as the active observer 
        on the registered MessageBroker instance.
        """
        self.broker = broker
        self.budget_limit_usd = budget_limit_usd
        self.accumulated_cost = 0.0
        self.discussion_history: List[dict] = []
        ...

    def audit_message(self, sender: str, recipient: str, msg_type: str, payload: dict):
        """
        Asynchronously intercepts and audits routed messages.
        Enforces budget gates and runs the sliding-window lexical overlap deadlock analyzer.
        """
        ...

    def trigger_intervention(self, command: str, target: str, reason: str):
        """
        Dispatches an overriding supervisor intervention command 
        to the target AgentNode via the MessageBroker.
        """
        ...
```
