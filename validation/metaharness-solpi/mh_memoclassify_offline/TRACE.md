# mh_memoclassify_offline (metaharness)

## Setup and summary
**Run start.** seed `no_memory=72ed6e6ad8,fewshot_all=6af10e5d5d`; config: `{"iterations": 6, "k": 2, "history_mode": "full", "window": 5, "objectives": ["score", "context_cost"], "cost_metric": "context_chars", "search_split": "evolve", "test_splits": ["test"], "trials": 1, "workers": 1, "eval_budget": null, "leakage_screen": false, "validate": true, "validate_timeout_s": 30.0, "validate_in_subprocess": true, "proposer_timeout_s": 2400.0, "finalize": true, "summaries": "auto", "seed": 0, "trace": true, "shadow_monitor": true, "shadow_splits": null, "shadow_k": 1, "shadow_workers": 2, "notes": {}}`

**Noise band.** delta=None (none, z=None); Meta-Harness has no noise band and no keep gate: every valid candidate is evaluated once on the search split with trials=1 and kept in the population; the output is the Pareto frontier (score up, context cost down)

**note:** `{"what": "finalize (one-time test evaluation)", "systems": ["no_memory", "fewshot_all", "i06_0_param_char_budget_4500", "i05_1_crossover", "i01_1_label_list", "i03_1_notes"], "test": {"test": {"no_memory": {"S": 0.14814814814814814, "context_cost": 191.0}, "fewshot_all": {"S": 0.523148148148148, "context_cost": 17728.51388888889}, "i06_0_param_char_budget_4500": {"S": 0.6342592592592593, "context_cost": 5658.0}, "i05_1_crossover": {"S": 0.6296296296296297, "context_cost": 1216.6666666666667}, "i01_1_label_list": {"S": 0.40277777777777773, "context_cost": 428.3333333333333}, "i03_1_notes": {"S": 0.6342592592592592, "context_cost": 3518.740740740741}}}, "status": "complete", "failures": []}`

**Run end:** `{"frontier": {"best": {"system": "i06_0_param_char_budget_4500", "score": 0.5902777777777777}, "pareto": [{"system": "i06_0_param_char_budget_4500", "score": 0.5902777777777777, "context_cost": 5658.0}, {"system": "i05_1_crossover", "score": 0.5694444444444445, "context_cost": 1216.6666666666667}, {"system": "i01_1_label_list", "score": 0.3819444444444445, "context_cost": 428.3333333333333}, {"system": "no_memory", "score": 0.1111111111111111, "context_cost": 191.0}], "per_unit_best": {"ds_alpha/val": "i06_0_param_char_budget_4500", "ds_beta/val": "i03_1_notes", "ds_gamma/val": "i05_1_crossover"}, "hypervolume": 11025.404779128088}, "n_evaluated": 12, "n_proposed": 12, "best_system": "i06_0_param_char_budget_4500", "stop_reason": "iterations", "usage": {"proposer": {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "latency_s": 0.0, "total_tokens": 0}, "task": {"task": {"calls": 11592, "input_tokens": 10571629, "output_tokens": 264939, "cost_usd": 0.0, "latency_s": 0.0, "total_tokens": 10836568}, "shadow:task": {"calls": 4488, "input_tokens": 5426889, "output_tokens": 103332, "cost_usd": 0.0, "latency_s": 0.0, "total_tokens": 5530221}, "validate": {"calls": 48, "input_tokens": 3543, "output_tokens": 1008, "cost_usd": 0.0, "latency_s": 0.0, "total_tokens": 4551}, "_total": {"calls": 16128, "input_tokens": 16002061, "output_tokens": 369279, "cost_usd": 0.0, "latency_s": 0.0, "total_tokens": 16371340}}, "_total": {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "latency_s": 0.0, "total_tokens": 0}, "shadow_monitor": {"task": {"calls": 4488, "input_tokens": 5426889, "output_tokens": 103332, "cost_usd": 0.0, "latency_s": 0.0, "total_tokens": 5530221}, "_total": {"calls": 4488, "input_tokens": 5426889, "output_tokens": 103332, "cost_usd": 0.0, "latency_s": 0.0, "total_tokens": 5530221}}}, "final_status": "complete", "curve": [{"n_eval": 1, "iteration": 1, "system": "i01_0_retrieve_topk", "score": 0.5347222222222222, "context_cost": 24`

## Round 0
**Baseline evaluation** `no_memory`: S=0.1111, C=19096.3000 tokens/trial, n_tasks=3, k=1

**Baseline evaluation** `fewshot_all`: S=0.4583, C=605757.0000 tokens/trial, n_tasks=3, k=1

**State after round:** `{"phase": "after baselines (H0)", "frontier": {"best": {"system": "fewshot_all", "score": 0.4583333333333333}, "pareto": [{"system": "fewshot_all", "score": 0.4583333333333333, "context_cost": 17723.61805555556}, {"system": "no_memory", "score": 0.1111111111111111, "context_cost": 191.0}], "per_unit_best": {"ds_alpha/val": "fewshot_all", "ds_beta/val": "fewshot_all", "ds_gamma/val": "fewshot_all"}, "hypervolume": 2760.8595003858027}, "population": [{"system": "no_memory", "status": "evaluated", "iteration": 0, "base": null, "score": 0.1111111111111111, "context_cost": 191.0}, {"system": "fewshot_all", "status": "evaluated", "iteration": 0, "base": null, "score": 0.4583333333333333, "context_cost": 17723.61805555556}]}`

**Shadow monitor (never shown to the loop)** `fewshot_all` (decision score 0.4583): ood: S=0.5042

## Round 1
**State at round start:** `{"iteration": 1, "k_requested": 2, "k": 2, "history_mode": "full", "n_evaluated": 0, "n_proposed": 0, "eval_budget": null, "eval_budget_left": null, "frontier": {"best": {"system": "fewshot_all", "score": 0.4583333333333333}, "pareto": [{"system": "fewshot_all", "score": 0.4583333333333333, "context_cost": 17723.61805555556}, {"system": "no_memory", "score": 0.1111111111111111, "context_cost": 191.0}], "per_unit_best": {"ds_alpha/val": "fewshot_all", "ds_beta/val": "fewshot_all", "ds_gamma/val": "fewshot_all"}, "hypervolume": 2760.8595003858027}, "view": {"n_files": 20, "chars": 579540, "by_kind": {"code": 2, "traces": 6, "per_task": 6, "scores": 2, "summaries": 0, "run_files": 2, "other": 2}, "visible_systems": ["fewshot_all", "no_memory"]}, "population": [{"system": "no_memory", "status": "evaluated", "iteration": 0, "base": null, "score": 0.1111111111111111, "context_cost": 191.0}, {"system": "fewshot_all", "status": "evaluated", "iteration": 0, "base": null, "score": 0.4583333333333333, "context_cost": 17723.61805555556}]}`

**Analysis of the incumbent's failures/successes:**
```
- i01_0_retrieve_topk: move `retrieve_topk` on `fewshot_all` - evidence: 96/144 prompts longer than 11000 chars (acc 0.48 vs 0.42 on short prompts)
- i01_1_label_list: move `label_list` on `no_memory` - evidence: target label absent from the prompt in 24/36 inspected errors
```


### Proposal `i01_0_retrieve_topk` (parent `fewshot_all`)
- **claimed change:** Showing only the demonstrations most similar to the query keeps the prompt inside the model's effective context and removes distracting examples, raising accuracy at lower context.
- **hypothesis:** Showing only the demonstrations most similar to the query keeps the prompt inside the model's effective context and removes distracting examples, raising accuracy at lower context.
- **components:** retrieve_topk, axis:C
- **details:** `{"axis": "exploitation", "parents_read": ["fewshot_all"], "files_changed": ["memory.py"], "base_known": true, "identical_to_base": false, "move": "retrieve_topk", "evidence": "96/144 prompts longer than 11000 chars (acc 0.48 vs 0.42 on short prompts)"}`
<details><summary>proposer reply</summary>

