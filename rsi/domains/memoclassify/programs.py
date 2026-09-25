"""A library of memory-system *programs* for offline Meta-Harness runs.

The offline :class:`rsi.metaharness.MockProposer` cannot write novel code, so it
writes whole ``memory.py`` programs from a small design space that covers the
exploitation axes named in the Meta-Harness skill (A prompt template, B memory
content, C selection algorithm, D memory sizing, E learning trigger, F derived
memory). A *genome* is a dict of choices; :func:`render` turns it into a complete,
readable Python file with the genome as a ``CONFIG`` literal at the top, and
:func:`parse` reads it back from source - so a proposer can learn what earlier
candidates did by reading their code, exactly as a coding agent would.

Also here (domain knowledge the mock needs):

* :data:`MOVES` - mechanism-level edits (not parameter sweeps), each with an axis
  and a falsifiable hypothesis;
* :func:`diagnose` - what a careful reader of *raw* per-example traces can infer
  (context overflow, target label missing from the prompt, recurring confusions,
  heavy context at high accuracy);
* :func:`summarize` - a lossy "LLM summary" of traces (the ``scores_summary``
  view of the paper's Table 3 ablation);
* :func:`leaky_genome` - a hard-coded lookup table built from search-set traces
  (the overfitting failure mode M5 measures).
"""
from __future__ import annotations

import ast
import json
import pprint
import re
from collections import Counter
from typing import Any, Optional

DEFAULT_GENOME: dict[str, Any] = {
    "prompt": "json",            # A: json | answer_line
    "label_list": False,         # A: prime the prompt with every label seen so far
    "store": "all",              # B: all | errors_first | per_label_cap
    "cap": 6,                    # D: max stored examples per label (per_label_cap)
    "select": "all",             # C: all | recent | topk | topk_coverage | contrastive
    "k": 16,                     # D: examples retrieved (topk*, recent)
    "char_budget": 30000,        # D: max characters of demonstrations
    "notes": False,              # F: per-label keyword notes (compressed memory)
    "note_words": 5,
    "learn": "always",           # E: always | errors_only
    "demo_chars": 0,             # D: truncate each demonstration to this many characters (0 = full)
    "lookup": {},                # hard-coded answers (overfitting; never generic)
}

AXES = {"prompt": "A", "label_list": "A", "store": "B", "cap": "D", "select": "C", "k": "D", "char_budget": "D",
        "notes": "F", "note_words": "F", "learn": "E", "demo_chars": "D", "lookup": "leak"}

#: mechanism moves: name -> (changes, axis, hypothesis)
MOVES: dict[str, tuple[dict, str, str]] = {
    "retrieve_topk": ({"select": "topk", "k": 16, "char_budget": 6000}, "C",
                      "Showing only the demonstrations most similar to the query keeps the prompt inside the model's "
                      "effective context and removes distracting examples, raising accuracy at lower context."),
    "coverage": ({"select": "topk_coverage", "k": 12, "char_budget": 7000}, "C",
                 "Adding one demonstration of every label not yet retrieved lets the model answer labels that "
                 "similarity search missed."),
    "label_list": ({"label_list": True}, "A",
                   "Listing every label seen so far lets the model name labels without a retrieved example."),
    "contrastive": ({"select": "contrastive", "k": 12, "char_budget": 7000}, "C",
                    "Adding demonstrations of the labels most often confused with the top retrieved labels (learned "
                    "from online errors) helps the model separate near-duplicates."),
    "notes": ({"notes": True, "note_words": 5}, "F",
              "Distilling each label into its most distinctive words gives the model a compact description of "
              "every label at small context cost."),
    "notes_only": ({"notes": True, "note_words": 6, "select": "topk", "k": 4, "char_budget": 1500,
                    "label_list": False}, "F",
                   "Keyword notes for every label plus a handful of nearest demonstrations should match the full "
                   "demonstration set at a fraction of the context."),
    "per_label_cap": ({"store": "per_label_cap", "cap": 4}, "B",
                      "Keeping only the most recent few examples per label balances the memory and bounds context."),
    "errors_first": ({"store": "errors_first"}, "B",
                     "Storing mistakes preferentially concentrates memory on hard examples."),
    "answer_line": ({"prompt": "answer_line"}, "A",
                    "A plain 'Answer: <label>' format is easier to follow than JSON."),
    "learn_errors_only": ({"learn": "errors_only"}, "E",
                          "Learning only from mistakes avoids storing redundant easy examples."),
    "recent_window": ({"select": "recent", "k": 40, "char_budget": 9000}, "C",
                      "A recency window adapts to drift in the stream."),
    # plausible-sounding mechanisms that do not help in this world (most edits don't)
    "truncate_demos": ({"demo_chars": 45}, "D",
                       "Shortening every demonstration to its opening words keeps the gist at a fraction of the "
                       "context."),
    "recent_only": ({"select": "recent", "k": 12, "char_budget": 3000}, "C",
                    "The most recent examples are the most relevant for an online stream."),
    "few_demos": ({"select": "topk", "k": 3, "char_budget": 1200}, "C",
                  "Three nearest demonstrations are enough; more only distract the model."),
    "one_per_label": ({"store": "per_label_cap", "cap": 1}, "B",
                      "One canonical example per label is a cleaner memory than many noisy ones."),
    "errors_only_recent": ({"learn": "errors_only", "select": "recent", "k": 20, "char_budget": 4000}, "E",
                           "Remembering only recent mistakes focuses the prompt on what the model gets wrong."),
}

