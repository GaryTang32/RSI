**Iteration 1 Overview**: Two candidates tested code-based approaches.

**Results**:
- `code_execution`: **100% accuracy** (12/12), 1491 avg tokens → **FRONTIER BEST**
- `python_answer`: **8.3% accuracy** (1/12), 1396.5 avg tokens → on frontier by cost

**code_execution Success**: Mechanism of writing Python code and extracting from tool execution output proved perfect. All 12 tasks correct because:
- Tasks require deterministic computation (factorials, digit sums, modular arithmetic)
- Python execution is ground truth; LLM only generates code structure
- ANSWER: marker reliably present in stdout

**python_answer Failure**: Tried enforcing LLM text markers (PYTHON_RESULT:) without tool execution. Failed on 11/12 because:
- LLM's mental arithmetic still unreliable (wrong digit sums, wrong modulos in narratives)
- Marker extraction failed; LLM didn't consistently output the marker
- Model couldn't follow structured output protocol reliably (gap between instruction and execution)

**Key Insight**: Removing LLM from computation chain (trust tool output, not text) was essential. Verbosity in prompts (A axis) didn't matter; mechanism (C: execution vs. parsing) was decisive.

**Takeaway for Iter 2**: `code_execution` is saturated at 100%. Next iteration should exploit cost reduction (terse prompts, Axis A+D) and explore robustness on non-numeric tasks (alternative retrieval methods, Axis C+E).

---
