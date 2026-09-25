"""The GEPA loop (paper Algorithm 1; ``gepa/core/engine.py:GEPAEngine.run``).

Per iteration (stop conditions checked only at the top, state saved first):

1. **merge** (GEPA+Merge only) if one is due and the previous iteration accepted a
   reflective child: propose a module-wise crossover, score it on a 5-id validation
   subsample, accept iff its sum >= the better parent's, then full-validate and add it.
   Accepted or rejected, the iteration ends there; if no valid triplet exists the
   iteration falls through to reflection;
2. **reflective mutation**: select a parent (Pareto by default), draw the next
   epoch-shuffled minibatch (b = 3) from D_train, run the parent with traces, skip if
   every score is perfect, pick the component(s) (round-robin), build the reflective
   dataset, ask the reflection LM for new text(s), run the child on the same
   minibatch, and accept iff the minibatch sum strictly improves; accepted children
   are evaluated on all of D_pareto and join the pool (rejected ones are logged only).

Returns argmax mean D_pareto score. Every rollout is counted by phase; every
candidate and every rejected proposal is an :class:`rsi.core.Ledger` node.
"""
from __future__ import annotations

import hashlib
import json
import random
import time
from pathlib import Path
from typing import Callable, Optional, Sequence

from ..core.artifact import Artifact
from ..core.ledger import ArtifactStore, Ledger, Node
from ..core.llm import LLM, Usage
from .adapter import DomainAdapter, EvalBatch
from .config import Config
from .merge import MergeProposer
from .reflection import ReflectionProposer, reflection_seed
from .state import SearchState
from .stoppers import (BudgetStopper, Composite, FileStopper, MaxCandidateProposals, MaxIterations, MaxMetricCalls,
                       MaxReflectionCost, NoImprovement, ScoreThreshold, Timeout)
from .strategies import make_acceptance, make_component_selector, make_selector, EpochShuffledBatchSampler


class BudgetExhausted(RuntimeError):
    """Raised by the hard budget cap in the middle of an iteration."""


def _rng_state_to_json(st) -> list:
    return [st[0], list(st[1]), st[2]]


def _rng_state_from_json(d) -> tuple:
    return (d[0], tuple(d[1]), d[2])


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


