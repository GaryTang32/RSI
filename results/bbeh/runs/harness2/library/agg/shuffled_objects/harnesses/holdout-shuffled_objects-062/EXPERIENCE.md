# holdout-shuffled_objects-062

- adopted: `agg1`  (harness `1570d67c43`)
- components: 3 files (1 code, 2 text); 83 text chars

## Why this was chosen
trivial pool: single final at k=1; delivered without a selector session

## What each edit did
component: harness.py - solve() function implementation, and prompts/task.md
candidates: dom1
change: Kept the LLM-only solve() approach (identical to base), but added 2 sentences of prompt guidance to task.md: "Use Python to systematically track all partner exchanges. Execute the sequence of operations in order, then provide the final answer."
observed: base1 shows 25313 tokens and incomplete reasoning. dom1 shows 13751 tokens (lower token cost) but the rollout trajectory reveals the LLM attempted to use bash function calls (invoke_name_of_function) outside the harness tools.python() API. The produced.md file is truncated mid-Python-script without a final ANSWER line; the trajectory cuts off at an incomplete code block. The rollout was marked delivered=yes but the deliverable itself is incomplete and does not end with the required ANSWER line.
verdict: hurt

## Transferable lesson
- **cue**: A task requires mechanical execution of many sequential deterministic operations (state tracking through repeated transformations). The LLM reasoning identifies the correct method ("I need to systematically track...") but fails to execute it completely in a single response, showing the gap between recognized approach and actual implementation.
- **lesson**: Replace the LLM reasoning with Python code embedded directly in the harness.solve() function. The code should parse the operations from the input using regex or string matching, apply each operation as a deterministic state transformation (loop and update), and return the final result via tools.python(). This moves the mechanical repetition from reasoning budget to verified code execution, ensuring all operations are tracked rather than selectively reasoned about.
- **evidence**: inv1 vs base1 shows the same final deliverable (both ANSWER: (A) Patrick) but inv1's execution trajectory demonstrates complete tracking—the regex finds all operations and the loop processes every one—while base1's trajectory explicitly shows incomplete reasoning ("hand-waves through 360+ operations," only spot-checks final steps). dom1's attempt to use prompt guidance alone fails (non-delivery, LLM tried invalid bash calls outside the API). The successful approach (inv1, agg1) is independent code execution, not LLM guidance. The decision documents cite this as the "mechanical repetition" pattern requiring "deterministic helper in harness code" per mission guidance, not prompt enhancement.
- **confidence**: high — Two independent candidate executions (inv1, agg1) confirm the same successful mechanism. The contrasting failure mode (dom1) isolates the working component. Base1's trajectory explicitly surfaces the vulnerability (incomplete manual reasoning) that this edit eliminates. No confounding: the change is isolated (code strategy), and the execution trajectories show the complete vs incomplete divide clearly.
