# holdout-linguini-016

- adopted: `agg1`  (harness `751d8650c1`)
- components: 3 files (1 code, 2 text); 83 text chars

## Why this was chosen
trivial pool: single final at k=1; delivered without a selector session

## What each edit did
component: harness.py — solve() function, return statement
candidates: inv1, dom1, agg1
change: Replaced `return reply.strip()` with extraction logic that scans the LLM reply line-by-line, finds the first line starting with "ANSWER:", returns only the substring after the prefix (whitespace-stripped), and falls back to `return reply.strip()` if no ANSWER line is found.
observed: base1 trajectory shows LLM reply ending "ANSWER: mɨtay" surrounded by reasoning text; base1 deliverable returned the full reply (~500+ characters of analysis). inv1/dom1/agg1 trajectories show identical LLM outputs, but extraction logic isolated the answer: inv1 produced "**mɨtay** for (8) and **zaːiʔ⁴²** for (9)" (the full ANSWER line, including element 9), dom1 produced "mɨtay" (element 8 only), agg1 produced "**mɨtay**" (element 8 with markdown). The extraction separated the answer value from reasoning. All three candidates delivered successfully. Base1 also delivered, but returned what the LLM said, not what the task asked for.
verdict: helped

## Transferable lesson
- **cue**: A task requests a single specific value (not reasoning), and the harness instructs the LLM to output with a fixed "ANSWER: <value>" line format at the end.
- **lesson**: In solve(), extract the answer by scanning the LLM reply line-by-line, finding the first line beginning with "ANSWER:", and returning only the substring after that prefix (stripped). Include a fallback to return the full reply if no such line exists. This separates the deliverable (the requested value) from any preceding reasoning.
- **evidence**: All three candidates (inv1, dom1, agg1) applied this identical change. The task explicitly requests "Fill in the masked element indicated with (8)"—a specific value, not a derivation. The decisions documents (inv1.md, dom1.md, agg1.md) all cite library precedent from holdout-linguini-137 and holdout-linguini-194, which documents the exact same cue and solution as beneficial. All candidates delivered successfully. The extraction cleanly isolates the answer value from reasoning text, aligning the deliverable with what the task actually requests.
- **confidence**: high — The pattern is documented in multiple prior tasks in the library with explicit cue and solution. This task matches the cue exactly: specific value requested + ANSWER line format instructed. All implementations of the extraction succeeded.
