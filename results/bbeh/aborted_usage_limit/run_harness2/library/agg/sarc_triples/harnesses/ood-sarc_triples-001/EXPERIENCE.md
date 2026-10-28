# ood-sarc_triples-001

- adopted: `agg1`  (harness `567f1450f5`)
- components: 3 files (1 code, 2 text); 83 text chars

## Why this was chosen
trivial pool: single final at k=1; delivered without a selector session

## What each edit did
component: harness.py (solve function)
candidates: inv1, agg1
change: Added logic to extract the ANSWER line: parse the LLM reply for lines starting with "ANSWER:", extract the value after it, and return only that comma-separated label value (fallback to full reply if not found)
observed: base1 rollout (no extraction): delivered full reasoning text with "ANSWER: 1,0,1" embedded within it—format mismatch with explicit task request "provide your final answer as a comma-separated set of labels". inv1 rollout (with extraction): delivered "1,0,1" only, matching requested format (produced.md shows labels alone). dom1 rollout (no extraction): delivered full reasoning text, reproducing the format mismatch. agg1 rollout (with extraction): delivered "1,0,1", matching format. decisions/inv1.md explicitly documents the format problem: "FORMAT MISMATCH between what was requested and what was delivered... Current deliverable: Full explanation + ANSWER line."
verdict: helped

## Transferable lesson
- **cue**: A task explicitly specifies a narrow, structured output format (such as comma-separated labels, a code, or specific field values), but the language model naturally produces verbose reasoning or explanation with that formatted answer embedded or marked within it.
- **lesson**: In the harness solve() function, add extraction logic to identify and isolate the formatted answer from surrounding text. If the prompt instructs the LLM to mark the answer with a line prefix (e.g., "ANSWER:"), parse the reply for that marker, extract the value after it, and return only that. Include a fallback to return the full reply if the marker is not found.
- **evidence**: base1 (no extraction) failed the format requirement documented in inv1.decisions—it delivered full reasoning instead of "comma-separated labels" as the task requested. inv1 and agg1 (both with extraction) each delivered only "1,0,1", matching the specified format. All observed LLM trajectories consistently end with an "ANSWER:" line as the prompt instructs (confirmed by rollout outputs), so extraction is reliable. dom1's attempt without extraction reproduced the base format mismatch, confirming extraction solves it. The labels themselves (1,0,1) are correct in all versions, so extraction adds format compliance without sacrificing accuracy.
- **confidence**: high
