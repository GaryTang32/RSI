Looking at the evolution history, I can see that **code_first** and **code_answer_marked** have both reached 100% accuracy on numeric tasks at 1295.7 context cost. This suggests we're at a strong frontier, but the question is how to improve robustness for the unseen task types (dates, text manipulation, lists, number theory).

Let me read key traces and formulate hypotheses.
<function_calls>
<invoke name="bash">
<parameter name="tool_name">bash</parameter>
<parameter name="command">cd /home/user/RSI && head -100 history/candidates/code_first/eval/search/traces/evolve-numeric-005.jsonl && echo "---" && head -100 history/candidates/code_first/eval/search/traces/evolve-numeric-002.jsonl</parameter>
</invoke>
</function_calls>
<function_calls>
<invoke name="bash">
<parameter name="tool_name">bash</parameter>
<parameter name="command">cd /home/user/RSI && find history/candidates -name "*.py" -type f | head -5 | xargs -I {} wc -l {}</parameter>
</invoke>
</function_calls>

## Analysis & Hypotheses

**Observation 1**: Both frontier systems (code_first, code_answer_marked) mandate Python code generation. This works perfectly for numeric tasks (modular exponentiation, digit sums, binary operations, ledger arithmetic). The model reliably writes `pow()`, string operations, arithmetic.

**Observation 2**: Cost variation per task (1052–1725 tokens) comes from different problem sizes and model response lengths. Both systems tie at frontier because they use identical mechanisms—only the output format expectation differs (and both fallback gracefully).

**Challenge for unseen tasks**: The current approach is strong for computational problems (dates, text, lists can all be coded). However:
- The system prompt is **math-specific** ("mathematician", mentions computational code implicitly)
- The extraction logic is **number-only** (regex for `-?[\d,]+`)
- These constraints may not generalize well

---

## Two Falsifiable Hypotheses

### **Hypothesis 1 (Exploitation)**: Generalized system prompt + flexible output extraction
A system prompt that emphasizes **general-purpose Python coding** (not math-specific) and extraction that handles **multiple output types** (strings, dates, lists in addition to numbers) will maintain 100% accuracy on numeric tasks while being more robust for non-numeric tasks.

**Falsifiable claim**: If true, harness works on string/date problems where current system prompt assumes numeric computation.

### **Hypothesis 2 (Exploration)**: Problem classification before code generation
Asking the model to first **classify the problem type and computational approach** (before writing code) improves code quality and error recovery by forcing explicit reasoning about solution strategy. This leads to better code on complex problems without requiring multiple code attempts.

**Falsifiable claim**: If true, explicit classification → better code generation on first attempt, fewer fallback scenarios.

---

```json
{
  "iteration": 3,
  "candidates": [
    {
      "name": "code_generalized_extract",
      "base_system": "code_first",
      "hypothesis": "Removing math-specific language from system prompt and implementing flexible output extraction that handles strings, dates, lists (not just numbers) improves robustness across diverse problem types without sacrificing numeric accuracy.",
      "axis": "exploitation",
      "components": ["code-generation", "generalized-system-prompt", "multi-type-extraction"]
    },
    {
      "name": "code_with_classification",
      "base_system": "code_first",
      "hypothesis": "Asking the model to classify problem type and computational approach before code generation produces better-quality code on first attempt by forcing explicit reasoning about solution strategy, improving generalization to new task types.",
      "axis": "exploration",
      "components": ["code-generation", "problem-classification", "strategy-guided-coding"]
    }
  ]
}
```

---

## Candidate 1: code_generalized_extract

