# Hierarchical Dynamic Delegation Specification

This document details the lifecycle, execution protocol, and spawning gates of the **Hierarchical Dynamic Delegation** framework.

## 1. AgentNode Lifecycle & Spawning Depth Limits

Every autonomous task or research query spawns an `AgentNode` inside a tree structure. To prevent infinite model recursion, runaway token usage, and stack overflows, spawning is strictly gated at a maximum depth of 2.

* **Depth 0 (Root)**: Core system workflow agents (e.g. Writer, Planner).
* **Depth 1 (Child)**: Spawned by Depth 0 to research a specific domain or compile context (e.g., Timeline Auditor, Backstory Researcher).
* **Depth 2 (Grandchild)**: Spawned by Depth 1 to run micro-specialized validations (e.g., Character status checkers, MBTI profiles).
* **Depth 3+ (Blocked)**: Spawning is strictly blocked. If a Depth 2 node attempts to spawn a subagent, the system triggers the **Escalation Protocol**.

## 2. Bidirectional Escalation Channel

When a Depth 2 Grandchild agent determines that it needs further automated delegation to fulfill its task:

1. It is blocked from spawning a subagent.
2. It constructs a structured JSON request detailing the target objective and rationale:

   ```json
   {
     "request_type": "spawn_subagent",
     "name": "Subagent_Name",
     "role": "Subagent_Role",
     "target": "Grandchild_Node_Name",
     "objective": "Task details to be delegated...",
     "rationale": "I am at max depth level 2 and cannot spawn subagents myself; I require this domain-specific audit."
   }
   ```

3. It dispatches this escalation message upward to its parent Child node (Depth 1) via the `MessageBroker`.
4. The parent proxy agent intercepts the request, runs the tool or spawns a peer sibling under itself, collects the results, and returns them back down to the Grandchild node, keeping the stack flat.

## 3. Runtime Identity & Role Discipline

To prevent conversational drift and maintain role consistency across hierarchical teams, every agent is injected with a **Runtime Identity Profile Header** prepended to its system prompts. This guarantees that the agent understands its position, boundaries, sibling peers, and constraints:

```markdown
## AGENT IDENTITY PROFILE
- **Role Name**: Timeline Integrity Auditor
- **Agent Node Name**: Auditor_Node_02
- **Parent Agent**: Backstory_Researcher_01
- **Active Sibling Peers**: MBTI_Specialist, Location_Tracker
- **Depth Level**: 2 / 2 [RECURSIVE SPAWN BLOCKED]
- **Current Objective**: Check Chapter 4 draft timeline consistency and compile SQLite relationships.
```

## 4. Bounded ReAct Execution Loop

Tasks are resolved inside a structured **Reasoning & Action (ReAct)** loop. The loop alternates between `Thought`, `Action` (tool call), and `Observation` until a `Final Answer` is reached or the step limit is hit (default: 5).

### Prompt Sequence Protocol

1. **System Instruction**: Injects instructions for ReAct formatting:
   * **Format Option 1 (Tool Call)**:

     ```text
     Thought: Analyzing the characters table schema.
     Action: {"tool": "query_sqlite", "arguments": {"sql_command": "SELECT name, status FROM characters"}}
     ```

   * **Format Option 2 (Final Answer)**:

     ```text
     Thought: The character Iris is dead in the DB but alive in the draft.
     Final Answer: Timeline conflict found: Iris is dead, contradiction exists.
     ```

2. **Turn Registry**: Each action returns an `Observation` formatted as:

   ```text
   Observation: [('Iris', 'dead'), ('Bob', 'alive')]
   ```

## 5. Public Python Class Interface: `AgentNode`

```python
class AgentNode:
    def __init__(
        self,
        name: str,
        role: str,
        depth: int,
        parent: Optional['AgentNode'] = None,
        max_depth: int = 2,
        llm_client: Optional[Any] = None,
        tools: Optional[Dict[str, Any]] = None,
        broker: Optional[Any] = None
    ):
        """Initializes the agent node and registers it on the shared MessageBroker."""
        ...

    def spawn_child(self, name: str, role: str, llm_client: Optional[Any] = None) -> 'AgentNode':
        """
        Spawns a child agent at depth + 1.
        Raises RuntimeError and escalates JSON payload if depth exceeds max_depth.
        """
        ...

    def execute_task(self, task: str, max_steps: int = 5) -> str:
        """
        Runs the bounded ReAct reasoning and tool execution loop.
        Returns the string block following 'Final Answer:' or error fallback.
        """
        ...

    def receive_message(self, message: dict):
        """Appends incoming broker payloads to the inbox queue."""
        ...
```

## 6. Bound Autonomy Tools

Agents are dynamically equipped with a map of bound Python functions matching their roles:

* **`query_sqlite(sql_command: str) -> str`**: Strictly staged schema-read queries.
* **`search_faiss(query_text: str, limit: int = 3) -> str`**: Semantic searches over Tier 3 vector memory.
* **`read_file_chunk(path: str, start_line: int, end_line: int) -> str`**: Paginated file slice reads (gated context protection).
* **`read_file_tail(path: str, line_count: int) -> str`**: Last `line_count` lines of logs or active streams.
* **`dispatch_subagent(name: str, role: str, task: str) -> str`**: Spawns and executes a child subagent research task.
* **`delegate_escalation(objective: str, rationale: str) -> str`**: For Depth 2 nodes to escalate requests upward.
