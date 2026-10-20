"""The GEPA loop (paper Algorithm 1; ``gepa/core/engine.py:GEPAEngine.run``)."""
from __future__ import annotations

import json
import random
import warnings
from pathlib import Path
from typing import Callable, Optional, Sequence

from ..core.artifact import Artifact
from ..core.ledger import ArtifactStore, Ledger, Node
from ..core.llm import LLM, Usage
from .adapter import DomainAdapter, EvalBatch, resolve_splits
from .config import Config
from .frontier import pareto_frequencies
from .merge import MergeProposer
from .reflection import ReflectionProposer, reflection_seed, rollout_seed
from .state import SearchState
from .stoppers import (BudgetStopper, Composite, ConsecutiveInfraFailures, FileStopper, MaxCandidateProposals,
                       MaxIterations, MaxMetricCalls, MaxReflectionCost, NoImprovement, ScoreThreshold, Timeout)
from .strategies import EpochShuffledBatchSampler, make_acceptance, make_component_selector, make_selector
from .tracing import SHADOW_PREFIX, make_tracer


class BudgetExhausted(RuntimeError):
    """Raised by the hard budget cap in the middle of an iteration."""


def merge_usage(*snapshots: dict) -> dict:
    """Sum per-role usage snapshots (``UsageMeter.snapshot`` dicts)."""
    out: dict[str, dict] = {}
    for snap in snapshots:
        for role, u in (snap or {}).items():
            if role == "_total":
                continue
            acc = out.setdefault(role, {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0,
                                        "latency_s": 0.0})
            for k in acc:
                acc[k] += u.get(k, 0)
    tot = Usage()
    for u in out.values():
        tot = tot + Usage(u["calls"], u["input_tokens"], u["output_tokens"], u["cost_usd"], u["latency_s"])
    for u in out.values():
        u["total_tokens"] = u["input_tokens"] + u["output_tokens"]
    out["_total"] = tot.to_dict()
    return out


def _distinct(*llms) -> list:
    return list({id(x): x for x in llms if x is not None}.values())


def llm_usage(*llms) -> dict:
    """Merged usage of the distinct LLMs given (None skipped)."""
    return merge_usage(*[l.meter.snapshot() for l in _distinct(*llms)])