=== FILE: agents/code_generalized_extract/harness.py ===
```python
"""
Code-first solving with generalized output extraction.
Removes math-specific language; handles numeric, string, date, and list outputs.
"""

import re


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    
    # Prompt model to write Python code that solves and prints the answer
    code_prompt = f"""Solve this problem by writing Python code:

{question}

Write Python code that:
1. Computes the answer
2. Prints the final result (could be a number, string, date, list, etc.)

Provide ONLY the Python code, no explanation."""
    
    code_response = llm(code_prompt, system=system)
    
    # Extract Python code (remove markdown wrappers if present)
    code = code_response
    for wrapper in ['```python\n', '```python', '```\n', '```']:
        code = code.replace(wrapper, '')
    code = code.rstrip('`').strip()
    
    # Execute the code
    try:
        output = tools.python(code)
        
        # Try multiple extraction strategies for different output types
        result = _extract_answer(output)
        if result:
            return result
    except Exception:
        # If code execution fails, fall back to text-based solve
        pass
    
    # Fallback: solve via text reasoning
    fallback_prompt = files["prompts/task.md"].replace("{question}", question)
    reply = llm(fallback_prompt, system=system)
    lines = [line for line in reply.strip().splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _extract_answer(output):
    """
    Extract answer from code output, handling multiple data types.
    Tries: numbers, quoted strings, labeled values, last line.
    """
    output = output.strip()
    if not output:
        return ""
    
    lines = [line.strip() for line in output.split('\n') if line.strip()]
    
    # Strategy 1: Look for standalone numbers (most common for numeric problems)
    numbers = re.findall(r'-?[\d,]+(?:\.\d+)?', lines[-1] if lines else "")
    if numbers:
        last_num = numbers[-1].replace(',', '')
        # Return if it looks like a real answer (not just single digit unless expected)
        if len(last_num) > 0:
            return last_num
    
    # Strategy 2: Look for quoted strings (text answers)
    quoted_match = re.search(r'''["']([^"']+)["']''', lines[-1] if lines else "")
    if quoted_match:
        return quoted_match.group(1)
    
    # Strategy 3: Look for dates (YYYY-MM-DD or similar)
    date_match = re.search(r'\d{4}-\d{2}-\d{2}', lines[-1] if lines else "")
    if date_match:
        return date_match.group(0)
    
    # Strategy 4: Look for list/array output
    list_match = re.search(r'(\[[^\]]+\])', lines[-1] if lines else "")
    if list_match:
        return list_match.group(1)
    
    # Strategy 5: Return entire last line as fallback
    if lines:
        return lines[-1]
    
    return ""
```

=== FILE: agents/code_generalized_extract/prompts/system.md ===
```
You are a skilled programmer who solves computational problems with Python.
You excel at writing correct, efficient code for numeric problems, text manipulation,
date arithmetic, list operations, and other computational tasks.
```

=== FILE: agents/code_generalized_extract/prompts/task.md ===
```
{question}
```

---

## Candidate 2: code_with_classification

=== FILE: agents/code_with_classification/harness.py ===
```python
"""
Code-first solving with problem classification.
Model classifies problem type and computational approach before writing code.
"""

import re


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    
    # Step 1: Ask model to classify problem and identify approach
    classify_prompt = f"""Analyze this problem and identify:
1. Problem type (e.g., modular arithmetic, text manipulation, date arithmetic, counting, string search, etc.)
2. What computational operations or algorithms are needed
3. Expected output type (number, string, date, list, etc.)

Problem: {question}

Provide a brief analysis (1-2 sentences on approach, then state: "Solution approach: <method>")"""
    
    classify_response = llm(classify_prompt, system=system)
    
    # Step 2: Now write code, informed by the classification
    code_prompt = f"""Based on this analysis:

{classify_response}

Write Python code that solves this problem:

{question}

Write Python code that:
1. Computes the answer correctly
2. Prints the final result clearly

Provide ONLY the Python code, no explanation."""
    
    code_response = llm(code_prompt, system=system)
    
    # Extract Python code (remove markdown wrappers if present)
    code = code_response
    for wrapper in ['```python\n', '```python', '```\n', '```']:
        code = code.replace(wrapper, '')
    code = code.rstrip('`').strip()
    
    # Execute the code
    try:
        output = tools.python(code)
        
        # Extract answer from output
        result = _extract_answer(output)
        if result:
            return result
    except Exception:
        # If code execution fails, fall back to text-based solve
        pass
    
    # Fallback: solve via text reasoning
    fallback_prompt = files["prompts/task.md"].replace("{question}", question)
    reply = llm(fallback_prompt, system=system)
    lines = [line for line in reply.strip().splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _extract_answer(output):
    """
    Extract answer from code output.
    Handles: numbers, strings, dates, lists.
    """
    output = output.strip()
    if not output:
        return ""
    
    lines = [line.strip() for line in output.split('\n') if line.strip()]
    if not lines:
        return ""
    
    last_line = lines[-1]
    
    # Strategy 1: Numbers (highest priority for numeric problems)
    numbers = re.findall(r'-?[\d,]+(?:\.\d+)?', last_line)
    if numbers:
        return numbers[-1].replace(',', '')
    
    # Strategy 2: Quoted strings
    quoted_match = re.search(r'''["']([^"']+)["']''', last_line)
    if quoted_match:
        return quoted_match.group(1)
    
    # Strategy 3: Dates
    date_match = re.search(r'\d{4}-\d{2}-\d{2}', last_line)
    if date_match:
        return date_match.group(0)
    
    # Strategy 4: Lists
    list_match = re.search(r'(\[[^\]]+\])', last_line)
    if list_match:
        return list_match.group(1)
    
    # Strategy 5: Return entire last line
    return last_line
```

=== FILE: agents/code_with_classification/prompts/system.md ===
```
You are an expert problem solver and programmer.
You excel at analyzing problems to identify their type and computational approach,
then writing correct Python code to solve them.
When solving problems, think step-by-step about the approach before coding.
```

=== FILE: agents/code_with_classification/prompts/task.md ===
```
{question}
```

---

## Self-Critique

**Candidate 1 (code_generalized_extract)**: 
- ✅ Genuine mechanism: Removes math-specific framing + adds flexible multi-type extraction
- ✅ Exploitation on code_first: keeps core logic, broadens scope
- ✅ Testable: If it fails on numeric, we've broken something. If it succeeds and generalizes, the hypothesis holds.
- ⚠️ Risk: Could regress on numeric if extraction order is wrong (I prioritize numbers first, so should be safe)

**Candidate 2 (code_with_classification)**:
- ✅ Genuine mechanism: Adds explicit classification step before code generation
- ✅ Exploration: Different approach (classify → code vs. direct code)
- ✅ Testable: Does explicit classification improve code quality on first attempt?
- ⚠️ Risk: Adds 1 extra LLM call (~33% more cost on numeric tasks). Hypothesis must show improvement on generalization to justify this cost.

Both are genuinely different from base and from each other. Neither is parameter-tweak-only.

Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01WGuinNac91tRBBDjcwnTxW