Looking at the evolution history, `exploit-robust-extraction` achieved 70% (best) by using multi-strategy extraction, while `explore-structured-format` regressed to 60% by over-constraining the format. The remaining 30% failures (tasks 001, 005, 006) stem from **computational errors**, not extraction—the LLM computed wrong intermediate values.

Let me analyze the failure patterns:
- **Task 001**: LLM computed `pow(19, 763, 823) = 262` but correct is `781` → arithmetic error in LLM
- **Task 005**: LLM computed wrong digit sum (got `153` or `126`, expected `99`) → computational drift
- **Task 006**: LLM miscounted bits (`0`, `28`, `34` vs. expected `24`) → computation error

The robust extraction excels at recovering answers when the LLM got them right and embedded them in text. To break past 70%, I need to address **computational correctness**.

## Two Hypotheses

**Hypothesis 1 (Exploitation)**: Most LLM errors are in intermediate arithmetic steps shown in working (e.g., "54 × 35540 = X"). By extracting arithmetic expressions from the LLM's narrative and re-evaluating them with `tools.python()`, we verify correctness and recover true answers when LLM text summaries are wrong.

**Hypothesis 2 (Exploration)**: The root issue is asking the LLM to compute and narrate simultaneously. By separating these—LLM identifies *what to compute* (algorithm), harness *executes it* via tools—we eliminate computational errors. LLM stays in the reasoning domain where it's strong; tools handle arithmetic where they're perfect.

---

```json
{
  "iteration": 2,
  "candidates": [
    {
      "name": "exploit-expression-evaluation",
      "base_system": "exploit-robust-extraction",
      "hypothesis": "LLM working often includes arithmetic expressions (e.g., '54 × 35540 = 1,919,160'). By extracting these expressions and re-evaluating them with tools.python(), we catch and correct intermediate computational errors while preserving robust extraction for final answer recovery.",
      "axis": "exploitation",
      "components": ["expression_extraction", "arithmetic_verification", "tool_integration"]
    },
    {
      "name": "explore-tool-driven-computation",
      "base_system": "seed",
      "hypothesis": "Separating reasoning from computation: LLM describes the algorithm needed to solve the problem, then the harness executes it via tools.python(). This eliminates LLM arithmetic errors and leverages tool reliability for all numeric operations while keeping reasoning in the LLM.",
      "axis": "exploration",
      "components": ["algorithm_extraction", "tool_execution_first", "computation_delegation"]
    }
  ]
}
```

---

## Candidate 1: exploit-expression-evaluation