class GEPAEngine:
    """Runs GEPA on a :class:`DomainAdapter` from a seed :class:`Artifact`; stoppers are checked between iterations."""

    def __init__(self, adapter: DomainAdapter, seed_artifact: Artifact, components: Sequence[str], *,
                 llm_propose: Optional[LLM], config: Config, out_dir: Optional[str | Path] = None,
                 proposer=None, llm_task: Optional[LLM] = None, budget=None, stoppers: Sequence[Callable] = (),
                 callbacks: Sequence[Callable] = (), verbose: bool = False, method: str = "gepa",
                 critic=None, selector=None, monitor=None) -> None:
        config.validate()
        self.cfg = cfg = config
        self.adapter = adapter
        self.seed_artifact = seed_artifact
        self.components = list(components)
        missing = [c for c in self.components if c not in seed_artifact]
        if missing:
            raise KeyError(f"components {missing} are not files of the seed artifact")
        self.llm_propose, self.llm_task = llm_propose, llm_task
        self.method = method
        self.verbose = verbose
        self.callbacks = list(callbacks)
        self.critic = critic            # optional pre-evaluation screen, e.g. rsi.core.LeakageCritic
        self.rng = random.Random(cfg.seed)
        if selector is None:
            self.selector = make_selector(cfg.candidate_selection, self.rng, epsilon=cfg.epsilon, top_k=cfg.top_k,
                                          beam_n=cfg.beam_n)
        else:                           # an instance, or a factory taking the shared RNG
            self.selector = selector if hasattr(selector, "select") else selector(self.rng)
        self.comp_selector = make_component_selector(cfg.module_selector)
        self.sampler = EpochShuffledBatchSampler(cfg.minibatch_size, self.rng)
        self.acceptance = make_acceptance(cfg.acceptance, cfg.noise_margin)
        self.merge = MergeProposer(self.rng, cfg.max_merge_invocations, cfg.merge_val_overlap_floor,
                                   cfg.merge_subsample_size, cfg.merge_cap_mode, cfg.merge_zero_weight) \
            if cfg.use_merge else None
        if proposer is None:
            if llm_propose is None:
                raise ValueError("need llm_propose (the reflection LM) or a proposer")
            proposer = ReflectionProposer(llm_propose, template=cfg.reflection_template, system=cfg.reflection_system)
        self.proposer = proposer
        tr, vs = resolve_splits(adapter.domain, cfg.train_split, cfg.val_split)
        self.train_ids = adapter.ids(tr)
        self.val_ids = adapter.ids(vs) if vs else list(self.train_ids)
        self.split_names = {"train": tr, "val": vs or tr}
        if not self.train_ids:
            raise ValueError(f"train split {tr!r} is empty")
        self.perfect_score = cfg.perfect_score if cfg.perfect_score is not None else \
            float(getattr(adapter.domain, "score_range", (0.0, 1.0))[1])
        self.out_dir = Path(out_dir) if out_dir else None
        self.store = ArtifactStore(self.out_dir / "artifacts") if self.out_dir else None
        self.ledger = Ledger(self.out_dir / "ledger.jsonl" if self.out_dir else None)
        self._val_cache: dict[tuple[str, str], float] = {}
        self._usage_prior: dict = {}
        self.stopper = self._make_stopper(budget, stoppers)
        self.monitor_wall_s = 0.0
        self.state = SearchState(self.components, cfg.frontier_type)
        self.stop_reason = ""
        self.resumed_at: Optional[int] = None
        self._last_charge = 0
        self._last_eb: Optional[EvalBatch] = None
        # monitor: None/True = auto (sealed holdout/ood, else test), False = off, or a ShadowMonitor
        self.tr = make_tracer(self, self.out_dir, enabled=cfg.trace, max_text=cfg.trace_max_text,
                              monitor=monitor if monitor is not None else cfg.shadow_monitor,
                              domain=adapter.domain, llm_task=llm_task, splits=cfg.shadow_splits, k=cfg.shadow_k,
                              workers=cfg.shadow_workers)

    def _make_stopper(self, budget, extra: Sequence[Callable]) -> Composite:
        cfg = self.cfg
        limits = [(cfg.max_metric_calls, MaxMetricCalls), (cfg.max_iterations, MaxIterations),
                  (cfg.stop_at_score, ScoreThreshold), (cfg.no_improvement_patience, NoImprovement),
                  (cfg.max_candidate_proposals, MaxCandidateProposals), (cfg.max_reflection_cost, MaxReflectionCost),
                  (cfg.timeout_s, Timeout)]
        st = [cls(v) for v, cls in limits if v is not None]
        if self.out_dir:
            st.append(FileStopper(self.out_dir / "gepa.stop"))
        if budget is not None:
            st.append(BudgetStopper(budget))
        st.extend(extra)
        if not st:
            raise ValueError("no stop condition: set max_metric_calls, a budget or a stopper")
        if cfg.max_consecutive_infra_failures is not None:      # a safety net, not a stop condition of its own
            st.append(ConsecutiveInfraFailures(cfg.max_consecutive_infra_failures))
        return Composite(st)

    def _llms(self) -> list[LLM]:
        return _distinct(self.llm_propose, self.llm_task, getattr(self.critic, "llm", None))

    def _role_snapshots(self, shadow: bool) -> list[dict]:
        return [{r: u for r, u in l.meter.snapshot().items() if r.startswith(SHADOW_PREFIX) == shadow}
                for l in self._llms()]

    def usage_snapshot(self) -> dict:
        """The loop's own spend by role (shadow-monitor roles excluded)."""
        prior = {r: u for r, u in self._usage_prior.items() if not r.startswith(SHADOW_PREFIX)}
        return merge_usage(prior, *self._role_snapshots(False))

    def shadow_usage_snapshot(self) -> dict:
        return merge_usage(*self._role_snapshots(True))

    def usd(self) -> float:
        return float(self.usage_snapshot()["_total"]["cost_usd"])

    def _merge_window_open(self) -> bool:
        f = self.cfg.merge_start_frac
        return f <= 0 or self.state.counter.total >= f * float(self.cfg.max_metric_calls)

    def _emit(self, event: str, **payload) -> None:
        for cb in self.callbacks:
            cb(event, payload)

    def credit_wall_time(self, seconds: float) -> None:
        """Give shadow-monitor wall time back to the wall-clock stoppers (kept off ``state.json``)."""
        s = max(0.0, float(seconds or 0.0))
        if s:
            self.monitor_wall_s += s
            self.stopper.credit(s)

    def _charge(self, phase: str, n: int) -> None:
        cap = self.cfg.max_metric_calls
        if self.cfg.budget_mode == "hard" and cap is not None and self.state.counter.total + n > cap:
            raise BudgetExhausted(f"{phase}: {self.state.counter.total}+{n} > {cap}")
        self.state.counter.add(phase, n)
        self._last_charge = n

    def _seeds(self, role: str, n: int) -> list[int]:
        return [rollout_seed(self.cfg.seed, "mb", self.state.i, role, j) for j in range(n)]

    def evaluate_minibatch(self, cand: Artifact, ids: list[str], role: str) -> EvalBatch:
        self._charge("minibatch_parent" if role == "parent" else "minibatch_child", len(ids))
        eb = self.adapter.evaluate(ids, cand, True, self._seeds(role, len(ids)))
        self._count("n_exec_errors", eb.n_errors)
        self._last_eb = eb
        return eb

    def evaluate_merge_subsample(self, cand: Artifact, ids: list[str]) -> list[float]:
        """Merged child's subsample scores; with ``cache_evaluation`` this is its D_pareto draw, reused later."""
        if self.cfg.cache_evaluation:
            scores, _ = self.evaluate_val(cand, ids, "merge_subsample")
            return [scores[t] for t in ids]
        self._charge("merge_subsample", len(ids))
        eb = self.adapter.evaluate(ids, cand, False, self._seeds("merge", len(ids)))
        self._count("n_infra", eb.n_infra)
        self._count("n_exec_errors", eb.n_errors)
        self._last_eb = eb
        return list(eb.scores)

    def evaluate_val(self, cand: Artifact, ids: list[str], phase: str) -> tuple[dict, Optional[dict]]:
        n = len(ids)
        if self.cfg.cache_evaluation:
            n = sum(1 for t in ids if (cand.id, t) not in self._val_cache)
        self._charge(phase, n)
        eb = self.adapter.evaluate(ids, cand, False, [self.cfg.val_seed] * len(ids))
        # infra failures left after retries score 0; both kinds of failure are counted so they stay visible
        self._count("n_infra", eb.n_infra)
        self._count("n_exec_errors", eb.n_errors)
        self._last_val_errors = (eb.n_errors, eb.first_error, len(ids))
        self._last_eb = eb
        scores = dict(zip(ids, eb.scores))
        for t, s in scores.items():
            self._val_cache[(cand.id, t)] = s
        objs = dict(zip(ids, eb.objective_scores)) if eb.objective_scores else None
        return scores, objs

    def full_eval_and_add(self, cand: Artifact, parents: list[Optional[int]], kind: str, phase: str,
                          meta: Optional[dict] = None) -> int:
        st = self.state
        evals_before = st.counter.total
        scores, objs = self.evaluate_val(cand, self.val_ids, phase)
        if self.tr.enabled:
            baseline = kind == "baseline"
            self.tr.evaluation(None if baseline else st.i, f"c{len(st.candidates)}", self._last_eb, self.val_ids,
                               split=self.split_names["val"], phase=phase, charged=self._last_charge,
                               kind="baseline" if baseline else "eval", with_text=True,
                               role="full D_pareto evaluation")
        idx, delta = st.add_candidate(cand, parents, scores, objs, kind, evals_before)
        self._last_delta = delta
        if self.store is not None:
            self.store.put(cand)
        agg = st.agg_scores()[idx]
        p0 = parents[0] if parents and parents[0] is not None else None
        parent_art = st.candidates[p0] if p0 is not None else None
        diff = parent_art.diff(cand)[: self.cfg.diff_chars] if parent_art is not None else ""
        changed = parent_art.changed_files(cand) if parent_art is not None else []
        self.ledger.add(Node(
            id=f"c{idx}", parent=f"c{p0}" if p0 is not None else None, round=max(st.i, 0),
            kind=kind, status="keep" if kind == "baseline" else "accepted", score=agg,
            change=(f"{kind}: " + ", ".join(changed)) if changed else kind, artifact_id=cand.id, diff=diff,
            metrics={"val_mean": agg, "rollouts_at_discovery": evals_before, "frontier_won": len(delta["won"]),
                     "frontier_tied": len(delta["tied"])},
            meta={"parents": parents, "idx": idx, "iteration": st.i, **(meta or {})}))
        return idx

    def _log_rejected(self, cand: Artifact, **node) -> None:
        if self.store is not None:
            self.store.put(cand)
        self.ledger.add(Node(artifact_id=cand.id, **node))

    def _save(self) -> None:
        if self.out_dir is None:
            return
        st = self.state
        version, internal, gauss = self.rng.getstate()
        st.extra["rng"] = [version, list(internal), gauss]
        st.extra["sampler"] = self.sampler.get_state()
        st.extra["selector"] = self.selector.get_state() if hasattr(self.selector, "get_state") else {}
        st.extra["merge"] = self.merge.get_state() if self.merge else None
        st.extra["usage"] = self.usage_snapshot()
        st.extra["trace_len"] = len(st.trace)
        st.extra["val_cache"] = [[a, t, s] for (a, t), s in self._val_cache.items()] if self.cfg.cache_evaluation \
            else []
        trace, st.trace = st.trace, []          # the trace lives in run_log.jsonl
        try:
            st.save(self.out_dir, self.store)
        finally:
            st.trace = trace
        (self.out_dir / "config.json").write_text(json.dumps(self.cfg.to_json(), indent=1, default=str))

    def _append_trace(self, entry: dict) -> None:
        if self.out_dir is None:
            return
        with (self.out_dir / "run_log.jsonl").open("a") as f:
            f.write(json.dumps(entry, default=str) + "\n")

    def _try_resume(self) -> bool:
        if self.out_dir is None:
            return False
        st = SearchState.load(self.out_dir, self.store)
        if st is None:
            return False
        n = int(st.extra.get("trace_len", 0))
        log = self.out_dir / "run_log.jsonl"
        lines = (log.read_text().splitlines() if log.exists() else [])[:n]
        st.trace = [json.loads(l) for l in lines]
        log.write_text("".join(l + "\n" for l in lines))   # drop the killed iteration's partial entries
        self.state = st
        # without snapshots, resume like the reference: fresh RNG and sampler
        if st.extra.get("rng"):
            r = st.extra["rng"]
            self.rng.setstate((r[0], tuple(r[1]), r[2]))
        if st.extra.get("sampler"):
            self.sampler.set_state(st.extra["sampler"])
        if hasattr(self.selector, "set_state"):
            self.selector.set_state(st.extra.get("selector") or {})
        if self.merge and st.extra.get("merge"):
            self.merge.set_state(st.extra["merge"])
        self._usage_prior = st.extra.get("usage") or {}
        self._prune_ledger(st.i)
        self._val_cache = {(a, t): s for a, t, s in st.extra.get("val_cache", [])}
        self.resumed_at = st.i
        return True

    def _prune_ledger(self, last_iter: int) -> None:
        """Drop ledger nodes of the killed, unsaved iteration."""
        path = self.out_dir / "ledger.jsonl"
        keep = [n for n in self.ledger.nodes() if int(n.meta.get("iteration", n.round)) <= last_iter]
        if len(keep) == len(self.ledger):
            return
        tmp = path.with_name("ledger.jsonl.tmp")
        tmp.write_text("".join(json.dumps(n.to_json(), default=str) + "\n" for n in keep))
        tmp.replace(path)
        self.ledger = Ledger(path)

    def initialize(self) -> None:
        tr = self.tr if self.tr.enabled else None
        if self._try_resume():
            if tr:
                tr.run_start(self.state.i)
            if self.verbose:
                print(f"[gepa] resumed at iteration {self.state.i + 1} with {len(self.state.candidates)} candidates")
            return
        if tr:
            tr.run_start(None)
            tr.noise()
        if self.out_dir is not None:
            self.out_dir.mkdir(parents=True, exist_ok=True)
            for f in ("run_log.jsonl", "ledger.jsonl"):
                (self.out_dir / f).unlink(missing_ok=True)
            self.ledger = Ledger(self.out_dir / "ledger.jsonl")
        self.full_eval_and_add(self.seed_artifact, [None], "baseline", "seed_val")
        if tr:
            tr.kept_if_new_incumbent(None, None)        # shadow score of the seed
        n_err, first, n = getattr(self, "_last_val_errors", (0, None, 0))
        self.state.extra["seed_val_error_rate"] = n_err / n if n else 0.0
        if n and n_err == n:        # warn, not raise: reflection can still repair prompts from the error traces
            warnings.warn(f"GEPA: the seed artifact raised an execution error on all {n} D_pareto examples "
                          f"(first: {first!r}). Check llm_task and the domain setup.", RuntimeWarning, stacklevel=3)
        if self.verbose:
            print(f"[gepa] seed val={self.state.agg_scores()[0]:.4f} |V|={len(self.val_ids)} "
                  f"|T|={len(self.train_ids)} components={self.components}")

    def run(self) -> SearchState:
        self.initialize()
        while not (reason := self.stopper(self)):
            if (self.state.i + 1) % max(self.cfg.save_every, 1) == 0:
                self._save()
            try:
                self._iteration()
            except BudgetExhausted as e:
                self.state.trace[-1]["budget_exhausted"] = str(e)
                if self.tr.enabled:
                    self.tr.tr.event("note", self.state.i, what="hard budget cap",
                                     text=f"BudgetExhausted mid-iteration ({e}); the partial iteration adds nothing")
                self._finish_entry(self.state.trace[-1])
                reason = "max_metric_calls(hard)"
                break
        self.stop_reason = reason
        self._save()
        if self.tr.enabled:
            self.tr.run_end(self.stop_reason)
        failed = int(self.state.extra.get("n_reflection_failed", 0))
        if self.state.n_reflection_calls and failed == self.state.n_reflection_calls:
            warnings.warn(f"GEPA: all {failed} reflection-LM calls failed (see run_log 'rejected_outputs'); "
                          "the result is the seed or earlier candidates only.", RuntimeWarning, stacklevel=3)
        return self.state

    def _count(self, key: str, n: int) -> None:
        if n:
            self.state.extra[key] = self.state.extra.get(key, 0) + int(n)

    def _finish_entry(self, entry: dict) -> None:
        st = self.state
        b = st.best_idx()
        entry.update(rollouts=st.counter.total, n_candidates=len(st.candidates), best_idx=b,
                     best_val=st.agg_scores()[b], frontier_size=len(st.frontier.members()))
        self._append_trace(entry)
        if self.tr.enabled:
            self.tr.state(entry["i"])
        self._emit("iteration_end", engine=self, entry=entry)
        if self.verbose:
            ev = entry.get("event", "-")
            print(f"[gepa] it={entry['i']:4d} {ev:18s} rollouts={entry['rollouts']:6d} "
                  f"cands={entry['n_candidates']:3d} best={entry['best_val']:.4f}")

    def _iteration(self) -> None:
        st = self.state
        st.i += 1
        i = st.i
        entry: dict = {"i": i, "iteration_id": f"it{i:05d}"}
        st.trace.append(entry)
        self._emit("iteration_start", engine=self, i=i)
        tr = self.tr if self.tr.enabled else None          # write-only audit trace
        best_before = st.best_idx()
        if tr:
            tr.round_start(i)
        if self.merge is not None:
            if self.merge.should_attempt() and self._merge(entry, tr, best_before):
                return
            self.merge.last_iter_found_new_program = False
        self._reflect(entry, tr, best_before)

    def _merge(self, entry: dict, tr, best_before: int) -> bool:
        """Merge step (Algorithms 3-4); False when no valid triplet exists, so the iteration falls through."""
        st, i = self.state, self.state.i
        entry["invoked_merge"] = True
        prop = self.merge.propose(st, self.evaluate_merge_subsample)
        self.merge.last_iter_found_new_program = False
        if prop is None:
            if tr:
                tr.tr.event("note", i, what="merge attempt",
                            text="merge was due but no valid (i, j, ancestor) triplet was found; "
                                 "falling through to reflective mutation (merges_due is not consumed)")
            return False
        entry.update(merged=True, merged_entities=[*prop.parents, prop.ancestor], subsample_ids=prop.subsample_ids,
                     id1_subsample_score=prop.sub_before[0], id2_subsample_score=prop.sub_before[1],
                     new_program_subsample_scores=prop.sub_after)
        label = f"m{i}"
        if tr:
            tr.merge_proposal(i, label, prop, st)
            if self._last_eb is not None:
                tr.evaluation(i, label, self._last_eb, prop.subsample_ids, split=self.split_names["val"],
                              phase="merge_subsample", charged=self._last_charge,
                              role="merge subsample (D_pareto ids)")
        ok = sum(prop.sub_after) >= max(prop.sub_before)
        if tr:
            tr.gate_merge(i, label, prop, ok)
        if ok:
            idx = self.full_eval_and_add(prop.candidate, list(prop.parents), "merge", "val_merge",
                                         {"ancestor": prop.ancestor, "sources": list(prop.sources)})
            self.merge.on_accepted()
            entry.update(event="merge_accepted", new_program_idx=idx)
            if tr:
                tr.decision(i, event="merge_accepted", label=label, new_idx=idx, best_before=best_before,
                            why=f"merge accepted on its subsample; added to the pool as c{idx} after a "
                                f"full D_pareto evaluation", delta=self._last_delta)
                tr.kept_if_new_incumbent(i, best_before)
            self._emit("merge_accepted", engine=self, idx=idx, proposal=prop)
        else:
            self._log_rejected(prop.candidate, id=f"m{i}", parent=f"c{prop.parents[0]}", round=i, kind="merge",
                               status="rejected", change="merge rejected on subsample",
                               metrics={"sub_before": prop.sub_before, "sub_after": sum(prop.sub_after)},
                               meta={"parents": list(prop.parents), "ancestor": prop.ancestor})
            entry["event"] = "merge_rejected"
            if tr:
                tr.decision(i, event="merge_rejected", label=label, new_idx=None, best_before=best_before,
                            why="merge rejected on its subsample (logged as ledger node m%d)" % i)
            self._emit("merge_rejected", engine=self, proposal=prop)
        self._finish_entry(entry)
        return True

    def _reflect(self, entry: dict, tr, best_before: int) -> None:
        """Reflective mutation of one parent on one minibatch."""
        st, cfg, i = self.state, self.cfg, self.state.i
        weights = None
        if tr and getattr(self.selector, "name", "") == "pareto":
            weights = pareto_frequencies(st.frontier.mapping(), st.agg_scores())   # what select() samples from
        k = self.selector.select(st)
        parent = st.candidates[k]
        ids = self.sampler.next_ids(self.train_ids, i)
        entry.update(selected_program_candidate=k, subsample_ids=list(ids))
        if tr:
            tr.selection(i, k, ids, weights if weights is not None else {k: 1})
        before = self.evaluate_minibatch(parent, ids, "parent")
        entry["subsample_scores"] = before.scores
        label = f"x{i}"
        if tr:
            tr.evaluation(i, f"c{k}", before, ids, split=self.split_names["train"], phase="minibatch_parent",
                          charged=self._last_charge, role="parent on the minibatch (with traces)")

        def skip(event: str, why: str) -> None:
            entry["event"] = event
            if tr:
                tr.decision(i, event=event, label=None, new_idx=None, best_before=best_before, why=why)
            self._finish_entry(entry)

        if not before.trajectories:
            return skip("skip_no_trajectories", "the parent evaluation returned no trajectories: nothing to reflect on")
        if before.n_infra:          # a backend outage must not decide a minibatch comparison
            self._count("n_infra", before.n_infra)
            return skip("skip_infra_error", f"{before.n_infra} parent rollouts failed for infrastructure reasons")
        if cfg.skip_perfect_score and all(s >= self.perfect_score for s in before.scores):
            return skip("skip_perfect", f"every parent minibatch score >= perfect_score={self.perfect_score:g}: "
                                        "no failure to learn from (reference skip rule)")
        rr_before = st.rr[k]
        comps = self.comp_selector(st, k)
        entry["components"] = comps
        refl = self.adapter.make_reflective_dataset(parent, before, comps)
        if tr:
            tr.analysis(i, k, comps, refl, rr_before)
        res = self.proposer.propose(parent, refl, comps, seed_fn=lambda c: reflection_seed(cfg.seed, i, c))
        st.n_reflection_calls += res.calls
        if res.rejected:
            entry["rejected_outputs"] = {c: r[:200] for c, r in res.rejected.items()}
            n_failed = sum(1 for r in res.rejected.values() if r.startswith("llm error"))
            self._count("n_reflection_failed", n_failed)
            self._count("n_reflection_unparsed", len(res.rejected) - n_failed)
        if not res.new_texts:
            if tr:
                tr.reflective_proposal(i, label, k, parent, None, res, comps)
            return skip("no_proposal", "the reflection LM returned no usable text for " + ", ".join(comps))
        child = parent.with_files(res.new_texts)
        if tr:
            tr.reflective_proposal(i, label, k, parent, child, res, comps)
        if self.critic is not None:          # screen the diff before spending rollouts
            verdict = self.critic.screen(parent.diff(child), "reflective rewrite of " + ", ".join(comps))
            if tr:
                tr.critic(i, label, verdict)
            if not verdict.accept:
                self._log_rejected(child, id=f"x{i}", parent=f"c{k}", round=i, kind="reflective",
                                   status="critic_rejected", change="; ".join(verdict.objections)[:300],
                                   diff=parent.diff(child)[: cfg.diff_chars],
                                   meta={"parents": [k], "components": comps, "stage": verdict.stage})
                entry.update(event="critic_rejected", objections=verdict.objections[:5])
                return skip("critic_rejected", "the critic rejected the rewrite before any child rollout")
        st.n_proposals += 1
        after = self.evaluate_minibatch(child, ids, "child")
        entry["new_subsample_scores"] = after.scores
        if tr:
            tr.evaluation(i, label, after, ids, split=self.split_names["train"], phase="minibatch_child",
                          charged=self._last_charge, role="child on the same minibatch (fresh rollouts)")
        if after.n_infra:
            self._count("n_infra", after.n_infra)
            return skip("skip_infra_error", f"{after.n_infra} child rollouts failed for infrastructure reasons")
        accept = self.acceptance.accept(before.scores, after.scores)
        if tr:
            tr.gate_minibatch(i, label, before.scores, after.scores, accept, ids)
        if accept:
            idx = self.full_eval_and_add(child, [k], "reflective", "val_reflective",
                                         {"components": comps, "sub_before": sum(before.scores),
                                          "sub_after": sum(after.scores), "subsample_ids": list(ids)})
            entry.update(event="accepted", new_program_idx=idx)
            if self.merge is not None and self._merge_window_open():
                self.merge.schedule_if_needed()
            if tr:
                tr.decision(i, event="accepted", label=label, new_idx=idx, best_before=best_before,
                            why=f"child passed the minibatch gate; scored on all of D_pareto and added to the pool as "
                                f"c{idx} (GEPA keeps every accepted child, whatever its D_pareto score)",
                            delta=self._last_delta)
                tr.kept_if_new_incumbent(i, best_before)
            self._emit("candidate_accepted", engine=self, idx=idx, parent=k, before=before, after=after, child=child)
        else:
            self._log_rejected(child, id=f"x{i}", parent=f"c{k}", round=i, kind="reflective", status="rejected",
                               change="rejected: " + ", ".join(comps), diff=parent.diff(child)[: cfg.diff_chars],
                               metrics={"sub_before": sum(before.scores), "sub_after": sum(after.scores)},
                               meta={"parents": [k], "components": comps, "subsample_ids": list(ids)})
            entry["event"] = "rejected"
            if tr:
                tr.decision(i, event="rejected", label=label, new_idx=None, best_before=best_before,
                            why="child failed the minibatch gate: discarded (never scored on D_pareto; ledger node "
                                f"x{i})")
            self._emit("candidate_rejected", engine=self, parent=k, before=before, after=after, child=child)
        self._finish_entry(entry)
