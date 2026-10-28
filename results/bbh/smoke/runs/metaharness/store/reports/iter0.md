# Iteration 0: Seed Baseline

## System
**seed** - Single direct model call with generic system prompt.

## Design
- System prompt: "You are a helpful assistant."
- Task prompt: "{question}\n\nEnd with a final line `ANSWER: <answer>`."
- Architecture: One-shot direct answering without intermediate steps or memory

## Results
- **Accuracy**: 100% (2/2 correct on search set)
- **Context cost**: 1889 tokens
- **Steps**: 1 (single model call)
- **Task type**: multistep_arithmetic_two

## Details
The seed baseline perfectly solves complex arithmetic expressions (e.g., `((4 + 7 * 4 - -5) - (-4 - 1 - -4 - 4)) = 42`) by having the model perform step-by-step reasoning naturally. The model breaks down operations and arrives at correct answers without explicit scaffolding.

## Takeaway for Next Iteration
The baseline is simple but effective on arithmetic tasks. Future improvements should explore:
1. **Mechanism diversity**: Test whether the natural CoT behavior generalizes to other task types (multiple choice, yes/no, counting, sequences)
2. **Generalization**: Introduce reasoning architectures that work across task families, not just arithmetic
3. **Efficiency**: If accuracy remains high, explore ways to reduce context cost while preserving correctness
