Run iteration 1 of the evolution loop.

## Domain
The artifact is an LLM agent harness. harness.py defines solve(question, llm, tools, files) -> str, where llm(prompt, system=None) -> str calls a frozen language model (each call costs tokens), tools.python(code) -> str runs Python in a sandbox and returns stdout, and files maps artifact file paths to their text (prompts, skills, memory). The returned string is graded by exact match of the final answer (a line 'ANSWER: <value>' is extracted if present, else the last line); the comparison is case-insensitive, ignores spaces after commas, bold and LaTeX wrappers, and takes only the first line of the answer. Practice tasks are multi-step reasoning questions of these kinds: ['boolean_expressions', 'causal_understanding', 'linguini', 'object_properties', 'shuffled_objects', 'web_of_lies']. The harness will later be run unchanged on other kinds of long, multi-step reasoning questions with exact answers (counting, tracking, ordering, multiple choice), so improvements must be general.

## Objective
Candidates are compared by Pareto dominance on (search score: higher is better; context cost = characters injected into the model's prompt(s) besides the input, summed over every model call made for one query: lower is better). Every non-dominated harness is kept on the frontier, so accurate-but-costly and cheap-but-weaker designs are both useful; the highest-score frontier point is reported as the best.

## Run directories
The full history of this run is under `_context/` (read-only):
- `_context/evolution_summary.jsonl` - past results (one row per candidate)
- `_context/frontier_val.json` - Pareto frontier on the search set (score up, context cost down) and per-unit bests
- `_context/candidates/<name>/src/` - every candidate's source; `.../eval/search/scores.json`, `.../traces/*.jsonl`
- `_context/reports/` - post-eval reports (write NEW reports to `reports/iter<NNN>.md`, NNN = the iteration reported)
(Some of these may be absent: you see exactly what this run's history mode exposes.)

## Output
For each of the 2 candidates create a directory `agents/<new_name>/` containing the COMPLETE harness files
(same file layout as INSIDE the candidates' `src/` directories, e.g. `agents/<new_name>/harness.py`, not
`agents/<new_name>/src/harness.py`; unchanged files may be omitted and are copied from the base system). Then write `pending_eval.json`:
{"iteration": 1, "candidates": [{"name": "<new_name>", "base_system": "<name of the system you started
from>", "hypothesis": "<falsifiable claim>", "axis": "exploitation|exploration", "components": ["<tags>"]}]}
Use new names (lowercase letters, digits, underscores) that do not exist yet. Write post-eval reports (Step 0) as
`reports/iter<NNN>.md`.
