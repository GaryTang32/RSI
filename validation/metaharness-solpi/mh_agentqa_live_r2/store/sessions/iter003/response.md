Looking at the evolution history, I need to understand the current frontier and identify failure patterns:

**Current best**: `exploit-expression-evaluation` (90% accuracy, context cost 2228.8)
- Combines robust extraction + arithmetic expression verification
- Fails only on task 006 (bit counting: returns 0, expects 24)

**Second best**: `explore-tool-driven-computation` (80% accuracy, context cost 1516.3)
- Asks LLM to describe algorithm, then executes with tools
- Fails on complex ledger calculations (tasks 002, 008: wrong pattern extraction)

Let me read the key traces to formulate hypotheses:

From `exploit-expression-evaluation/eval/search/traces/evolve-numeric-006.jsonl`:
- Question: "How many 1 bits are in the binary representation of 62^10?"
- LLM computes correctly: "62^10 = 839,299,365,868,340,224" with "34 bits that are 1"
- But extracted answer is 0 (not 34, not the expected 24)
- Issue: `verify_via_expressions()` only handles multiplication/addition/modular patterns, missing bit operations

From `explore-tool-driven-computation` failures:
- Task 002 (ledger): extracts wrong code, gets 32 instead of 2695810
- The LLM planning approach doesn't generate precise multi-step Python code

---

## Hypothesis 1 (Exploitation): Enhanced pattern-driven verification

**Target**: Fix `exploit-expression-evaluation` by extending computation pattern detection.

The current verification has limited pattern coverage. Extending to all computation types (modular arithmetic, digit sums, bit counting, ledger operations) via universal tool re-execution will handle the full space of numeric problems and improve from 90% → 95%+.

## Hypothesis 2 (Exploration): Pattern-detected direct computation

**Target**: Reverse the strategy from `explore-tool-driven-computation`.

Instead of asking LLM to describe algorithms (imprecise), directly parse question text for computational patterns and generate corresponding Python code. Tools-first execution with LLM fallback is more reliable for computations than LLM-guided planning.

---

```json
{
  "iteration": 3,
  "candidates": [
    {
      "name": "exploit-enhanced-computation-verification",
      "base_system": "exploit-expression-evaluation",
      "hypothesis": "The expression verification is limited to basic arithmetic patterns. Extending pattern detection to cover bit operations, digit sums, modular arithmetic, and ledger calculations, then re-executing all detected computations via tools.python(), will catch computational errors that extraction misses and improve from 90% to near-perfect accuracy.",
      "axis": "exploitation",
      "components": ["expanded_pattern_library", "universal_tool_execution", "multi_type_verification", "ledger_pattern_detection"]
    },
    {
      "name": "explore-direct-pattern-computation",
      "base_system": "seed",
      "hypothesis": "LLM-guided algorithm extraction (explore-tool-driven-computation) fails on complex multi-step problems. Directly parsing question text for computational patterns and generating precise Python code yields higher reliability than LLM planning. Pattern-first strategy with robust extraction fallback handles modular arithmetic, ledgers, digit/bit operations without LLM planning ambiguity.",
      "axis": "exploration",
      "components": ["question_pattern_parsing", "direct_code_generation", "pattern_specific_executors", "tool_first_execution"]
    }
  ]
}
```

=== FILE: agents/exploit-enhanced-computation-verification/harness.py ===
"""
Enhanced verification: extract robustly, then verify all detected computations via tools.
Extends expression-evaluation with complete pattern coverage (bit ops, ledgers, digit sums).
"""

import re


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    prompt = files["prompts/task.md"].replace("{question}", question)
    reply = llm(prompt, system=system)
    
    answer = extract_answer_robust(reply)
    
    # Enhanced: verify via expanded pattern library
    verified = verify_via_all_patterns(answer, reply, question, tools)
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
    
    # Strategy 1: Explicit ANSWER: pattern (highest confidence)
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


def verify_via_all_patterns(answer, reply, question, tools):
    """
    Enhanced verification: detect all computation types and re-execute via tools.
    Handles modular arithmetic, digit sums, bit counting, and ledger calculations.
    """
    
    if not answer:
        return None
    
    answer_clean = answer.replace(',', '')
    
    # Pattern 1: Modular exponentiation (X^Y mod Z)
    mod_result = verify_modular_arithmetic(answer_clean, question, tools)
    if mod_result:
        return mod_result
    
    # Pattern 2: Digit sum of power (sum of digits of X^Y)
    digit_result = verify_digit_sum(answer_clean, question, tools)
    if digit_result:
        return digit_result
    
    # Pattern 3: Bit counting (1 bits in X^Y)
    bit_result = verify_bit_counting(answer_clean, question, tools)
    if bit_result:
        return bit_result
    
    # Pattern 4: Ledger calculation (complex multi-step)
    ledger_result = verify_ledger_calculation(answer_clean, question, tools)
    if ledger_result:
        return ledger_result
    
    # Pattern 5: Original expression patterns (multiplication, addition)
    expr_result = verify_via_expressions(answer_clean, reply, tools)
    if expr_result:
        return expr_result
    
    return None


