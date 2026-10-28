# holdout-linguini-137

- adopted: `agg1`  (harness `751d8650c1`)
- components: 3 files (1 code, 2 text); 83 text chars

## Why this was chosen
trivial pool: single final at k=1; delivered without a selector session

## What each edit did
component: solve() function / answer extraction logic
candidates: dom1, agg1
change: Added loop to parse reply for line starting with "ANSWER:", extract the value after the prefix, and return only that extracted value (with fallback to reply.strip() if no ANSWER line found). Modified docstring from "the reply (ending in an ANSWER line) is the deliverable" to "extract and return the final answer value".
observed: base1 (base_harness, no extraction): returned full text including reasoning and "ANSWER: atlow nopъl ńollow" line. dom1 (extraction added): extracted and returned "ńollow atlow", but LLM generated different answer than base1's. agg1 (extraction added): extracted and returned "atlow nopъl ńollow", same answer value as base1's but in cleaned format (value only, not full reasoning text). Extraction logic functioned correctly in both dom1 and agg1 — it successfully found and extracted the ANSWER line in each case.
verdict: confounded (dom1) / helped (agg1). Dom1 changed both the harness extraction logic and triggered different LLM reasoning, producing different numerical answer — impossible to isolate which caused the difference. Agg1 used same LLM output as base1 (both "atlow nopъl ńollow"), so the harness change is isolated: extraction successfully cleaned the deliverable format from full text + reasoning to value-only, matching the task requirement to "spell out" a numeral (a value, not an explanation).

component: solve() function / answer extraction logic (inv1 candidate)
candidates: inv1
change: No change from base_harness (both versions return reply.strip() without extraction logic; inv1 remains identical to base_harness).
observed: inv1 generated and delivered full text (same format as base1), but LLM produced different answer "atlow ńollow" instead of base1's "atlow nopъl ńollow". Harness code executed identically to base_harness; the execution difference was entirely in LLM output variation, not harness behavior.
verdict: no observed effect (the harness code did not change, so no harness effect to measure; the answer difference is LLM variation, not a result of the harness edit).

## Transferable lesson
- **cue**: A task requests a specific value as the answer (not explanation or reasoning), and the harness prompts the LLM to format its response with an "ANSWER: value" line.
- **lesson**: Extract and return only the ANSWER line value in the solve() function's return statement, using safe fallback logic (return full reply if ANSWER line not found), rather than returning the full LLM reply that includes reasoning. Apply this when task instructions ask for a value, not a derivation.
- **evidence**: agg1 execution. The base1 rollout and agg1 rollout generated identical LLM reasoning and the same final answer ("atlow nopъl ńollow"), but base1's deliverable included the full reasoning text while agg1's returned only the extracted value. This isolates the harness change's effect: extraction logic successfully separated the answer value from the reasoning, delivering clean output matching the task's request for a Mansi numeral spelling (a value, not guidance). The fallback logic (return reply.strip() if no ANSWER line found) provides safety. Dom1 cannot support this lesson because it changed both harness logic and LLM output (confounded).
- **confidence**: medium. The evidence is solid in one execution (agg1 showing extraction working correctly with same LLM input as base), and the principle (extract values when values are requested) is sound and stated in the decision reasoning. However, only one clean execution isolates the effect; dom1's confounded results prevent stronger support. The lesson is narrow enough to be reusable (clear condition: task asks for value + ANSWER line format) but rests on limited direct observation.
