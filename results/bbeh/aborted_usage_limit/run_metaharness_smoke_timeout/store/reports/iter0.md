# Iteration 0: Seed Baseline

## System Design
Single-shot approach: pass question directly to LLM with minimal prompt ("End with ANSWER: <answer>").

## Results
- Score: 62.5% (15/24)
- Context cost: 19,006.5

## Performance by Category
**Succeeding (10/10):** boolean_expressions variants, causal_understanding (3/4)
**Failing:** linguini tasks (0/4), shuffled_objects (1/4), web_of_lies (3/4)

## Key Observations
1. **Linguistic tasks completely break**: All linguini translation problems fail. The model struggles with pattern-matching across unfamiliar language examples.
2. **Complex reordering fails**: shuffled_objects tasks are mostly wrong, suggesting the model doesn't reliably track object positions through transformations.
3. **Arithmetic + logic struggles**: causal_understanding and web_of_lies have mixed results—reasoning tasks with multiple constraints are brittle.

## Hypotheses for Improvement
- Breaking complex problems into sub-steps via intermediate tool calls or reflection
- Storing and retrieving relevant examples (few-shot memory) for pattern extraction
- Structured reasoning pathways (e.g., Python validation for logic problems)
- Better error recovery when the first answer fails