```
i01_0_retrieve_topk: retrieve_topk on fewshot_all (96/144 prompts longer than 11000 chars (acc 0.48 vs 0.42 on short prompts))
i01_1_label_list: label_list on no_memory (target label absent from the prompt in 24/36 inspected errors)
```
</details>
**Actual diff:**
```diff
--- a/memory.py
+++ b/memory.py
@@ -1,64 +1,164 @@
-"""Few-shot baseline using ALL training examples (global character cap)."""
-import hashlib
+"""Memory system: select=topk, k=16, budget=6000."""
 import json
-import random
+import re
+from collections import Counter, defaultdict
 from typing import Any
 
-PROMPT_TEMPLATE = """Solve the problem below based on the examples provided.
+CONFIG = {'cap': 6,
+ 'char_budget': 6000,
+ 'demo_chars': 0,
+ 'k': 16,
+ 'label_list': False,
+ 'learn': 'always',
+ 'lookup': {},
+ 'note_words': 5,
+ 'notes': False,
+ 'prompt': 'json',
+ 'select': 'topk',
+ 'store': 'all'}
 
-{examples_section}
-
-**Problem:**
-{input}
-
-**Instructions:**
-- Follow the patterns shown in the examples above
-- Respond in JSON format
-
-{{"reasoning": "[your reasoning]", "final_answer": "[your answer]"}}"""
-
-MAX_CHARS = 30000
-MAX_EXAMPLES = 9999
+WORD = re.compile(r"[a-z0-9]{3,}")
 
 
-def _seed_for_input(text: str) -> int:
-    return int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big")
+def _toks(text: str) -> set:
+    return set(WORD.findall(text.lower().split("options:")[0]))
+
+
+def _norm(s: Any) -> str:
+    return str(s or "").strip().strip("`*\"'.,;[]{}() ").lower()
 
 
 class Memory(MemorySystem):
+    """Memory system: select=topk, k=16, budget=6000."""
+
     def __init__(self, llm):
         super().__init__(llm)
-        self.examples = []
+        self.examples = []          # {"input", "target"}
+        self.labels = []            # labels in order of first appearance
+        self.confusions = Counter() # (predicted, gold) -> count, learned from online mistakes
+        self.words = defaultdict(Counter)
 
-    def _format_examples_section(self, seed=None) -> str:
-        if not self.examples:
-            return ""
-        to_use = list(self.examples[-MAX_EXAMPLES:])
-        if seed is not None:
-            random.Random(seed).shuffle(to_use)
-        parts, total = [], 0
-        for ex in to_use:
-            part = f"Q: {ex['input']}\nA: {ex['target']}"
-            if total + len(part) > MAX_CHARS:
-                break
-            parts.append(part)
-            total += len(part) + 2
-        return "\n\n".join(parts)
-
-    def predict(self, input: str) -> tuple[str, dict[str, Any]]:
-        section = self._format_examples_section(seed=_seed_for_input(input))
-        response = self.call_llm(PROMPT_TEMPLATE.format(examples_section=section, input=input))
-        return extract_json_field(response, "final_answer"), {"num_examples": len(self.examples)}
-
+    # ---------------------------------------------------------------- learning
     def learn_from_batch(self, batch_results):
         for r in batch_results:
-            self.examples.append({"input": r["input"], "target": r["ground_truth"]})
+            gold = _norm(r["ground_truth"])
+            ok = bool(r.get("was_correct", True))
+            if gold not in self.labels:
+                self.labels.append(gold)
+            if not ok:
+                self.confusions[(_norm(r.get("prediction")), gold)] += 1
+            if CONFIG["notes"]:
+                self.words[gold].update(_toks(r["input"]))
+            if CONFIG["learn"] == "errors_only" and ok and any(e["target"] == gold for e in self.examples):
+                continue
+            ex = {"input": r["input"], "target": gold, "hard": not ok}
+            if CONFIG["store"] == "per_label_cap":
+                same = [e for e in self.examples if e["target"] == gold]
+                if len(same) >= CONFIG["cap"]:
+                    self.examples.remove(same[0])
+            self.examples.append(ex)
+        if CONFIG["store"] == "errors_first":
+            self.examples.sort(key=lambda e: not e.get("hard", False))
 
-    def get_context_length(self) -> int:
-        return len(self._format_examples_section())
+    # --------------------------------------------------------------- retrieval
+    def _similar(self, query: str):
+        q = _
...[truncated]
```


### Proposal `i01_1_label_list` (parent `no_memory`)
- **claimed change:** Listing every label seen so far lets the model name labels without a retrieved example.
- **hypothesis:** Listing every label seen so far lets the model name labels without a retrieved example.
- **components:** label_list, axis:A
- **details:** `{"axis": "exploration", "parents_read": ["no_memory"], "files_changed": ["memory.py"], "base_known": true, "identical_to_base": false, "move": "label_list", "evidence": "target label absent from the prompt in 24/36 inspected errors"}`
<details><summary>proposer prompt</summary>

```
(same proposer call as the first candidate)
```
</details>
**Actual diff:**
```diff
--- a/memory.py
+++ b/memory.py
@@ -1,34 +1,164 @@
-"""NoMemory baseline - no learning, direct prompting."""
+"""Memory system: select=recent, k=0, budget=0, label list."""
+import json
+import re
+from collections import Counter, defaultdict
 from typing import Any
 
-PROMPT = """Answer the following question.
+CONFIG = {'cap': 6,
+ 'char_budget': 0,
+ 'demo_chars': 0,
+ 'k': 0,
+ 'label_list': True,
+ 'learn': 'always',
+ 'lookup': {},
+ 'note_words': 5,
+ 'notes': False,
+ 'prompt': 'json',
+ 'select': 'recent',
+ 'store': 'all'}
 
-{input}
+WORD = re.compile(r"[a-z0-9]{3,}")
 
-**Answer in this exact JSON format:**
-{{
-  "reasoning": "[Your chain of thought / reasoning process]",
-  "final_answer": "[Your concise final answer here]"
-}}
-"""
+
+def _toks(text: str) -> set:
+    return set(WORD.findall(text.lower().split("options:")[0]))
+
+
+def _norm(s: Any) -> str:
+    return str(s or "").strip().strip("`*\"'.,;[]{}() ").lower()
 
 
 class Memory(MemorySystem):
-    """Baseline that does not learn - just prompts the LLM directly."""
+    """Memory system: select=recent, k=0, budget=0, label list."""
 
     def __init__(self, llm):
         super().__init__(llm)
-        self._state = "{}"
+        self.examples = []          # {"input", "target"}
+        self.labels = []            # labels in order of first appearance
+        self.confusions = Counter() # (predicted, gold) -> count, learned from online mistakes
+        self.words = defaultdict(Counter)
 
-    def predict(self, input: str) -> tuple[str, dict[str, Any]]:
-        response = self.call_llm(PROMPT.format(input=input))
-        return extract_json_field(response, "final_answer"), {"full_response": response}
+    # ---------------------------------------------------------------- learning
+    def learn_from_batch(self, batch_results):
+        for r in batch_results:
+            gold = _norm(r["ground_truth"])
+            ok = bool(r.get("was_correct", True))
+            if gold not in self.labels:
+                self.labels.append(gold)
+            if not ok:
+                self.confusions[(_norm(r.get("prediction")), gold)] += 1
+            if CONFIG["notes"]:
+                self.words[gold].update(_toks(r["input"]))
+            if CONFIG["learn"] == "errors_only" and ok and any(e["target"] == gold for e in self.examples):
+                continue
+            ex = {"input": r["input"], "target": gold, "hard": not ok}
+            if CONFIG["store"] == "per_label_cap":
+                same = [e for e in self.examples if e["target"] == gold]
+                if len(same) >= CONFIG["cap"]:
+                    self.examples.remove(same[0])
+            self.examples.append(ex)
+        if CONFIG["store"] == "errors_first":
+            self.examples.sort(key=lambda e: not e.get("hard", False))
 