def verify_modular_arithmetic(answer, question, tools):
    """Detect X^Y mod Z pattern and verify with tools.python()."""
    pattern = r'(\d+)\s*\^\s*(\d+).*?(?:divided by|mod|modulo)\s*(\d+)'
    match = re.search(pattern, question, re.IGNORECASE)
    
    if match:
        base, exp, modulus = match.groups()
        try:
            code = f"print(pow({base}, {exp}, {modulus}))"
            result = tools.python(code)
            result_val = re.findall(r'\d+', result)
            if result_val:
                computed = result_val[-1]
                if computed == answer:
                    return answer  # Matches extracted
                else:
                    return computed  # Tool result takes precedence
        except:
            pass
    
    return None


def verify_digit_sum(answer, question, tools):
    """Detect digit sum pattern and verify with tools."""
    pattern = r'sum.*?(?:decimal\s+)?digits.*?(\d+)\s*\^\s*(\d+)'
    match = re.search(pattern, question, re.IGNORECASE)
    
    if match:
        base, exp = match.groups()
        try:
            code = f"n = {base}**{exp}\nprint(sum(int(d) for d in str(n)))"
            result = tools.python(code)
            result_val = re.findall(r'\d+', result)
            if result_val:
                computed = result_val[-1]
                if computed == answer:
                    return answer
                else:
                    return computed
        except:
            pass
    
    return None


def verify_bit_counting(answer, question, tools):
    """Detect bit counting pattern and verify with tools."""
    # Pattern: "How many 1 bits ... X^Y" or similar
    pattern = r'(?:how many|count).*?1\s*bits?.*?(\d+)\s*\^\s*(\d+)'
    match = re.search(pattern, question, re.IGNORECASE)
    
    if match:
        base, exp = match.groups()
        try:
            code = f"n = {base}**{exp}\nprint(bin(n).count('1'))"
            result = tools.python(code)
            result_val = re.findall(r'\d+', result)
            if result_val:
                computed = result_val[-1]
                if computed == answer:
                    return answer
                else:
                    return computed
        except:
            pass
    
    return None


def verify_ledger_calculation(answer, question, tools):
    """Detect ledger pattern and execute via tools."""
    # Check if question mentions ledger/account/balance
    if not re.search(r'(?:ledger|account|balance|starts at)', question, re.IGNORECASE):
        return None
    
    # Extract starting balance
    start_match = re.search(r'(?:starts?\s+at|starting\s+(?:at|balance)\s+of)\s+(\d+)', question, re.IGNORECASE)
    if not start_match:
        return None
    
    try:
        start_val = int(start_match.group(1))
        code = generate_ledger_code(question, start_val)
        if code:
            result = tools.python(code)
            result_val = re.findall(r'\d+', result.strip())
            if result_val:
                computed = result_val[-1]
                if computed == answer:
                    return answer
                else:
                    return computed
    except:
        pass
    
    return None


def generate_ledger_code(question, starting_balance):
    """Generate Python code to compute ledger balance."""
    code_lines = [f"balance = {starting_balance}"]
    
    # Extract all operations in order they appear
    # This regex-based parsing handles the common ledger patterns
    
    # Pattern: "Add X" or "Subtract X"
    simple_ops = list(re.finditer(
        r'(add|subtract)\s+(\d+)(?:\s|,|;|and)',
        question, re.IGNORECASE
    ))
    
    # Pattern: "N payments/deposits/refunds of X"
    batch_ops = list(re.finditer(
        r'(add|subtract)?\s*(\d+)\s+(payments?|deposits?|refunds?)\s+of\s+(\d+)',
        question, re.IGNORECASE
    ))
    
    # Combine and sort by position
    all_ops = []
    for match in simple_ops:
        op, amount = match.groups()
        all_ops.append((match.start(), ('simple', op.lower(), int(amount))))
    
    for match in batch_ops:
        prefix, count, kind, amount = match.groups()
        # Determine operation: add for deposits/payments, subtract for refunds
        if 'refund' in kind.lower():
            op = 'subtract'
        else:
            op = prefix.lower() if prefix else 'add'
        total = int(count) * int(amount)
        all_ops.append((match.start(), ('batch', op, total)))
    
    # Sort by position
    all_ops.sort(key=lambda x: x[0])
    
    # Generate code
    for _, (op_type, op, amount) in all_ops:
        if op.lower() == 'add':
            code_lines.append(f"balance += {amount}")
        elif op.lower() == 'subtract':
            code_lines.append(f"balance -= {amount}")
    
    code_lines.append("print(balance)")
    return "\n".join(code_lines)


