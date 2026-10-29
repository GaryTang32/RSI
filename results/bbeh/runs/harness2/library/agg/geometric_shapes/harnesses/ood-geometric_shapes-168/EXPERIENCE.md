# ood-geometric_shapes-168

- adopted: `agg1`  (harness `c042704b6f`)
- components: 3 files (1 code, 2 text); 2396 text chars

## Why this was chosen
trivial pool: single final at k=1; delivered without a selector session

## What each edit did
component: prompts/task.md
candidates: inv1
change: Removed 43+ lines of procedural guidance including the "ANALYSIS PROCEDURE" section with step-by-step parsing instructions, geometric check definitions (dot product, parallel sides, convexity), the 10 shape type definitions, and "Key observations" about collinear segments and coordinate tolerance. Changed the closing line from `ANSWER: <letter>` to `ANSWER: <answer>`.
observed: Executed 85334 tokens vs base 58603 (+46%). Trajectory shows extensive exploratory code (regex parsing attempts, matplotlib visualization loops, graph cycle-detection algorithms) without structured convergence. Answer changed from F to E but arrived at through meandering attempts rather than systematic checking. Per inv1/rollout.md, agent created multiple Python scripts attempting visualization and graph traversal without producing definitive geometric analysis.
verdict: hurt

component: prompts/task.md
candidates: dom1, agg1
change: Added 43+ lines of procedural guidance structure: "ANALYSIS PROCEDURE" with four numbered steps (parse and normalize, find closed shapes, classify by geometric checks, select answer); explicit mathematical checks (dot product ≈ 0 for 90° angles, slope equality for parallel sides, distance formula for side lengths, cross product for convexity); complete list of 10 shape type definitions; "Key observations" about collinear segment handling and coordinate tolerance (0.1 units).
observed: dom1: executed 28903 tokens vs base 58603 (−51%). Answer E (vs base F). Trajectory shows structured reasoning: "**Step 1: Parse Key Segments**" with explicit segment extraction, "**Step 2: Identify Closed Shapes**" with vertex-by-vertex analysis, "**Step 3: Classify**" with concrete calculations ("Dot product: 0 × (−7.5495) + (−8.95663) × 0 = **0** ✓", explicit collinearity checks). Reasoning steps in dom1 trajectory directly correspond to the procedure provided in task.md. agg1: executed 97115 tokens vs dom1 28903 (+236%), but harness.diff shows "(no change)" — agg1's task.md is identical to dom1's. Trajectory shows different execution (cycle-finding code, graph algorithms) despite identical prompt. Answer same (E).
verdict: helped

## Transferable lesson
- **cue**: An LLM agent must classify structured input into one of several fixed categories, applying deterministic mathematical or algorithmic checks (e.g., geometric properties, logical rules, category membership tests).
- **lesson**: In the task prompt component, include a numbered procedural section that explicitly names each step and specifies the algorithmic check to apply, with concrete examples of the calculation (e.g., "compute dot product of vectors v1 and v2; if result ≈ 0, the angle is 90°"). Define all classification categories upfront with their distinguishing features. This guides the agent away from exploratory code attempts toward systematic verification.
- **evidence**: Edit 2 (addition of procedural guidance) reduced token consumption by 51% in dom1 (28903 vs base 58603) while maintaining equivalent classification performance; dom1's trajectory shows step-wise reasoning that explicitly follows the provided procedure structure, with concrete numerical calculations matching the procedure's geometric checks. Edit 1 (removal of procedural guidance) caused tokens to increase 46% in inv1 and trajectory to become exploratory and unstructured. The difference is not in tools available (both have access to tools.python) but in the guidance structure in the prompt itself.
- **confidence**: medium-high. The 51% token reduction in dom1 is large and consistent with the more structured trajectory. However: (1) only one clean comparison (dom1 vs base) shows the improvement; (2) no independent answer key validates correctness of either F or E; (3) agg1's re-run with identical harness but higher tokens suggests execution variance that could confound future applications; (4) the transfer assumes future tasks will benefit from procedure-driven reasoning, which is true for deterministic classification but may not apply to open-ended or exploratory tasks.
