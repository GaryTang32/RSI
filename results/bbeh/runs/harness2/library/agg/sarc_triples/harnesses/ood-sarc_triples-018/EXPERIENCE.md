# ood-sarc_triples-018

- adopted: `agg1`  (harness `567f1450f5`)
- components: 3 files (1 code, 2 text); 83 text chars

## Why this was chosen
trivial pool: single final at k=1; delivered without a selector session

## What each edit did
component: harness.py / solve()
candidates: inv1, dom1, agg1
change: Added line-by-line parsing of the LLM reply to extract only the value after "ANSWER:" prefix, with fallback to full reply if the marker is not found. Replaced `return reply.strip()` with extraction logic.
observed: base1 (no extraction) delivered full reasoning text violating the task requirement of "comma-separated set of labels"; inv1, dom1, agg1 (with extraction) delivered only the labels, matching the format requirement. Token usage fell ~25% (3945 → 2948–3148) due to stripping verbose reasoning from deliverable. Format compliance is the divergence point: base1 failed the requirement, candidates with extraction met it.
verdict: helped

## Transferable lesson
- **cue**: A task requires structured, concise output format (e.g., comma-separated labels). The LLM naturally produces verbose reasoning. The harness prompt explicitly instructs the LLM to "End with a final line `ANSWER: <value>`", creating a marked answer line within verbose text.
- **lesson**: In solve(), parse the LLM reply line-by-line, extract the value after the "ANSWER:" prefix, and return only that extracted value. This ensures the deliverable matches the task's output format requirement when the format differs from the natural reasoning output. The extraction belongs in solve() as deterministic control flow (not in the prompt), and needs a fallback (return full reply if marker absent).
- **evidence**: base1 (no extraction) violated the task format requirement—deliverable was full reasoning text ending in "ANSWER: 1,1,0", not the required "1,1,0". All three candidates (inv1, dom1, agg1) applied identical extraction logic and delivered format-compliant labels. The decisions document cites library entries ood-sarc_triples-001 and ood-sarc_triples-196 as independent validation of this pattern and solution within the domain. The extraction is mechanical and deterministic; the difference in reasoning correctness between inv1 ("1,1,0") and dom1/agg1 ("0,1,0") is model variance in sarcasm judgment, not a harness effect.
- **confidence**: high. Multiple candidates converged independently on the same fix. Library evidence validates the pattern as recurrent and solved. The mechanism is deterministic (extraction does not depend on language model behavior), and the cause-effect link is direct (base1 format violation → extraction applied → format compliant). The lesson transfers: any task pairing a marked answer line in verbose output with a requirement for structured-only deliverable will benefit from identical extraction logic.