#: parameter variants - what an uninformed proposer tries most often ("parameter sweeps (pool sizes,
#: retrieval counts, context budgets ...) almost always regress or tie" - release SKILL.md)
PARAM_MOVES: dict[str, tuple[dict, str, str]] = {
    f"param_{key}_{val}": ({key: val}, "D", f"Tuning {key} to {val} should balance recall and prompt length.")
    for key, vals in (("k", (6, 10, 24, 32)), ("char_budget", (2500, 4500, 9000, 14000)),
                      ("note_words", (3, 8)), ("cap", (2, 10)))
    for val in vals
}
ALL_MOVES = {**MOVES, **PARAM_MOVES}


# ------------------------------------------------------------------ rendering
_TEMPLATE = '''"""{doc}"""
import json
import re
from collections import Counter, defaultdict
from typing import Any

CONFIG = {config}

WORD = re.compile(r"[a-z0-9]{{3,}}")


def _toks(text: str) -> set:
    return set(WORD.findall(text.lower().split("options:")[0]))


def _norm(s: Any) -> str:
    return str(s or "").strip().strip("`*\\"'.,;[]{{}}() ").lower()


class Memory(MemorySystem):
    """{doc}"""

    def __init__(self, llm):
        super().__init__(llm)
        self.examples = []          # {{"input", "target"}}
        self.labels = []            # labels in order of first appearance
        self.confusions = Counter() # (predicted, gold) -> count, learned from online mistakes
        self.words = defaultdict(Counter)

    # ---------------------------------------------------------------- learning
    def learn_from_batch(self, batch_results):
        for r in batch_results:
            gold = _norm(r["ground_truth"])
            ok = bool(r.get("was_correct", True))
            if gold not in self.labels:
                self.labels.append(gold)
            if not ok:
                self.confusions[(_norm(r.get("prediction")), gold)] += 1
            if CONFIG["notes"]:
                self.words[gold].update(_toks(r["input"]))
            if CONFIG["learn"] == "errors_only" and ok and any(e["target"] == gold for e in self.examples):
                continue
            ex = {{"input": r["input"], "target": gold, "hard": not ok}}
            if CONFIG["store"] == "per_label_cap":
                same = [e for e in self.examples if e["target"] == gold]
                if len(same) >= CONFIG["cap"]:
                    self.examples.remove(same[0])
            self.examples.append(ex)
        if CONFIG["store"] == "errors_first":
            self.examples.sort(key=lambda e: not e.get("hard", False))

    # --------------------------------------------------------------- retrieval
    def _similar(self, query: str):
        q = _toks(query)
        scored = []
        for i, e in enumerate(self.examples):
            t = _toks(e["input"])
            sim = len(q & t) / max(1, len(q | t))
            scored.append((sim, i, e))
        scored.sort(key=lambda x: (-x[0], -x[1]))
        return [e for _, _, e in scored]

    def _select(self, query: str):
        sel, k = CONFIG["select"], CONFIG["k"]
        if sel == "all":
            chosen = list(self.examples)
        elif sel == "recent":
            chosen = self.examples[-k:]
        else:
            ranked = self._similar(query)
            chosen = ranked[:k]
            if sel == "topk_coverage":
                have = {{e["target"] for e in chosen}}
                for e in ranked[k:]:
                    if e["target"] not in have:
                        chosen.append(e)
                        have.add(e["target"])
            elif sel == "contrastive":
                top = [e["target"] for e in chosen[:3]]
                rivals = []
                for (p, g), n in self.confusions.most_common():
                    if p in top and g not in top:
                        rivals.append(g)
                    if g in top and p not in top:
                        rivals.append(p)
                for lab in rivals[:4]:
                    for e in ranked:
                        if e["target"] == lab and e not in chosen:
                            chosen.append(e)
                            break
        out, used = [], 0
        for e in chosen:
            n = len(e["input"]) + len(e["target"]) + 8
            if used + n > CONFIG["char_budget"]:
                break
            out.append(e)
            used += n
        if sel not in ("all", "recent"):
            out.reverse()  # most similar demonstration closest to the question
        return out

    def _notes(self) -> str:
        if not self.words:
            return ""
        df = Counter()
        for c in self.words.values():
            df.update(set(c))
        lines = []
        for lab in self.labels:
            c = self.words.get(lab)
            if not c:
                continue
            best = sorted(c, key=lambda w: (-c[w] / df[w], -c[w], w))[:CONFIG["note_words"]]
            lines.append(f"- {{lab}}: {{' '.join(best)}}")
        return "\\n".join(lines)

    # -------------------------------------------------------------- prediction
    def predict(self, input: str):
        for key, lab in CONFIG["lookup"].items():
            if key in input:
                return lab, {{"lookup": True}}
        parts = ["Classify the input into one of the labels, following the demonstrations."]
        if CONFIG["label_list"] and self.labels:
            parts.append("Labels: " + ", ".join(self.labels))
        if CONFIG["notes"]:
            notes = self._notes()
            if notes:
                parts.append("Label notes:\\n" + notes)
        exs = self._select(input)
        if exs:
            dc = CONFIG["demo_chars"]
            parts.append("\\n\\n".join(f"Q: {{e['input'][:dc] if dc else e['input']}}\\nA: {{e['target']}}"
                                         for e in exs))
        parts.append("**Problem:**\\n" + input)
        if CONFIG["prompt"] == "json":
            parts.append('Respond in JSON: {{"reasoning": "...", "final_answer": "<label>"}}')
        else:
            parts.append("Reply with one line: Answer: <label>")
        response = self.call_llm("\\n\\n".join(parts))
        answer = extract_json_field(response, "final_answer")
        return answer, {{"n_examples": len(exs)}}

    def get_state(self) -> str:
        return json.dumps({{"examples": self.examples, "labels": self.labels,
                           "confusions": [[p, g, n] for (p, g), n in self.confusions.items()],
                           "words": {{k: dict(v) for k, v in self.words.items()}}}})

    def set_state(self, state: str) -> None:
        d = json.loads(state or "{{}}")
        self.examples = d.get("examples", [])
        self.labels = d.get("labels", [])
        self.confusions = Counter({{(p, g): n for p, g, n in d.get("confusions", [])}})
        self.words = defaultdict(Counter, {{k: Counter(v) for k, v in d.get("words", {{}}).items()}})
'''


