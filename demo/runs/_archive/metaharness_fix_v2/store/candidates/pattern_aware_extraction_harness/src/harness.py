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
