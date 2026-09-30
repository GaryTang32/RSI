# Iteration 3: Analysis & Candidates

## Post-Eval Report: Iteration 2

=== FILE: reports/iter002.md ===
**Iteration 2 Results:**
- `compute_then_extract` (0%): Completely failed due to broken Python output regex in extraction. Pattern `<invoke name="(?:python|bash)"` doesn't match Claude's actual tool format with nested parameters.
- `multi_check_verify` (66.7%, +25% vs smart_extraction): **New best system**. Two-stage solving (initial + independent re-verification) recovers 8/12 correct, at high cost (7199 tokens, 2x smart_extraction).

**Failures persist in multi_check_verify (4 wrong):**
- evolve-numeric-000: 129 vs 144 (41! digit sum) — computational error
- evolve-numeric-004: 92 vs 100 (52^12 digit sum) — computational error  
- evolve-numeric-005: 297 vs 351 — computational error
- evolve-numeric-007: 261 vs 324 — computational error

**Key insight:** Even with independent re-verification, the model repeats similar arithmetic mistakes. The bottleneck is **upstream computation quality**, not verification strategy.

**Takeaway:** To push beyond 66.7%, either (1) improve prompting to force more reliable computation (Python-first, stricter constraints), or (2) use ensemble methods (multiple independent attempts + voting) to mask occasional errors.

---

## Hypotheses for Iteration 3

**Hypothesis 1 (Exploitation):** Strengthening the requirement for Python computation in both stages and fixing extraction of tool output (which was broken in compute_then_extract) will reduce arithmetic errors and improve the 66.7% baseline.

**Hypothesis 2 (Exploration):** Running multiple independent solve attempts and selecting the most common extracted answer (ensemble voting) will catch occasional computation errors through redundancy, achieving accuracy gains without model modification.

---

## Prototype Walkthroughs

### Candidate 1: python_constrained_multi_check

**Mechanism:** Enforce Python-first computation in both verification stages; extract primarily from tool output.