def describe_genome(g: dict) -> str:
    bits = [f"select={g['select']}"]
    if g["select"] != "all":
        bits.append(f"k={g['k']}")
    if g["char_budget"] < 30000:
        bits.append(f"budget={g['char_budget']}")
    if g["label_list"]:
        bits.append("label list")
    if g["notes"]:
        bits.append(f"notes({g['note_words']})")
    if g["store"] != "all":
        bits.append(f"store={g['store']}")
    if g["learn"] != "always":
        bits.append(f"learn={g['learn']}")
    if g["prompt"] != "json":
        bits.append(f"format={g['prompt']}")
    if g["demo_chars"]:
        bits.append(f"demo_chars={g['demo_chars']}")
    if g["lookup"]:
        bits.append(f"lookup[{len(g['lookup'])}]")
    return "Memory system: " + ", ".join(bits) + "."


def render(genome: dict) -> str:
    g = {**DEFAULT_GENOME, **genome}
    cfg = pprint.pformat(g, width=100, sort_dicts=True)
    return _TEMPLATE.format(doc=describe_genome(g), config=cfg)


def read_config(source: str) -> Optional[dict]:
    """The literal value of a top-level ``CONFIG = {...}`` assignment (None if absent)."""
    try:
        tree = ast.parse(source or "")
    except SyntaxError:
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "CONFIG" for t in node.targets):
            try:
                val = ast.literal_eval(node.value)
            except (ValueError, SyntaxError):
                return None
            return val if isinstance(val, dict) else None
    return None


