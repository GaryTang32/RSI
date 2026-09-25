"""Per-iteration audit trace of a Meta-Harness run (``rsi.trace`` format) + write-only shadow monitor.

:class:`MHTracer` maps what :class:`~rsi.metaharness.loop.MetaHarnessLoop` does onto the uniform
:mod:`rsi.trace` event kinds (``<out_dir>/trace.jsonl``; render with ``rsi.trace.inspect``):

``run_start``   config, seed / baseline artifact ids, proposer class, split sizes, shadow splits
``baseline``    each baseline of the initial population H0 on the search split (per unit + raw trials)
``noise``       none: Meta-Harness has no noise band and no keep gate (recorded as such)
``round_start`` loop state before iteration t: evaluations used / budget, frontier (Pareto + best),
                population, what the history view exposes (files by kind, chars, visible systems)
``analysis``    the proposer's own diagnosis: mock = move + evidence per candidate; LLM = the reply text
                outside the file blocks (the paper has no separate analysis step - the proposer does it)
``proposal``    per candidate: proposer prompt + reply (first candidate of the batch), claimed hypothesis /
                axis / components / base system, the files it claimed vs the ACTUAL diff base -> candidate
``critic``      the optional pilot leakage screen (off in the faithful default)
``gate``        admissibility only: interface validation (+ screen). There is no score gate in Meta-Harness
``eval``        every search-split evaluation (per unit + raw trial scores, context cost)
``decision``    after the frontier is recomputed: per candidate score / cost / on-frontier / dominated-by,
                both deltas (pre-iteration best and the release's post-iteration best), incumbent
                (frontier ``_best``) before / after
``monitor``     :class:`rsi.trace.ShadowMonitor` on sealed splits for every new ``_best`` (never shown
                to the loop; its model calls are metered under ``shadow:*``)
``state``       loop state after the iteration (the release's iteration row + frontier)
``note``        finalisation (the one-time test evaluation) and skipped iterations
``run_end``     best system, frontier, usage (loop vs monitor), stop reason

Everything here only *reads* loop state; nothing is returned to the loop.
"""
from __future__ import annotations

from typing import Any, Optional, Sequence

from ..core.artifact import Artifact
from ..core.llm import LLM, LLMResponse
from ..trace import RunTracer, ShadowMonitor

SHADOW_PREFIX = "shadow:"


class ShadowLLM(LLM):
    """The task model as the shadow monitor sees it: calls are delegated unchanged (same cache
    identity) but metered under ``shadow:<role>`` so they never count as loop spend."""

    def __init__(self, inner: LLM) -> None:
        super().__init__()
        self.inner = inner
        self.name = inner.name

    def complete(self, prompt, *, system=None, max_tokens=None, seed=None, role="default") -> LLMResponse:
        resp = self.inner.complete(prompt, system=system, max_tokens=max_tokens, seed=seed,
                                   role=SHADOW_PREFIX + role)
        self.meter.add(role, resp.usage)
        return resp

    def __getattr__(self, item):               # domain-specific model attributes (e.g. MemoLM.variant)
        if item == "inner":
            raise AttributeError(item)
        return getattr(self.inner, item)


def default_shadow_splits(domain) -> list[str]:
    """Sealed ``holdout`` / ``ood`` splits of the domain (Meta-Harness' own ``test`` split is left to
    ``finalize()``, which evaluates it exactly once)."""
    ts = domain.tasks
    return [s for s in ("holdout", "ood") if s in ts.splits and ts.is_sealed(s) and ts.splits[s]]


def _kinds(paths) -> dict[str, int]:
    out = {"code": 0, "traces": 0, "per_task": 0, "scores": 0, "summaries": 0, "run_files": 0, "other": 0}
    for p in paths:
        if "/src/" in p:
            out["code"] += 1
        elif "/traces/" in p:
            out["traces"] += 1
        elif "/per_task/" in p:
            out["per_task"] += 1
        elif p.endswith("scores.json"):
            out["scores"] += 1
        elif p.endswith("summary.md"):
            out["summaries"] += 1
        elif not p.startswith("candidates/"):
            out["run_files"] += 1
        else:
            out["other"] += 1
    return out