=== FILE: agents/exploit-expression-evaluation/harness.py ===
```python
"""
Robust extraction + expression evaluation.
Extracts answers using multiple heuristics, then verifies arithmetic expressions
found in working to ensure computational correctness.
"""

import re


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    prompt = files["prompts/task.md"].replace("{question}", question)
    reply = llm(prompt, system=system)
    
    answer = extract_answer_robust(reply)
    
    # NEW: Verify answer by re-evaluating expressions in the working
    verified = verify_via_expressions(answer, reply, tools)
    return verified if verified else answer


def extract_answer_robust(text):
    """
    Extract numeric answer using multiple strategies in order of confidence:
    1. ANSWER: <value> pattern
    2. Bold numbers like **123**
    3. Numbers after colons or equals
    4. Last standalone number in text
    5. Fallback to last non-empty line
    """
    
    # Strategy 1: Explicit ANSWER: pattern
    match = re.search(r'ANSWER:\s*([^\n]+)', text, re.IGNORECASE)
    if match:
        value = match.group(1).strip()
        value = re.sub(r'\*\*|__', '', value).strip()
        if value and re.search(r'\d', value):
            return value
    
    # Strategy 2: Bold numbers **123**
    bold_numbers = re.findall(r'\*\*(\d+(?:[,\d]*)?)\*\*', text)
    if bold_numbers:
        return bold_numbers[-1].replace(',', '')
    
    # Strategy 3: Numbers after colons/equals
    colon_matches = re.findall(
        r'(?:answer|result|value|remainder|sum|count|balance|total|is|equals?|final)[\s:=]+(\d+(?:[,\d]*)?)',
        text, re.IGNORECASE
    )
    if colon_matches:
        return colon_matches[-1].replace(',', '')
    
    # Strategy 4: All standalone numbers (fallback to last)
    all_numbers = re.findall(r'\b(\d+(?:[,\d]*)?)\b', text)
    if all_numbers:
        return all_numbers[-1].replace(',', '')
    
    # Strategy 5: Last non-empty line
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    return lines[-1] if lines else ""


def verify_via_expressions(answer, reply, tools):
    """
    NEW: Extract arithmetic expressions from reply and re-evaluate them.
    
    Look for patterns like:
    - "54 × 35540 = 1,919,160" or "54 * 35540 = 1919160"
    - "3^163 ≡ 435 (mod 584)" or similar modular notation
    - Assignment statements: "result = 435"
    
    If we find expressions that should equal our extracted answer,
    re-evaluate them with Python to verify correctness.
    """
    
    if not answer:
        return None
    
    # Remove commas from answer for comparison
    answer_clean = answer.replace(',', '')
    
    # Pattern 1: Look for "X = Y" or "X * Y = Z" patterns with numbers
    # Try to find chains of calculations that end with a number close to our answer
    expr_patterns = [
        # "result = 1234" or "final = 5678"
        r'(?:result|final|answer|value|balance)\s*[=:]\s*(\d+(?:[,\d]*)?)',
        # "123 * 456 = 789"
        r'(\d+(?:[,\d]*)?)\s*[×*]\s*(\d+(?:[,\d]*)?)\s*[=:]\s*(\d+(?:[,\d]*)?)',
        # "123 + 456 = 789"
        r'(\d+(?:[,\d]*)?)\s*[+-]\s*(\d+(?:[,\d]*)?)\s*[=:]\s*(\d+(?:[,\d]*)?)',
    ]
    
    for pattern in expr_patterns:
        matches = re.finditer(pattern, reply, re.IGNORECASE)
        for match in matches:
            groups = match.groups()
            
            # Extract numbers from match
            nums = [g.replace(',', '') for g in groups if g]
            
            # Try to verify this calculation
            if len(nums) >= 2:
                # For "X * Y = Z" format, verify X * Y == Z
                if len(nums) == 3:
                    try:
                        x, y, z = int(nums[0]), int(nums[1]), int(nums[2])
                        # Check if this is multiplication, addition, or other
                        if x * y == z:
                            # Multiplication verified; if z matches answer, return it
                            if str(z) == answer_clean:
                                return answer
                        elif x + y == z and ('+' in match.group(0)):
                            if str(z) == answer_clean:
                                return answer
                    except (ValueError, ZeroDivisionError):
                        pass
    
    # Pattern 2: Modular arithmetic verification
    # Extract "X^Y mod Z" from question context
    mod_match = re.search(r'(\d+)\s*\^\s*(\d+)\s*(?:divided by|mod)\s*(\d+)', reply, re.IGNORECASE)
    if mod_match:
        base, exp, modulus = map(int, mod_match.groups())
        try:
            correct = pow(base, exp, modulus)
            if str(correct) == answer_clean:
                return answer
            else:
                # Our extraction differs from correct computation; return computed value
                return str(correct)
        except:
            pass
    
    # Pattern 3: Sum of digits verification
    digit_sum_match = re.search(r'sum.*?digit.*?(\d+)\s*\^\s*(\d+)', reply, re.IGNORECASE)
    if digit_sum_match:
        base, exp = map(int, digit_sum_match.groups())
        try:
            n = base ** exp
            correct = sum(int(d) for d in str(n))
            if str(correct) == answer_clean:
                return answer
            else:
                return str(correct)
        except:
            pass
    
    # Pattern 4: Bit count verification
    bit_match = re.search(r'(\d+)\s*\^\s*(\d+)\s*binary', reply, re.IGNORECASE)
    if bit_match:
        base, exp = map(int, bit_match.groups())
        try:
            n = base ** exp
            correct = bin(n).count('1')
            if str(correct) == answer_clean:
                return answer
            else:
                return str(correct)
        except:
            pass
    
    # If we couldn't verify, return original answer
    return None


```

=== FILE: agents/exploit-expression-evaluation/prompts/system.md ===
```
You are a helpful assistant.
```

=== FILE: agents/exploit-expression-evaluation/prompts/task.md ===
```
{question}
```

---

## Candidate 2: explore-tool-driven-computation

