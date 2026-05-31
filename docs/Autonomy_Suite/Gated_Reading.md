# Gated Context Protection & File Reading Specification

This document details the operational behavior, parameters, and slice boundaries of the **Gated File Reading** system.

## 1. Context Protection Paradigm

During autonomous ReAct reasoning runs, agents frequently need to query historic text drafts, large discussion indexes (`discussion_index.jsonl`), or detailed `.log` files. Direct, un-gated file reads represent a severe risk:

* **Context Pollution**: Dumping a 300 KB log into the prompt context leaves no room for system instructions.
* **Hallucination**: Massive noise causes the model to ignore strict narrative boundaries.
* **Token Inflation**: Massive context blocks incur high financial and latency costs.

The **GatedFileReader** solves this by enforcing size pre-filtering and paginated chunk access.

## 2. Gated Size Pre-filtering & Outline Fallbacks

When an agent invokes `read_file_chunk` (or when a process attempts a file read) on a target file:

* **Size Gate**: If the file size is $\le$ `large_file_threshold_kb` (default: 50 KB), the read proceeds normally.
* **Blocked Gate**: If the file exceeds `large_file_threshold_kb` and no specific `end_line` coordinate is supplied by the agent:
  1. The system blocks the read.
  2. It counts the total line count of the file in $O(N)$ speed.
  3. It extracts a sample of the first 5 lines of the target file.
  4. It constructs and returns a structured **Outline Warning**:

     ```markdown
     ### LARGE FILE WARNING
     - **File**: discussion_index.jsonl
     - **Size**: 120.4 KB (Exceeds threshold of 50 KB)
     - **Total Lines**: 1250
     
     Direct reading of large files is gated to protect the context window.
     Please read specific parts using the paginated `read_file_chunk(path, start_line, end_line)` tool.
     
     **First 5 Lines Sample**:

     1: {"log_id": "001", "timestamp": "...", "phase_type": "world", ...}
     2: {"log_id": "002", "timestamp": "...", "phase_type": "plot", ...}
     3: ...
     ```

  5. The agent must review the outline and formulate a paginated chunk request.

## 3. Paginated Line Chunking (Slicing Caps)

To read parts of a large file, the agent must supply precise coordinates (`start_line` and `end_line`). Slicing is governed by two boundaries:

1. **Maximum Chunk Size**: Single requests are strictly capped at `max_chunk_lines` (default: 100 lines).
2. **Auto-Cap Logic**:
   * If the requested window $(end\_line - start\_line + 1)$ exceeds `max_chunk_lines`, the system automatically shrinks the window:
     $$end\_line = start\_line + max\_chunk\_lines - 1$$
   * If the agent omits the `end_line`, the system defaults to:
     $$end\_line = start\_line + max\_chunk\_lines - 1$$

## 4. Tail Logs & Active Stream Operations

For continuous logs (`novel_generation.log`) or discussion indexes, the agent frequently needs to inspect only the latest events rather than the initial ones:

* **Tool**: `read_file_tail(path, line_count)` (default: 50 lines).
* **Logic**: Counts total lines in the file and returns a slice starting at:
  $$start\_line = \max(1, total\_lines - line\_count + 1)$$
  $$end\_line = total\_lines$$

## 5. Public Python Class Interface: `GatedFileReader`

```python
class GatedFileReader:
    def __init__(self, large_threshold_kb: int = 50, max_chunk: int = 100):
        """
        Initializes the gated reader with size limits and maximum line return caps.
        """
        self.large_threshold_kb = large_threshold_kb
        self.max_chunk = max_chunk
        ...

    def read_file(self, path: str, start_line: int = 1, end_line: Optional[int] = None) -> str:
        """
        Reads a file. Returns an Outline Warning if the file is larger than threshold 
        and no end_line coordinate is provided.
        Otherwise, returns a line-numbered paginated chunk capped at max_chunk lines.
        """
        ...

    def read_file_tail(self, path: str, line_count: int = 50) -> str:
        """
        Calculates file offset and returns only the last line_count lines of a file.
        """
        ...
```
