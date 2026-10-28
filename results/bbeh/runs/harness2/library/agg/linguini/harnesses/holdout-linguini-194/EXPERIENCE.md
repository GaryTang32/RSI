# holdout-linguini-194

- adopted: `agg1`  (harness `751d8650c1`)
- components: 3 files (1 code, 2 text); 83 text chars

## Why this was chosen
trivial pool: single final at k=1; delivered without a selector session

## What each edit did
component: harness.py - solve() function return logic
candidates: inv1, dom1, agg1
change: Replaced `return reply.strip()` with conditional extraction: searches for lines starting with "ANSWER:", extracts and returns the substring after "ANSWER:" if found, otherwise falls back to `return reply.strip()`
observed: base1 delivered the full LLM reasoning text plus "ANSWER: tʊ́rʊ́tʊ̂tʊ̂" as a single string (~600+ characters including reasoning). All three candidates (inv1, dom1, agg1) produced extracted values only: inv1 and dom1 delivered the answer with/without markdown formatting, agg1 delivered a different answer. The extraction logic successfully isolated the final answer from the preceding reasoning text. See base1 rollout trajectory (full reply) vs. inv1/dom1/agg1 trajectories (extracted portion matching ANSWER: line content).
verdict: helped

## Transferable lesson
- **cue**: A task requests a specific value as its answer (not explanation or derivation), and the harness instructs the LLM to format output with "End with a final line `ANSWER: <answer>`."
- **lesson**: Add conditional extraction logic to solve(). Search the LLM reply for a line starting with "ANSWER:", extract the substring after "ANSWER:" by stripping whitespace, and return only that value. Include fallback logic to return the full reply if no ANSWER line is found. This applies to the harness.py solve() function's return statement.
- **evidence**: All three candidates (inv1, dom1, agg1) implemented identical extraction logic and all successfully transformed the deliverable from full reasoning text to isolated answer values. base1 rollout shows the problem clearly: the reply string contains both derivation and the marked answer, and returning `reply.strip()` delivers the entire text. The decisions files (inv1.md, dom1.md) cite library lesson holdout-linguini-137 and identify that this task matches the cue exactly: it requests a specific value (the Agbirigba form), does not ask for reasoning, and instructs output format with ANSWER line. The pattern holds across all three candidates despite different LLM responses.
- **confidence**: high. The problem is clearly observable in base1 vs. the candidates' behavior. The harness change is mechanical and reusable: whenever solve() receives an LLM reply formatted with an ANSWER line, extracting that line's value correctly separates the deliverable from the reasoning. The library lesson directly applies and all three independent implementations confirm the approach works.