def parse(source: str) -> Optional[dict]:
    """Read the CONFIG literal back from a program (None for foreign code)."""
    g = read_config(source)
    return {**DEFAULT_GENOME, **g} if g is not None else None


def seed_genome(name: str) -> dict:
    """Genomes equivalent to the seed baselines (used when a seed's source is foreign code)."""
    if name == "no_memory":
        return {**DEFAULT_GENOME, "select": "recent", "k": 0, "char_budget": 0}
    return dict(DEFAULT_GENOME)


def apply_move(genome: dict, move: str) -> dict:
    return {**genome, **ALL_MOVES[move][0]}


def crossover(a: dict, b: dict) -> dict:
    """Retrieval from ``a``, prompt/memory content from ``b`` ("combining systems is valid")."""
    out = dict(b)
    for key in ("select", "k", "char_budget"):
        out[key] = a[key]
    out["label_list"] = a["label_list"] or b["label_list"]
    out["notes"] = a["notes"] or b["notes"]
    return out


# ------------------------------------------------------------------ diagnosis
def _records(trace_text: str) -> list[dict]:
    out = []
    for line in (trace_text or "").splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


#: The prompt length the trace diagnosis treats as "too long". NOTE (audit N9): 11,000 chars is TUNED to
#: MemoLM-A's hidden 12,000-char effective context (``model.VARIANTS``). It stands for the prior knowledge an
#: experienced engineer (or a frontier coding agent) has about a model's usable context; an LLM proposer
#: would have to infer it. The per-harness accuracy-vs-length signal in the traces is too noisy at this scale
#: to estimate it reliably (tested: the weighted gap flags ``fewshot_all`` in only about half the seeds),
#: so the mock keeps the prior, and M1 reports the sensitivity to un-tuned values
#: (``m1_history_ablation.py --budget-hint 6000|20000``).
DEFAULT_BUDGET_HINT = 11000


def _step_prompt(r: dict) -> str:
    """The prompt text(s) of one step record: every model call's prompt in full traces (``calls``), else the
    compact format's ``prompt`` (only some wrong eval steps carry one)."""
    calls = r.get("calls")
    if calls:
        return "\n".join(str(c.get("prompt") or "") for c in calls)
    return r.get("prompt") or ""


