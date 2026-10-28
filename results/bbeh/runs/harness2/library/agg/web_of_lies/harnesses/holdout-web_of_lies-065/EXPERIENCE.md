# holdout-web_of_lies-065

- adopted: `agg1`  (harness `532eacd330`)
- components: 3 files (1 code, 2 text); 83 text chars

## Why this was chosen
trivial pool: single final at k=1; delivered without a selector session

## What each edit did
component: Answer extraction logic in harness.py
candidates: inv1
change: Removes the for-loop that searches the LLM reply line-by-line for a line starting with "ANSWER:" and extracts only the text after that prefix
observed: inv1.produced outputs the full LLM reasoning followed by "ANSWER: <yes, yes, yes>". Compare inv1.rollout trajectory (which shows the full reply) to the task specification: "Your answer should be a list of three words separated by commas." The deliverable inv1 returns violates this—it includes pages of reasoning before the three-word answer. inv1.produced shows this failure: the output is not "a list of three words," it is reasoning + answer.
verdict: hurt

component: Answer extraction logic in harness.py
candidates: dom1, agg1
change: Adds a post-processing step that iterates through reply.split('\n'), finds the line beginning with "ANSWER:", and returns line.replace('ANSWER:', '').strip()
observed: dom1.produced outputs "yes, yes, yes" (extracted, unbracketed). agg1.produced outputs "<yes, yes, yes>" (extracted, bracketed). Both truncate the deliverable to only the answer portion, eliminating the reasoning and prefix. The task requires "a list of three words separated by commas" with examples `<unknown, no, yes>` and `<yes, yes, yes>` showing angle brackets. agg1 matches the task examples exactly; dom1 is missing the brackets. Both differ from base1.deliverables, which shows full reasoning. The extraction works (tokens: dom1 17614, agg1 17252 vs base1 16494; clarity unambiguous). The difference between dom1 and agg1 is not in the harness code but in what the LLM formatted into the ANSWER line (dom1's LLM omitted brackets; agg1's included them). base1.rollout delivered successfully with "yes" status, so extraction is a post-hoc improvement relative to the deployed baseline, not a fix for a failing mechanism.
verdict: helped

## Transferable lesson
- **cue**: A prompt instructs a language model to end output with a marked line ("End with ANSWER: <answer>") for clarity in a reasoning chain, but the actual deliverable specification requires a stricter format that differs from what the model was asked to produce. The two requirements are at odds.
- **lesson**: In harness.py's solve() function, add a deterministic post-processing step after the LLM call that extracts the marked line (search for a line beginning with "ANSWER:") and returns only the content after the prefix, stripping away surrounding reasoning and the marker itself. This bridges the gap between an instruction to the model (which benefits from a clear marker for reasoning) and the deliverable specification (which forbids the marker and context).
- **evidence**: inv1 removes this extraction and reverts to returning reply.strip(); inv1.produced shows the deliverable now includes full reasoning + ANSWER prefix, violating the task's requirement of "a list of three words." dom1 and agg1 add extraction; both produce deliverables containing only the answer. agg1.produced matches the task format examples exactly (`<yes, yes, yes>`), and agg1 was adopted. The extraction is a mechanical transformation, not a prompt change; it respects both the instruction to the model and the task's deliverable boundary.
- **confidence**: high. The evidence directly shows what each component does (extract or not) and what output results: omitting extraction produces oversized deliverables that violate the spec, including extraction produces compliant deliverables. The mechanism is deterministic and reusable across similar constraints (marked-line instructions + stricter deliverable specs).
