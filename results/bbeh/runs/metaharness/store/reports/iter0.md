# Iteration 0: Seed Baseline

## Summary
Baseline harness with single direct LLM call. Sets foundation for improvements.

## Results
- **Overall accuracy**: 62.5% (15/24 correct on search set)
- **Context cost**: 19,006.5 characters per query
- **Best tasks**: object_properties (100%), causal_understanding (75%), boolean_expressions (75%), web_of_lies (75%)
- **Worst tasks**: linguini (25%), shuffled_objects (25%)

## Key Observations
1. **Strengths**: Model handles well-defined reasoning (boolean logic, causal understanding) and object property queries
2. **Weaknesses**: 
   - Language pattern learning (linguini): Model derives patterns but often incorrectly
   - Complex state tracking (shuffled_objects): Manual reasoning error-prone; needs better structure
3. **Cost profile**: Efficient single-call baseline; room for improvement without breaking cost budget
4. **Architecture**: Simple system + task prompt structure; minimal guidance to model

## Takeaway
The 37.5% error rate is concentrated in two task families requiring explicit reasoning strategies. Future iterations should focus on prompt engineering to guide the model through:
1. Systematic decomposition for tracking problems
2. Pattern extraction and verification for language tasks

Both improvements can likely be achieved without multiple LLM calls.