-    def learn_from_batch(self, batch_results):
-        pass
+    # --------------------------------------------------------------- retrieval
+    def _similar(self, query: str):
+        q = _toks(query)
+        scored = []
+        for i, e in enumerate(self.examples):
+            t = _toks(e["input"])
+            sim = len(q & t) / max(1, len(q | t))
+            scored.append((sim, i, e))
+        scored.sort(key=lambda x: (-x[0], -x[1]))
+        return [e for _, _, e in scored]
+
+    def _select(self, query: str):
+        sel, k = CONFIG["select"], CONFIG["k"]
+        if sel == "all":
+            chosen = list(self.examples)
+        elif sel == "recent":
+            chosen = self.examples[-k:]
+        else:
+            ranked = self._similar(query)
+            chosen = ranked[:k]
+            if sel == "topk_coverage":
+                have = {e["target"] for e in chosen}
+                for e in ranked[k:]:
+                    if e["target"] not in have:
+                        chosen.append(e)
+                        have.add(e["target"])
+            elif sel == "contrastive":
+                top = [e["target"] for e in cho
...[truncated]
```


**Gate on `i01_0_retrieve_topk`: ADMISSIBLE** - interface validation passed: evaluate and add to the population
  arithmetic: `{"rule": "admissible iff (screen passes, when enabled) and interface validation passes; no score threshold", "validate": true, "validated": true, "status": "evaluated"}`

**Eval `i01_0_retrieve_topk`** on evolve: S=0.5347, C=124178.0000, errors=0.0, missing=0
  per-task: ds_alpha/val=0.3958, ds_beta/val=0.6042, ds_gamma/val=0.6042

**Gate on `i01_1_label_list`: ADMISSIBLE** - interface validation passed: evaluate and add to the population
  arithmetic: `{"rule": "admissible iff (screen passes, when enabled) and interface validation passes; no score threshold", "validate": true, "validated": true, "status": "evaluated"}`

**Eval `i01_1_label_list`** on evolve: S=0.3819, C=28353.7000, errors=0.0, missing=0
  per-task: ds_alpha/val=0.2292, ds_beta/val=0.3333, ds_gamma/val=0.5833

**Decision:** kept `i01_0_retrieve_topk,i01_1_label_list`; incumbent `fewshot_all` -> `i01_0_retrieve_topk`. Meta-Harness keeps every evaluated candidate in the population; 2 of 2 joined the Pareto frontier. Incumbent = highest-score Pareto point changed fewshot_all -> i01_0_retrieve_topk

**Shadow monitor (never shown to the loop)** `i01_0_retrieve_topk` (decision score 0.5347): ood: S=0.6042

**State after round:** `{"iteration_row": {"iteration": 1, "best_score": 0.5347222222222222, "n_candidates": 2, "n_valid": 2, "n_evaluated": 2, "frontier_size": 3, "hypervolume": 9908.675573881173, "files_read": 11, "view_chars": 579540, "read_chars": 570331, "proposer_tokens": 0, "proposer_usd": 0.0, "error": null}, "frontier": {"best": {"system": "i01_0_retrieve_topk", "score": 0.5347222222222222}, "pareto": [{"system": "i01_0_retrieve_topk", "score": 0.5347222222222222, "context_cost": 2484.4166666666665}, {"system": "i01_1_label_list", "score": 0.3819444444444445, "context_cost": 428.3333333333333}, {"system": "no_memory", "score": 0.1111111111111111, "context_cost": 191.0}], "per_unit_best": {"ds_alpha/val": "fewshot_all", "ds_beta/val": "i01_0_retrieve_topk", "ds_gamma/val": "i01_0_retrieve_topk"}, "hypervolume": 9908.675573881173}}`

## Round 2
**State at round start:** `{"iteration": 2, "k_requested": 2, "k": 2, "history_mode": "full", "n_evaluated": 2, "n_proposed": 2, "eval_budget": null, "eval_budget_left": null, "frontier": {"best": {"system": "i01_0_retrieve_topk", "score": 0.5347222222222222}, "pareto": [{"system": "i01_0_retrieve_topk", "score": 0.5347222222222222, "context_cost": 2484.4166666666665}, {"system": "i01_1_label_list", "score": 0.3819444444444445, "context_cost": 428.3333333333333}, {"system": "no_memory", "score": 0.1111111111111111, "context_cost": 191.0}], "per_unit_best": {"ds_alpha/val": "fewshot_all", "ds_beta/val": "i01_0_retrieve_topk", "ds_gamma/val": "i01_0_retrieve_topk"}, "hypervolume": 9908.675573881173}, "view": {"n_files": 39, "chars": 1065064, "by_kind": {"code": 4, "traces": 12, "per_task": 12, "scores": 4, "summaries": 0, "run_files": 3, "other": 4}, "visible_systems": ["fewshot_all", "i01_0_retrieve_topk", "i01_1_label_list", "no_memory"]}, "population": [{"system": "no_memory", "status": "evaluated", "iteration": 0, "base": null, "score": 0.1111111111111111, "context_cost": 191.0}, {"system": "fewshot_all", "status": "evaluated", "iteration": 0, "base": null, "score": 0.4583333333333333, "context_cost": 1772`

**Analysis of the incumbent's failures/successes:**
```
- i02_0_contrastive: move `contrastive` on `i01_0_retrieve_topk` - evidence: 30/67 errors are recurring label confusions (e.g. ('ledor-vinpi', 'nurim-torji'))
- i02_1_coverage: move `coverage` on `no_memory` - evidence: retrieval misses the gold label's demonstrations
```


### Proposal `i02_0_contrastive` (parent `i01_0_retrieve_topk`)
- **claimed change:** Adding demonstrations of the labels most often confused with the top retrieved labels (learned from online errors) helps the model separate near-duplicates.
- **hypothesis:** Adding demonstrations of the labels most often confused with the top retrieved labels (learned from online errors) helps the model separate near-duplicates.
- **components:** contrastive, axis:C
- **details:** `{"axis": "exploitation", "parents_read": ["i01_0_retrieve_topk"], "files_changed": ["memory.py"], "base_known": true, "identical_to_base": false, "move": "contrastive", "evidence": "30/67 errors are recurring label confusions (e.g. ('ledor-vinpi', 'nurim-torji'))"}`
<details><summary>proposer reply</summary>

```
i02_0_contrastive: contrastive on i01_0_retrieve_topk (30/67 errors are recurring label confusions (e.g. ('ledor-vinpi', 'nurim-torji')))
i02_1_coverage: coverage on no_memory (retrieval misses the gold label's demonstrations)
```
</details>
**Actual diff:**
```diff
--- a/memory.py
+++ b/memory.py
@@ -1,20 +1,20 @@
-"""Memory system: select=topk, k=16, budget=6000."""
+"""Memory system: select=contrastive, k=12, budget=7000."""
 import json
 import re
 from collections import Counter, defaultdict
 from typing import Any
 
 CONFIG = {'cap': 6,
- 'char_budget': 6000,
+ 'char_budget': 7000,
  'demo_chars': 0,
- 'k': 16,
+ 'k': 12,
  'label_list': False,
  'learn': 'always',
  'lookup': {},
  'note_words': 5,
  'notes': False,
  'prompt': 'json',
- 'select': 'topk',
+ 'select': 'contrastive',
  'store': 'all'}
 
 WORD = re.compile(r"[a-z0-9]{3,}")
@@ -29,7 +29,7 @@
 
 
 class Memory(MemorySystem):
-    """Memory system: select=topk, k=16, budget=6000."""
+    """Memory system: select=contrastive, k=12, budget=7000."""
 
     def __init__(self, llm):
         super().__init__(llm)

```


### Proposal `i02_1_coverage` (parent `no_memory`)
- **claimed change:** Adding one demonstration of every label not yet retrieved lets the model answer labels that similarity search missed.
- **hypothesis:** Adding one demonstration of every label not yet retrieved lets the model answer labels that similarity search missed.
- **components:** coverage, axis:C
- **details:** `{"axis": "exploration", "parents_read": ["no_memory"], "files_changed": ["memory.py"], "base_known": true, "identical_to_base": false, "move": "coverage", "evidence": "retrieval misses the gold label's demonstrations"}`
<details><summary>proposer prompt</summary>

```
(same proposer call as the first candidate)
```
</details>
**Actual diff:**
```diff
--- a/memory.py
+++ b/memory.py
@@ -1,34 +1,164 @@
-"""NoMemory baseline - no learning, direct prompting."""
+"""Memory system: select=topk_coverage, k=12, budget=7000."""
+import json
+import re
+from collections import Counter, defaultdict
 from typing import Any
 
-PROMPT = """Answer the following question.
+CONFIG = {'cap': 6,
+ 'char_budget': 7000,
+ 'demo_chars': 0,
+ 'k': 12,
+ 'label_list': False,
+ 'learn': 'always',
+ 'lookup': {},
+ 'note_words': 5,
+ 'notes': False,
+ 'prompt': 'json',
+ 'select': 'topk_coverage',
+ 'store': 'all'}
 
-{input}
+WORD = re.compile(r"[a-z0-9]{3,}")
 
-**Answer in this exact JSON format:**
-{{
-  "reasoning": "[Your chain of thought / reasoning process]",
-  "final_answer": "[Your concise final answer here]"
-}}
-"""
+
+def _toks(text: str) -> set:
+    return set(WORD.findall(text.lower().split("options:")[0]))
+
+
+def _norm(s: Any) -> str:
+    return str(s or "").strip().strip("`*\"'.,;[]{}() ").lower()
 
 
 class Memory(MemorySystem):
-    """Baseline that does not learn - just prompts the LLM directly."""
+    """Memory system: select=topk_coverage, k=12, budget=7000."""
 
     def __init__(self, llm):
         super().__init__(llm)
-        self._state = "{}"
+        self.examples = []          # {"input", "target"}
+        self.labels = []            # labels in order of first appearance
+        self.confusions = Counter() # (predicted, gold) -> count, learned from online mistakes
+        self.words = defaultdict(Counter)
 
-    def predict(self, input: str) -> tuple[str, dict[str, Any]]:
-        response = self.call_llm(PROMPT.format(input=input))
-        return extract_json_field(response, "final_answer"), {"full_response": response}
+    # ---------------------------------------------------------------- learning
+    def learn_from_batch(self, batch_results):
+        for r in batch_results:
+            gold = _norm(r["ground_truth"])
+            ok = bool(r.get("was_correct", True))
+            if gold not in self.labels:
+                self.labels.append(gold)
+            if not ok:
+                self.confusions[(_norm(r.get("prediction")), gold)] += 1
+            if CONFIG["notes"]:
+                self.words[gold].update(_toks(r["input"]))
+            if CONFIG["learn"] == "errors_only" and ok and any(e["target"] == gold for e in self.examples):
+                continue
+            ex = {"input": r["input"], "target": gold, "hard": not ok}
+            if CONFIG["store"] == "per_label_cap":
+                same = [e for e in self.examples if e["target"] == gold]
+                if len(same) >= CONFIG["cap"]:
+                    self.examples.remove(same[0])
+            self.examples.append(ex)
+        if CONFIG["store"] == "errors_first":
+            self.examples.sort(key=lambda e: not e.get("hard", False))
 
-    def learn_from_batch(self, batch_results):
-        pass
+    # --------------------------------------------------------------- retrieval
+    def _similar(self, query: str):
+        q = _toks(query)
+        scored = []
+        for i, e in enumerate(self.examples):
+            t = _toks(e["input"])
+            sim = len(q & t) / max(1, len(q | t))
+            scored.append((sim, i, e))
+        scored.sort(key=lambda x: (-x[0], -x[1]))
+        return [e for _, _, e in scored]
+
+    def _select(self, query: str):
+        sel, k = CONFIG["select"], CONFIG["k"]
+        if sel == "all":
+            chosen = list(self.examples)
+        elif sel == "recent":
+            chosen = self.examples[-k:]
+        else:
+            ranked = self._similar(query)
+            chosen = ranked[:k]
+            if sel == "topk_coverage":
+                have = {e["target"] for e in chosen}
+                for e in ranked[k:]:
+                    if e["target"] not in have:
+                        chosen.append(e)
+                        have.add(e["target"])
+            elif sel == "contrastive":
+                top = [e["target"] fo
...[truncated]
```


**Gate on `i02_0_contrastive`: ADMISSIBLE** - interface validation passed: evaluate and add to the population
  arithmetic: `{"rule": "admissible iff (screen passes, when enabled) and interface validation passes; no score threshold", "validate": true, "validated": true, "status": "evaluated"}`

**Eval `i02_0_contrastive`** on evolve: S=0.5208, C=122019.7000, errors=0.0, missing=0
  per-task: ds_alpha/val=0.3750, ds_beta/val=0.5833, ds_gamma/val=0.6042

**Gate on `i02_1_coverage`: ADMISSIBLE** - interface validation passed: evaluate and add to the population
  arithmetic: `{"rule": "admissible iff (screen passes, when enabled) and interface validation passes; no score threshold", "validate": true, "validated": true, "status": "evaluated"}`

**Eval `i02_1_coverage`** on evolve: S=0.5069, C=159139.3000, errors=0.0, missing=0
  per-task: ds_alpha/val=0.3542, ds_beta/val=0.5833, ds_gamma/val=0.5833

**Decision:** kept `i02_0_contrastive`; incumbent `i01_0_retrieve_topk` -> `i01_0_retrieve_topk`. Meta-Harness keeps every evaluated candidate in the population; 1 of 2 joined the Pareto frontier. Incumbent = highest-score Pareto point (unchanged)

**State after round:** `{"iteration_row": {"iteration": 2, "best_score": 0.5347222222222222, "n_candidates": 2, "n_valid": 2, "n_evaluated": 4, "frontier_size": 4, "hypervolume": 9911.094555362657, "files_read": 15, "view_chars": 1065064, "read_chars": 457977, "proposer_tokens": 0, "proposer_usd": 0.0, "error": null}, "frontier": {"best": {"system": "i01_0_retrieve_topk", "score": 0.5347222222222222}, "pareto": [{"system": "i01_0_retrieve_topk", "score": 0.5347222222222222, "context_cost": 2484.4166666666665}, {"system": "i02_0_contrastive", "score": 0.5208333333333334, "context_cost": 2467.0}, {"system": "i01_1_label_list", "score": 0.3819444444444445, "context_cost": 428.3333333333333}, {"system": "no_memory", "score": 0.1111111111111111, "context_cost": 191.0}], "per_unit_best": {"ds_alpha/val": "fewshot_all", "ds_beta/val": "i01_0_retrieve_topk", "ds_gamma/val": "i02_0_contrastive"}, "hypervolume": 9911.094555362657}}`

## Round 3
**State at round start:** `{"iteration": 3, "k_requested": 2, "k": 2, "history_mode": "full", "n_evaluated": 4, "n_proposed": 4, "eval_budget": null, "eval_budget_left": null, "frontier": {"best": {"system": "i01_0_retrieve_topk", "score": 0.5347222222222222}, "pareto": [{"system": "i01_0_retrieve_topk", "score": 0.5347222222222222, "context_cost": 2484.4166666666665}, {"system": "i02_0_contrastive", "score": 0.5208333333333334, "context_cost": 2467.0}, {"system": "i01_1_label_list", "score": 0.3819444444444445, "context_cost": 428.3333333333333}, {"system": "no_memory", "score": 0.1111111111111111, "context_cost": 191.0}], "per_unit_best": {"ds_alpha/val": "fewshot_all", "ds_beta/val": "i01_0_retrieve_topk", "ds_gamma/val": "i02_0_contrastive"}, "hypervolume": 9911.094555362657}, "view": {"n_files": 58, "chars": 1665890, "by_kind": {"code": 6, "traces": 18, "per_task": 18, "scores": 6, "summaries": 0, "run_files": 4, "other": 6}, "visible_systems": ["fewshot_all", "i01_0_retrieve_topk", "i01_1_label_list", "i02_0_contrastive", "i02_1_coverage", "no_memory"]}, "population": [{"system": "no_memory", "status": "evaluated", "iteration": 0, "base": null, "score": 0.1111111111111111, "context_cost": 191.0}, {"sys`

**Analysis of the incumbent's failures/successes:**
```
- i03_0_notes: move `notes` on `i01_0_retrieve_topk` - evidence: mean context 2484 chars
- i03_1_notes: move `notes` on `i02_0_contrastive` - evidence: mean context 2467 chars
```


### Proposal `i03_0_notes` (parent `i01_0_retrieve_topk`)
- **claimed change:** Distilling each label into its most distinctive words gives the model a compact description of every label at small context cost.
- **hypothesis:** Distilling each label into its most distinctive words gives the model a compact description of every label at small context cost.
- **components:** notes, axis:F
- **details:** `{"axis": "exploitation", "parents_read": ["i01_0_retrieve_topk"], "files_changed": ["memory.py"], "base_known": true, "identical_to_base": false, "move": "notes", "evidence": "mean context 2484 chars"}`
<details><summary>proposer reply</summary>

```
i03_0_notes: notes on i01_0_retrieve_topk (mean context 2484 chars)
i03_1_notes: notes on i02_0_contrastive (mean context 2467 chars)
```
</details>
**Actual diff:**
```diff
--- a/memory.py
+++ b/memory.py
@@ -1,4 +1,4 @@
-"""Memory system: select=topk, k=16, budget=6000."""
+"""Memory system: select=topk, k=16, budget=6000, notes(5)."""
 import json
 import re
 from collections import Counter, defaultdict
@@ -12,7 +12,7 @@
  'learn': 'always',
  'lookup': {},
  'note_words': 5,
- 'notes': False,
+ 'notes': True,
  'prompt': 'json',
  'select': 'topk',
  'store': 'all'}
@@ -29,7 +29,7 @@
 
 
 class Memory(MemorySystem):
-    """Memory system: select=topk, k=16, budget=6000."""
+    """Memory system: select=topk, k=16, budget=6000, notes(5)."""
 
     def __init__(self, llm):
         super().__init__(llm)

```


### Proposal `i03_1_notes` (parent `i02_0_contrastive`)
- **claimed change:** Distilling each label into its most distinctive words gives the model a compact description of every label at small context cost.
- **hypothesis:** Distilling each label into its most distinctive words gives the model a compact description of every label at small context cost.
- **components:** notes, axis:F
- **details:** `{"axis": "exploration", "parents_read": ["i02_0_contrastive"], "files_changed": ["memory.py"], "base_known": true, "identical_to_base": false, "move": "notes", "evidence": "mean context 2467 chars"}`
<details><summary>proposer prompt</summary>

```
(same proposer call as the first candidate)
```
</details>
**Actual diff:**
```diff
--- a/memory.py
+++ b/memory.py
@@ -1,4 +1,4 @@
-"""Memory system: select=contrastive, k=12, budget=7000."""
+"""Memory system: select=contrastive, k=12, budget=7000, notes(5)."""
 import json
 import re
 from collections import Counter, defaultdict
@@ -12,7 +12,7 @@
  'learn': 'always',
  'lookup': {},
  'note_words': 5,
- 'notes': False,
+ 'notes': True,
  'prompt': 'json',
  'select': 'contrastive',
  'store': 'all'}
@@ -29,7 +29,7 @@
 
 
 class Memory(MemorySystem):
-    """Memory system: select=contrastive, k=12, budget=7000."""
+    """Memory system: select=contrastive, k=12, budget=7000, notes(5)."""
 
     def __init__(self, llm):
         super().__init__(llm)

```


**Gate on `i03_0_notes`: ADMISSIBLE** - interface validation passed: evaluate and add to the population
  arithmetic: `{"rule": "admissible iff (screen passes, when enabled) and interface validation passes; no score threshold", "validate": true, "validated": true, "status": "evaluated"}`

**Eval `i03_0_notes`** on evolve: S=0.5347, C=165445.0000, errors=0.0, missing=0
  per-task: ds_alpha/val=0.3958, ds_beta/val=0.6250, ds_gamma/val=0.5833

**Gate on `i03_1_notes`: ADMISSIBLE** - interface validation passed: evaluate and add to the population
  arithmetic: `{"rule": "admissible iff (screen passes, when enabled) and interface validation passes; no score threshold", "validate": true, "validated": true, "status": "evaluated"}`

**Eval `i03_1_notes`** on evolve: S=0.5556, C=163009.7000, errors=0.0, missing=0
  per-task: ds_alpha/val=0.4375, ds_beta/val=0.6458, ds_gamma/val=0.5833

**Decision:** kept `i03_1_notes`; incumbent `i01_0_retrieve_topk` -> `i03_1_notes`. Meta-Harness keeps every evaluated candidate in the population; 1 of 2 joined the Pareto frontier. Incumbent = highest-score Pareto point changed i01_0_retrieve_topk -> i03_1_notes

**Shadow monitor (never shown to the loop)** `i03_1_notes` (decision score 0.5556): ood: S=0.6500

**State after round:** `{"iteration_row": {"iteration": 3, "best_score": 0.5555555555555557, "n_candidates": 2, "n_valid": 2, "n_evaluated": 6, "frontier_size": 5, "hypervolume": 10243.896074459883, "files_read": 19, "view_chars": 1665890, "read_chars": 567236, "proposer_tokens": 0, "proposer_usd": 0.0, "error": null}, "frontier": {"best": {"system": "i03_1_notes", "score": 0.5555555555555557}, "pareto": [{"system": "i03_1_notes", "score": 0.5555555555555557, "context_cost": 3522.506944444444}, {"system": "i01_0_retrieve_topk", "score": 0.5347222222222222, "context_cost": 2484.4166666666665}, {"system": "i02_0_contrastive", "score": 0.5208333333333334, "context_cost": 2467.0}, {"system": "i01_1_label_list", "score": 0.3819444444444445, "context_cost": 428.3333333333333}, {"system": "no_memory", "score": 0.1111111111111111, "context_cost": 191.0}], "per_unit_best": {"ds_alpha/val": "i03_1_notes", "ds_beta/val": "i03_1_notes", "ds_gamma/val": "i02_0_contrastive"}, "hypervolume": 10243.896074459883}}`

## Round 4
**State at round start:** `{"iteration": 4, "k_requested": 2, "k": 2, "history_mode": "full", "n_evaluated": 6, "n_proposed": 6, "eval_budget": null, "eval_budget_left": null, "frontier": {"best": {"system": "i03_1_notes", "score": 0.5555555555555557}, "pareto": [{"system": "i03_1_notes", "score": 0.5555555555555557, "context_cost": 3522.506944444444}, {"system": "i01_0_retrieve_topk", "score": 0.5347222222222222, "context_cost": 2484.4166666666665}, {"system": "i02_0_contrastive", "score": 0.5208333333333334, "context_cost": 2467.0}, {"system": "i01_1_label_list", "score": 0.3819444444444445, "context_cost": 428.3333333333333}, {"system": "no_memory", "score": 0.1111111111111111, "context_cost": 191.0}], "per_unit_best": {"ds_alpha/val": "i03_1_notes", "ds_beta/val": "i03_1_notes", "ds_gamma/val": "i02_0_contrastive"}, "hypervolume": 10243.896074459883}, "view": {"n_files": 77, "chars": 2305378, "by_kind": {"code": 8, "traces": 24, "per_task": 24, "scores": 8, "summaries": 0, "run_files": 5, "other": 8}, "visible_systems": ["fewshot_all", "i01_0_retrieve_topk", "i01_1_label_list", "i02_0_contrastive", "i02_1_coverage", "i03_0_notes", "i03_1_notes", "no_memory"]}, "population": [{"system": "no_memory", "stat`

**Analysis of the incumbent's failures/successes:**
```
- i04_0_coverage: move `coverage` on `i03_1_notes` - evidence: untried mechanism
- i04_1_param_char_budget_14000: move `param_char_budget_14000` on `i01_0_retrieve_topk` - evidence: untried mechanism
```


### Proposal `i04_0_coverage` (parent `i03_1_notes`)
- **claimed change:** Adding one demonstration of every label not yet retrieved lets the model answer labels that similarity search missed.
- **hypothesis:** Adding one demonstration of every label not yet retrieved lets the model answer labels that similarity search missed.
- **components:** coverage, axis:C
- **details:** `{"axis": "exploitation", "parents_read": ["i03_1_notes"], "files_changed": ["memory.py"], "base_known": true, "identical_to_base": false, "move": "coverage", "evidence": "untried mechanism"}`
<details><summary>proposer reply</summary>

```
i04_0_coverage: coverage on i03_1_notes (untried mechanism)
i04_1_param_char_budget_14000: param_char_budget_14000 on i01_0_retrieve_topk (untried mechanism)
```
</details>
**Actual diff:**
```diff
--- a/memory.py
+++ b/memory.py
@@ -1,4 +1,4 @@
-"""Memory system: select=contrastive, k=12, budget=7000, notes(5)."""
+"""Memory system: select=topk_coverage, k=12, budget=7000, notes(5)."""
 import json
 import re
 from collections import Counter, defaultdict
@@ -14,7 +14,7 @@
  'note_words': 5,
  'notes': True,
  'prompt': 'json',
- 'select': 'contrastive',
+ 'select': 'topk_coverage',
  'store': 'all'}
 
 WORD = re.compile(r"[a-z0-9]{3,}")
@@ -29,7 +29,7 @@
 
 
 class Memory(MemorySystem):
-    """Memory system: select=contrastive, k=12, budget=7000, notes(5)."""
+    """Memory system: select=topk_coverage, k=12, budget=7000, notes(5)."""
 
     def __init__(self, llm):
         super().__init__(llm)

```


### Proposal `i04_1_param_char_budget_14000` (parent `i01_0_retrieve_topk`)
- **claimed change:** Tuning char_budget to 14000 should balance recall and prompt length.
- **hypothesis:** Tuning char_budget to 14000 should balance recall and prompt length.
- **components:** param_char_budget_14000, axis:D
- **details:** `{"axis": "exploration", "parents_read": ["i01_0_retrieve_topk"], "files_changed": ["memory.py"], "base_known": true, "identical_to_base": false, "move": "param_char_budget_14000", "evidence": "untried mechanism"}`
<details><summary>proposer prompt</summary>

```
(same proposer call as the first candidate)
```
</details>
**Actual diff:**
```diff
--- a/memory.py
+++ b/memory.py
@@ -1,11 +1,11 @@
-"""Memory system: select=topk, k=16, budget=6000."""
+"""Memory system: select=topk, k=16, budget=14000."""
 import json
 import re
 from collections import Counter, defaultdict
 from typing import Any
 
 CONFIG = {'cap': 6,
- 'char_budget': 6000,
+ 'char_budget': 14000,
  'demo_chars': 0,
  'k': 16,
  'label_list': False,
@@ -29,7 +29,7 @@
 
 
 class Memory(MemorySystem):
-    """Memory system: select=topk, k=16, budget=6000."""
+    """Memory system: select=topk, k=16, budget=14000."""
 
     def __init__(self, llm):
         super().__init__(llm)

```


**Gate on `i04_0_coverage`: ADMISSIBLE** - interface validation passed: evaluate and add to the population
  arithmetic: `{"rule": "admissible iff (screen passes, when enabled) and interface validation passes; no score threshold", "validate": true, "validated": true, "status": "evaluated"}`

**Eval `i04_0_coverage`** on evolve: S=0.5625, C=200407.3000, errors=0.0, missing=0
  per-task: ds_alpha/val=0.4375, ds_beta/val=0.6458, ds_gamma/val=0.6042

**Gate on `i04_1_param_char_budget_14000`: ADMISSIBLE** - interface validation passed: evaluate and add to the population
  arithmetic: `{"rule": "admissible iff (screen passes, when enabled) and interface validation passes; no score threshold", "validate": true, "validated": true, "status": "evaluated"}`

**Eval `i04_1_param_char_budget_14000`** on evolve: S=0.5347, C=124178.0000, errors=0.0, missing=0
  per-task: ds_alpha/val=0.3958, ds_beta/val=0.6042, ds_gamma/val=0.6042

**Decision:** kept `i04_0_coverage,i04_1_param_char_budget_14000`; incumbent `i03_1_notes` -> `i04_0_coverage`. Meta-Harness keeps every evaluated candidate in the population; 2 of 2 joined the Pareto frontier. Incumbent = highest-score Pareto point changed i03_1_notes -> i04_0_coverage

**Shadow monitor (never shown to the loop)** `i04_0_coverage` (decision score 0.5625): ood: S=0.6542

**State after round:** `{"iteration_row": {"iteration": 4, "best_score": 0.5625, "n_candidates": 2, "n_valid": 2, "n_evaluated": 8, "frontier_size": 7, "hypervolume": 10346.863864776238, "files_read": 23, "view_chars": 2305378, "read_chars": 620121, "proposer_tokens": 0, "proposer_usd": 0.0, "error": null}, "frontier": {"best": {"system": "i04_0_coverage", "score": 0.5625}, "pareto": [{"system": "i04_0_coverage", "score": 0.5625, "context_cost": 4669.618055555556}, {"system": "i03_1_notes", "score": 0.5555555555555557, "context_cost": 3522.506944444444}, {"system": "i01_0_retrieve_topk", "score": 0.5347222222222222, "context_cost": 2484.4166666666665}, {"system": "i04_1_param_char_budget_14000", "score": 0.5347222222222222, "context_cost": 2484.4166666666665}, {"system": "i02_0_contrastive", "score": 0.5208333333333334, "context_cost": 2467.0}, {"system": "i01_1_label_list", "score": 0.3819444444444445, "context_cost": 428.3333333333333}, {"system": "no_memory", "score": 0.1111111111111111, "context_cost": 191.0}], "per_unit_best": {"ds_alpha/val": "i03_1_notes", "ds_beta/val": "i03_1_notes", "ds_gamma/val": "i02_0_contrastive"}, "hypervolume": 10346.863864776238}}`

## Round 5
**State at round start:** `{"iteration": 5, "k_requested": 2, "k": 2, "history_mode": "full", "n_evaluated": 8, "n_proposed": 8, "eval_budget": null, "eval_budget_left": null, "frontier": {"best": {"system": "i04_0_coverage", "score": 0.5625}, "pareto": [{"system": "i04_0_coverage", "score": 0.5625, "context_cost": 4669.618055555556}, {"system": "i03_1_notes", "score": 0.5555555555555557, "context_cost": 3522.506944444444}, {"system": "i01_0_retrieve_topk", "score": 0.5347222222222222, "context_cost": 2484.4166666666665}, {"system": "i04_1_param_char_budget_14000", "score": 0.5347222222222222, "context_cost": 2484.4166666666665}, {"system": "i02_0_contrastive", "score": 0.5208333333333334, "context_cost": 2467.0}, {"system": "i01_1_label_list", "score": 0.3819444444444445, "context_cost": 428.3333333333333}, {"system": "no_memory", "score": 0.1111111111111111, "context_cost": 191.0}], "per_unit_best": {"ds_alpha/val": "i03_1_notes", "ds_beta/val": "i03_1_notes", "ds_gamma/val": "i02_0_contrastive"}, "hypervolume": 10346.863864776238}, "view": {"n_files": 96, "chars": 2941009, "by_kind": {"code": 10, "traces": 30, "per_task": 30, "scores": 10, "summaries": 0, "run_files": 6, "other": 10}, "visible_systems": [`

**Analysis of the incumbent's failures/successes:**
```
- i05_0_notes_only: move `notes_only` on `i04_0_coverage` - evidence: mean context 4670 chars at accuracy 0.56
- i05_1_crossover: move `crossover` on `no_memory` - evidence: combine no_memory + i04_0_coverage
```


### Proposal `i05_0_notes_only` (parent `i04_0_coverage`)
- **claimed change:** Keyword notes for every label plus a handful of nearest demonstrations should match the full demonstration set at a fraction of the context.
- **hypothesis:** Keyword notes for every label plus a handful of nearest demonstrations should match the full demonstration set at a fraction of the context.
- **components:** notes_only, axis:F
- **details:** `{"axis": "exploitation", "parents_read": ["i04_0_coverage"], "files_changed": ["memory.py"], "base_known": true, "identical_to_base": false, "move": "notes_only", "evidence": "mean context 4670 chars at accuracy 0.56"}`
<details><summary>proposer reply</summary>

```
i05_0_notes_only: notes_only on i04_0_coverage (mean context 4670 chars at accuracy 0.56)
i05_1_crossover: crossover on no_memory (combine no_memory + i04_0_coverage)
```
</details>
**Actual diff:**
```diff
--- a/memory.py
+++ b/memory.py
@@ -1,20 +1,20 @@
-"""Memory system: select=topk_coverage, k=12, budget=7000, notes(5)."""
+"""Memory system: select=topk, k=4, budget=1500, notes(6)."""
 import json
 import re
 from collections import Counter, defaultdict
 from typing import Any
 
 CONFIG = {'cap': 6,
- 'char_budget': 7000,
+ 'char_budget': 1500,
  'demo_chars': 0,
- 'k': 12,
+ 'k': 4,
  'label_list': False,
  'learn': 'always',
  'lookup': {},
- 'note_words': 5,
+ 'note_words': 6,
  'notes': True,
  'prompt': 'json',
- 'select': 'topk_coverage',
+ 'select': 'topk',
  'store': 'all'}
 
 WORD = re.compile(r"[a-z0-9]{3,}")
@@ -29,7 +29,7 @@
 
 
 class Memory(MemorySystem):
-    """Memory system: select=topk_coverage, k=12, budget=7000, notes(5)."""
+    """Memory system: select=topk, k=4, budget=1500, notes(6)."""
 
     def __init__(self, llm):
         super().__init__(llm)

```


### Proposal `i05_1_crossover` (parent `no_memory`)
- **claimed change:** combine no_memory + i04_0_coverage
- **hypothesis:** combine no_memory + i04_0_coverage
- **components:** crossover, axis:combo
- **details:** `{"axis": "exploration", "parents_read": ["no_memory", "i04_0_coverage"], "files_changed": ["memory.py"], "base_known": true, "identical_to_base": false, "move": "crossover", "evidence": "combine no_memory + i04_0_coverage"}`
<details><summary>proposer prompt</summary>

```
(same proposer call as the first candidate)
```
</details>
**Actual diff:**
```diff
--- a/memory.py
+++ b/memory.py
@@ -1,34 +1,164 @@
-"""NoMemory baseline - no learning, direct prompting."""
+"""Memory system: select=recent, k=0, budget=0, notes(5)."""
+import json
+import re
+from collections import Counter, defaultdict
 from typing import Any
 
-PROMPT = """Answer the following question.
+CONFIG = {'cap': 6,
+ 'char_budget': 0,
+ 'demo_chars': 0,
+ 'k': 0,
+ 'label_list': False,
+ 'learn': 'always',
+ 'lookup': {},
+ 'note_words': 5,
+ 'notes': True,
+ 'prompt': 'json',
+ 'select': 'recent',
+ 'store': 'all'}
 
-{input}
+WORD = re.compile(r"[a-z0-9]{3,}")
 
-**Answer in this exact JSON format:**
-{{
-  "reasoning": "[Your chain of thought / reasoning process]",
-  "final_answer": "[Your concise final answer here]"
-}}
-"""
+
+def _toks(text: str) -> set:
+    return set(WORD.findall(text.lower().split("options:")[0]))
+
+
+def _norm(s: Any) -> str:
+    return str(s or "").strip().strip("`*\"'.,;[]{}() ").lower()
 
 
 class Memory(MemorySystem):
-    """Baseline that does not learn - just prompts the LLM directly."""
+    """Memory system: select=recent, k=0, budget=0, notes(5)."""
 
     def __init__(self, llm):
         super().__init__(llm)
-        self._state = "{}"
+        self.examples = []          # {"input", "target"}
+        self.labels = []            # labels in order of first appearance
+        self.confusions = Counter() # (predicted, gold) -> count, learned from online mistakes
+        self.words = defaultdict(Counter)
 
-    def predict(self, input: str) -> tuple[str, dict[str, Any]]:
-        response = self.call_llm(PROMPT.format(input=input))
-        return extract_json_field(response, "final_answer"), {"full_response": response}
+    # ---------------------------------------------------------------- learning
+    def learn_from_batch(self, batch_results):
+        for r in batch_results:
+            gold = _norm(r["ground_truth"])
+            ok = bool(r.get("was_correct", True))
+            if gold not in self.labels:
+                self.labels.append(gold)
+            if not ok:
+                self.confusions[(_norm(r.get("prediction")), gold)] += 1
+            if CONFIG["notes"]:
+                self.words[gold].update(_toks(r["input"]))
+            if CONFIG["learn"] == "errors_only" and ok and any(e["target"] == gold for e in self.examples):
+                continue
+            ex = {"input": r["input"], "target": gold, "hard": not ok}
+            if CONFIG["store"] == "per_label_cap":
+                same = [e for e in self.examples if e["target"] == gold]
+                if len(same) >= CONFIG["cap"]:
+                    self.examples.remove(same[0])
+            self.examples.append(ex)
+        if CONFIG["store"] == "errors_first":
+            self.examples.sort(key=lambda e: not e.get("hard", False))
 
-    def learn_from_batch(self, batch_results):
-        pass
+    # --------------------------------------------------------------- retrieval
+    def _similar(self, query: str):
+        q = _toks(query)
+        scored = []
+        for i, e in enumerate(self.examples):
+            t = _toks(e["input"])
+            sim = len(q & t) / max(1, len(q | t))
+            scored.append((sim, i, e))
+        scored.sort(key=lambda x: (-x[0], -x[1]))
+        return [e for _, _, e in scored]
+
+    def _select(self, query: str):
+        sel, k = CONFIG["select"], CONFIG["k"]
+        if sel == "all":
+            chosen = list(self.examples)
+        elif sel == "recent":
+            chosen = self.examples[-k:]
+        else:
+            ranked = self._similar(query)
+            chosen = ranked[:k]
+            if sel == "topk_coverage":
+                have = {e["target"] for e in chosen}
+                for e in ranked[k:]:
+                    if e["target"] not in have:
+                        chosen.append(e)
+                        have.add(e["target"])
+            elif sel == "contrastive":
+                top = [e["target"] for e in chosen[
...[truncated]
```


**Gate on `i05_0_notes_only`: ADMISSIBLE** - interface validation passed: evaluate and add to the population
  arithmetic: `{"rule": "admissible iff (screen passes, when enabled) and interface validation passes; no score threshold", "validate": true, "validated": true, "status": "evaluated"}`

**Eval `i05_0_notes_only`** on evolve: S=0.5486, C=92090.0000, errors=0.0, missing=0
  per-task: ds_alpha/val=0.4792, ds_beta/val=0.5833, ds_gamma/val=0.5833

**Gate on `i05_1_crossover`: ADMISSIBLE** - interface validation passed: evaluate and add to the population
  arithmetic: `{"rule": "admissible iff (screen passes, when enabled) and interface validation passes; no score threshold", "validate": true, "validated": true, "status": "evaluated"}`

**Eval `i05_1_crossover`** on evolve: S=0.5694, C=58884.0000, errors=0.0, missing=0
  per-task: ds_alpha/val=0.5208, ds_beta/val=0.5625, ds_gamma/val=0.6250

**Decision:** kept `i05_1_crossover`; incumbent `i04_0_coverage` -> `i05_1_crossover`. Meta-Harness keeps every evaluated candidate in the population; 1 of 2 joined the Pareto frontier. Incumbent = highest-score Pareto point changed i04_0_coverage -> i05_1_crossover

**Shadow monitor (never shown to the loop)** `i05_1_crossover` (decision score 0.5694): ood: S=0.6250

**State after round:** `{"iteration_row": {"iteration": 5, "best_score": 0.5694444444444445, "n_candidates": 2, "n_valid": 2, "n_evaluated": 10, "frontier_size": 3, "hypervolume": 10737.092698688275, "files_read": 27, "view_chars": 2941009, "read_chars": 574171, "proposer_tokens": 0, "proposer_usd": 0.0, "error": null}, "frontier": {"best": {"system": "i05_1_crossover", "score": 0.5694444444444445}, "pareto": [{"system": "i05_1_crossover", "score": 0.5694444444444445, "context_cost": 1216.6666666666667}, {"system": "i01_1_label_list", "score": 0.3819444444444445, "context_cost": 428.3333333333333}, {"system": "no_memory", "score": 0.1111111111111111, "context_cost": 191.0}], "per_unit_best": {"ds_alpha/val": "i05_1_crossover", "ds_beta/val": "i03_1_notes", "ds_gamma/val": "i05_1_crossover"}, "hypervolume": 10737.092698688275}}`

## Round 6
**State at round start:** `{"iteration": 6, "k_requested": 2, "k": 2, "history_mode": "full", "n_evaluated": 10, "n_proposed": 10, "eval_budget": null, "eval_budget_left": null, "frontier": {"best": {"system": "i05_1_crossover", "score": 0.5694444444444445}, "pareto": [{"system": "i05_1_crossover", "score": 0.5694444444444445, "context_cost": 1216.6666666666667}, {"system": "i01_1_label_list", "score": 0.3819444444444445, "context_cost": 428.3333333333333}, {"system": "no_memory", "score": 0.1111111111111111, "context_cost": 191.0}], "per_unit_best": {"ds_alpha/val": "i05_1_crossover", "ds_beta/val": "i03_1_notes", "ds_gamma/val": "i05_1_crossover"}, "hypervolume": 10737.092698688275}, "view": {"n_files": 115, "chars": 3436430, "by_kind": {"code": 12, "traces": 36, "per_task": 36, "scores": 12, "summaries": 0, "run_files": 7, "other": 12}, "visible_systems": ["fewshot_all", "i01_0_retrieve_topk", "i01_1_label_list", "i02_0_contrastive", "i02_1_coverage", "i03_0_notes", "i03_1_notes", "i04_0_coverage", "i04_1_param_char_budget_14000", "i05_0_notes_only", "i05_1_crossover", "no_memory"]}, "population": [{"system": "no_memory", "status": "evaluated", "iteration": 0, "base": null, "score": 0.1111111111111111, "c`

**Analysis of the incumbent's failures/successes:**
```
- i06_0_param_char_budget_4500: move `param_char_budget_4500` on `i05_1_crossover` - evidence: untried mechanism
- i06_1_label_list: move `label_list` on `i04_0_coverage` - evidence: untried mechanism
```


### Proposal `i06_0_param_char_budget_4500` (parent `i05_1_crossover`)
- **claimed change:** Tuning char_budget to 4500 should balance recall and prompt length.
- **hypothesis:** Tuning char_budget to 4500 should balance recall and prompt length.
- **components:** param_char_budget_4500, axis:D
- **details:** `{"axis": "exploitation", "parents_read": ["i05_1_crossover"], "files_changed": ["memory.py"], "base_known": true, "identical_to_base": false, "move": "param_char_budget_4500", "evidence": "untried mechanism"}`
<details><summary>proposer reply</summary>

```
i06_0_param_char_budget_4500: param_char_budget_4500 on i05_1_crossover (untried mechanism)
i06_1_label_list: label_list on i04_0_coverage (untried mechanism)
```
</details>
**Actual diff:**
```diff
--- a/memory.py
+++ b/memory.py
@@ -1,11 +1,11 @@
-"""Memory system: select=recent, k=0, budget=0, notes(5)."""
+"""Memory system: select=recent, k=0, budget=4500, notes(5)."""
 import json
 import re
 from collections import Counter, defaultdict
 from typing import Any
 
 CONFIG = {'cap': 6,
- 'char_budget': 0,
+ 'char_budget': 4500,
  'demo_chars': 0,
  'k': 0,
  'label_list': False,
@@ -29,7 +29,7 @@
 
 
 class Memory(MemorySystem):
-    """Memory system: select=recent, k=0, budget=0, notes(5)."""
+    """Memory system: select=recent, k=0, budget=4500, notes(5)."""
 
     def __init__(self, llm):
         super().__init__(llm)

```


### Proposal `i06_1_label_list` (parent `i04_0_coverage`)
- **claimed change:** Listing every label seen so far lets the model name labels without a retrieved example.
- **hypothesis:** Listing every label seen so far lets the model name labels without a retrieved example.
- **components:** label_list, axis:A
- **details:** `{"axis": "exploration", "parents_read": ["i04_0_coverage"], "files_changed": ["memory.py"], "base_known": true, "identical_to_base": false, "move": "label_list", "evidence": "untried mechanism"}`
<details><summary>proposer prompt</summary>

```
(same proposer call as the first candidate)
```
</details>
**Actual diff:**
```diff
--- a/memory.py
+++ b/memory.py
@@ -1,4 +1,4 @@
-"""Memory system: select=topk_coverage, k=12, budget=7000, notes(5)."""
+"""Memory system: select=topk_coverage, k=12, budget=7000, label list, notes(5)."""
 import json
 import re
 from collections import Counter, defaultdict
@@ -8,7 +8,7 @@
  'char_budget': 7000,
  'demo_chars': 0,
  'k': 12,
- 'label_list': False,
+ 'label_list': True,
  'learn': 'always',
  'lookup': {},
  'note_words': 5,
@@ -29,7 +29,7 @@
 
 
 class Memory(MemorySystem):
-    """Memory system: select=topk_coverage, k=12, budget=7000, notes(5)."""
+    """Memory system: select=topk_coverage, k=12, budget=7000, label list, notes(5)."""
 
     def __init__(self, llm):
         super().__init__(llm)

```


**Gate on `i06_0_param_char_budget_4500`: ADMISSIBLE** - interface validation passed: evaluate and add to the population
  arithmetic: `{"rule": "admissible iff (screen passes, when enabled) and interface validation passes; no score threshold", "validate": true, "validated": true, "status": "evaluated"}`

**Eval `i06_0_param_char_budget_4500`** on evolve: S=0.5903, C=241409.3000, errors=0.0, missing=0
  per-task: ds_alpha/val=0.5417, ds_beta/val=0.6250, ds_gamma/val=0.6042

**Gate on `i06_1_label_list`: ADMISSIBLE** - interface validation passed: evaluate and add to the population
  arithmetic: `{"rule": "admissible iff (screen passes, when enabled) and interface validation passes; no score threshold", "validate": true, "validated": true, "status": "evaluated"}`

**Eval `i06_1_label_list`** on evolve: S=0.5625, C=211167.0000, errors=0.0, missing=0
  per-task: ds_alpha/val=0.4583, ds_beta/val=0.6250, ds_gamma/val=0.6042

**Decision:** kept `i06_0_param_char_budget_4500`; incumbent `i05_1_crossover` -> `i06_0_param_char_budget_4500`. Meta-Harness keeps every evaluated candidate in the population; 1 of 2 joined the Pareto frontier. Incumbent = highest-score Pareto point changed i05_1_crossover -> i06_0_param_char_budget_4500

**Shadow monitor (never shown to the loop)** `i06_0_param_char_budget_4500` (decision score 0.5903): ood: S=0.5583

**State after round:** `{"iteration_row": {"iteration": 6, "best_score": 0.5902777777777777, "n_candidates": 2, "n_valid": 2, "n_evaluated": 12, "frontier_size": 4, "hypervolume": 11025.404779128088, "files_read": 31, "view_chars": 3436430, "read_chars": 636776, "proposer_tokens": 0, "proposer_usd": 0.0, "error": null}, "frontier": {"best": {"system": "i06_0_param_char_budget_4500", "score": 0.5902777777777777}, "pareto": [{"system": "i06_0_param_char_budget_4500", "score": 0.5902777777777777, "context_cost": 5658.0}, {"system": "i05_1_crossover", "score": 0.5694444444444445, "context_cost": 1216.6666666666667}, {"system": "i01_1_label_list", "score": 0.3819444444444445, "context_cost": 428.3333333333333}, {"system": "no_memory", "score": 0.1111111111111111, "context_cost": 191.0}], "per_unit_best": {"ds_alpha/val": "i06_0_param_char_budget_4500", "ds_beta/val": "i03_1_notes", "ds_gamma/val": "i05_1_crossover"}, "hypervolume": 11025.404779128088}}`