def verify_via_expressions(answer, reply, tools):
    """
    Original expression verification: extract arithmetic from working
    and re-evaluate for correctness.
    """
    
    if not answer:
        return None
    
    answer_clean = answer.replace(',', '')
    
    # Pattern 1: "X * Y = Z" style expressions
    expr_patterns = [
        r'(\d+)\s*[×*]\s*(\d+)\s*[=:]\s*(\d+(?:[,\d]*)?)',
        r'(\d+)\s*[+-]\s*(\d+)\s*[=:]\s*(\d+(?:[,\d]*)?)',
    ]
    
    for pattern in expr_patterns:
        matches = re.finditer(pattern, reply)
        for match in matches:
            groups = match.groups()
            nums = [g.replace(',', '') for g in groups]
            
            if len(nums) == 3:
                try:
                    x, y, z = int(nums[0]), int(nums[1]), int(nums[2])
                    if x * y == z or x + y == z:
                        if str(z) == answer_clean:
                            return answer
                except:
                    pass
    
    return None

=== FILE: agents/exploit-enhanced-computation-verification/src/prompts/system.md ===
You are a helpful assistant.

=== FILE: agents/exploit-enhanced-computation-verification/src/prompts/task.md ===
{question}

=== FILE: agents/explore-direct-pattern-computation/harness.py ===
"""
Direct pattern computation: parse question for computation patterns, generate
precise Python code, execute via tools. Tools-first execution with LLM fallback.
Avoids LLM planning ambiguity that fails on complex multi-step calculations.
"""

import re


def solve(question, llm, tools, files):
    """
    Strategy: Try direct pattern-based computation first.
    Only use LLM if tool execution fails.
    """
    
    # Step 1: Detect computation pattern and try direct execution
    answer = execute_direct_computation(question, tools)
    if answer:
        return answer
    
    # Step 2: Fallback to LLM + robust extraction
    system = files.get("prompts/system.md", "")
    prompt = files["prompts/task.md"].replace("{question}", question)
    reply = llm(prompt, system=system)
    answer = extract_answer_robust(reply)
    
    return answer


def execute_direct_computation(question, tools):
    """
    Detect computation type from question text and execute via tools.
    Handles: modular arithmetic, digit sum, bit counting, ledger calculations.
    """
    
    # Pattern 1: Modular exponentiation (X^Y mod Z)
    result = handle_modular_arithmetic(question, tools)
    if result:
        return result
    
    # Pattern 2: Digit sum of power
    result = handle_digit_sum(question, tools)
    if result:
        return result
    
    # Pattern 3: Bit counting
    result = handle_bit_counting(question, tools)
    if result:
        return result
    
    # Pattern 4: Ledger calculation
    result = handle_ledger_calculation(question, tools)
    if result:
        return result
    
    return None


def handle_modular_arithmetic(question, tools):
    """Execute X^Y mod Z pattern."""
    pattern = r'(?:remainder|modulo|mod).*?(\d+)\s*\^\s*(\d+).*?(?:divided by|mod|modulo)\s*(\d+)'
    match = re.search(pattern, question, re.IGNORECASE)
    
    if match:
        base, exp, modulus = match.groups()
        try:
            code = f"print(pow({base}, {exp}, {modulus}))"
            result = tools.python(code)
            numbers = re.findall(r'\d+', result)
            if numbers:
                return numbers[-1]
        except:
            pass
    
    return None


def handle_digit_sum(question, tools):
    """Execute digit sum of X^Y pattern."""
    pattern = r'sum.*?(?:decimal\s+)?digits.*?(\d+)\s*\^\s*(\d+)'
    match = re.search(pattern, question, re.IGNORECASE)
    
    if match:
        base, exp = match.groups()
        try:
            code = f"n = {base}**{exp}\nprint(sum(int(d) for d in str(n)))"
            result = tools.python(code)
            numbers = re.findall(r'\d+', result)
            if numbers:
                return numbers[-1]
        except:
            pass
    
    return None