class GEPAEngine:
    """Runs GEPA on a :class:`DomainAdapter` from a seed :class:`Artifact`."""

    def __init__(self, adapter: DomainAdapter, seed_artifact: Artifact, components: Sequence[str], *,
                 llm_propose: Optional[LLM], config: Config, out_dir: Optional[str | Path] = None,
                 proposer=None, llm_task: Optional[LLM] = None, budget=None, stoppers: Sequence[Callable] = (),
                 callbacks: Sequence[Callable] = (), verbose: bool = False, method: str = "gepa",
                 critic=None) -> None:
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
        self.critic = critic            # optional pre-evaluation screen (e.g. rsi.core.LeakageCritic); off by default
        # one shared RNG, as in gepa.optimize
        self.rng = random.Random(cfg.seed)
        self.selector = make_selector(cfg.candidate_selection, self.rng, epsilon=cfg.epsilon, top_k=cfg.top_k,
                                      beam_n=cfg.beam_n)
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
        # splits: D_feedback and D_pareto (None/missing val -> D_train, multi-task mode)
        splits = adapter.domain.tasks.splits
        tr = cfg.train_split if cfg.train_split in splits else ("train" if "train" in splits else cfg.train_split)
        self.train_ids = adapter.ids(tr)
        vs = cfg.val_split if (cfg.val_split and cfg.val_split in splits) else None
        self.val_ids = adapter.ids(vs) if vs else list(self.train_ids)
        self.split_names = {"train": tr, "val": vs or tr}
        if not self.train_ids:
            raise ValueError(f"train split {tr!r} is empty")
        # persistence
        self.out_dir = Path(out_dir) if out_dir else None
        self.store = ArtifactStore(self.out_dir / "artifacts") if self.out_dir else None
        self.ledger = Ledger(self.out_dir / "ledger.jsonl" if self.out_dir else None)
        self._val_cache: dict[tuple[str, str], float] = {}
        self._usage_prior: dict = {}
        # stoppers
        st = []
        if cfg.max_metric_calls is not None:
            st.append(MaxMetricCalls(cfg.max_metric_calls))
        if cfg.max_iterations is not None:
            st.append(MaxIterations(cfg.max_iterations))
        if cfg.stop_at_score is not None:
            st.append(ScoreThreshold(cfg.stop_at_score))
        if cfg.no_improvement_patience is not None:
            st.append(NoImprovement(cfg.no_improvement_patience))
        if cfg.max_candidate_proposals is not None:
            st.append(MaxCandidateProposals(cfg.max_candidate_proposals))
        if cfg.max_reflection_cost is not None:
            st.append(MaxReflectionCost(cfg.max_reflection_cost))
        if cfg.timeout_s is not None:
            st.append(Timeout(cfg.timeout_s))
        if self.out_dir:
            st.append(FileStopper(self.out_dir / "gepa.stop"))
        if budget is not None:
            st.append(BudgetStopper(budget))
        st.extend(stoppers)
        if not st:
            raise ValueError("no stop condition: set max_metric_calls, a budget or a stopper")
        self.stopper = Composite(st)
        self.state = SearchState(self.components, cfg.frontier_type)
        self.stop_reason = ""
        self.resumed_at: Optional[int] = None

    # ----------------------------------------------------------------- usage --
    def _llms(self) -> list[LLM]:
        out, seen = [], set()
        for l in (self.llm_propose, self.llm_task):
            if l is not None and id(l) not in seen:
                seen.add(id(l))
                out.append(l)
        return out

    def usage_snapshot(self) -> dict:
        return merge_usage(self._usage_prior, *[l.meter.snapshot() for l in self._llms()])

    def usd(self) -> float:
        return float(self.usage_snapshot()["_total"]["cost_usd"])

    def _emit(self, event: str, **payload) -> None:
        for cb in self.callbacks:
            cb(event, payload)

    # ------------------------------------------------------------ evaluation --
    def _mb_seed(self, i: int, role: str, j: int) -> int:
        h = hashlib.sha256(f"{self.cfg.seed}|mb|{i}|{role}|{j}".encode()).hexdigest()
        return 1 + int(h[:8], 16) % (2 ** 31 - 2)

    def _charge(self, phase: str, n: int) -> None:
        cap = self.cfg.max_metric_calls
        if self.cfg.budget_mode == "hard" and cap is not None and self.state.counter.total + n > cap:
            raise BudgetExhausted(f"{phase}: {self.state.counter.total}+{n} > {cap}")
        self.state.counter.add(phase, n)

    def evaluate_minibatch(self, cand: Artifact, ids: list[str], role: str) -> EvalBatch:
        phase = "minibatch_parent" if role == "parent" else "minibatch_child"
        self._charge(phase, len(ids))
        seeds = [self._mb_seed(self.state.i, role, j) for j in range(len(ids))]
        return self.adapter.evaluate(ids, cand, True, seeds)

    def evaluate_val(self, cand: Artifact, ids: list[str], phase: str) -> tuple[dict, Optional[dict]]:
        n = len(ids)
        if self.cfg.cache_evaluation:
            n = sum(1 for t in ids if (cand.id, t) not in self._val_cache)
        self._charge(phase, n)
        eb = self.adapter.evaluate(ids, cand, False, [self.cfg.val_seed] * len(ids))
        scores = dict(zip(ids, eb.scores))
        for t, s in scores.items():
            self._val_cache[(cand.id, t)] = s
        objs = dict(zip(ids, eb.objective_scores)) if eb.objective_scores else None
        return scores, objs

    # --------------------------------------------------------------- pool ops --
    def _node_id(self, idx: int) -> str:
        return f"c{idx}"

    def full_eval_and_add(self, cand: Artifact, parents: list[Optional[int]], kind: str, phase: str,
                          meta: Optional[dict] = None) -> int:
        evals_before = self.state.counter.total
        scores, objs = self.evaluate_val(cand, self.val_ids, phase)
        idx, delta = self.state.add_candidate(cand, parents, scores, objs, kind, evals_before)
        if self.store is not None:
            self.store.put(cand)
        agg = self.state.agg_scores()[idx]
        p0 = parents[0] if parents and parents[0] is not None else None
        parent_art = self.state.candidates[p0] if p0 is not None else None
        diff = parent_art.diff(cand)[: self.cfg.diff_chars] if parent_art is not None else ""
        changed = parent_art.changed_files(cand) if parent_art is not None else []
        self.ledger.add(Node(
            id=self._node_id(idx), parent=self._node_id(p0) if p0 is not None else None, round=max(self.state.i, 0),
            kind=kind, status="keep" if kind == "baseline" else "accepted", score=agg,
            change=(f"{kind}: " + ", ".join(changed)) if changed else kind, artifact_id=cand.id, diff=diff,
            metrics={"val_mean": agg, "rollouts_at_discovery": evals_before, "frontier_won": len(delta["won"]),
                     "frontier_tied": len(delta["tied"])},
            meta={"parents": parents, "idx": idx, "iteration": self.state.i, **(meta or {})}))
        return idx

    # ------------------------------------------------------------ persistence --
    def _save(self) -> None:
        if self.out_dir is None:
            return
        st = self.state
        st.extra["rng"] = _rng_state_to_json(self.rng.getstate())
        st.extra["sampler"] = self.sampler.get_state()
        st.extra["selector"] = self.selector.get_state()
        st.extra["merge"] = self.merge.get_state() if self.merge else None
        st.extra["usage"] = self.usage_snapshot()
        st.extra["trace_len"] = len(st.trace)
        st.extra["val_cache"] = [[a, t, s] for (a, t), s in self._val_cache.items()] if self.cfg.cache_evaluation \
            else []
        trace, st.trace = st.trace, []
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
        lines = log.read_text().splitlines() if log.exists() else []
        st.trace = [json.loads(l) for l in lines[:n]]
        log.write_text("".join(l + "\n" for l in lines[:n]))   # drop the killed iteration's partial entries
        self.state = st
        # A state without RNG / sampler snapshots resumes like the reference implementation:
        # a fresh random.Random(seed) and a fresh epoch sampler (weakness 15).
        if st.extra.get("rng"):
            self.rng.setstate(_rng_state_from_json(st.extra["rng"]))
        if st.extra.get("sampler"):
            self.sampler.set_state(st.extra["sampler"])
        self.selector.set_state(st.extra.get("selector") or {})
        if self.merge and st.extra.get("merge"):
            self.merge.set_state(st.extra["merge"])
        self._usage_prior = st.extra.get("usage") or {}
        self._val_cache = {(a, t): s for a, t, s in st.extra.get("val_cache", [])}
        self.resumed_at = st.i
        return True

    # ------------------------------------------------------------------ loop --
    def initialize(self) -> None:
        if self._try_resume():
            if self.verbose:
                print(f"[gepa] resumed at iteration {self.state.i + 1} with {len(self.state.candidates)} candidates")
            return
        if self.out_dir is not None:
            self.out_dir.mkdir(parents=True, exist_ok=True)
            for f in ("run_log.jsonl", "ledger.jsonl"):
                (self.out_dir / f).unlink(missing_ok=True)
            self.ledger = Ledger(self.out_dir / "ledger.jsonl")
        self.full_eval_and_add(self.seed_artifact, [None], "baseline", "seed_val")
        if self.verbose:
            print(f"[gepa] seed val={self.state.agg_scores()[0]:.4f} |V|={len(self.val_ids)} "
                  f"|T|={len(self.train_ids)} components={self.components}")

    def run(self) -> SearchState:
        self.initialize()
        it = 0
        while True:
            reason = self.stopper(self)
            if reason:
                self.stop_reason = reason
                break
            if it % max(self.cfg.save_every, 1) == 0:
                self._save()
            it += 1
            try:
                self._iteration()
            except BudgetExhausted as e:
                self.state.trace[-1]["budget_exhausted"] = str(e)
                self._finish_entry(self.state.trace[-1])
                self.stop_reason = "max_metric_calls(hard)"
                break
        self._save()
        return self.state

    def _finish_entry(self, entry: dict) -> None:
        st = self.state
        b = st.best_idx()
        entry.update(rollouts=st.counter.total, n_candidates=len(st.candidates), best_idx=b,
                     best_val=st.agg_scores()[b], frontier_size=len(st.frontier.members()))
        self._append_trace(entry)
        self._emit("iteration_end", engine=self, entry=entry)
        if self.verbose:
            ev = entry.get("event", "-")
            print(f"[gepa] it={entry['i']:4d} {ev:18s} rollouts={entry['rollouts']:6d} "
                  f"cands={entry['n_candidates']:3d} best={entry['best_val']:.4f}")

    def _iteration(self) -> None:
        st, cfg = self.state, self.cfg
        st.i += 1
        i = st.i
        entry: dict = {"i": i, "iteration_id": f"it{i:05d}"}
        st.trace.append(entry)
        self._emit("iteration_start", engine=self, i=i)
        # ---- (1) merge
        if self.merge is not None:
            if self.merge.should_attempt():
                entry["invoked_merge"] = True
                prop = self.merge.propose(
                    st, lambda c, ids: [self.evaluate_val(c, ids, "merge_subsample")[0][t] for t in ids])
                self.merge.last_iter_found_new_program = False
                if prop is not None:
                    entry.update(merged=True, merged_entities=[*prop.parents, prop.ancestor],
                                 subsample_ids=prop.subsample_ids, id1_subsample_score=prop.sub_before[0],
                                 id2_subsample_score=prop.sub_before[1], new_program_subsample_scores=prop.sub_after)
                    if sum(prop.sub_after) >= max(prop.sub_before):
                        idx = self.full_eval_and_add(prop.candidate, list(prop.parents), "merge", "val_merge",
                                                     {"ancestor": prop.ancestor, "sources": list(prop.sources)})
                        self.merge.on_accepted()
                        entry.update(event="merge_accepted", new_program_idx=idx)
                        self._emit("merge_accepted", engine=self, idx=idx, proposal=prop)
                    else:
                        if self.store is not None:
                            self.store.put(prop.candidate)
                        self.ledger.add(Node(id=f"m{i}", parent=self._node_id(prop.parents[0]), round=i, kind="merge",
                                             status="rejected", change="merge rejected on subsample",
                                             artifact_id=prop.candidate.id,
                                             metrics={"sub_before": prop.sub_before, "sub_after": sum(prop.sub_after)},
                                             meta={"parents": list(prop.parents), "ancestor": prop.ancestor}))
                        entry["event"] = "merge_rejected"
                        self._emit("merge_rejected", engine=self, proposal=prop)
                    self._finish_entry(entry)
                    return
            self.merge.last_iter_found_new_program = False
        # ---- (2) reflective mutation
        k = self.selector.select(st)
        parent = st.candidates[k]
        ids = self.sampler.next_ids(self.train_ids, i)
        entry.update(selected_program_candidate=k, subsample_ids=list(ids))
        before = self.evaluate_minibatch(parent, ids, "parent")
        entry["subsample_scores"] = before.scores
        if not before.trajectories:
            entry["event"] = "skip_no_trajectories"
            return self._finish_entry(entry)
        if cfg.skip_perfect_score and all(s >= cfg.perfect_score for s in before.scores):
            entry["event"] = "skip_perfect"
            return self._finish_entry(entry)
        comps = self.comp_selector(st, k)
        entry["components"] = comps
        refl = self.adapter.make_reflective_dataset(parent, before, comps)
        res = self.proposer.propose(parent, refl, comps, seed_fn=lambda c: reflection_seed(cfg.seed, i, c))
        st.n_reflection_calls += res.calls
        if res.rejected:
            entry["rejected_outputs"] = {c: r[:200] for c, r in res.rejected.items()}
        if not res.new_texts:
            entry["event"] = "no_proposal"
            return self._finish_entry(entry)
        child = parent.with_files(res.new_texts)
        if self.critic is not None:          # RRSI-style guard: screen the diff before spending rollouts
            verdict = self.critic.screen(parent.diff(child), "reflective rewrite of " + ", ".join(comps))
            if not verdict.accept:
                if self.store is not None:
                    self.store.put(child)
                self.ledger.add(Node(id=f"x{i}", parent=self._node_id(k), round=i, kind="reflective",
                                     status="critic_rejected", change="; ".join(verdict.objections)[:300],
                                     artifact_id=child.id, diff=parent.diff(child)[: cfg.diff_chars],
                                     meta={"parents": [k], "components": comps, "stage": verdict.stage}))
                entry.update(event="critic_rejected", objections=verdict.objections[:5])
                return self._finish_entry(entry)
        st.n_proposals += 1
        after = self.evaluate_minibatch(child, ids, "child")
        entry["new_subsample_scores"] = after.scores
        if self.acceptance.accept(before.scores, after.scores):
            idx = self.full_eval_and_add(child, [k], "reflective", "val_reflective",
                                         {"components": comps, "sub_before": sum(before.scores),
                                          "sub_after": sum(after.scores), "subsample_ids": list(ids)})
            entry.update(event="accepted", new_program_idx=idx)
            if self.merge is not None:
                self.merge.schedule_if_needed()
            self._emit("candidate_accepted", engine=self, idx=idx, parent=k, before=before, after=after, child=child)
        else:
            if self.store is not None:
                self.store.put(child)
            self.ledger.add(Node(id=f"x{i}", parent=self._node_id(k), round=i, kind="reflective", status="rejected",
                                 change="rejected: " + ", ".join(comps), artifact_id=child.id,
                                 diff=parent.diff(child)[: cfg.diff_chars],
                                 metrics={"sub_before": sum(before.scores), "sub_after": sum(after.scores)},
                                 meta={"parents": [k], "components": comps, "subsample_ids": list(ids)}))
            entry["event"] = "rejected"
            self._emit("candidate_rejected", engine=self, parent=k, before=before, after=after, child=child)
        self._finish_entry(entry)
