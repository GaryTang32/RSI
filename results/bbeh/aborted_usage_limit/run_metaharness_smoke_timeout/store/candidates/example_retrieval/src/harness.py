"""
example_retrieval harness: In-context learning with similar problem examples.

Mechanism (Axis C): Detect problem type and inject relevant examples.
- For linguini (translation/pattern) problems: Show examples of successful pattern analysis
- For boolean_expressions and logic: Keep it simple, let model work through logic
- For other types: Use seed approach
"""

import re


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")

    # Detect problem family
    family = detect_problem_family(question)

    if family == "linguini":
        # Use linguini-specific prompt with examples
        prompt = build_linguini_prompt(question)
    else:
        # Use seed behavior for other problem types
        prompt = files["prompts/task.md"].replace("{question}", question)

    reply = llm(prompt, system=system)
    return reply.strip()


def detect_problem_family(question):
    """Detect problem type based on question content."""
    question_lower = question.lower()

    # Linguini: translation, plural, singular, language patterns
    if any(word in question_lower for word in [
        'translate', 'plural', 'singular', 'nouns', 'verbs', 'grammar',
        'language', 'words', 'morpheme', 'phonetic'
    ]):
        return "linguini"

    # Other families are handled by seed logic
    return "other"


def build_linguini_prompt(question):
    """Build enhanced prompt for linguini/translation problems."""

    # Embed strategy for analyzing linguistic patterns
    strategy = """# Strategy for Linguistic Pattern Analysis

When analyzing linguistic patterns:
1. **Identify the transformation rule**: What changes between source and target?
   - Look at all examples systematically
   - Note prefix/suffix changes
   - Track which parts stay the same

2. **Find the pattern**: What rule explains ALL examples?
   - Check if it's consistent
   - Look for exceptions that reveal the true rule

3. **Apply to new case**: Use the pattern on the unseen example
   - Verify your answer fits the pattern
   - Check for edge cases

4. **Give final answer in format**: ANSWER: <your answer>
"""

    # Build the prompt with strategy and the original question
    prompt = f"""{strategy}

Now solve this linguistic pattern problem:

{question}

End with a final line `ANSWER: <answer>`."""

    return prompt
