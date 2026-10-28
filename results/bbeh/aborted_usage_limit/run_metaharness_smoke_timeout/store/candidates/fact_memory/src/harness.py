"""
fact_memory harness: Two-phase problem solving with fact extraction.

Mechanism (Axis B+C): Extract and store facts/rules, then use them to solve.
- Phase 1: Extract rules/patterns/facts from examples
- Phase 2: Use extracted facts to solve the target problem
- Helps with linguistic problems, pattern matching, and symbolic reasoning
"""

import re


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    task_template = files["prompts/task.md"]

    # Detect if this is a problem with example-based patterns
    if has_examples(question):
        # Extract examples from the question
        examples_section = extract_examples_section(question)
        target_section = extract_target_section(question)

        if examples_section and target_section:
            # Phase 1: Extract facts/rules from examples
            extraction_prompt = f"""Given the following examples, extract all the key facts, rules, and patterns you can identify:

{examples_section}

Provide a concise extraction of:
1. Key word/morpheme meanings or mappings
2. Grammatical patterns or rules
3. Any other relevant patterns for solving problems of this type

Format your response as a numbered list of facts and rules."""

            facts_response = llm(extraction_prompt, system=system)

            # Phase 2: Solve the problem using the extracted facts
            solving_prompt = f"""Using the following extracted facts and rules:

{facts_response}

Now solve this problem:
{target_section}

End with a final line `ANSWER: <answer>`."""

            reply = llm(solving_prompt, system=system)
            return reply.strip()

    # Fallback to simple one-shot for other problems
    prompt = task_template.replace("{question}", question)
    reply = llm(prompt, system=system)
    return reply.strip()


def has_examples(question):
    """Detect if question contains example patterns (e.g., translation pairs)."""
    # Look for patterns like "Here are...", numbered examples with |, etc.
    patterns = [
        r"Here are some sentences",
        r"\d+\.\s+\S+.*\|",  # numbered items with | separator
        r"Here are.*translations?",
        r"Here are.*examples?",
        r"Translate into",
    ]
    return any(re.search(pattern, question, re.IGNORECASE) for pattern in patterns)


def extract_examples_section(question):
    """Extract the part of the question containing examples."""
    # Find the examples (usually numbered items) and the separator before the target
    # Look for pattern: number. text | translation or similar
    match = re.search(r"((?:\d+\.\s+.+?\n)+)", question)
    if match:
        return match.group(1)

    # Alternative: everything before "Translate into" or "Now translate" etc.
    match = re.search(r"(.*?)(Translate into|Now translate|What is|What does)", question, re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1)

    return None


def extract_target_section(question):
    """Extract the target problem (what we're actually trying to solve)."""
    # Find from "Translate into" or similar onwards
    match = re.search(r"(Translate into.*|Now translate.*|What (?:is|does).*)", question, re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1)

    # If no clear separator, return last few lines
    lines = question.strip().split('\n')
    if len(lines) > 3:
        return '\n'.join(lines[-3:])

    return None
