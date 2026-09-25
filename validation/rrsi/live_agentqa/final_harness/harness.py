"""Harness: extract ANSWER: marker or return last line."""
import re


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    prompt = files["prompts/task.md"].replace("{question}", question)
    reply = llm(prompt, system=system)
    
    # Strategy 1: Look for ANSWER: pattern on any line
    for line in reply.split('\n'):
        if 'ANSWER:' in line:
            match = re.search(r'ANSWER:\s*([^\n]+)', line)
            if match:
                value = match.group(1).strip()
                # Remove any markdown formatting
                value = re.sub(r'\*\*([^*]*)\*\*', r'\1', value)
                value = re.sub(r'[*_]', '', value).strip()
                return value
    
    # Strategy 2: Fallback to last non-empty line
    lines = [line.strip() for line in reply.strip().split('\n') if line.strip()]
    return lines[-1] if lines else ""
