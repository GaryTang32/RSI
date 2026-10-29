# holdout-shuffled_objects-154

- adopted: `agg1`  (harness `457d5e0569`)
- components: 3 files (1 code, 2 text); 83 text chars

## Why this was chosen
trivial pool: single final at k=1; delivered without a selector session

## What each edit did
component: solve() function and embedded code strategy
candidates: inv1, dom1, agg1
change: Replace direct LLM call with deterministic Python code embedded in solve() that parses the question text using regex, builds a map of named actions to their swap operations, and executes all swaps (including repeats) in order via tools.python(). No LLM involvement in execution.
observed: base1 (using base_harness simple LLM call) delivered (E) green using 29,873 tokens; inv1, dom1, agg1 all produce (D) white using 0 tokens. The decisions files (dom1.md, inv1.md) characterize base1's manual transcription as incomplete and assert that the embedded parsing code converges on the correct answer. The shift from reasoning tokens to zero reasoning tokens shows the computational load moved from LLM to deterministic code.
verdict: helped

component: question parsing method
candidates: inv1 (uses string.split) vs dom1/agg1 (uses re.split)
change: inv1 splits input with `chunks = question.split('Then, ')` while dom1/agg1 split with `re.split(r'Then, |First, |Finally, ', question)` to capture all event boundaries
observed: inv1 rollout outputs white; dom1 rollout outputs white; agg1 rollout outputs white. All three produce identical final answer (D) and identical token cost (0). Both parsing methods successfully extract and execute the same set of swap operations.
verdict: no observed effect

component: variable names and docstring
candidates: inv1 refactored naming vs dom1/agg1 original naming
change: inv1 renames `balls`→`state`, `action_defs`→`action_map`, `swaps`→`swap_log`, and expands docstring with explanatory text about parsing strategy; dom1/agg1 retain original names and concise docstring
observed: Identical output (white, answer D) from both inv1 and dom1/agg1. No difference in execution trajectory or result. The decisions file (agg1.md) notes that dom1's variable names are "more explicit and defensive" but acknowledges both produce "identical output".
verdict: no observed effect

## Transferable lesson
- **cue**: A task requires the agent to execute a long sequence (100+) of sequential deterministic operations that are explicitly specified in the task text; operations may be named for reference and later repeated by name; the LLM can identify the correct solution method but cannot exhaustively execute without consuming tokens on mechanical work.
- **lesson**: Embed deterministic parsing code in solve() that extracts operations from the task text using pattern matching (regex or string split), builds a map of named operations to their definitions, logs all operations including repeats in order, executes them via tools.python(), and returns the final state. Do not delegate the execution loop to the LLM; use code.
- **evidence**: The trajectory from base1 (LLM reasoning on manual transcription: 29,873 tokens, answer E) to inv1/dom1/agg1 (embedded parsing: 0 tokens, answer D) shows deterministic parsing eliminates the transcription burden. The decisions files (inv1.md, dom1.md) cite library precedent (holdout-shuffled_objects-020 and -062) solving equivalent problems with the same pattern. The fact that inv1's string.split() and dom1/agg1's re.split() both converge on the same answer shows the pattern is robust to implementation variation in the parsing strategy.
- **confidence**: medium. The execution improvement is clear and the decisions files invoke library precedent, but the record does not independently verify which answer (D vs E) is actually correct—only that the decisions chose to trust the embedded code. The task's structure (explicit action labels and repeat directives) is somewhat specific, so the transferability depends on whether future tasks have similar machine-readable operation references. However, the library precedent cited suggests the pattern generalizes to other "100+ operations" problems.