def diagnose(traces: dict[str, str], budget_hint: int = DEFAULT_BUDGET_HINT) -> list[tuple[str, float, str]]:
    """Read raw per-example traces (``{unit: jsonl}``) and return ranked
    ``(move, weight, evidence)`` suggestions, as a proposer reading log.jsonl would."""
    evals = [r for t in traces.values() for r in _records(t) if r.get("type") == "eval_step"]
    if not evals:
        return []
    errs = [r for r in evals if not r.get("ok")]
    n = len(evals)
    out: list[tuple[str, float, str]] = []
    long = [r for r in evals if (r.get("prompt_len") or 0) > budget_hint]
    if long:
        acc_long = sum(r["ok"] for r in long) / len(long)
        short = [r for r in evals if (r.get("prompt_len") or 0) <= budget_hint]
        acc_short = sum(r["ok"] for r in short) / len(short) if short else acc_long
        w = len(long) / n * (1.0 + max(0.0, acc_short - acc_long))
        out.append(("retrieve_topk", 1.5 * w, f"{len(long)}/{n} prompts longer than {budget_hint} chars "
                                               f"(acc {acc_long:.2f} vs {acc_short:.2f} on short prompts)"))
    with_prompt = [r for r in errs if _step_prompt(r)]
    missing = [r for r in with_prompt if r["tgt"] not in _step_prompt(r)]
    if with_prompt:
        frac = len(missing) / len(with_prompt)
        if frac > 0.15:
            out.append(("label_list", 1.2 * frac, f"target label absent from the prompt in {len(missing)}/"
                                                   f"{len(with_prompt)} inspected errors"))
            out.append(("coverage", 1.0 * frac, "retrieval misses the gold label's demonstrations"))
    pairs = Counter(tuple(sorted((r["pred"], r["tgt"]))) for r in errs if r.get("pred"))
    rec = sum(c for c in pairs.values() if c >= 2)
    if errs and rec / len(errs) > 0.15:
        out.append(("contrastive", 1.3 * rec / len(errs), f"{rec}/{len(errs)} errors are recurring label confusions "
                                                          f"(e.g. {pairs.most_common(1)[0][0]})"))
    bad = [r for r in errs if "-" not in str(r.get("pred", ""))]
    if errs and len(bad) / len(errs) > 0.3:
        out.append(("label_list", 1.0 * len(bad) / len(errs), f"{len(bad)} predictions are not label names"))
    ctx = sum(r.get("context_chars", 0) for r in evals) / n
    acc = 1 - len(errs) / n
    if ctx > 4000 and acc > 0.5:
        out.append(("notes_only", 0.6 * min(1.0, ctx / 10000), f"mean context {ctx:.0f} chars at accuracy {acc:.2f}"))
    elif ctx > 1500:
        out.append(("notes", 0.4, f"mean context {ctx:.0f} chars"))
    best: dict[str, tuple[str, float, str]] = {}
    for m, w, ev in out:
        if m not in best or w > best[m][1]:
            best[m] = (m, w, ev)
    return sorted(best.values(), key=lambda x: -x[1])


def summarize(traces: dict[str, str]) -> str:
    """Lossy summary of traces (stands in for an LLM summariser): aggregate accuracy,
    prompt length and the most frequent wrong prediction - per-example evidence is lost."""
    evals = [r for t in traces.values() for r in _records(t) if r.get("type") == "eval_step"]
    if not evals:
        return "No evaluation records."
    n = len(evals)
    errs = [r for r in evals if not r.get("ok")]
    plen = sum(r.get("prompt_len") or 0 for r in evals) / n
    common = Counter(r.get("pred") for r in errs).most_common(1)
    lines = [f"The system answered {n - len(errs)} of {n} evaluation examples correctly.",
             f"Average prompt length was about {int(plen / 1000)}k characters."]
    if common and common[0][1] >= 3:
        lines.append(f"The most frequent wrong answer was {common[0][0]!r} ({common[0][1]} times), which suggests "
                     f"some label confusion.")
    return " ".join(lines)


def diagnose_summary(summary: str, budget_hint: int = DEFAULT_BUDGET_HINT) -> list[tuple[str, float, str]]:
    """What a proposer can infer from a summary alone (same length prior as :func:`diagnose`)."""
    out = []
    m = re.search(r"about (\d+)k characters", summary or "")
    if m and int(m.group(1)) * 1000 >= budget_hint:
        out.append(("retrieve_topk", 0.8, f"summary: prompts ~{m.group(1)}k chars"))
    if "confusion" in (summary or ""):
        out.append(("contrastive", 0.3, "summary mentions label confusions"))
    return out


def leaky_genome(base: dict, traces: dict[str, str], max_entries: int = 200) -> dict:
    """Hard-code (input snippet -> label) pairs read from search-set traces. Scores well
    on the search set and not at all elsewhere - the overfitting a leakage screen must catch."""
    table: dict[str, str] = {}
    for t in traces.values():
        for r in _records(t):
            if r.get("type") != "eval_step":
                continue
            prev = str(r.get("input_preview", ""))
            m = re.match(r"\[ref (\w+)\]", prev)
            key = m.group(0) if m else prev.split(": ", 1)[-1][:40]
            if key and len(table) < max_entries:
                table[key] = r["tgt"]
    return {**base, "lookup": table}
