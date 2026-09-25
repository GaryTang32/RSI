"""One RRSI run: baseline, calibration, rounds (Algorithm 1 then Algorithm 2), and the
maintenance commands (mirrors ``rrsi/loop.py``)::

    run.round(t)
      1. F_t  <- Analyze(H_t, D_evolve)                  analyst over the incumbent's own stored evaluation
      2. b_t  <- annealed edit budget                       schedule.edit_budget
      3. sigma_t, T_t, U_t, E_t, B_t                         history.stall_flag / History
      4. for each of m variants (drafted from the SAME incumbent):
             C_t^(v) ~ P_reg(. | H_t, F_t, L_t, b_t, E_t, B_t)   propose.Proposer (rsi.core Editor)
             Critic(H_t, C_t^(v)) with bounded repair          critic.RRSICritic
             tag edits from the diff; liveness smoke           components.Taxonomy / Domain.smoke
      5. Evaluate(H', D_evolve, k) for the screened set       evaluate.Measurer (rsi.core Evaluator)
      6. admissibility, argmax, S*, history, attribution      selection.select_round (rsi.core gates)
      7. H_{t+1} and the frontier (settles the round)

Everything is written under ``out_dir`` and is resume-safe: a round interrupted after
drafting or evaluating a variant reuses that variant's ``prep.json`` / ``eval.json``.
Git worktrees/branches are replaced by the content-addressed :class:`rsi.core.ArtifactStore`
and every attempt is also a node in an :class:`rsi.core.Ledger` tree.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Optional, Sequence

from ..core.artifact import Artifact
from ..core.domain import Domain
from ..core.editors import Editor
from ..core.evaluate import Evaluator
from ..core.gates import Gate
from ..core.ledger import ArtifactStore, Ledger, Node
from ..core.llm import LLM
from .analyst import Analyst, build_traces, render_trace
from .attribution import Scoreboard
from .calibrate import calibrate as _calibrate
from .components import Taxonomy
from .config import Config
from .constitution import default_constitution
from .critic import RRSICritic
from .evaluate import Measurement, Measurer
from .frontier import Frontier, read_json, write_json
from .history import History, append_lines, exploration, read_jsonl, stall_flag
from .propose import Proposer, RRSIRewriteEditor
from .schedule import edit_budget
from .selection import Candidate, build_gates, select_round
from .switches import RegularizerSwitches

VARIANT_LABELS = "ABCDEFGH"


def _no_critic(diff: str, *args, **kwargs) -> dict:
    """Stand-in review when the critic is switched off (ablations): accept every non-empty diff."""
    if not (diff or "").strip():
        return {"verdict": "reject", "reasons": ["empty diff"], "risk_notes": [], "stage": "precheck"}
    return {"verdict": "accept", "reasons": [], "risk_notes": ["critic disabled"], "stage": "none"}


def _seed(*parts) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8], 16) % 1_000_000


class RRSIRun:
    """Paths, state and plumbing of one RRSI evolution (see :func:`rsi.rrsi.run`)."""

    def __init__(self, domain: Domain, seed_artifact: Artifact, *, out_dir: str | Path,
                 llm_task: Optional[LLM] = None, llm_propose: Optional[LLM] = None, llm_critic: Optional[LLM] = None,
                 llm_analyst: Optional[LLM] = None, editor: Optional[Editor] = None, config: Optional[Config] = None,
                 switches: Optional[RegularizerSwitches] = None, guards: Sequence[Gate] = (),
                 constitution: Optional[tuple[str, str]] = None, critic_patterns: Sequence[tuple[str, str]] = (),
                 hooks: Optional[dict[str, Callable]] = None, verbose: bool = False) -> None:
        self.domain, self.seed_artifact = domain, seed_artifact
        self.cfg = config or Config()
        self.sw = switches or RegularizerSwitches.full()
        if not 1 <= self.cfg.m <= len(VARIANT_LABELS) or not 0 <= self.cfg.m_draft <= self.cfg.m:
            raise ValueError(f"need 1 <= m <= {len(VARIANT_LABELS)} and 0 <= m_draft <= m "
                             f"(got m={self.cfg.m}, m_draft={self.cfg.m_draft})")
        self.out = Path(out_dir)
        self.out.mkdir(parents=True, exist_ok=True)
        self.verbose = verbose
        self.hooks = dict(hooks or {})
        self.tax = Taxonomy.from_domain(domain)
        self.llm_task, self.llm_propose = llm_task, llm_propose
        self.llm_critic = llm_critic if llm_critic is not None else llm_propose
        use_llm_analyst = self.cfg.analyst == "llm" or (self.cfg.analyst == "auto" and llm_analyst is not None)
        self.llm_analyst = (llm_analyst if llm_analyst is not None else llm_propose) if use_llm_analyst else None
        self.analyst = Analyst(self.llm_analyst, mode="llm" if use_llm_analyst else "heuristic",
                               domain_brief=domain.describe(), max_digests=self.cfg.max_digests,
                               workers=max(1, min(6, self.cfg.workers)))
        self.history = History(self.out / "history.jsonl", self.tax.K, timestamps=self.cfg.record_timestamps)
        thr = domain.regression_threshold(self.cfg.k) if hasattr(domain, "regression_threshold") \
            else 1.0 / max(1, self.cfg.k)
        self.scoreboard = Scoreboard(self.out / "attribution.jsonl", threshold=thr)
        self.store = ArtifactStore(self.out / "artifacts")
        self.ledger = Ledger(self.out / "ledger.jsonl")
        self.frontier = Frontier(self.out / "frontier.json")
        self.measurer = Measurer(domain, llm_task, workers=self.cfg.workers, run_seed=self.cfg.seed,
                                 cache_dir=(self.out / "trials") if self.cfg.trial_cache else None,
                                 trace_chars=self.cfg.trace_chars)
        self.critic = RRSICritic(domain, self.llm_critic, patterns=critic_patterns) if self.sw.critic else None
        self.editor = editor or (RRSIRewriteEditor(llm_propose) if llm_propose is not None else None)
        if constitution is None:
            constitution = domain.rrsi_constitution() if hasattr(domain, "rrsi_constitution") \
                else default_constitution(self.cfg, self.tax, self.sw)
        self.proposer = Proposer(self.editor, self.tax, self.cfg, domain_brief=domain.describe(),
                                 constitution=constitution, editable=self.cfg.editable,
                                 history_mode=self.sw.history_conditioning)
        dom_guards = getattr(domain, "rrsi_guards", ())
        self.guards = list(guards) + list(dom_guards() if callable(dom_guards) else dom_guards)
        self.gates = build_gates(self.cfg, self.sw, self.guards)
        write_json(self.out / "config.json", {"config": self.cfg.dump(), "switches": self.sw.to_json(),
                                              "domain": getattr(domain, "name", "domain"), "K": self.tax.K,
                                              "K_str": self.tax.K_str})

    # ------------------------------------------------------------------ utils --
    def log(self, msg: str) -> None:
        if self.verbose:
            print(f"[rrsi:{getattr(self.domain, 'name', 'domain')}] {time.strftime('%H:%M:%S')} {msg}", flush=True)

    def _hook(self, event: str, **info) -> None:
        fn = self.hooks.get(event) or self.hooks.get("*")
        if fn is not None:
            fn(event, **info)

    def eval_path(self, job: str) -> Path:
        return self.out / "evals" / f"{job}.json"

    def load_eval(self, job: str) -> Measurement:
        return Measurement.load(self.eval_path(job))

    def delta(self) -> float:
        if self.cfg.delta is not None:
            return float(self.cfg.delta)
        cal = read_json(self.out / "calibration.json")
        if cal is None:
            raise RuntimeError("no noise band: set Config.delta or run calibrate()")
        return float(cal["delta"])

    def _measure_valid(self, art: Artifact, job: str) -> tuple[Optional[Measurement], str]:
        """Evaluate with one retry when too many trials are missing (infrastructure)."""
        ev = None
        for attempt in range(2):
            try:
                ev = self.measurer.measure(art, job, self.cfg.k)
            except Exception as e:  # noqa: BLE001 - an evaluation crash invalidates only this candidate
                return None, f"evaluation crashed: {e!r}"[:600]
            if ev.missing <= self.cfg.invalid_missing_frac * ev.n_expected:
                return ev, ""
            self.log(f"{job}: {ev.missing}/{ev.n_expected} trials missing{'; retrying once' if attempt == 0 else ''}")
        return None, f"{ev.missing}/{ev.n_expected} trials missing (infrastructure) after a retry"

    # --------------------------------------------------------------- baseline --
    def baseline(self, job: str = "base") -> Measurement:
        """Evaluate H_0 and seed the frontier."""
        art = self.seed_artifact
        self.store.put(art)
        ev = self.load_eval(job) if self.eval_path(job).exists() else None
        if ev is None:
            ev, why = self._measure_valid(art, job)
            if ev is None:
                raise RuntimeError(f"baseline invalid: {why}")
            ev.save(self.eval_path(job))
        fr = {"domain": getattr(self.domain, "name", "domain"),
              "incumbent": {"t": 0, "artifact_id": art.id, "job": job, "S": ev.S, "C": ev.C, "extra": ev.extra,
                            "variant": None, "node": "H0"},
              "S_star": ev.S,
              "trajectory": [{"t": 0, "S": ev.S, "C": ev.C, "artifact_id": art.id, "job": job, "node": "H0",
                              "variant": None, "extra": ev.extra}],
              "config": self.cfg.dump(), "switches": self.sw.to_json()}
        if not any(r.get("outcome") == "BASELINE" for r in self.history.records()):
            self.history.append({"t": 0, "variant": "-", "edit_id": None, "component": None,
                                 "hypothesis": "H_0 baseline", "outcome": "BASELINE", "S": round(ev.S, 6),
                                 "C": None if ev.C is None else round(ev.C, 1), "accepted": True,
                                 "delta_S": None, "delta_C": None, "bundle": 0})
        self._upsert(Node("H0", None, round=0, kind="baseline", status="keep", score=ev.S, cost=ev.C,
                          change="H_0 baseline", artifact_id=art.id, metrics={"S": ev.S, "C": ev.C}))
        self.frontier.save(fr)
        self.log(f"baseline S={ev.S:.4f} C={ev.C} missing={ev.missing}/{ev.n_expected}")
        self._hook("baseline", S=ev.S)
        return ev

    def calibrate(self) -> dict:
        """delta from the base evaluation (bootstrap) or R repeated base evaluations."""
        jobs = ["base"] + [f"base_r{i}" for i in range(1, max(1, self.cfg.calibration_repeats))]
        evals = []
        for j in jobs:
            if self.eval_path(j).exists():
                evals.append(self.load_eval(j))
                continue
            ev, why = self._measure_valid(self.seed_artifact, j)
            if ev is None:
                raise RuntimeError(f"calibration evaluation {j} invalid: {why}")
            ev.save(self.eval_path(j))
            evals.append(ev)
        cal = _calibrate(evals, z=self.cfg.delta_z, reps=self.cfg.calibration_reps, seed=self.cfg.calibration_seed,
                         small_k_correction=self.cfg.bootstrap_small_k_correction)
        cal["jobs"] = jobs
        write_json(self.out / "calibration.json", cal)
        self.log(f"calibrated delta={cal['delta']:.5f} (sd_null {cal['sd_null']:.5f}, {cal['method']})")
        if cal.get("warning"):
            import warnings
            warnings.warn(f"rsi.rrsi calibration: {cal['warning']}", RuntimeWarning, stacklevel=2)
        return cal

    # ---------------------------------------------------------------- round --
    def round(self, t: int) -> None:
        cfg, sw = self.cfg, self.sw
        fr = self.frontier.load()
        if len(fr["trajectory"]) < t + 1:
            raise RuntimeError(f"round {t} needs trajectory up to t={t}; have {len(fr['trajectory'])} entries")
        if len(fr["trajectory"]) > t + 1:
            return                                                      # already settled
        delta = self.delta()
        rdir = self.out / f"r{t}"
        rdir.mkdir(parents=True, exist_ok=True)
        inc = fr["incumbent"]
        inc_ev = self.load_eval(inc["job"])
        inc_art = self.store.get(inc["artifact_id"])
        self.log(f"=== round {t}/{cfg.T} S={inc_ev.S:.4f} C={inc_ev.C} S*={fr['S_star']:.4f} delta={delta:.4f}")

        # 1) F_t <- Analyze(H_t, D_evolve) on the incumbent's own evaluation
        traces = build_traces(inc_ev, cfg.n_fail_traces, cfg.n_success_traces)
        if len(traces) < 0.5 * min(len(inc_ev.per_task), cfg.n_fail_traces + cfg.n_success_traces):
            # the code's precondition: without the incumbent's traces there is no evidence to propose from
            raise RuntimeError(f"only {len(traces)} traces available from {inc['job']}")
        report_path, digests_path = rdir / "analysis_report.json", rdir / "digests.json"
        if report_path.exists():
            report, digests = read_json(report_path), read_json(digests_path, [])
        else:
            prior = read_json(self.out / "global_analysis.json", {})
            inputs = {tid: self.domain.tasks.get(tid).input for tid in traces if tid in self.domain.tasks.tasks}
            report, digests = self.analyst.analyze(traces, inc_ev, inputs, prior, seed=_seed(cfg.seed, "an", t))
            write_json(digests_path, digests)
            write_json(report_path, report)
        write_json(self.out / "global_analysis.json", {"failure_modes": report.get("failure_modes"),
                                                       "success_habits": report.get("success_habits")})

        # 2-3) b_t, sigma_t, T_t, U_t, E_t, B_t
        if sw.budget_anneal:
            budget = edit_budget(t, cfg.T, cfg.b_min, cfg.b_max, cfg.budget_rounding)
        else:
            budget = sw.constant_budget or cfg.b_max
        traj = [x["S"] for x in fr["trajectory"]]
        tried = self.history.tried()
        if sw.stall_exploration:
            sigma = stall_flag(traj, t, cfg.w, delta)
            explore = exploration(t, sigma, tried, cfg.m_draft, self.tax.K)
        else:
            sigma, explore = 0, {"sigma": 0, "untried": [], "m_draft": 0, "text": ""}
        prune = self.history.prune_set(t, cfg.n_prune) if sw.prune_directives else []
        hist_rows = self.history.render(cfg.history_render_n, mode=sw.history_conditioning)
        write_json(rdir / "directives.json", {"t": t, "b_t": budget, "sigma_t": sigma, "tried": sorted(tried),
                                              "explore": explore, "prune_set": prune, "delta": delta,
                                              "S_star": fr["S_star"]})
        shared = dict(report=report, digests=digests, traces=traces, inc_ev=inc_ev, inc_art=inc_art,
                      budget=budget, explore=explore, prune=prune, hist_rows=hist_rows, delta=delta,
                      S_star=fr["S_star"], sigma=sigma)

        # 4) draw and screen m candidates, each from the same incumbent
        cands: list[Candidate] = []
        for v in range(cfg.m):
            vid = VARIANT_LABELS[v]
            reserved = bool(sigma and explore["untried"] and v >= cfg.m - cfg.m_draft)
            cands.append(self._draft(t, vid, rdir, reserved, **shared))

        # 5) Evaluate(H', D_evolve, k) for the screened set
        live = [c for c in cands if c.gate_failure is None]
        if cfg.eval_parallel > 1 and len(live) > 1:
            with ThreadPoolExecutor(max_workers=cfg.eval_parallel) as ex:
                list(ex.map(lambda c: self._evaluate(t, c, rdir), live))
        else:
            for c in live:
                self._evaluate(t, c, rdir)

        # 6) Algorithm 2
        # accepted-edit counts from rounds < t only: a resumed round may already hold some of its own records
        counts = self.history.incumbent_component_counts(before_t=t)
        winner, decisions = select_round(cands, inc_ev, fr["S_star"], delta, cfg, counts, self.gates,
                                         taxonomy=self.tax, t=t)
        write_json(rdir / "decisions.json", [d.to_json() for d in decisions])
        self._record(t, cands, decisions, winner, inc_ev, inc.get("node", "H0"))

        # 7) H_{t+1}
        if winner is not None:
            new = {"t": t + 1, "artifact_id": winner.artifact_id, "job": winner.ev.job, "S": winner.ev.S,
                   "C": winner.ev.C, "extra": winner.ev.extra, "variant": winner.variant, "node": f"r{t}{winner.variant}"}
            fr["incumbent"] = new
            fr["S_star"] = max(fr["S_star"], winner.ev.S)
            self.log(f"ACCEPTED r{t}{winner.variant} S={winner.ev.S:.4f} S*={fr['S_star']:.4f}")
        else:
            new = dict(inc, t=t + 1)
            fr["incumbent"] = new
            self.log(f"no admissible candidate; H_{t + 1} = H_{t}")
        fr["trajectory"] = [x for x in fr["trajectory"] if x["t"] <= t] + [
            {"t": t + 1, "S": new["S"], "C": new["C"], "artifact_id": new["artifact_id"], "job": new["job"],
             "node": new.get("node"), "variant": new.get("variant"), "extra": new.get("extra")}]
        write_json(rdir / "summary.json", {
            "t": t, "b_t": budget, "sigma_t": sigma, "delta": delta, "S_star": fr["S_star"], "S": new["S"],
            "C": new["C"], "incumbent": new["artifact_id"], "winner": winner.variant if winner else None,
            "n_candidates": len(cands), "n_screened": len(live), "n_untried": len(explore.get("untried") or []),
            "prune_set": [p["component"] for p in prune],
            "decisions": [d.to_json() for d in decisions],
            "gate_failures": {c.variant: c.gate_failure for c in cands if c.gate_failure}})
        if self.cfg.heldout_monitor and winner is not None:
            self._monitor(t, winner)
        self.frontier.save(fr)                                          # settles round t
        self._hook("settled", t=t)

    def _record(self, t, cands, decisions, winner, inc_ev, parent_node) -> None:
        for c, dec in zip(cands, decisions):
            if c.ev is None:
                outcome = c.gate_failure or "not_evaluated"
                if not self.history.has(t, c.variant):
                    self.history.append_candidate(t, c.variant, c.edits, outcome, None, None, False, None, None,
                                                  c.diff_path, c.detail)
            else:
                outcome = "ACCEPTED" if c is winner else ("LOST" if dec.admissible else "REJECTED")
                if not self.history.has(t, c.variant):
                    self.history.append_candidate(t, c.variant, c.edits, outcome, dec.delta_S, dec.delta_C,
                                                  c is winner, dec.S, dec.C, c.diff_path, dec.reason)
                if not self.scoreboard.has(t, c.variant):
                    self.scoreboard.attribute(t, c.variant, c.edits, inc_ev, c.ev)
                self.log(f"{c.variant}: S={dec.S:.4f} dS={dec.delta_S:+.4f} dC={dec.delta_C:+.3f} "
                         f"nu={dec.novelty} -> {outcome}: {dec.reason[:120]}")
            dp = (self.out / c.diff_path) if c.diff_path else None
            diff = dp.read_text() if dp is not None and dp.exists() else ""
            self._upsert(Node(f"r{t}{c.variant}", parent_node, round=t, kind="candidate", status=outcome,
                              score=dec.S, cost=dec.C, change=(c.mechanism or "")[:300],
                              artifact_id=c.artifact_id, diff=diff[:20000],
                              metrics={"delta_S": dec.delta_S, "delta_C": dec.delta_C, "novelty": dec.novelty},
                              meta={"edits": c.edits, "reason": dec.reason, "gate_failure": c.gate_failure,
                                    "detail": c.detail[:600]}))
            self._hook("recorded", t=t, variant=c.variant)

    # ---------------------------------------------------------------- helpers --
    def _upsert(self, node: Node) -> None:
        """Add a ledger node, or update it in place (keeping its creation order) when a resumed
        round records it again."""
        try:
            self.ledger[node.id]
        except KeyError:
            self.ledger.add(node)
            return
        fields = {k: v for k, v in node.to_json().items() if k not in ("id", "seq", "t")}
        fields["metrics"], fields["meta"] = dict(node.metrics), dict(node.meta)
        self.ledger.update(node.id, **fields)

    def _scoreboard_view(self) -> list[dict]:
        """The attribution scoreboard shown to the proposer. It is evidence about past edits, so it
        follows ``history_conditioning``: all rows (full), rows of accepted candidates only
        (accepted_only: no negative evidence), or nothing (none)."""
        mode, n = self.sw.history_conditioning, self.cfg.scoreboard_n
        if mode == "none":
            return []
        if mode == "accepted_only":
            acc = {(r.get("t"), r.get("variant")) for r in self.history.records() if r.get("accepted") and r.get("edit_id")}
            return [r for r in self.scoreboard.rows() if (r.get("t"), r.get("variant")) in acc][-n:]
        return self.scoreboard.recent(n)

    def _traces_text(self, traces: dict, cap_each: int = 1200, max_n: int = 8) -> str:
        parts = []
        for tid in list(traces)[:max_n]:
            task = self.domain.tasks.tasks.get(tid)
            parts.append(render_trace(traces[tid], task.input if task else None, cap=cap_each))
        return "\n\n".join(parts)

    def _draft(self, t, vid, rdir, reserved, *, report, digests, traces, inc_ev, inc_art, budget, explore, prune,
               hist_rows, delta, S_star, sigma) -> Candidate:
        cfg = self.cfg
        vdir = rdir / vid
        vdir.mkdir(parents=True, exist_ok=True)
        prep_path = vdir / "prep.json"
        prep = read_json(prep_path)
        if prep is not None:                                            # resume
            if prep.get("gate_failure"):
                return Candidate(vid, prep.get("edits") or [], gate_failure=prep["gate_failure"],
                                 detail=prep.get("detail", ""), diff_path=prep.get("diff_path"),
                                 artifact_id=prep.get("artifact_id"), mechanism=prep.get("mechanism", ""))
            return Candidate(vid, prep["edits"], diff_path=prep["diff_path"], artifact_id=prep["artifact_id"],
                             mechanism=prep.get("mechanism", ""))
        variant_brief = (f"You are variant {vid} of round {t}. {cfg.m} variants are drafted independently from the "
                         f"same incumbent this round and each is evaluated on the full evolve set; the best "
                         f"admissible one becomes H_{t + 1}.")
        directives = {"t": t, "T": cfg.T, "variant": vid, "b_t": budget, "reserved_slot": reserved,
                      "untried": explore.get("untried") or [], "sigma_t": sigma,
                      "prune_components": [p["component"] for p in prune], "delta": round(delta, 6),
                      "S_star": round(S_star, 6), "S_incumbent": round(inc_ev.S, 6), "m": cfg.m,
                      "trace_task_ids": list(traces)}
        diff_file = vdir / "diff.patch"
        diff_path = f"r{t}/{vid}/diff.patch"                       # relative to out_dir (relocatable runs)

        def finish(gate_failure: str, detail: str = "", edits=None, artifact_id=None, mechanism=""):
            dp = diff_path if diff_file.exists() else None
            write_json(prep_path, {"gate_failure": gate_failure, "detail": detail, "edits": edits or [],
                                   "diff_path": dp, "artifact_id": artifact_id, "mechanism": mechanism})
            self.log(f"{vid}: {gate_failure} {detail[:160]}")
            self._hook("drafted", t=t, variant=vid)
            return Candidate(vid, edits or [], gate_failure=gate_failure, detail=detail, diff_path=dp,
                             artifact_id=artifact_id, mechanism=mechanism)

        if self.editor is None:
            raise RuntimeError("RRSI needs a proposer: pass llm_propose or editor")
        common = dict(directives=directives, variant_brief=variant_brief, history_rows=hist_rows,
                      scoreboard=self._scoreboard_view(), explore=explore, reserved=reserved,
                      prune_set=prune, report=report, budget=budget, digests=digests,
                      traces_text=self._traces_text(traces))
        prop = self.proposer.propose(inc_art, seed=_seed(cfg.seed, t, vid, 0), **common)
        write_json(vdir / "proposal.json", {k: v for k, v in prop.items() if k not in ("artifact", "usage")})
        if prop["status"] != "done" or prop.get("artifact") is None:
            return finish("no_proposal", str(prop.get("reason") or prop["status"]))
        cand, edits, mech = prop["artifact"], prop["edits"], prop.get("mechanism") or ""

        # Critic(H_t, H') with bounded repair. The reserved-slot check on the diff-normalized tags belongs to
        # stall exploration (a proposal-side regularizer), so it still runs when the critic is ablated.
        if self.critic is not None or (reserved and explore.get("untried")):
            review = self.critic.review if self.critic is not None else _no_critic
            verdict = None
            for attempt in range(1 + cfg.repair_rounds):
                diff = inc_art.diff(cand)
                diff_file.write_text(diff)
                verdict = review(diff, mech, prop.get("targets_mode") or "", edits,
                                 seed=_seed(cfg.seed, t, vid, "critic", attempt))
                if verdict.get("verdict") == "accept":
                    tagged = [self.tax.normalize(e.get("component"), diff) for e in edits]
                    if reserved and explore.get("untried") and not any(c in explore["untried"] for c in tagged):
                        verdict = {"verdict": "reject", "stage": "reserved_slot",
                                   "reasons": [f"this variant holds a RESERVED EXPLORATION SLOT: at least one edit "
                                               f"must be on a never-exercised component from {explore['untried']}, "
                                               f"judged by the DIFF, and none is"],
                                   "risk_notes": verdict.get("risk_notes")}
                write_json(vdir / f"critic_a{attempt}.json", verdict)
                if verdict.get("verdict") == "accept" or attempt >= cfg.repair_rounds:
                    break
                prop = self.proposer.propose(
                    inc_art, working=cand, seed=_seed(cfg.seed, t, vid, attempt + 1),
                    repair_brief={"reasons": verdict.get("reasons"), "risk_notes": verdict.get("risk_notes"),
                                  "your_declared_edits": edits}, **common)
                write_json(vdir / f"proposal_r{attempt + 1}.json",
                           {k: v for k, v in prop.items() if k not in ("artifact", "usage")})
                if prop["status"] != "done" or prop.get("artifact") is None:
                    break
                cand, edits, mech = prop["artifact"], prop["edits"], prop.get("mechanism") or mech
            write_json(vdir / "critic.json", verdict)
            if not verdict or verdict.get("verdict") != "accept":
                return finish("critic_reject", str((verdict or {}).get("reasons"))[:600], edits, mechanism=mech)

        # tag (l', h', d'): validate the declared components against the diff
        diff = inc_art.diff(cand)
        diff_file.write_text(diff)
        for e in edits:
            e["declared_component"] = e.get("component")
            e["component"] = self.tax.normalize(e.get("component"), diff)
        aid = self.store.put(cand)

        # liveness smoke (not a selection rule)
        if self.sw.smoke and cfg.smoke:
            try:
                err = self.domain.smoke(cand, self.llm_task)
            except Exception as ex:  # noqa: BLE001
                err = f"smoke crashed: {ex!r}"
            write_json(vdir / "smoke.json", {"ok": err is None, "error": err})
            if err:
                return finish("smoke_fail", str(err)[:600], edits, aid, mech)
        write_json(prep_path, {"artifact_id": aid, "edits": edits, "diff_path": diff_path, "mechanism": mech})
        self.log(f"{vid}: {len(edits)} edit(s) on {[e['component'] for e in edits]} -> {aid[:10]}")
        self._hook("drafted", t=t, variant=vid)
        return Candidate(vid, edits, diff_path=diff_path, artifact_id=aid, mechanism=mech)

    def _evaluate(self, t: int, c: Candidate, rdir: Path) -> None:
        ep = rdir / c.variant / "eval.json"
        if ep.exists():
            c.ev = Measurement.load(ep)
            return
        ev, why = self._measure_valid(self.store.get(c.artifact_id), f"r{t}{c.variant}")
        if ev is None:
            c.gate_failure, c.detail = "eval_invalid", why
            return
        ev.save(ep)
        ev.save(self.eval_path(ev.job))
        c.ev = ev
        self._hook("evaluated", t=t, variant=c.variant)

    def _monitor(self, t: int, winner: Candidate) -> None:
        """Extension (spec HeldoutMonitor): score each new incumbent on holdout. Logged only;
        never read by the proposer or the selector."""
        if "holdout" not in self.domain.tasks.splits:
            return
        path = self.out / "heldout_monitor.jsonl"
        rows = read_jsonl(path) if path.exists() else []
        if any(r.get("t") == t + 1 for r in rows):                   # already logged (resumed round)
            return
        ev = Evaluator(self.domain, self.llm_task, workers=self.cfg.workers, allow_sealed=True)
        m = ev.evaluate(self.store.get(winner.artifact_id), "holdout", self.cfg.k)
        append_lines(path, [json.dumps({"t": t + 1, "S_evolve": winner.ev.S, "S_holdout": m.score,
                                        "C_holdout": m.cost}) + "\n"])

    # ------------------------------------------------------------ maintenance --
    def _load_round_candidates(self, t: int) -> list[Candidate]:
        rdir = self.out / f"r{t}"
        cands = []
        for vdir in sorted(p for p in rdir.iterdir() if p.is_dir() and len(p.name) == 1):
            prep = read_json(vdir / "prep.json", {})
            c = Candidate(vdir.name, prep.get("edits") or [], diff_path=prep.get("diff_path"),
                          artifact_id=prep.get("artifact_id"), gate_failure=prep.get("gate_failure"),
                          detail=prep.get("detail", ""), mechanism=prep.get("mechanism", ""))
            if (vdir / "eval.json").exists():
                c.ev = Measurement.load(vdir / "eval.json")
            elif not c.gate_failure:
                c.gate_failure = "eval_invalid"
            cands.append(c)
        return cands

    def truncate(self, t: int) -> None:
        """Remove every round after t (history, attribution, frontier, round dirs) so that
        round t can be re-adjudicated. Ledger nodes of removed rounds are marked ``truncated``."""
        fr = self.frontier.load()
        traj = [x for x in fr["trajectory"] if x["t"] <= t + 1]
        fr["trajectory"] = traj
        last = traj[-1]
        fr["incumbent"] = {"t": last["t"], "artifact_id": last["artifact_id"], "job": last["job"], "S": last["S"],
                           "C": last["C"], "extra": last.get("extra"), "variant": last.get("variant"),
                           "node": last.get("node")}
        fr["S_star"] = max(x["S"] for x in traj)
        self.frontier.save(fr)
        self.history.truncate_after(t)
        self.scoreboard.truncate_after(t)
        for p in self.out.glob("r*"):
            if p.is_dir() and p.name[1:].isdigit() and int(p.name[1:]) > t:
                shutil.rmtree(p)
        for n in self.ledger.nodes():
            if n.round > t and n.kind == "candidate" and n.status != "truncated":
                self.ledger.update(n.id, status="truncated")

    def readjudicate(self, t: int, config: Optional[Config] = None) -> list:
        """Re-run Algorithm 2 on the STORED measurements of round t after a change of delta
        or of the acceptance weights (``config``). No new evaluation is spent. Rounds after
        t must have been removed first (:meth:`truncate`)."""
        cfg = config or self.cfg
        fr = self.frontier.load()
        if len(fr["trajectory"]) > t + 2:
            raise RuntimeError(f"rounds after {t} exist in the frontier; truncate({t}) first")
        inc_entry = fr["trajectory"][t]
        inc_ev = self.load_eval(inc_entry["job"])
        S_star = max(x["S"] for x in fr["trajectory"][: t + 1])
        delta = float(cfg.delta) if cfg.delta is not None else self.delta()
        rdir = self.out / f"r{t}"
        cands = self._load_round_candidates(t)
        counts = self.history.incumbent_component_counts(before_t=t)
        gates = build_gates(cfg, self.sw, self.guards)
        winner, decisions = select_round(cands, inc_ev, S_star, delta, cfg, counts, gates, taxonomy=self.tax, t=t)
        if not (rdir / "decisions.orig.json").exists() and (rdir / "decisions.json").exists():
            shutil.copy(rdir / "decisions.json", rdir / "decisions.orig.json")
        write_json(rdir / "decisions.json", [d.to_json() for d in decisions])
        self.history.replace_round(t)
        for c, dec in zip(cands, decisions):
            if c.ev is None:
                self.history.append_candidate(t, c.variant, c.edits, c.gate_failure or "not_evaluated", None, None,
                                              False, None, None, c.diff_path, c.detail)
                continue
            outcome = "ACCEPTED" if c is winner else ("LOST" if dec.admissible else "REJECTED")
            self.history.append_candidate(t, c.variant, c.edits, outcome, dec.delta_S, dec.delta_C, c is winner,
                                          dec.S, dec.C, c.diff_path, f"[re-adjudicated delta={delta:.5f}] {dec.reason}")
            self.ledger.update(f"r{t}{c.variant}", status=outcome, meta={"reason": dec.reason, "readjudicated": True})
        if winner is not None:
            new = {"t": t + 1, "artifact_id": winner.artifact_id, "job": winner.ev.job, "S": winner.ev.S,
                   "C": winner.ev.C, "extra": winner.ev.extra, "variant": winner.variant,
                   "node": f"r{t}{winner.variant}"}
        else:
            new = {"t": t + 1, "artifact_id": inc_entry["artifact_id"], "job": inc_entry["job"], "S": inc_ev.S,
                   "C": inc_ev.C, "extra": inc_ev.extra, "variant": inc_entry.get("variant"),
                   "node": inc_entry.get("node")}
        fr["incumbent"] = new
        fr["S_star"] = max(S_star, new["S"])
        fr["trajectory"] = fr["trajectory"][: t + 1] + [
            {k: new[k] for k in ("t", "S", "C", "artifact_id", "job", "node", "variant", "extra")}]
        fr.setdefault("readjudications", []).append({"t": t, "delta": delta, "winner": winner.variant if winner else None})
        self.frontier.save(fr)
        return decisions

    def reevaluate(self, t: int, variants: Optional[Sequence[str]] = None) -> list:
        """Re-measure the stored candidates of round t (after an infrastructure failure),
        then re-adjudicate the round."""
        rdir = self.out / f"r{t}"
        for vdir in sorted(p for p in rdir.iterdir() if p.is_dir() and len(p.name) == 1):
            if variants and vdir.name not in variants:
                continue
            prep = read_json(vdir / "prep.json", {})
            if not prep.get("artifact_id") or prep.get("gate_failure"):
                continue
            (vdir / "eval.json").unlink(missing_ok=True)
            c = Candidate(vdir.name, prep.get("edits") or [], artifact_id=prep["artifact_id"])
            ev, why = self._measure_valid(self.store.get(c.artifact_id), f"r{t}{vdir.name}_re")
            if ev is not None:
                ev.save(vdir / "eval.json")
                ev.save(self.eval_path(ev.job))
        return self.readjudicate(t)