**Example (evolve-numeric-000: 41! digit sum, expected 144):**
- Stage 1 prompt: "Solve via Python. Show code that computes and prints the answer."
- Model writes and runs: `print(sum(int(d) for d in str(math.factorial(41))))` → output: 129
- Extract "129" from Python output
- Stage 2 prompt: "You got 129. Re-solve independently using Python. Only trust code output."
- Model re-runs same or similar code → output: 129 (consistent, but WRONG—model's underlying computation is faulty)
- Return 129 (fails, but this is a hard case where model's math is wrong)

**For a case that should improve (evolve-numeric-001: mod operation, expected 91):**
- Stage 1: Model tries direct modulo → 7 (wrong)
- Extract 7
- Stage 2: Model re-thinks, uses CRT method → 91 (correct, as seen in traces)
- Return 91 (succeeds via re-reasoning)

**Key difference from multi_check_verify:** Prompts explicitly say "trust only Python output, not your text summary" and extraction prioritizes code output.

---

### Candidate 2: ensemble_voting_simple

**Mechanism:** Three independent solves, extract answer from each, return the most frequent answer (or first if all different).

**Example (evolve-numeric-000: 41! digit sum, expected 144):**
- Attempt 1: Model computes, gets 129, extracts 129
- Attempt 2: Model computes (independent state), gets 129, extracts 129  
- Attempt 3: Model computes, might try different approach, could get 129 or 144
- If 2+ return 129, return 129 (fails, but **failure mode is less isolated**)

**For a case that should improve (evolve-numeric-001: mod 148, expected 91):**
- Attempt 1: Direct modulo → 7
- Attempt 2: Direct modulo again → 7
- Attempt 3: Uses CRT reasoning → 91
- Majority is 7 (fails by vote), OR voting rule could prefer "confidence" of each method
- **Refinement:** weight by extraction confidence (ANSWER: format > output line > last number)

**Cost:** ~3650 tokens/task (3x seed cost ≈ 1525), vs 7199 for multi_check_verify. Potentially cheaper per point if accuracy improves.

---

```json
{
  "iteration": 3,
  "candidates": [
    {
      "name": "python_constrained_multi_check",
      "base_system": "multi_check_verify",
      "hypothesis": "Enforcing Python computation as the sole source of truth in both solve and verify stages, with extraction prioritizing tool output, will eliminate text-based misreporting and reduce arithmetic errors.",
      "axis": "exploitation",
      "components": ["axis:A", "axis:C"]
    },
    {
      "name": "ensemble_voting_simple",
      "base_system": "seed",
      "hypothesis": "Three independent solve attempts with majority-vote answer selection will catch occasional computational errors through redundancy and improve accuracy at lower cost than multi-stage verification.",
      "axis": "exploration",
      "components": ["axis:E", "axis:B", "axis:C"]
    }
  ]
}
```

---

## Files

=== FILE: agents/python_constrained_multi_check/harness.py ===
"""Multi-stage solve with strict Python-first computation and tool-output-aware extraction."""

import re


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    
    # Stage 1: Initial Python-first solve
    prompt1 = files["prompts/task.md"].replace("{question}", question)
    reply1 = llm(prompt1, system=system)
    answer1 = extract_from_python_output(reply1)
    if not answer1:
        answer1 = extract_from_text(reply1)
    
    # Stage 2: Independent re-verification with Python requirement
    if answer1:
        prompt2 = files["prompts/verify.md"].format(question=question, previous_answer=answer1)
    else:
        prompt2 = files["prompts/verify.md"].format(question=question, previous_answer="unknown")
    
    reply2 = llm(prompt2, system=system)
    answer2 = extract_from_python_output(reply2)
    if not answer2:
        answer2 = extract_from_text(reply2)
    
    # Return verified answer, fallback to first attempt
    return answer2 if answer2 else (answer1 if answer1 else "")


def extract_from_python_output(reply):
    """Extract answer from Python code output (highest priority)."""
    # Look for function_calls/invoke blocks with bash executing Python
    # Format: <invoke name="bash"><parameter name="command">python3 << 'EOF'...EOF</parameter></invoke>
    
    # Strategy 1: Look for explicit print() outputs near code blocks
    # Match patterns like "print(...123...)" or numbers shown after EOF
    
    # Extract content between code markers
    eof_pattern = r"EOF\s*\n(.*?)(?:\n\s*<|$)"
    eof_blocks = re.findall(eof_pattern, reply, re.DOTALL)
    
    for block in eof_blocks:
        # Look for numbers in output that appear to be results
        numbers = re.findall(r'\n\s*([-]?[\d,]+(?:\.\d+)?)\s*$', block, re.MULTILINE)
        if numbers:
            # Take last/most relevant number from output block
            return clean_numeric(numbers[-1])
    
    # Strategy 2: Look for ``` python ``` code blocks
    python_blocks = re.findall(r'```(?:python|py)\s*(.*?)```', reply, re.DOTALL)
    for block in reversed(python_blocks):
        # Extract numbers that appear to be outputs
        numbers = re.findall(r'=\s*([-]?[\d,]+(?:\.\d+)?)\s*$', block, re.MULTILINE)
        if numbers:
            return clean_numeric(numbers[-1])
    
    # Strategy 3: Look for "= number" assignments in code
    all_equals = re.findall(r'=\s*([-]?[\d,]+(?:\.\d+)?)\s*(?:\n|$)', reply)
    if all_equals:
        return clean_numeric(all_equals[-1])
    
    return ""


def extract_from_text(reply):
    """Extract answer from text-based patterns (fallback)."""
    lines = reply.strip().splitlines()
    
    # Priority 1: ANSWER: <value> format
    for line in lines:
        if "ANSWER:" in line:
            match = re.search(r'ANSWER:\s*([-]?[\d,]+(?:\.\d+)?)', line)
            if match:
                return clean_numeric(match.group(1))
    
    # Priority 2: "= number" at end of line
    for line in reversed(lines):
        match = re.search(r'=\s*([-]?[\d,]+(?:\.\d+)?)\s*$', line)
        if match:
            return clean_numeric(match.group(1))
    
    # Priority 3: **number** (bold format)
    for line in reversed(lines):
        match = re.search(r'\*\*\s*([-]?[\d,]+(?:\.\d+)?)\s*\*\*', line)
        if match:
            return clean_numeric(match.group(1))
    
    # Priority 4: Last number in last line
    if lines:
        numbers = re.findall(r'[-]?[\d,]+(?:\.\d+)?', lines[-1])
        if numbers:
            return clean_numeric(numbers[-1])
    
    return ""


def clean_numeric(value):
    """Clean and validate numeric value."""
    if not value:
        return ""
    value = value.replace(",", "").strip()
    try:
        if "." in value:
            float(value)
        else:
            int(value)
        return value
    except ValueError:
        return ""

=== FILE: agents/python_constrained_multi_check/prompts/system.md ===
You are a precise computational assistant. You solve problems using Python and trust only code output.

Key principles:
- Always write Python code to solve the problem
- The answer comes from your code's print output, never from your text summary
- Double-check code logic before running
- Be willing to re-examine your work when asked

=== FILE: agents/python_constrained_multi_check/prompts/task.md ===
Solve this problem using Python code:

{question}

Write Python code that computes the answer and prints the final numeric result. Your answer is what your code outputs, not what you write in text.

Show your work, then provide your final answer clearly. You may use ANSWER: <value> format after running your code.

=== FILE: agents/python_constrained_multi_check/prompts/verify.md ===
You previously solved this problem and your code output was: {previous_answer}

Now, solve the exact same problem **independently from scratch** using Python. Write new code (don't reuse your previous approach). Run it and rely only on what your code outputs.

Problem: {question}

After running your code, state the answer in this format:
ANSWER: <the numeric value your code printed>

Trust your code output above all else.

=== FILE: agents/ensemble_voting_simple/harness.py ===
"""Ensemble approach: three independent solves with majority-vote answer selection."""

import re
from collections import Counter


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    prompt_template = files["prompts/task.md"]
    
    answers = []
    
    # Run three independent solves
    for attempt in range(3):
        prompt = prompt_template.replace("{question}", question)
        reply = llm(prompt, system=system)
        answer = extract_answer(reply)
        if answer:
            answers.append(answer)
    
    # Select answer by majority vote
    if not answers:
        return ""
    
    # Count occurrences of each answer
    counter = Counter(answers)
    most_common = counter.most_common(1)
    
    if most_common:
        # Return the most frequently occurring answer
        return most_common[0][0]
    
    # Fallback: return first answer if no consensus
    return answers[0] if answers else ""


def extract_answer(reply):
    """Extract numeric answer from reply using multi-strategy approach."""
    lines = reply.strip().splitlines()
    
    if not lines:
        return ""
    
    # Strategy 1: Look for ANSWER: <value> line
    for line in lines:
        if "ANSWER:" in line:
            match = re.search(r'ANSWER:\s*([-]?[\d,]+(?:\.\d+)?)', line)
            if match:
                return clean_numeric(match.group(1))
    
    # Strategy 2: Look for "= <number>" at end of line
    for line in reversed(lines):
        match = re.search(r'=\s*([-]?[\d,]+(?:\.\d+)?)\s*$', line)
        if match:
            return clean_numeric(match.group(1))
    
    # Strategy 3: Look for bold markup around number
    for line in reversed(lines):
        match = re.search(r'\*\*\s*([-]?[\d,]+(?:\.\d+)?)\s*\*\*', line)
        if match:
            return clean_numeric(match.group(1))
    
    # Strategy 4: Extract last number found in last line
    if lines:
        last = lines[-1]
        numbers = re.findall(r'[-]?[\d,]+(?:\.\d+)?', last)
        if numbers:
            return clean_numeric(numbers[-1])
    
    return ""


def clean_numeric(value):
    """Clean numeric value by removing formatting characters."""
    if not value:
        return ""
    value = value.replace(",", "").strip()
    try:
        if "." in value:
            float(value)
        else:
            int(value)
        return value
    except ValueError:
        return ""

=== FILE: agents/ensemble_voting_simple/prompts/system.md ===
You are a helpful assistant skilled at solving mathematical and computational problems. 

Approach each problem carefully:
- Show clear step-by-step reasoning
- Use Python code when helpful to verify calculations
- Provide your final answer clearly

=== FILE: agents/ensemble_voting_simple/prompts/task.md ===
{question}

Solve this problem step-by-step. Feel free to show your work and reasoning. When you provide your final answer, format it as ANSWER: <value> or state it clearly in the last line.