"""
Candidate: Pattern Table Extraction for Language Problems

Mechanism: Explicit morphological/pattern table construction for language learning.
1. Detect language/pattern learning problems (linguini, morphology, grammar)
2. Ask model to build explicit mapping tables from examples
3. Verify table against all given examples
4. Apply rule to generate answer
5. For all other problems: use baseline text reasoning

Hypothesis: Linguini problems are at 25% success with informal pattern matching.
By asking the model to build EXPLICIT tables of morphological components and
verify them systematically against examples (rather than reasoning informally),
we create better scaffolding for pattern discovery. This doesn't over-constrain
other problem types (like cand_decomposed_subgoals did).

Axis: A (prompt template) - specialized prompting for pattern problems
Axis: B (memory/state) - build and verify explicit component mapping tables
"""


def solve(question, llm, tools, files):
    """Solve using pattern tables for language problems, text for others."""

    system = files.get("prompts/system.md", "")

    # Detect if this is a language/pattern problem
    if is_language_pattern_problem(question):
        # Try pattern-table-based solution
        pattern_answer = solve_with_pattern_table(question, llm, files)
        if pattern_answer:
            return pattern_answer

    # Fall back to text reasoning (like seed)
    return solve_with_text(question, llm, files)


def is_language_pattern_problem(question):
    """
    Detect language/morphology/pattern learning problems.

    Key signals:
    1. Explicit examples provided ("Here are...", "Given...")
    2. Translation or morphological learning ("translate", "forms", "verb", "noun")
    3. Reasonable problem length (<5000 chars - pattern problems are typically concise)
    """
    lower_q = question.lower()

    # Pattern/morphology keywords
    pattern_keywords = [
        'translate',
        'translation',
        'plural',
        'verb',
        'noun',
        'grammar',
        'morphology',
        'forms',
        'form of',
        'ending',
        'prefix',
        'suffix',
    ]

    # Example-providing keywords
    example_keywords = [
        'here are',
        'following',
        'examples',
        'given',
        'provided',
    ]

    has_pattern = any(kw in lower_q for kw in pattern_keywords)
    has_examples = any(kw in lower_q for kw in example_keywords)
    reasonable_length = len(question) < 5000  # pattern problems are typically concise

    return has_pattern and has_examples and reasonable_length


def solve_with_pattern_table(question, llm, files):
    """Solve using explicit pattern table construction and verification."""
    system = files.get("prompts/system.md", "")

    # Ask model to build and verify pattern table
    table_prompt = files["prompts/pattern_table.md"].replace("{question}", question)
    reply = llm(table_prompt, system=system)

    # Extract answer from the reply
    # The model should provide ANSWER: line in the pattern_table prompt
    answer = extract_answer(reply)
    if answer:
        return answer

    return None


def solve_with_text(question, llm, files):
    """Solve using text reasoning (like seed)."""
    system = files.get("prompts/system.md", "")
    text_prompt = files["prompts/task.md"].replace("{question}", question)
    reply = llm(text_prompt, system=system)
    return reply.strip()


def extract_answer(text):
    """Extract ANSWER line from response."""
    lines = text.split('\n')
    for line in lines:
        if line.startswith('ANSWER:'):
            return line[7:].strip()

    # Fallback: last non-empty line
    for line in reversed(lines):
        if line.strip():
            return line.strip()

    return None
