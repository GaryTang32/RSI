"""The Meta-Harness outer loop (spec A3.2, ``reference_examples/text_classification/meta_harness.py``).

::

    baselines -> evaluate on the search split -> frontier                        (phase 0)
    for t in 1..N:                                                               (evolution)
        view = store.view(history_mode)                    # what the proposer may read
        candidates = proposer.propose(t, view, k)          # k whole-program candidates
        valid = [c for c in candidates if interface_ok(c) (and not leaky, if screened)]
        evaluate valid on the search split; store code + scores + raw traces
        recompute frontier (per-unit best + Pareto score up / context cost down)
        append evolution_summary rows
    finalize(): test ONCE on baselines + Pareto + per-unit best, write results/, lock

No parent-selection rule, no mutation operators, no archive policy: every valid
candidate is evaluated and kept; the frontier is the output. The test split is
sealed during evolution (``rsi.core.TaskSuite`` raises on access) and results go
to ``results/``, which no history view exposes.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from ..core.artifact import Artifact
from ..core.domain import Domain
from ..core.evaluate import EvalResult, Evaluator
from ..core.ledger import Ledger, Node
from ..core.llm import LLM, Usage
from ..core.run import ImprovementResult
from .config import Config
from .frontier import hypervolume, pareto_frontier, per_unit_best
from .proposer import CandidateSpec, Proposer
from .store import ExperienceStore, FinalizedError, safe_name
from .validate import InterfaceValidator, LeakageScreen


class MetaHarnessLoop:
    """Stateful loop over one :class:`ExperienceStore` (resumable, finalise once)."""

    def __init__(self, domain: Domain, *, llm_task: Optional[LLM], proposer: Proposer, config: Config,
                 out_dir: str | Path, baselines: dict[str, Artifact],
                 summarizer: Optional[Callable[[dict], str]] = None,
                 leakage_screen: Optional[LeakageScreen] = None,
                 validator: Optional[InterfaceValidator] = None,
                 on_eval: Optional[Callable[[dict], None]] = None) -> None:
        self.domain = domain
        self.llm_task = llm_task
        self.proposer = proposer
        self.cfg = config
        self.out = Path(out_dir)
        self.store = ExperienceStore(self.out / "store")
        self.ledger = Ledger(self.out / "ledger.jsonl")
        self.baselines = dict(baselines)
        self.summarizer = summarizer
        self.screen = leakage_screen if config.leakage_screen else None
        if config.leakage_screen and self.screen is None:
            self.screen = LeakageScreen.for_domain(domain)
        self.validator = validator or InterfaceValidator(config.validate_timeout_s, config.validate_in_subprocess)
        self.evaluator = Evaluator(domain, llm_task, workers=config.workers)
        self.on_eval = on_eval
        self.node_of: dict[str, str] = {n.meta.get("system"): n.id for n in self.ledger.nodes() if n.meta}
        self.n_evaluated = len([n for n in self.ledger.nodes(kind="candidate") if n.status == "evaluated"])
        self.n_proposed = len(self.ledger.nodes(kind="candidate"))
        self.curve: list[dict] = []          # one row per evaluation: best-so-far (search) vs #evaluations
        self.iter_rows: list[dict] = []
        self.proposer_usage = Usage()
        self.order = len(self.store.names())

    # ------------------------------------------------------------ evaluation
    def _cost(self, ev: EvalResult) -> dict[str, float]:
        out = {}
        for tid, trs in ev.trials.items():
            vals = [t.meta.get(self.cfg.cost_metric) for t in trs if t.meta.get(self.cfg.cost_metric) is not None]
            if self.cfg.cost_metric == "tokens" or not vals:
                vals = [float(t.tokens) for t in trs]
            out[tid] = float(np.mean(vals)) if vals else 0.0
        return out

    def _evaluate(self, name: str, artifact: Artifact) -> dict:
        ev = self.evaluator.evaluate(artifact, self.cfg.search_split, k=self.cfg.trials)
        cost = self._cost(ev)
        scores = self.store.write_eval(name, ev, cost=cost)
        if self.summarizer is not None and self._want_summaries():
            self.store.write_summary_text(name, self.summarizer(self.store.traces(name)))
        return scores

    def _want_summaries(self) -> bool:
        return self.cfg.summaries == "always" or (self.cfg.summaries == "auto" and
                                                   self.cfg.history_mode == "scores_summary")

    def _cost_value(self, scores: dict) -> float:
        return float(scores["context_cost"]) if "context_cost" in self.cfg.objectives else 0.0

    def recompute_frontier(self) -> dict:
        pts, per_unit = [], {}
        for n in self.store.names():
            s = self.store.scores(n)
            if not s:
                continue
            pts.append((n, float(s["score"]), self._cost_value(s)))
            pu_cost = s.get("per_unit_cost", {})
            per_unit[n] = {u: (v, pu_cost.get(u, 0.0) if "context_cost" in self.cfg.objectives else 0.0)
                           for u, v in s["per_unit"].items()}
        front = pareto_frontier(pts)
        fr: dict = {u: v for u, v in per_unit_best(per_unit).items()}
        fr["_pareto"] = [{"system": n, "score": s, "val_accuracy": round(100 * s, 1), "context_cost": c}
                         for n, s, c in front]
        fr["_best"] = {"system": front[0][0], "score": front[0][1]} if front else None
        if pts:
            ref = max(p[2] for p in pts) * 1.1 + 1.0
            fr["_hypervolume"] = hypervolume(pts, ref_cost=ref)
            fr["_hv_ref_cost"] = ref
        self.store.write_frontier(fr)
        return fr

    def best_score(self) -> float:
        fr = self.store.frontier()
        return float(fr["_best"]["score"]) if fr.get("_best") else 0.0

    # ------------------------------------------------------------ phase 0
    def run_baselines(self) -> None:
        for name, art in self.baselines.items():
            if self.store.has(name) and self.store.scores(name):
                continue
            self.store.add_candidate(name, art, {"iteration": 0, "kind": "baseline", "order": self._next_order()})
            scores = self._evaluate(name, art)
            self.store.update_meta(name, status="evaluated")
            node = self.ledger.add(Node(id=f"mh_{safe_name(name)}", parent=None, round=0, kind="baseline",
                                        status="evaluated", score=scores["score"], cost=scores["context_cost"],
                                        change=f"baseline {name}", artifact_id=art.id,
                                        metrics=scores["per_unit"], meta={"system": name}))
            self.node_of[name] = node.id
            self.store.append_summary({"iteration": 0, "system": name, "avg_val": scores["avg_val"],
                                       "axis": "baseline", "hypothesis": "baseline", "delta": None,
                                       "outcome": f"{scores['avg_val']:.1f}% (baseline)",
                                       "context_cost": scores["context_cost"]})
        self.recompute_frontier()

    def _next_order(self) -> int:
        self.order += 1
        return self.order

    def budget_left(self) -> Optional[int]:
        return None if self.cfg.eval_budget is None else max(0, self.cfg.eval_budget - self.n_evaluated)

    # ------------------------------------------------------------ one iteration
    def iterate(self, t: int) -> list[dict]:
        if self.store.is_finalized():
            raise FinalizedError("run is finalised; start a new run to evolve further")
        self.store.clear_pending()
        pre_best = self.best_score()
        view = self.store.view(self.cfg.history_mode, window=self.cfg.window, seeds=list(self.baselines))
        visible = {n: self.store.artifact(n) for n in self.store.names()
                   if any(p.startswith(f"candidates/{safe_name(n)}/src/") for p in view)}
        k = self.cfg.k
        left = self.budget_left()
        if left is not None:
            k = min(k, left)
        t0 = time.time()
        batch = self.proposer.propose(iteration=t, view=view, k=k, brief=self.domain.describe(),
                                      artifacts=visible, seed=self.cfg.seed)
        t_prop = time.time() - t0
        self.proposer_usage = self.proposer_usage + batch.usage
        view_chars = sum(len(v) for v in view.values())
        read_chars = sum(len(view.get(p, "")) for p in batch.files_read)
        self.store.log_session(t, prompt=batch.prompt, response=batch.transcript, meta={
            "iteration": t, "history_mode": self.cfg.history_mode, "usage": batch.usage.to_dict(),
            "files_read": batch.files_read, "n_files_read": len(batch.files_read),
            "files_read_by_kind": _kinds(batch.files_read), "view_files": len(view), "view_chars": view_chars,
            "read_chars": read_chars, "error": batch.error, "seconds": round(t_prop, 3),
            "candidates": [c.pending_row() for c in batch.candidates]})
        self.store.write_pending(t, [c.pending_row() for c in batch.candidates])
        rows = []
        t1 = time.time()
        for c in batch.candidates:
            rows.append(self._handle(t, c, pre_best))
        post = self.recompute_frontier()
        post_best = float(post["_best"]["score"]) if post.get("_best") else 0.0
        for i, r in enumerate(rows):
            if r["outcome"] != "failed":
                r["delta"] = round(r["avg_val"] - 100 * post_best, 1)          # release quirk: post-iteration best
                r["outcome"] = f"{r['avg_val']:.1f}% ({r['avg_val'] - 100 * post_best:+.1f})"
            if i == 0:
                r["timing_s"] = {"propose": round(t_prop, 2), "bench": round(time.time() - t1, 2),
                                 "wall": round(time.time() - t0, 2)}
            self.store.append_summary(r)
        self.iter_rows.append({"iteration": t, "best_score": post_best, "n_candidates": len(batch.candidates),
                               "n_valid": sum(r["outcome"] != "failed" for r in rows),
                               "n_evaluated": self.n_evaluated, "frontier_size": len(post.get("_pareto", [])),
                               "hypervolume": post.get("_hypervolume"), "files_read": len(batch.files_read),
                               "view_chars": view_chars, "read_chars": read_chars,
                               "proposer_tokens": batch.usage.total_tokens, "proposer_usd": batch.usage.cost_usd,
                               "error": batch.error})
        return rows

    def _handle(self, t: int, c: CandidateSpec, pre_best: float) -> dict:
        name = c.name
        while self.store.has(name):
            name = f"{name}_x"
        c.name = name
        self.n_proposed += 1
        self.store.add_candidate(name, c.artifact, {
            "iteration": t, "kind": "candidate", "base_system": c.base_system, "hypothesis": c.hypothesis,
            "axis": c.axis, "components": c.components, "parents_read": c.parents_read,
            "order": self._next_order(), **{k: v for k, v in c.meta.items() if isinstance(v, (str, int, float))}})
        parent_node = self.node_of.get(c.base_system)
        base_art = self.store.artifact(c.base_system) if c.base_system and self.store.has(c.base_system) else None
        row = {"iteration": t, "system": name, "avg_val": 0.0, "axis": c.axis, "hypothesis": c.hypothesis,
               "components": c.components, "delta": None, "outcome": "failed"}
        status, reason, scores = "evaluated", "", None
        if self.screen is not None:
            r = self.screen.check(c.artifact, base_art)
            if r:
                status, reason = "rejected_leakage", r
        if status == "evaluated" and self.cfg.validate:
            ok, msg = self.validator.validate(self.domain, c.artifact, self.llm_task)
            if not ok:
                status, reason = "invalid", msg[:500]
        if status == "evaluated":
            scores = self._evaluate(name, c.artifact)
            self.n_evaluated += 1
            row.update(avg_val=scores["avg_val"], delta_pre=round(scores["avg_val"] - 100 * pre_best, 1),
                       context_cost=scores["context_cost"], outcome="ok")
            best = max(pre_best, max((r["score"] for r in self.curve), default=0.0), scores["score"])
            self.curve.append({"n_eval": self.n_evaluated, "iteration": t, "system": name, "score": scores["score"],
                               "context_cost": scores["context_cost"], "best_so_far": best})
            if self.on_eval:
                self.on_eval(self.curve[-1])
        self.store.update_meta(name, status=status, reason=reason)
        diff = base_art.diff(c.artifact)[:20000] if base_art is not None else ""
        node = self.ledger.add(Node(
            id=f"mh_{safe_name(name)}", parent=parent_node, round=t, kind="candidate", status=status,
            score=scores["score"] if scores else None, cost=scores["context_cost"] if scores else None,
            change=(c.hypothesis or "")[:300], artifact_id=c.artifact.id, diff=diff,
            metrics=scores["per_unit"] if scores else {},
            meta={"system": name, "axis": c.axis, "components": c.components, "base_system": c.base_system,
                  "parents_read": c.parents_read, "reason": reason}))
        self.node_of[name] = node.id
        return row

    # ------------------------------------------------------------ driver
    def run(self, iterations: Optional[int] = None) -> list[dict]:
        self.run_baselines()
        start = self.store.last_iteration() + 1
        n = self.cfg.iterations if iterations is None else iterations
        for t in range(start, start + n):
            if self.budget_left() == 0:
                break
            self.iterate(t)
        return self.iter_rows

    # ------------------------------------------------------------ finalisation
    def finalize(self, splits: Optional[tuple[str, ...]] = None, llm: Optional[LLM] = None) -> dict:
        """Evaluate baselines + Pareto systems + per-unit bests ONCE on the test split(s),
        write ``results/`` and ``frontier.json``, and lock further evolution. Idempotent."""
        splits = tuple(splits or self.cfg.test_splits)
        fr = self.recompute_frontier()
        systems = list(dict.fromkeys(list(self.baselines) + [p["system"] for p in fr.get("_pareto", [])] +
                                     [v["best_system"] for u, v in fr.items() if not u.startswith("_")]))
        if self.store.is_finalized():
            return self._final_report(splits, systems)
        self.store.write_finalized("in_progress", systems)
        ev = Evaluator(self.domain, llm or self.llm_task, workers=self.cfg.workers, allow_sealed=True)
        failures = []
        for split in splits:
            if split not in self.domain.tasks.splits:
                continue
            for s in systems:
                try:
                    r = ev.evaluate(self.store.artifact(s), split, k=self.cfg.trials)
                    self.store.write_test_result(split, s, r, self._cost(r))
                except Exception as e:  # noqa: BLE001
                    failures.append(f"{s}/{split}: {e}")
        rep = self._final_report(splits, systems)
        (self.store.root / "frontier.json").write_text(__import__("json").dumps(rep, indent=1, default=float))
        self.store.write_finalized("complete" if not failures else "incomplete", systems, failures=failures)
        return rep

    def _final_report(self, splits, systems) -> dict:
        out = {"systems": systems, "splits": {}}
        for split in splits:
            rows = {s: self.store.test_result(split, s) for s in systems}
            rows = {s: r for s, r in rows.items() if r}
            pts = [(s, r["score"], r["context_cost"] if "context_cost" in self.cfg.objectives else 0.0)
                   for s, r in rows.items()]
            out["splits"][split] = {"results": rows, "_pareto": [{"system": n, "test_accuracy": sc, "context_cost": c}
                                                                 for n, sc, c in pareto_frontier(pts)]}
        return out


def _kinds(paths: list[str]) -> dict[str, int]:
    out = {"code": 0, "traces": 0, "scores": 0, "other": 0}
    for p in paths:
        if "/src/" in p:
            out["code"] += 1
        elif "/traces/" in p or "/per_task/" in p:
            out["traces"] += 1
        elif p.endswith(("scores.json", "summary.md")) or p in ("evolution_summary.jsonl", "frontier_val.json"):
            out["scores"] += 1
        else:
            out["other"] += 1
    return out