def handle_bit_counting(question, tools):
    """Execute bit counting in X^Y pattern."""
    pattern = r'(?:how many|count).*?1\s*bits?.*?(\d+)\s*\^\s*(\d+)'
    match = re.search(pattern, question, re.IGNORECASE)
    
    if match:
        base, exp = match.groups()
        try:
            code = f"n = {base}**{exp}\nprint(bin(n).count('1'))"
            result = tools.python(code)
            numbers = re.findall(r'\d+', result)
            if numbers:
                return numbers[-1]
        except:
            pass
    
    return None


def handle_ledger_calculation(question, tools):
    """Execute ledger calculation pattern."""
    
    # Check for ledger keywords
    if not re.search(r'(?:ledger|account|balance|starts at)', question, re.IGNORECASE):
        return None
    
    # Extract starting balance
    start_match = re.search(r'(?:starts?\s+at|starting\s+(?:at|balance))\s+(\d+)', question, re.IGNORECASE)
    if not start_match:
        return None
    
    try:
        start_val = int(start_match.group(1))
        code = generate_ledger_code(question, start_val)
        if code:
            result = tools.python(code)
            numbers = re.findall(r'\d+', result.strip())
            if numbers:
                return numbers[-1]
    except:
        pass
    
    return None


def generate_ledger_code(question, starting_balance):
    """
    Generate Python code for ledger calculation.
    Parses: "Add X, subtract Y, add N payments of Z, ..." patterns.
    """
    code_lines = [f"balance = {starting_balance}"]
    
    # Find all operations with their positions
    all_ops = []
    
    # Simple operations: "Add X" or "Subtract X"
    for match in re.finditer(r'(add|subtract)\s+(\d+)', question, re.IGNORECASE):
        op, amount = match.groups()
        all_ops.append((match.start(), op.lower(), int(amount), 'direct'))
    
    # Batch operations: "N payments/deposits of M" or "N refunds of M"
    for match in re.finditer(
        r'(?:add\s+)?(\d+)\s+(payments?|deposits?)\s+of\s+(\d+)',
        question, re.IGNORECASE
    ):
        count, kind, amount = match.groups()
        total = int(count) * int(amount)
        all_ops.append((match.start(), 'add', total, 'batch'))
    
    for match in re.finditer(
        r'subtract\s+(\d+)\s+(refunds?)\s+of\s+(\d+)',
        question, re.IGNORECASE
    ):
        count, kind, amount = match.groups()
        total = int(count) * int(amount)
        all_ops.append((match.start(), 'subtract', total, 'batch'))
    
    # Sort by position in text
    all_ops.sort(key=lambda x: x[0])
    
    # Generate code for each operation
    for _, op, amount, _ in all_ops:
        if op == 'add':
            code_lines.append(f"balance += {amount}")
        elif op == 'subtract':
            code_lines.append(f"balance -= {amount}")
    
    code_lines.append("print(balance)")
    return "\n".join(code_lines)


def extract_answer_robust(text):
    """
    Robust extraction fallback: extract numeric answer from LLM response.
    Uses multiple strategies: ANSWER: pattern, bold numbers, numbers after keys, last number.
    """
    
    # Strategy 1: Explicit ANSWER: pattern
    match = re.search(r'ANSWER:\s*([^\n]+)', text, re.IGNORECASE)
    if match:
        value = match.group(1).strip()
        value = re.sub(r'\*\*|__', '', value).strip()
        if value and re.search(r'\d', value):
            return value
    
    # Strategy 2: Bold numbers
    bold_numbers = re.findall(r'\*\*(\d+(?:[,\d]*)?)\*\*', text)
    if bold_numbers:
        return bold_numbers[-1].replace(',', '')
    
    # Strategy 3: Numbers after key phrases
    colon_matches = re.findall(
        r'(?:answer|result|value|remainder|sum|count|balance|total|is|equals?|final)[\s:=]+(\d+(?:[,\d]*)?)',
        text, re.IGNORECASE
    )
    if colon_matches:
        return colon_matches[-1].replace(',', '')
    
    # Strategy 4: Last standalone number
    all_numbers = re.findall(r'\b(\d+(?:[,\d]*)?)\b', text)
    if all_numbers:
        return all_numbers[-1].replace(',', '')
    
    # Strategy 5: Last non-empty line
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    return lines[-1] if lines else ""

=== FILE: agents/explore-direct-pattern-computation/src/prompts/system.md ===
You are a helpful assistant.

=== FILE: agents/explore-direct-pattern-computation/src/prompts/task.md ===
{question}