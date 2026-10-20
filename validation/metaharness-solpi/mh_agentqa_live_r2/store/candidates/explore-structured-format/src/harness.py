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
