# Iteration 1: Fact Memory & Step Validate (Failed)

## Candidates
1. **fact_memory** (Axis B+C): Two-phase problem solving with fact extraction before solving
2. **step_validate** (Axis F): Code extraction and execution for validation

## Results
- Both candidates: 0% accuracy (complete failure)
- Baseline (seed): 62.5% (still best)

## What Failed
**fact_memory**: The heuristic-based extraction (looking for "Translate into", numbered items) broke on the actual diverse question formats. The two-phase approach added overhead without improving pattern matching, and the LLM's extracted "facts" were often incorrect or irrelevant.

**step_validate**: Code extraction regex was too fragile. The model didn't reliably write complete, runnable Python blocks in its responses, and the refinement prompt didn't fix the missing ANSWER line issue.

## Key Insights
- **Heuristic-based detection fails**: String pattern matching for problem types is brittle and doesn't generalize
- **Intermediate LLM calls hurt more than help**: Each added call (fact extraction, refinement) without better retrieval increases cost and compounds errors
- **Code extraction alone isn't enough**: Models don't always write code or write incomplete code when asked implicitly

## Takeaway for Iteration 2
- Avoid heuristic-based problem decomposition; use learned patterns instead
- Focus on retrieval mechanisms (which examples/strategies to show) rather than explicit fact extraction
- If using code, must explicitly require and validate it before trusting the output
- Mechanisms must be deterministic and robust to diverse input formats
