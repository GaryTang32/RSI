"""MemoClassify: online text classification where the *harness is a memory system*.

A candidate artifact is one file, ``memory.py``, defining ``class Memory(MemorySystem)``
(see :mod:`.memory`). One :class:`~rsi.core.Task` is one dataset *part*:

* ``evolve``  - ``<dataset>/val`` for the search datasets (Meta-Harness's search set);
* ``test``    - ``<dataset>/test`` for the same datasets (sealed; finalisation only);
* ``ood``     - ``<dataset>/test`` for unseen datasets (sealed; transfer reports).

``execute`` runs the inner loop of ``reference_examples/text_classification/inner_loop.py``:
online mode (default, the paper's setting) streams the train split through
``predict -> score -> learn_from_batch``; offline mode (the release default)
calls ``learn_from_batch`` with ``prediction = ground_truth``. The memory then
predicts every example of the evaluated part.

**Traces** (``trace_detail="full"``, the default) hold what the paper says a harness
directory holds: "prompts, tool calls, model outputs, and state updates" [paper:MH §3].
Every model call made during a step is recorded with its full prompt and the raw model
reply (train and eval phases, and calls made inside ``learn_from_batch``); every learning
step records the memory-state size; and the full memory state is checkpointed at the
release's default checkpoint steps (step 0 and the last training step). Per-example rows
keep the release's fields ``{step, input_preview, pred, tgt, ok, prompt_len, prompt_hash}``.
``trace_detail="compact"`` is the earlier, thinner format (full prompts only for up to 12
wrong eval predictions per unit, cut to 6,000 chars; no raw replies; no train prompts;
one checkpoint cut to 3,000 chars) for very large runs.

**Context cost.** The release (``inner_loop.py:evaluate_memory``) measures only the LAST
model call per prediction: ``context_len = max(0, prompt_len - len(input))``. A harness that
makes its big call first and a tiny confirm call last then reports almost no context
(audit N2: 17,724 -> 46.5 chars while real tokens rose 2%). ``Execution.meta["context_chars"]``
is therefore the mean over evaluated examples of the injected context summed over ALL
model calls made for the prediction, ``sum_calls max(0, len(prompt_i) - len(input))``. It
equals the release's value for single-call harnesses, and it matches the paper's own
accounting of its two-call Draft-Verification harness ("both calls use short retrieved
contexts, so the overall context cost stays near the low end", App. B.1). The release's
last-call value is kept as ``meta["context_chars_last_call"]`` (select it with
``rsi.metaharness.Config(cost_metric="context_chars_last_call")``). Prompts are recorded by
the domain's model wrapper, not by the candidate's ``call_llm`` bookkeeping, which a
candidate could bypass or override.
The grader (accuracy against labels) lives here, outside the artifact.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from typing import Any, Optional

from ...core.artifact import Artifact
from ...core.domain import Domain, Execution
from ...core.llm import LLM
from ...core.tasks import Task, TaskSuite
from .data import LEAKY_SPEC, SEARCH_SPECS, ClassDataset, make_datasets
from .memory import COMPARATOR_PROGRAMS, SEED_PROGRAMS, MemorySystem, extract_json_field
from .model import MemoLM

DEFAULT_MAX_WALL_S = 120.0


def normalize_label(s: Any) -> str:
    return str(s or "").strip().strip("`*\"'.,;[]{}() ").lower()


def load_memory_class(source: str):
    """Exec a candidate ``memory.py`` and return its MemorySystem subclass."""
    ns: dict[str, Any] = {"MemorySystem": MemorySystem, "extract_json_field": extract_json_field,
                          "__name__": "memory_candidate"}
    exec(compile(source, "memory.py", "exec"), ns)  # noqa: S102 - the harness code is the artifact
    cls = ns.get("Memory")
    if isinstance(cls, type) and issubclass(cls, MemorySystem):
        return cls
    for v in ns.values():
        if isinstance(v, type) and issubclass(v, MemorySystem) and v is not MemorySystem and \
                v.__module__ == "memory_candidate":
            return v
    raise TypeError("memory.py defines no MemorySystem subclass (expected class Memory)")


class HarnessTimeout(RuntimeError):
    pass


def _md5(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()[:8]


class MemoClassifyDomain(Domain):
    name = "memoclassify"
    components = {"memory": ["memory.py"], "prompt": ["memory.py"], "retrieval": ["memory.py"]}
    structural_components = ("memory",)

    def __init__(self, search: Optional[list[ClassDataset]] = None, ood: Optional[list[ClassDataset]] = None, *,
                 seed: int = 0, scale: float = 1.0, leaky: bool = False, mode: str = "online", batch_size: int = 1,
                 trace_prompts: str = "errors", trace_detail: str = "full",
                 max_wall_s: float = DEFAULT_MAX_WALL_S) -> None:
        if trace_detail not in ("full", "compact"):
            raise ValueError("trace_detail must be 'full' or 'compact'")
        if search is None or ood is None:
            specs = SEARCH_SPECS + ((LEAKY_SPEC,) if leaky else ())
            s, o = make_datasets(seed, search=specs, scale=scale)
            search = search if search is not None else s
            ood = ood if ood is not None else o
        self.search = list(search)
        self.ood = list(ood)
        self.datasets = {d.name: d for d in self.search + self.ood}
        self.mode = mode
        self.batch_size = batch_size
        self.trace_prompts = trace_prompts          # compact traces only: "errors" | "all" | "none"
        self.trace_detail = trace_detail
        self.max_wall_s = max_wall_s
        tasks, splits = [], {"evolve": [], "test": [], "ood": []}
        for d in self.search:
            for part, split in (("val", "evolve"), ("test", "test")):
                t = Task(f"{d.name}/{part}", {"dataset": d.name, "part": part}, None, d.name, {"entities": [d.name]})
                tasks.append(t)
                splits[split].append(t.id)
        for d in self.ood:
            t = Task(f"{d.name}/test", {"dataset": d.name, "part": "test"}, None, d.name, {"entities": [d.name]})
            tasks.append(t)
            splits["ood"].append(t.id)
        splits["smoke"] = splits["evolve"][:1]
        super().__init__(TaskSuite(tasks, splits, name=f"memoclassify-s{seed}"))
        self._models: dict[str, MemoLM] = {}

    # ------------------------------------------------------------ artifacts
    @staticmethod
    def seed_artifact(name: str = "fewshot_all") -> Artifact:
        return Artifact({"memory.py": SEED_PROGRAMS[name]}, meta={"name": name})

    @classmethod
    def baselines(cls) -> dict[str, Artifact]:
        return {n: cls.seed_artifact(n) for n in SEED_PROGRAMS}

    @staticmethod
    def comparators() -> dict[str, Artifact]:
        """Few-shot-N comparators (``fewshot_4`` ... ``fewshot_64``; release ``fewshot_memory.py``). Not part of
        the initial population; used to check context claims against more than the overflowing ``fewshot_all``."""
        return {n: Artifact({"memory.py": src}, meta={"name": n}) for n, src in COMPARATOR_PROGRAMS.items()}

    def make_model(self, variant: str = "A") -> MemoLM:
        """The frozen base model (``A`` = selection model, ``B`` = unseen model)."""
        if variant not in self._models:
            self._models[variant] = MemoLM(self.datasets.values(), variant)
        return self._models[variant]

    def describe(self) -> str:
        return (
            "The artifact is a memory system for ONLINE text classification around a frozen LLM, in one file "
            "memory.py defining `class Memory(MemorySystem)` with predict(input)->(answer, metadata), "
            "learn_from_batch(batch_results) (dicts with input, prediction, ground_truth, was_correct, metadata), "
            "get_state()->str and set_state(str). Call the model only through self.call_llm(prompt)->str and parse "
            "answers with extract_json_field(response, 'final_answer') (both are pre-imported). Labelled examples "
            "stream through predict -> learn_from_batch; then predict() is scored by exact label accuracy on unseen "
            "examples. The frozen model reads demonstrations written as 'Q: <text>' / 'A: <label>' line pairs (also "
            "'Input:'/'Label:'), label lists written as 'Labels: a, b, c', and label notes written as "
            "'- <label>: word word'; it can only answer labels that appear in the prompt, and it degrades when the "
            "prompt is very long (demonstrations in the middle of a long prompt get ignored). A second objective is "
            "context cost: characters injected into the prompt besides the input. The same memory system will be run "
            "unchanged on other datasets (other vocabularies, label counts) and another model, so it must be general."
        )

    def leakage_terms(self, split: str = "evolve") -> list[str]:
        """Dataset names, search-set labels, reference ids and input snippets."""
        terms: set[str] = set()
        for d in self.search:
            terms.add(d.name)
            terms.update(d.labels)
            for ex in d.val:
                m = re.match(r"\[ref (\w+)\]", ex.text)
                if m:
                    terms.add(m.group(1))
                body = ex.text.split(": ", 1)[-1]
                terms.add(body[:32])
        return sorted(terms)

    # ------------------------------------------------------------ execution
    def execute(self, artifact: Artifact, task: Task, *, seed: int, llm: Optional[LLM]) -> Execution:
        state: dict[str, Any] = {}
        try:
            ex = self._execute(artifact, task, seed=seed, llm=llm, state=state)
        except Exception:  # noqa: BLE001
            if "infra" in state:                   # a backend failure, not the harness's fault
                return Execution(error=state["infra"], steps=state.get("calls", 0))
            raise
        if "infra" in state:     # the candidate swallowed a backend failure: a missing trial, not a wrong answer
            return Execution(error=state["infra"], trace=ex.trace, tokens=ex.tokens, steps=ex.steps)
        return ex

    def _execute(self, artifact: Artifact, task: Task, *, seed: int, llm: Optional[LLM], state: dict) -> Execution:
        if llm is None:
            llm = self.make_model("A")
        src = artifact.get("memory.py")
        if src is None:
            return Execution(error="memory.py missing")
        ds = self.datasets[task.input["dataset"]]
        part = task.input["part"]
        full = self.trace_detail == "full"
        state.update({"calls": 0, "tokens": 0, "usd": 0.0, "cur": []})
        lock = threading.Lock()
        t0 = time.time()

        def call(prompt: str) -> str:
            prompt = str(prompt)
            rec = {"prompt": prompt, "response": None}
            with lock:
                state["calls"] += 1
                i = state["calls"]
                # context cost is measured HERE, on the domain side of the model boundary, so a candidate
                # cannot hide injected context by bypassing MemorySystem.call_llm's bookkeeping; every call
                # of the current step is kept (the context metric sums them, the full trace logs them)
                state["last_prompt"] = prompt
                state["cur"].append(rec)
            if time.time() - t0 > self.max_wall_s:
                raise HarnessTimeout(f"harness exceeded {self.max_wall_s}s")
            resp = llm.complete(prompt, seed=seed * 100003 + i, role="task")
            if not resp.ok:
                msg = f"infra: llm backend error: {resp.error}"
                state.setdefault("infra", msg)     # remembered even if the candidate swallows the exception
                raise RuntimeError(msg)
            with lock:
                rec["response"] = resp.text
                state["tokens"] += resp.usage.total_tokens
                state["usd"] += resp.usage.cost_usd
            return resp.text

        def begin_step() -> None:
            with lock:
                state["cur"] = []

        def step_calls() -> list[dict]:
            with lock:
                return list(state["cur"])

        def call_records(calls: list[dict]) -> list[dict]:
            return [{"prompt": c["prompt"], "response": c["response"], "prompt_len": len(c["prompt"]),
                     "prompt_hash": _md5(c["prompt"])} for c in calls]

        def last_info(calls: list[dict]) -> dict:
            p = calls[-1]["prompt"] if calls else None
            if p is None:
                return {"prompt_len": None, "prompt_hash": None, "prompt_text": None}
            return {"prompt_len": len(p), "prompt_hash": _md5(p), "prompt_text": p}

        def get_state_safe(m) -> str:
            try:
                st = m.get_state()
                return st if isinstance(st, str) else str(st)
            except Exception as e:  # noqa: BLE001
                return f"<get_state failed: {e}>"

        cls = load_memory_class(src)
        mem = cls(call)
        records: list[dict] = [{"type": "meta", "dataset": ds.name, "part": part, "mode": self.mode,
                                "n_train": len(ds.train), "n_eval": len(ds.part(part)),
                                "trace_detail": self.trace_detail}]
        n_train = len(ds.train)
        # release default checkpoint steps (eval_interval 0): step 0 and the last training index
        ckpt_steps = {0, n_train - 1} if n_train else set()
        train_ok = 0
        for b in range(0, n_train, self.batch_size):
            batch = ds.train[b:b + self.batch_size]
            results = []
            for j, ex in enumerate(batch):
                if self.mode == "online":
                    begin_step()
                    pred, meta = mem.predict(ex.text)
                    calls = step_calls()
                    ok = normalize_label(pred) == ex.label
                    train_ok += ok
                    info = last_info(calls)
                    rec = {"type": "step", "phase": "train", "step": b + j, "input_preview": ex.text[:200],
                           "pred": str(pred)[:120], "tgt": ex.label, "ok": bool(ok),
                           "prompt_len": info["prompt_len"], "prompt_hash": info["prompt_hash"]}
                    if full:
                        rec.update(n_calls=len(calls), calls=call_records(calls))
                    records.append(rec)
                else:
                    pred, meta, ok = ex.label, {}, True
                results.append({"input": ex.text, "prediction": pred, "ground_truth": ex.label, "was_correct": ok,
                                "metadata": meta if isinstance(meta, dict) else {}})
            begin_step()
            mem.learn_from_batch(results)
            if full:
                calls = step_calls()
                last = b + len(batch) - 1
                st = get_state_safe(mem)
                lrec = {"type": "learn", "step": last, "batch_size": len(batch), "memory_state_chars": len(st),
                        "n_calls": len(calls)}
                if calls:                              # model calls made while learning (e.g. LLM-written notes)
                    lrec["calls"] = call_records(calls)
                records.append(lrec)
                for s_ in sorted(ckpt_steps):
                    if b <= s_ <= last:
                        records.append({"type": "checkpoint", "step": s_, "memory_state": st,
                                        "memory_state_chars": len(st)})
        mem_state = get_state_safe(mem)
        if not full:
            records.append({"type": "checkpoint", "memory_state": mem_state[:3000],
                            "memory_state_chars": len(mem_state)})
        preds, ctx, ctx_last = [], [], []
        n_err_prompts = 0
        for i, ex in enumerate(ds.part(part)):
            begin_step()
            pred, meta = mem.predict(ex.text)
            calls = step_calls()
            info = last_info(calls)
            plen = info["prompt_len"] or 0
            c_last = max(0, plen - len(ex.text))
            c = sum(max(0, len(cl["prompt"]) - len(ex.text)) for cl in calls)   # all model calls of this query
            ctx.append(c)
            ctx_last.append(c_last)
            ok = normalize_label(pred) == ex.label
            preds.append(str(pred))
            rec = {"type": "eval_step", "step": i, "input_preview": ex.text[:200], "pred": str(pred)[:120],
                   "tgt": ex.label, "ok": bool(ok), "prompt_len": plen, "context_chars": c,
                   "context_chars_last_call": c_last, "n_calls": len(calls), "prompt_hash": info["prompt_hash"]}
            if full:
                rec["calls"] = call_records(calls)
            else:
                want = self.trace_prompts == "all" or (self.trace_prompts == "errors" and not ok and n_err_prompts < 12)
                if want and info.get("prompt_text"):
                    rec["prompt"] = info["prompt_text"][:6000]
                    n_err_prompts += 1
            records.append(rec)
        n_eval = max(1, len(preds))
        done = {"type": "done", "train_acc": train_ok / max(1, n_train) if self.mode == "online" else None,
                "eval_acc": sum(r["ok"] for r in records if r["type"] == "eval_step") / n_eval,
                "memory_context_chars": sum(ctx) / n_eval, "memory_context_chars_last_call": sum(ctx_last) / n_eval,
                "llm_calls": state["calls"], "runtime_seconds": round(time.time() - t0, 3)}
        records.append(done)
        return Execution(output=preds, trace="\n".join(json.dumps(r) for r in records), tokens=state["tokens"],
                         cost_usd=state["usd"], steps=state["calls"],
                         meta={"context_chars": sum(ctx) / n_eval, "context_chars_last_call": sum(ctx_last) / n_eval,
                               "llm_calls": state["calls"], "train_acc": done["train_acc"],
                               "memory_state_chars": len(mem_state), "dataset": ds.name, "part": part})

    def grade(self, task: Task, execution: Execution) -> tuple[float, str]:
        ds = self.datasets[task.input["dataset"]]
        gold = [ex.label for ex in ds.part(task.input["part"])]
        preds = list(execution.output or [])
        correct = sum(1 for p, g in zip(preds, gold) if normalize_label(p) == g)
        errs = [f"pred {normalize_label(p)!r} expected {g!r}" for p, g in zip(preds, gold)
                if normalize_label(p) != g][:5]
        fb = f"accuracy {correct}/{len(gold)}" + (("; errors: " + "; ".join(errs)) if errs else "")
        return correct / max(1, len(gold)), fb

    def smoke(self, artifact: Artifact, llm: Optional[LLM] = None) -> Optional[str]:
        """Interface check on a tiny stream (cold-start predict, learn, predict, state round trip)."""
        return validate_memory(artifact, self, llm)


def validate_memory(artifact: Artifact, domain: MemoClassifyDomain, llm: Optional[LLM] = None,
                    n: int = 3) -> Optional[str]:
    """Meta-Harness pre-evaluation check (paper App. D): import the module, instantiate the
    class, call ``predict`` cold and after ``learn_from_batch`` on a few examples, and round-trip
    ``get_state``/``set_state``. Returns an error string or None."""
    src = artifact.get("memory.py")
    if src is None:
        return "memory.py missing"
    llm = llm or domain.make_model("A")
    ds = domain.search[0]
    try:
        cls = load_memory_class(src)
        mem = cls(lambda p: llm.complete(str(p), seed=0, role="validate").text)
        for ex in ds.train[:n]:
            ans, meta = mem.predict(ex.text)
            if not isinstance(ans, str) or not isinstance(meta, dict):
                return "predict must return (str, dict)"
        mem.learn_from_batch([{"input": ex.text, "prediction": ex.label, "ground_truth": ex.label,
                               "was_correct": True, "metadata": {}} for ex in ds.train[:n]])
        mem.predict(ds.val[0].text if ds.val else ds.train[0].text)
        st = mem.get_state()
        if not isinstance(st, str):
            return "get_state must return str"
        mem2 = cls(lambda p: "")
        mem2.set_state(st)
    except Exception as e:  # noqa: BLE001
        return f"{type(e).__name__}: {e}"
    return None


def make_domain(seed: int = 0, **kw) -> MemoClassifyDomain:
    return MemoClassifyDomain(seed=seed, **kw)