def reply_commentary(text: str) -> str:
    """The proposer's text outside ``=== FILE:`` blocks (its stated diagnosis and plan)."""
    return (text or "").split("=== FILE:")[0].strip()


class MHTracer:
    """Writes the audit trace of one :class:`MetaHarnessLoop` (no-op when the tracer is disabled)."""

    def __init__(self, loop, tracer: RunTracer, monitor: Optional[ShadowMonitor] = None) -> None:
        self.loop = loop
        self.tr = tracer
        if monitor is not None:
            tracer.monitor = monitor
        self.monitor = monitor
        self.last_best: Optional[str] = None

    @property
    def enabled(self) -> bool:
        return self.tr.enabled

    # ------------------------------------------------------------------ helpers
    def frontier_state(self, fr: Optional[dict] = None) -> dict:
        fr = fr if fr is not None else self.loop.store.frontier()
        return {"best": fr.get("_best"), "pareto": [{k: p[k] for k in ("system", "score", "context_cost")}
                                                    for p in fr.get("_pareto", [])],
                "per_unit_best": {u: v["best_system"] for u, v in fr.items() if not u.startswith("_")},
                "hypervolume": fr.get("_hypervolume")}

    def population(self) -> list[dict]:
        st = self.loop.store
        out = []
        for n in st.names():
            s = st.scores(n) or {}
            m = st.meta(n)
            out.append({"system": n, "status": m.get("status"), "iteration": m.get("iteration"),
                        "base": m.get("base_system"), "score": s.get("score"), "context_cost": s.get("context_cost")})
        return out

    # ------------------------------------------------------------------ events
    def run_start(self, seed_names: Sequence[str], extra: Optional[dict] = None) -> None:
        if not self.enabled:
            return
        lp = self.loop
        ts = lp.domain.tasks
        self.tr.event("run_start", None, seed=",".join(f"{n}={lp.baselines[n].short_id}" for n in lp.baselines),
                      seed_names=list(seed_names), config=lp.cfg.to_json(), domain=getattr(lp.domain, "name", "?"),
                      proposer=type(lp.proposer).__name__,
                      splits={s: len(ids) for s, ids in ts.splits.items()},
                      search_units=list(ts.splits.get(lp.cfg.search_split, [])),
                      shadow_splits=(self.monitor.splits if self.monitor else []),
                      resumed_from_iteration=lp.store.last_iteration(), **(extra or {}))
        if lp.cfg.reeval_incumbent > 0:
            self.tr.event("noise", None, mode="reeval_incumbent", delta=None, z=None,
                          detail=f"optional (not in the paper): every new frontier _best is re-evaluated on "
                                 f"{lp.cfg.trials + lp.cfg.reeval_incumbent} seeds and the pooled score replaces its "
                                 f"single-seed score before the frontier is recomputed; no keep gate otherwise")
        else:
            self.tr.event("noise", None, mode="none", delta=None, z=None,
                          detail="Meta-Harness has no noise band and no keep gate: every valid candidate is evaluated "
                                 f"once on the search split with trials={lp.cfg.trials} and kept in the population; "
                                 "the output is the Pareto frontier (score up, context cost down)")

    def baseline(self, name: str, ev, scores: dict) -> None:
        if not self.enabled:
            return
        self.tr.event("baseline", 0, candidate=name, artifact=self.loop.baselines[name].short_id,
                      summary=ev.summary(), per_task=ev.task_scores(),
                      trials={t: [x.score for x in trs] for t, trs in ev.trials.items()},
                      context_cost=scores["context_cost"], per_unit_cost=scores["per_unit_cost"])

    def after_baselines(self) -> None:
        if not self.enabled:
            return
        fr = self.loop.store.frontier()
        self.tr.event("state", 0, phase="after baselines (H0)", frontier=self.frontier_state(fr),
                      population=self.population())
        self._maybe_monitor(0, fr)

    def round_start(self, t: int, view: dict[str, str], visible: Sequence[str], k: int) -> None:
        if not self.enabled:
            return
        lp = self.loop
        self.tr.event("round_start", t, iteration=t, k_requested=lp.cfg.k, k=k, history_mode=lp.cfg.history_mode,
                      n_evaluated=lp.n_evaluated, n_proposed=lp.n_proposed, eval_budget=lp.cfg.eval_budget,
                      eval_budget_left=lp.budget_left(), frontier=self.frontier_state(),
                      view={"n_files": len(view), "chars": sum(len(v) for v in view.values()),
                            "by_kind": _kinds(view), "visible_systems": sorted(visible)},
                      population=self.population())

    def proposals(self, t: int, batch) -> None:
        if not self.enabled:
            return
        lp = self.loop
        # analysis: what the proposer said it saw (the paper has no separate analysis step)
        if batch.candidates and all("move" in c.meta for c in batch.candidates):
            text = "\n".join(f"- {c.name}: move `{c.meta.get('move')}` on `{c.base_system}` - evidence: "
                             f"{c.meta.get('evidence')}" for c in batch.candidates)
        else:
            text = reply_commentary(batch.transcript)
        self.tr.event("analysis", t, text=text or "(no commentary)", files_read=len(batch.files_read),
                      files_read_by_kind=_kinds(batch.files_read),
                      files_scanned=len(getattr(batch, "files_scanned", []) or []),
                      reports=sorted((getattr(batch, "reports", {}) or {}).keys()),
                      proposer_usage=batch.usage.to_dict(), error=batch.error)
        if not batch.candidates:
            self.tr.proposal(t, "(none)", parent=None, prompt=batch.prompt, reply=batch.transcript,
                             error=batch.error or "no candidates")
            return
        for i, c in enumerate(batch.candidates):
            base = lp.store.artifact(c.base_system) if c.base_system and lp.store.has(c.base_system) else None
            actual = sorted(base.changed_files(c.artifact)) if base is not None else sorted(c.artifact.files)
            self.tr.proposal(t, c.name, parent=c.base_system or None,
                             prompt=batch.prompt if i == 0 else "(same proposer call as the first candidate)",
                             reply=batch.transcript if i == 0 else "",
                             change=c.hypothesis, hypothesis=c.hypothesis, components=c.components,
                             diff=base.diff(c.artifact) if base is not None else "(base system unknown: whole files)",
                             axis=c.axis, parents_read=c.parents_read, files_changed=actual,
                             base_known=base is not None, identical_to_base=(base is not None and not actual),
                             **{k: v for k, v in c.meta.items() if isinstance(v, (str, int, float))})

    def screen(self, t: int, name: str, reason: str) -> None:
        if self.enabled:
            self.tr.event("critic", t, candidate=name, stage="leakage screen (pilot)", accept=not reason,
                          objections=[reason] if reason else [])

    def admissibility(self, t: int, name: str, status: str, reason: str, validated: Optional[bool]) -> None:
        if self.enabled:
            self.tr.gate(t, name, status == "evaluated",
                         "interface validation passed: evaluate and add to the population" if status == "evaluated"
                         else f"{status}: {reason}",
                         math={"rule": "admissible iff (screen passes, when enabled) and interface validation "
                                       "passes; no score threshold", "validate": bool(self.loop.cfg.validate),
                               "validated": validated, "status": status})

    def evaluation(self, t: int, name: str, ev, scores: dict) -> None:
        if self.enabled:
            self.tr.evaluation(t, name, ev, context_cost=scores["context_cost"],
                               per_unit_cost=scores["per_unit_cost"])

    def decision(self, t: int, rows: list[dict], pre_best_name: Optional[str], pre_best: float, post: dict) -> None:
        if not self.enabled:
            return
        pts = {p["system"] for p in post.get("_pareto", [])}
        allpts = {n: (s["score"], self.loop._cost_value(s)) for n in self.loop.store.names()
                  if (s := self.loop.store.scores(n))}
        per = {}
        for r in rows:
            n = r["system"]
            if n in allpts:
                sc, cc = allpts[n]
                dom = sorted(o for o, (s2, c2) in allpts.items()
                             if o != n and s2 >= sc and c2 <= cc and (s2 > sc or c2 < cc))
                per[n] = {"score": sc, "context_cost": cc, "on_frontier": n in pts, "dominated_by": dom[:6],
                          "delta_vs_pre_best_pts": round(100 * (sc - pre_best), 2),
                          "delta_logged_release": r.get("delta")}
            else:
                per[n] = {"status": "not evaluated", "outcome": r.get("outcome")}
        best = post.get("_best") or {}
        after = best.get("system")
        new = [n for n in per if per[n].get("on_frontier")]
        why = (f"Meta-Harness keeps every evaluated candidate in the population; {len(new)} of {len(rows)} "
               f"joined the Pareto frontier. Incumbent = highest-score Pareto point "
               + ("(unchanged)" if after == pre_best_name else f"changed {pre_best_name} -> {after}"))
        self.tr.decision(t, kept=",".join(new) or None, incumbent_before=pre_best_name, incumbent_after=after,
                         why=why, per_candidate=per, pre_best_score=pre_best, post_best_score=best.get("score"))
        self._maybe_monitor(t, post)

    def _maybe_monitor(self, t: int, fr: dict) -> None:
        best = (fr.get("_best") or {}).get("system")
        if best and best != self.last_best:
            self.last_best = best
            self.tr.kept(t, best, self.loop.store.artifact(best), decision_score=fr["_best"]["score"])

    def state(self, t: int, row: dict) -> None:
        if self.enabled:
            self.tr.event("state", t, iteration_row=row, frontier=self.frontier_state())

    def reevaluation(self, t: int, name: str, ev, scores: dict, before: dict) -> None:
        """``Config.reeval_incumbent``: a new ``_best`` re-scored on more seeds (pooled scores replace the
        single-seed ones before the frontier is recomputed)."""
        if self.enabled:
            self.tr.evaluation(t, name, ev, context_cost=scores["context_cost"], per_unit_cost=scores["per_unit_cost"],
                               phase="incumbent re-evaluation (Config.reeval_incumbent)", n_seeds=ev.k,
                               score_before=before.get("score"), k_before=before.get("k"), score_after=scores["score"])

    def note(self, t: Optional[int], what: str, **data) -> None:
        if self.enabled:
            self.tr.event("note", t, what=what, **data)

    def skipped(self, t: int, why: str) -> None:
        if self.enabled:
            self.tr.event("note", t, what="iteration skipped", why=why)

    def finalize(self, rep: dict, status: str, failures: list) -> None:
        if not self.enabled:
            return
        tests = {sp: {s: {"S": r.get("score"), "context_cost": r.get("context_cost")}
                      for s, r in d.get("results", {}).items()} for sp, d in rep.get("splits", {}).items()}
        self.tr.event("note", None, what="finalize (one-time test evaluation)", systems=rep.get("systems"),
                      test=tests, status=status, failures=failures)

    def run_end(self, extra: dict) -> None:
        if self.enabled:
            self.tr.event("run_end", None, frontier=self.frontier_state(), n_evaluated=self.loop.n_evaluated,
                          n_proposed=self.loop.n_proposed, **extra)


def make_tracer(loop, out_dir, *, enabled: bool, monitor: Any, llm_task: Optional[LLM], splits=None, k: int = 1,
                workers: int = 2) -> MHTracer:
    """``monitor``: True = auto (sealed holdout/ood of the domain), False/None = off, or a ShadowMonitor."""
    tr = RunTracer(out_dir if enabled else None, "metaharness")
    mon = None
    if tr.enabled and monitor:
        if isinstance(monitor, ShadowMonitor):
            mon = monitor
        else:
            sp = list(splits) if splits else default_shadow_splits(loop.domain)
            if sp:
                mon = ShadowMonitor(loop.domain, ShadowLLM(llm_task) if llm_task is not None else None, splits=sp,
                                    k=k, workers=workers)
    return MHTracer(loop, tr, mon)


def shadow_usage(mh: MHTracer) -> dict:
    if mh.monitor is None or mh.monitor.ev.llm is None:
        return {}
    return mh.monitor.ev.llm.meter.snapshot()


__all__ = ["MHTracer", "ShadowLLM", "default_shadow_splits", "make_tracer", "shadow_usage", "reply_commentary"]