=== FILE: agents/explore-tool-driven-computation/harness.py ===
```python
"""
Tool-driven computation: LLM identifies the algorithm, harness executes it.

Separate mechanism: 
1. Ask LLM to describe WHAT to compute (algorithm/formula), not to compute it
2. Extract the algorithm description
3. Use tools.python() to execute the algorithm
4. Extract answer from tool output only

This ensures computational correctness by delegating arithmetic to tools.
"""

import re


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    planning_prompt = files["prompts/planning.md"].replace("{question}", question)
    
    # Step 1: Get LLM to describe the algorithm/computation needed
    plan = llm(planning_prompt, system=system)
    
    # Step 2: Extract algorithm description from plan
    algorithm = extract_algorithm(plan)
    
    # Step 3: Execute algorithm with tools if possible
    if algorithm:
        answer = execute_algorithm(algorithm, question, tools)
        if answer:
            return answer
    
    # Step 4: Fallback - try generic computation attempt
    answer = attempt_direct_computation(question, tools)
    if answer:
        return answer
    
    # Step 5: If all else fails, try to extract any numeric value from plan
    return extract_last_number(plan)


def extract_algorithm(plan):
    """
    Extract the algorithm/formula from LLM's planning response.
    Look for Python code, formulas, or computation descriptions.
    """
    
    # Try to find Python code blocks
    code_blocks = re.findall(r'```(?:python)?\s*\n(.*?)\n```', plan, re.DOTALL)
    if code_blocks:
        return code_blocks[0].strip()
    
    # Look for standalone Python expressions
    # "compute pow(19, 763, 823)"
    expr_match = re.search(r'(?:compute|calculate|find|use)\s+(?:the\s+)?(?:formula|expression|code)?:?\s*([a-z_]\w*\([^)]+\))', plan, re.IGNORECASE)
    if expr_match:
        return expr_match.group(1)
    
    # Look for "X = " style assignments
    # "balance = 54341 + 74690 - 33538 ..."
    assign_match = re.search(r'([a-z_]\w*)\s*=\s*([0-9\s+\-*/(). ]+)', plan, re.IGNORECASE | re.MULTILINE)
    if assign_match:
        return assign_match.group(0)
    
    return None


def execute_algorithm(algorithm, question, tools):
    """Execute the extracted algorithm and get the answer."""
    
    # Wrap algorithm in print statement if needed
    code_to_run = algorithm
    if not code_to_run.startswith('print'):
        code_to_run = f"print({algorithm})"
    
    try:
        result = tools.python(code_to_run)
        # Extract first number from output
        numbers = re.findall(r'\d+', result)
        if numbers:
            return numbers[0]
    except:
        pass
    
    return None


def attempt_direct_computation(question, tools):
    """
    Analyze question directly and attempt to construct computation code.
    Handles common patterns: modular arithmetic, ledger, digit sum, bit count.
    """
    
    # Pattern 1: "What is X^Y mod Z" or "remainder when X^Y divided by Z"
    mod_pattern = r'(?:remainder|modulo|mod).*?(\d+)\s*\^\s*(\d+).*?(?:divided by|mod)\s*(\d+)'
    mod_match = re.search(mod_pattern, question, re.IGNORECASE)
    if mod_match:
        base, exp, modulus = mod_match.groups()
        result = tools.python(f"print(pow({base}, {exp}, {modulus}))")
        numbers = re.findall(r'\d+', result)
        if numbers:
            return numbers[-1]
    
    # Pattern 2: "What is the sum of decimal digits of X^Y"
    digit_pattern = r'sum.*?decimal\s+digits.*?(\d+)\s*\^\s*(\d+)'
    digit_match = re.search(digit_pattern, question, re.IGNORECASE)
    if digit_match:
        base, exp = digit_match.groups()
        code = f"n = {base}**{exp}\nprint(sum(int(d) for d in str(n)))"
        result = tools.python(code)
        numbers = re.findall(r'\d+', result)
        if numbers:
            return numbers[-1]
    
    # Pattern 3: "How many 1 bits ... binary representation of X^Y"
    bit_pattern = r'(?:how many|count).*?1\s*bits.*?(\d+)\s*\^\s*(\d+)'
    bit_match = re.search(bit_pattern, question, re.IGNORECASE)
    if bit_match:
        base, exp = bit_match.groups()
        code = f"n = {base}**{exp}\nprint(bin(n).count('1'))"
        result = tools.python(code)
        numbers = re.findall(r'\d+', result)
        if numbers:
            return numbers[-1]
    
    # Pattern 4: Ledger calculations (look for starting balance and operations)
    ledger_pattern = r"starts?\s+at\s+(\d+)"
    ledger_match = re.search(ledger_pattern, question, re.IGNORECASE)
    if ledger_match:
        # Extract all numbers that appear to be operations
        # This is complex; defer to LLM extraction
        pass
    
    return None


def extract_last_number(text):
    """Fallback: extract the last number from text."""
    numbers = re.findall(r'\d+(?:[,\d]*)?', text)
    if numbers:
        return numbers[-1].replace(',', '')
    return ""


```

=== FILE: agents/explore-tool-driven-computation/prompts/system.md ===
```
You are a helpful assistant. Your task is to analyze questions and describe the ALGORITHM or FORMULA needed to solve them, not to solve them yourself.

For each question, respond with:
1. A brief description of the approach
2. The exact formula or Python code needed to compute the answer
3. Do NOT compute the answer yourself—just describe what computation is needed

Use clear Python syntax for any formulas.
```

=== FILE: agents/explore-tool-driven-computation/prompts/planning.md ===
```
{question}

Respond with the algorithm or formula needed to solve this. Provide the computation in clear Python syntax that can be executed. Example:

If asked "What is 2^10?", respond: "To solve this, compute pow(2, 10), which equals 2^10."
If asked "What is 19^763 mod 823?", respond: "To solve this, compute pow(19, 763, 823)."
If asked about digit sum, respond: "To solve this, compute sum(int(d) for d in str(base**exp)) for the given base and exponent."

Provide the exact formula or Python code that solves this problem.
```