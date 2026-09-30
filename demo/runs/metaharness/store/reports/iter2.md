# Iteration 2: Code Execution Breakthrough

**What changed**: 
- `clean_extraction`: multi_method with robust ANSWER: line parsing + markdown cleanup
- `code_execution`: LLM generates Python code → harness executes via tools.python() → parses stdout

**Score & cost**:
- **code_execution: 100% accuracy (12/12), 4,501 tokens/task** — NEW BEST, sole frontier point
- clean_extraction: 25% (3/12), 12,374 tokens — dominated
- All iter1 candidates off frontier

**Failure pattern root cause**:
- clean_extraction still parsed LLM text; extraction/markdown cleanup didn't fix core errors
- Arithmetic was computed correctly by model but output in ambiguous format (print statements, wrapped markdown)
- Extraction heuristics (ANSWER: regex, stripping **) fragile

**Why code_execution succeeds**:
- **Mechanism shift**: Push computation to deterministic tool execution, not LLM text parsing
- LLM writes Python code → tools.python() deterministically executes → stdout has clean "ANSWER: 144" 
- Two-turn verification (generate + alternative method) ensures correctness by execution, not reasoning
- No extraction ambiguity: regex finds "ANSWER: (\d+)" in stdout

**Frontier dynamics**:
- Iter1: multi_method (25%) cleared seed (8.3%) and guided_format (16.7%)
- Iter2: code_execution (100%) clears all prior candidates; hypervolume jumped 9,371

**Takeaway for iter3**:
- Axis F (tool usage) transformed the frontier
- Accuracy is now maxed; cost varies 3.1k–9.8k tokens/task across 12 units
- Next: exploit cost reduction (Axis E: single-pass vs. two-pass?); explore pattern matching (Axis C: templates for common problem types?)

---
