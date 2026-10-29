# Library: shuffled_objects

Each entry is an earlier task in THIS domain whose harness was chosen and whose improvement record supported a transferable lesson. The harness is kept whole, not summarised. Browse this index, then open the entries that look relevant to the matter in front of you.

- `harnesses/holdout-shuffled_objects-020/harness/` -- 3 files (1 code, 2 text); 83 text chars
  `harnesses/holdout-shuffled_objects-020/EXPERIENCE.md` -- cue: A task requires mechanical execution of many sequential deterministic operations (swaps, exchanges, position changes), where each operation follows a parseable pattern, is simple and deterministic, but there are so many (100s) that LLM reasoning must either hand-wave or exhaust reasoning budget. The LLM correctly identifies the right method but cannot execute all steps exhaustively in a single response without losing precision.
- `harnesses/holdout-shuffled_objects-062/harness/` -- 3 files (1 code, 2 text); 83 text chars
  `harnesses/holdout-shuffled_objects-062/EXPERIENCE.md` -- cue: A task requires mechanical execution of many sequential deterministic operations (state tracking through repeated transformations). The LLM reasoning identifies the correct method ("I need to systematically track...") but fails to execute it completely in a single response, showing the gap between recognized approach and actual implementation.
- `harnesses/holdout-shuffled_objects-154/harness/` -- 3 files (1 code, 2 text); 83 text chars
  `harnesses/holdout-shuffled_objects-154/EXPERIENCE.md` -- cue: A task requires the agent to execute a long sequence (100+) of sequential deterministic operations that are explicitly specified in the task text; operations may be named for reference and later repeated by name; the LLM can identify the correct solution method but cannot exhaustively execute without consuming tokens on mechanical work.
