Looking at the evolution history, I need to analyze the failures to understand what mechanisms to improve.

## Analysis

From `frontier_val.json` and the traces, the **seed baseline achieves 50% (5/10 tasks)**:
- ✓ Tasks 000, 002, 003, 004, 008: Correct
- ✗ Tasks 001, 005, 006, 007, 009: Incorrect

Examining the failure traces reveals two distinct failure modes:

**Extraction failures (tasks 007, 009):**
- Task 007: Computed correct answer (435) but output "This can be verified using modular exponentiation: 3^163 ≡ 435 (mod 584)." → Extraction took full line instead of number
- Task 009: Computed correct answer (364) but output "This gives x ≡ **364** (mod 781)" → Same extraction issue

**Computation failures (tasks 001, 005, 006):**
- Task 001: Wrong modular arithmetic result (262 vs 781 expected)
- Task 005: Correct 81^15 but arithmetic error in digit sum (got 153, expected 99)
- Task 006: Wrong bit count (34 vs 24)

## Hypotheses

1. **[Exploitation]** Many failures are *extraction issues* where the correct numeric answer exists but is embedded in explanatory text. Smart extraction (regex patterns for bold numbers, position heuristics, structured patterns) would fix ~40% of failures without deeper mechanism changes.

2. **[Exploration]** Current system leaves output *unstructured*. Constraining the output format (e.g., "ANSWER: <value>" pattern) makes extraction trivial and may encourage the LLM to think more rigorously about the final answer through explicit formatting.

```json
{
  "iteration": 1,
  "candidates": [
    {
      "name": "exploit-robust-extraction",
      "base_system": "seed",
      "hypothesis": "Failures on tasks 007 and 009 are extraction errors—the LLM computed correctly but embedded answers in text. Robust extraction using regex, bold markers, and positional heuristics will recover these answers and improve accuracy from 50% to 70%.",
      "axis": "exploitation",
      "components": ["extraction", "regex_parsing", "heuristic_ranking"]
    },
    {
      "name": "explore-structured-format",
      "base_system": "seed",
      "hypothesis": "Constraining output format with an explicit 'ANSWER: <value>' requirement will reduce extraction ambiguity, encourage cleaner thinking, and improve both parsing reliability and potentially reduce computational errors through explicit finalization.",
      "axis": "exploration",
      "components": ["prompt_engineering", "structured_output_constraint", "format_enforcement"]
    }
  ]
}
```

---

## Candidate 1: exploit-robust-extraction

=== FILE: agents/exploit-robust-extraction/harness.py ===
```python
"""Robust extraction: intelligently parse numeric answers from LLM output."""

import re


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    prompt = files["prompts/task.md"].replace("{question}", question)
    reply = llm(prompt, system=system)
    
    answer = extract_answer_robust(reply)
    return answer


def extract_answer_robust(text):
    """
    Extract numeric answer using multiple strategies in order of confidence:
    1. ANSWER: <value> pattern
    2. Bold numbers like **123**
    3. Numbers after colons or equals
    4. Last standalone number in text
    5. Fallback to last non-empty line
    """
    
    # Strategy 1: Explicit ANSWER: pattern (highest confidence)
    match = re.search(r'ANSWER:\s*([^\n]+)', text, re.IGNORECASE)
    if match:
        value = match.group(1).strip()
        # Clean up markdown/formatting
        value = re.sub(r'\*\*|__', '', value).strip()
        if value and re.search(r'\d', value):
            return value
    
    # Strategy 2: Bold numbers **123** (very common in model output)
    bold_numbers = re.findall(r'\*\*(\d+(?:[,\d]*)?)\*\*', text)
    if bold_numbers:
        # Return the last bold number, clean commas
        return bold_numbers[-1].replace(',', '')
    
    # Strategy 3: Numbers after colons/equals (e.g., "answer: 123" or "result = 456")
    colon_matches = re.findall(r'(?:answer|result|value|remainder|sum|count|balance|total)[\s:=]+(\d+(?:[,\d]*)?)', text, re.IGNORECASE)
    if colon_matches:
        return colon_matches[-1].replace(',', '')
    
    # Strategy 4: All standalone numbers in text (fallback to last)
    all_numbers = re.findall(r'\b(\d+(?:[,\d]*)?)\b', text)
    if all_numbers:
        return all_numbers[-1].replace(',', '')
    
    # Strategy 5: Last non-empty line
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    return lines[-1] if lines else ""
```

=== FILE: agents/exploit-robust-extraction/prompts/system.md ===
```
You are a helpful assistant.
```

=== FILE: agents/exploit-robust-extraction/prompts/task.md ===
```
{question}
```

---

## Candidate 2: explore-structured-format

=== FILE: agents/explore-structured-format/harness.py ===
```python
"""Structured format: enforce ANSWER: <value> output pattern for unambiguous extraction."""

import re


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    prompt = files["prompts/task.md"].replace("{question}", question)
    reply = llm(prompt, system=system)
    
    answer = extract_answer_structured(reply)
    return answer


def extract_answer_structured(text):
    """
    Extract answer from structured ANSWER: <value> format.
    Falls back to robust extraction if format not found.
    """
    
    # Primary: Look for ANSWER: <value> pattern (enforced by prompt)
    match = re.search(r'ANSWER:\s*([^\n]+?)(?:\n|$)', text, re.IGNORECASE)
    if match:
        value = match.group(1).strip()
        # Remove markdown formatting (bold, italics)
        value = re.sub(r'\*\*|__|\*|_', '', value).strip()
        # Remove trailing citations or extra text
        value = re.sub(r'\s*(?:mod|remainder|approximately|about).*$', '', value, flags=re.IGNORECASE).strip()
        if value:
            return value
    
    # Fallback 1: Bold numbers
    bold_numbers = re.findall(r'\*\*(\d+(?:[,\d]*)?)\*\*', text)
    if bold_numbers:
        return bold_numbers[-1].replace(',', '')
    
    # Fallback 2: Numbers after key phrases
    colon_matches = re.findall(r'(?:answer|result|value|remainder|sum|count|balance|final|is|equals?)[\s:=]+(\d+(?:[,\d]*)?)', text, re.IGNORECASE)
    if colon_matches:
        return colon_matches[-1].replace(',', '')
    
    # Fallback 3: Last number
    all_numbers = re.findall(r'\b(\d+(?:[,\d]*)?)\b', text)
    if all_numbers:
        return all_numbers[-1].replace(',', '')
    
    # Fallback 4: Last line
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    return lines[-1] if lines else ""
```

=== FILE: agents/explore-structured-format/prompts/system.md ===
```
You are a helpful assistant. For all questions, work through your reasoning step by step, then end your response with a single line containing:

ANSWER: <your final numeric answer>

Do not include any other text after this line.
```

=== FILE: agents/explore-structured-format/prompts/task.md ===
```
{question}

Remember to end your response with "ANSWER: <value>" on its own line.
```