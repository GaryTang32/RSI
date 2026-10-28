# ood-sarc_triples-196

- adopted: `agg1`  (harness `567f1450f5`)
- components: 3 files (1 code, 2 text); 83 text chars

## Why this was chosen
trivial pool: single final at k=1; delivered without a selector session

## What each edit did
component: harness.py / solve() function
candidates: inv1, dom1, agg1
change: Added logic to parse the LLM reply line-by-line, locate the line starting with "ANSWER:", extract the value after that prefix, and return only that extracted value. Fallback: return full reply if no "ANSWER:" line found.
observed: base1 (no extraction) returned full LLM reasoning text despite task explicitly requiring "comma-separated set of labels" (format mismatch). inv1/dom1/agg1 (with extraction) returned only "0,1,1", matching the required format. All four rollouts produced identical labels (0,1,1), confirming the LLM reasoning is unchanged and the issue is formatting, not correctness.
verdict: helped

## Transferable lesson
- **cue**: A task specifies a narrow, structured output format (e.g., comma-separated labels, JSON, or a specific template). The LLM is instructed via prompt to mark its final answer with a consistent prefix or line marker. The harness returns the full LLM response, producing a format mismatch with the task's explicit requirement.
- **lesson**: In harness.py's solve() function, add extraction logic that searches the LLM reply for the marked answer line (e.g., line starting with "ANSWER:"), parses out and returns only the value after the marker. Include a fallback that returns the full reply if the marker is not found. This decouples answer extraction from reasoning generation without requiring additional LLM calls or prompt rewrites.
- **evidence**: base1 trajectory shows LLM consistently producing "ANSWER: 0,1,1" at the end of reasoning, yet `reply.strip()` returns the full text, causing the deliverable to violate the task requirement. inv1, dom1, and agg1 independently applied the same extraction fix; all three delivered "0,1,1" matching the format. All rollouts produced identical labels (0,1,1), isolating the change to formatting only. The decision documents cite library entry ood-sarc_triples-001 documenting this pattern as proven reliable.
- **confidence**: high. The before-after contrast is clear and unambiguous (full text vs. labels only); all three candidates converged on the same solution independently; LLM behavior is consistent across rollouts; no trade-offs in reasoning quality; and the fix directly addresses an explicitly stated task requirement.
