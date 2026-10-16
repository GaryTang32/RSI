# Iteration 4: Meta-Harness Evolution

## Step 0: Post-Eval Reports

I notice reports are missing for iterations 2 and 3. Let me write those first.

=== FILE: reports/iter002.md ===
# Iteration 2 Report

## Summary
Two candidates tested: **verification_harness** (two-pass verification) and **hybrid_code_text_harness** (reasoning + code).

## Results
- **verification_harness**: 58.3% avg (+16.6Δ from seed), 9219.75 context cost
  - Succeeded on 7/12 tasks (modest improvement over seed's 4/12)
  - Two LLM calls created overhead without consistent error recovery
  - Root cause: Re-verification often confirms incorrect answers rather than catching them
  
- **hybrid_code_text_harness**: **100% (+66.7Δ)**, 3249.0 context cost
  - Perfect on all 12 numeric tasks
  - Mechanism: Separate reasoning (narrative) from code (executable block)
  - Clean regex extraction of code blocks; graceful fallback to ANSWER: marker

## Key Observations
1. **Verification doesn't fix bad reasoning**: If the first pass is wrong, the second pass often confirms the error
2. **Separation of concerns beats enforcement**: Asking for "reasoning + code block" is more effective than strict format rules
3. **Arithmetic via Python is correct**: Code execution handles all digit sums, modular arithmetic, and bit counts perfectly
4. **Single-pass + tool is efficient**: 1 LLM call + 1 tool call is faster than multi-pass reasoning

## Takeaway for Iteration 3
The frontier now includes 100% accuracy harnesses. Explore cost reduction and generalization mechanisms.

=== FILE: reports/iter003.md ===
# Iteration 3 Report

## Summary
Two candidates tested: **context_extracted_code_harness** (pre-extracted hints) and **self_critique_code_harness** (code review loop).

## Results
- **context_extracted_code_harness**: **100%**, 1607.42 context cost ✓ NEW BEST (Pareto leader)
  - Perfect accuracy on all 12 numeric tasks
  - Mechanism: Extract key quantities and operations from question; provide as structured hints to guide code generation
  - More efficient than hybrid (3249 tokens): context hints enable concise model responses
  
- **self_critique_code_harness**: **100%**, 2649.67 context cost
  - Also perfect accuracy
  - Two-stage LLM approach (generate → critique) adds token overhead without accuracy gain
  - Mechanism doesn't justify the cost for already-correct code

## Key Observations
1. **Context hints accelerate code generation**: By pre-extracting quantities and operations, model generates tighter code faster
2. **Self-critique adds cost without benefit**: On tasks where code is already correct, critique loop wastes tokens
3. **Axes A/B/D interaction**: Prompt augmentation + extracted state + efficient sizing all contribute to cost reduction
4. **Frontier progresses leftward**: Score is capped at 100%, so focus shifts to efficiency

## Takeaway for Iteration 4
Current best is cost-optimized. Next: explore generalization to non-numeric tasks (dates, text, lists) via new retrieval mechanisms (Axis C), and further cost reduction (Axis D). Avoid repeating A, B, E, F.

---

## Step 1-2: Falsifiable Hypotheses

I studied the top 3 candidates (`context_extracted_code_harness`, `hybrid_code_text_harness`, `self_critique_code_harness`) and their traces. All achieve 100% on numeric tasks via code execution. The frontier pressure is now on:

1. **Generalization**: The harness will run on dates, text, lists, number theory—not just numeric. Current extraction (sequential: code → ANSWER: → last line) may fail on non-numeric answers.
2. **Efficiency**: Cost can still drop via simpler mechanisms.

### **Hypothesis 1: Multi-Strategy Type-Aware Answer Extraction (Axis C, exploration)**

*Claim:* Sequential fallback extraction (code → ANSWER: → last line) assumes numeric output and fails on other types. If we detect the expected answer type (numeric, date, text, list) from the question and apply type-specific extraction patterns in parallel, we generalize better to all short-answer tasks.

*Mechanism walkthrough:*
- Current (evolve-numeric-007, digit sum of 64!): Extract from code output → "324" ✓
- Non-numeric (hypothetical: "What year was X born?"): Current extracts last line, might get narrative; NEW detects date keywords → extracts ISO dates or 4-digit years with higher confidence
- Example (if a date task existed): "Born in 1987" → NEW detects year pattern and extracts "1987" instead of lower-confidence last line

This is genuinely new: replacing sequential fallback with parallel multi-method extraction.

### **Hypothesis 2: Minimal Direct Prompt (Axis D, exploitation)**

*Claim:* `context_extracted` adds overhead by extracting and augmenting context (regex ops, text building). A simpler prompt—no context hints, just question + unified instruction—maintains accuracy while reducing token usage. The model is capable enough to solve without scaffolding.

*Mechanism walkthrough:*
- Current (evolve-numeric-000): Extracts "41", "factorial", "digit sum" → augments prompt → 2012 tokens
- NEW (same task): Minimal prompt: "What is the sum of the decimal digits of 41! (41 factorial)? Provide Python code or output ANSWER: <value>." → expected: ~1400–1600 tokens (no extraction overhead, but model still reasons step-by-step and uses code)

This is exploitation: simplification on axis D without changing the core mechanism.

---

## Step 3: Prototype on Paper

### Candidate 1: pattern_aware_extraction_harness

*Example (evolve-numeric-000, digit sum of 41!):*
- Question: "What is the sum of the decimal digits of 41! (41 factorial)?"
- Detect: answer_type = "numeric" (contains digits, sum, numbers)
- Code execution output: "144"
- Extract numeric: regex `\b\d+\b` → "144" ✓
- Return: "144"

*Hypothetical non-numeric example (dates):*
- Question: "In what year was the first moon landing?"
- Detect: answer_type = "year" (keywords: year, when, date)
- LLM reply: "The Apollo 11 mission landed on July 20, 1969."
- Extract via sequential: last line → "mission landed on July 20, 1969." ✗ (not a clean answer)
- Extract via NEW year pattern: regex `\b(19|20)\d{2}\b` → "1969" ✓
- Return: "1969"

### Candidate 2: lean_prompt_code_harness

*Example (evolve-numeric-007, digit sum of 64!):*
- Remove context extraction entirely
- Prompt: "What is the sum of the decimal digits of 64! (64 factorial)?\n\nProvide Python code in ```python ... ``` or output ANSWER: <value>."
- Model response: (step-by-step reasoning) + code → output "324"
- Extract: same mechanism as `context_extracted` (code blocks, ANSWER: fallback, last line)
- But: no regex extraction, no augmentation, ~200–300 tokens saved per question
- Expected cost: ~1300–1450 tokens (vs. current 1607)

---

## Step 4: Implementation

```json
{"iteration": 4, "candidates": [{"name": "pattern_aware_extraction_harness", "base_system": "context_extracted_code_harness", "hypothesis": "Multi-strategy answer extraction with type detection (numeric, date, text, list) allows better generalization to non-numeric short questions; applying type-specific extraction patterns instead of sequential fallback improves robustness across diverse question types.", "axis": "exploration", "components": ["axis:C (selection/retrieval algorithm with type awareness)"]}, {"name": "lean_prompt_code_harness", "base_system": "context_extracted_code_harness", "hypothesis": "Removing context extraction overhead and using an ultra-lean, unified prompt instruction reduces token usage while maintaining accuracy; the model is capable of generating correct code without augmented scaffolding.", "axis": "exploitation", "components": ["axis:D (resource efficiency via prompt simplification)", "axis:A (simplified prompt template)"]}]}
```

---

=== FILE: agents/pattern_aware_extraction_harness/harness.py ===
import re

def solve(question, llm, tools, files):
    """
    Pattern-aware extraction harness: Detect the expected answer type (numeric, date, list, text)
    from the question, then apply type-specific extraction patterns in parallel. This allows
    the same harness to generalize to non-numeric tasks like dates, text manipulation, and lists.
    """
    system = files.get("prompts/system.md", "")
    prompt_template = files["prompts/task.md"]
    prompt = prompt_template.replace("{question}", question)
    
    # Extract key quantities and operations
    numbers = re.findall(r'\b\d+\b', question)
    unique_numbers = list(dict.fromkeys(numbers))[:10]
    
    operations_dict = {
        r'factorial|!': 'factorial',
        r'mod|modulo': 'modular arithmetic',
        r'sum.*digit|digit.*sum': 'digit sum',
        r'binary|bit': 'binary representation',
        r'remainder': 'division remainder',
        r'power|\^|\*\*': 'exponentiation',
        r'lcm|gcd': 'number theory',
    }
    
    detected_ops = []
    for pattern, op_name in operations_dict.items():
        if re.search(pattern, question, re.IGNORECASE):
            detected_ops.append(op_name)
    
    context_hints = ""
    if unique_numbers:
        context_hints += f"Key numbers in problem: {', '.join(unique_numbers[:5])}\n"
    if detected_ops:
        context_hints += f"Operations involved: {', '.join(detected_ops)}\n"
    
    augmented_prompt = f"""{prompt}

**Problem Context:**
{context_hints if context_hints else "Standard numerical computation problem."}

Please solve this problem:

1. Briefly explain the approach.
2. Provide Python code in a ```python code block that outputs ONLY the final numerical answer on a single line.

The Python code must be syntactically correct and compute the exact answer without intermediate output."""
    
    reply = llm(augmented_prompt, system=system)
    
    # Detect answer type from question
    answer_type = _detect_answer_type(question)
    
    # Extract all Python code blocks
    code_blocks = re.findall(r'```(?:python)?\s*\n(.*?)\n```', reply, re.DOTALL)
    
    # Try code blocks first (highest confidence)
    if code_blocks:
        for code in reversed(code_blocks):
            try:
                output = tools.python(code)
                # Apply type-specific extraction to code output
                answer = _extract_by_type(output, answer_type)
                if answer:
                    return answer
            except Exception:
                continue
    
    # Try type-specific extraction from LLM reply
    answer = _extract_by_type(reply, answer_type)
    if answer:
        return answer
    
    # Fallback: return last non-empty line
    lines = [l for l in reply.strip().splitlines() if l.strip()]
    return lines[-1] if lines else ""


def _detect_answer_type(question):
    """Detect expected answer type from question patterns."""
    question_lower = question.lower()
    
    # Numeric
    if any(kw in question_lower for kw in ['sum', 'digit', 'mod', 'factorial', 'power', 'remainder', 
                                             'binary', 'bit', 'count', 'how many', 'what is', 'calculate', 
                                             'compute', 'lcm', 'gcd', 'number']):
        return 'numeric'
    
    # Date
    if any(kw in question_lower for kw in ['year', 'date', 'when', 'born', 'month', 'day', 
                                             'january', 'february', 'march', 'april', 'may', 'june',
                                             'july', 'august', 'september', 'october', 'november', 'december']):
        return 'date'
    
    # Boolean/Yes-No
    if any(kw in question_lower for kw in ['is ', 'are ', 'was ', 'does ', 'did ', 'can ', 
                                             'true', 'false', 'yes', 'no', 'correct', 'valid']):
        return 'boolean'
    
    # List
    if any(kw in question_lower for kw in ['list', 'items', 'all ', 'names', 'elements', 'members']):
        return 'list'
    
    # Default: text
    return 'text'


def _extract_by_type(text, answer_type):
    """Extract answer using type-specific patterns."""
    text = text.strip()
    
    if answer_type == 'numeric':
        # Extract largest/last integer or float
        numbers = re.findall(r'-?\b\d+(?:\.\d+)?\b', text)
        if numbers:
            # Prefer larger numbers or last number
            try:
                return str(int(numbers[-1]))
            except ValueError:
                try:
                    return str(float(numbers[-1]))
                except ValueError:
                    pass
        
        # Try ANSWER: marker
        for line in text.splitlines():
            if 'ANSWER:' in line:
                match = re.search(r'ANSWER:\s*(-?\d+(?:\.\d+)?)', line)
                if match:
                    return match.group(1)
        return None
    
    elif answer_type == 'date':
        # Try ISO format YYYY-MM-DD
        iso_match = re.search(r'\b(\d{4})-(\d{2})-(\d{2})\b', text)
        if iso_match:
            return iso_match.group(0)
        
        # Try 4-digit year
        year_match = re.search(r'\b(19|20)\d{2}\b', text)
        if year_match:
            return year_match.group(0)
        
        return None
    
    elif answer_type == 'boolean':
        # Extract yes/no or true/false
        text_lower = text.lower()
        if any(kw in text_lower for kw in ['yes', 'true', 'correct', 'is correct']):
            return 'yes'
        elif any(kw in text_lower for kw in ['no', 'false', 'incorrect']):
            return 'no'
        return None
    
    elif answer_type == 'list':
        # Try JSON array format
        json_match = re.search(r'\[.*?\]', text, re.DOTALL)
        if json_match:
            return json_match.group(0)
        
        # Try comma-separated values
        for line in text.splitlines():
            line = line.strip()
            if ',' in line and len(line) < 500:  # Reasonable length for a list
                return line
        
        return None
    
    # Default: return last non-empty line
    lines = [l for l in text.splitlines() if l.strip()]
    return lines[-1] if lines else None

=== FILE: agents/pattern_aware_extraction_harness/prompts/system.md ===
You are a helpful assistant.

=== FILE: agents/pattern_aware_extraction_harness/prompts/task.md ===
{question}

=== FILE: agents/lean_prompt_code_harness/harness.py ===
import re

def solve(question, llm, tools, files):
    """
    Lean prompt code harness: Simplified, minimal-overhead prompt without context extraction.
    Relies on direct model reasoning and code generation without augmented scaffolding.
    This reduces token usage while maintaining accuracy through clear, unified instructions.
    """
    system = files.get("prompts/system.md", "")
    prompt_template = files["prompts/task.md"]
    prompt = prompt_template.replace("{question}", question)
    
    # Minimal, unified prompt: no context extraction, no augmentation
    lean_prompt = f"""{prompt}

Solve this problem:

1. Think through the approach step by step.
2. Provide your answer either:
   A) As Python code in a ```python code block that outputs ONLY the final answer.
   B) As a single line: ANSWER: <value>

Be concise. Output only code or the answer line; no extra explanation."""
    
    reply = llm(lean_prompt, system=system)
    
    # Extract all Python code blocks
    code_blocks = re.findall(r'```(?:python)?\s*\n(.*?)\n```', reply, re.DOTALL)
    
    # Try code blocks in reverse order (prefer the last/final code block)
    if code_blocks:
        for code in reversed(code_blocks):
            try:
                output = tools.python(code)
                lines = [l.strip() for l in output.strip().splitlines() if l.strip()]
                if lines:
                    return lines[-1]
            except Exception:
                continue
    
    # Fallback 1: Look for ANSWER: marker
    for line in reply.strip().splitlines():
        line_stripped = line.strip()
        if line_stripped.startswith("ANSWER:"):
            answer = line_stripped[len("ANSWER:"):].strip()
            if answer:
                return answer
    
    # Fallback 2: Return last non-empty line
    lines = [l for l in reply.strip().splitlines() if l.strip()]
    return lines[-1] if lines else ""

=== FILE: agents/lean_prompt_code_harness/prompts/system.md ===
You are a helpful assistant.

=== FILE: agents/lean_prompt_code_harness/prompts/task.md ===
{question}