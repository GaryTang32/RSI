"""One resumable RRSI run: baseline, calibration, rounds (Alg. 1 then Alg. 2) and maintenance commands."""
from __future__ import annotations

import hashlib
import json
import shutil
import time
import warnings
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
from .agentic import AgenticAnalyst, JsonActionProposer
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
from .spend import SpendLedger
from .switches import RegularizerSwitches
from .tracing import RRSITrace

VARIANT_LABELS = "ABCDEFGH"
_PROP_SKIP = ("artifact", "usage", "turns")
_TRAJ_KEYS = ("t", "S", "C", "artifact_id", "job", "node", "variant", "extra")


def _no_critic(diff: str, *args, **kwargs) -> dict:
    """Review used when the critic is ablated: accept every non-empty diff."""
    if not (diff or "").strip():
        return {"verdict": "reject", "reasons": ["empty diff"], "risk_notes": [], "stage": "precheck"}
    return {"verdict": "accept", "reasons": [], "risk_notes": ["critic disabled"], "stage": "none"}


def _seed(*parts) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8], 16) % 1_000_000


def _outcome(c: Candidate, dec, winner: Optional[Candidate]) -> str:
    return "ACCEPTED" if c is winner else ("LOST" if dec.admissible else "REJECTED")


def _winner_entry(t: int, w: Candidate) -> dict:
    return {"t": t + 1, "artifact_id": w.artifact_id, "job": w.ev.job, "S": w.ev.S, "C": w.ev.C,
            "extra": w.ev.extra, "variant": w.variant, "node": f"r{t}{w.variant}"}


class RRSIRun:
    """Paths, state and plumbing of one RRSI evolution (see :func:`rsi.rrsi.run`)."""

    def __init__(self, domain: Domain, seed_artifact: Artifact, *, out_dir: str | Path,
                 llm_task: Optional[LLM] = None, llm_propose: Optional[LLM] = None, llm_critic: Optional[LLM] = None,
                 llm_analyst: Optional[LLM] = None, editor: Optional[Editor] = None, config: Optional[Config] = None,
                 switches: Optional[RegularizerSwitches] = None, guards: Sequence[Gate] = (),
                 constitution: Optional[tuple[str, str]] = None, critic_patterns: Sequence[tuple[str, str]] = (),
                 hooks: Optional[dict[str, Callable]] = None, verbose: bool = False) -> None:
        self.domain, self.seed_artifact = domain, seed_artifact
        cfg = self.cfg = config or Config()
        self.sw = switches or RegularizerSwitches.full()
        if not 1 <= cfg.m <= len(VARIANT_LABELS) or not 0 <= cfg.m_draft <= cfg.m:
            raise ValueError(f"need 1 <= m <= {len(VARIANT_LABELS)} and 0 <= m_draft <= m "
                             f"(got m={cfg.m}, m_draft={cfg.m_draft})")
        if cfg.precheck_scope not in ("added", "diff"):
            raise ValueError(f"Config.precheck_scope must be 'added' or 'diff' (got {cfg.precheck_scope!r})")
        if cfg.smoke_n < 1:
            raise ValueError(f"Config.smoke_n must be >= 1 (got {cfg.smoke_n})")
        self.out = Path(out_dir)
        self.out.mkdir(parents=True, exist_ok=True)
        self.verbose = verbose
        self.hooks = dict(hooks or {})
        self.tax = Taxonomy.from_domain(domain, aliases=cfg.component_aliases)
        self.llm_task, self.llm_propose = llm_task, llm_propose
        self.llm_critic = llm_critic if llm_critic is not None else llm_propose
        if cfg.analyst not in ("auto", "llm", "heuristic", "agentic"):
            raise ValueError(f"Config.analyst must be auto | llm | heuristic | agentic (got {cfg.analyst!r})")
        if cfg.proposer_protocol not in ("rewrite", "json_actions"):
            raise ValueError(f"Config.proposer_protocol must be rewrite | json_actions "
                             f"(got {cfg.proposer_protocol!r})")
        use_llm_analyst = cfg.analyst in ("llm", "agentic") or (cfg.analyst == "auto" and llm_analyst is not None)
        self.llm_analyst = (llm_analyst if llm_analyst is not None else llm_propose) if use_llm_analyst else None
        workers = max(1, min(6, cfg.workers))
        if cfg.analyst == "agentic":
            if self.llm_analyst is None:
                raise ValueError('Config.analyst="agentic" needs an analyst LLM (llm_analyst or llm_propose)')
            self.analyst = AgenticAnalyst(self.llm_analyst, domain_brief=domain.describe(), workers=workers)
        else:
            self.analyst = Analyst(self.llm_analyst, mode="llm" if use_llm_analyst else "heuristic",
                                   domain_brief=domain.describe(), max_digests=cfg.max_digests, workers=workers)
        self.history = History(self.out / "history.jsonl", self.tax.K, timestamps=cfg.record_timestamps)
        thr = domain.regression_threshold(cfg.k) if hasattr(domain, "regression_threshold") else 1.0 / max(1, cfg.k)
        self.scoreboard = Scoreboard(self.out / "attribution.jsonl", threshold=thr)
        self.store = ArtifactStore(self.out / "artifacts")
        self.ledger = Ledger(self.out / "ledger.jsonl")
        self.frontier = Frontier(self.out / "frontier.json")
        self.measurer = Measurer(domain, llm_task, workers=cfg.workers, run_seed=cfg.seed,
                                 cache_dir=(self.out / "trials") if cfg.trial_cache else None,
                                 trace_chars=cfg.trace_chars)
        self.critic = RRSICritic(domain, self.llm_critic, patterns=critic_patterns,
                                 precheck_answers=cfg.precheck_answers,
                                 scope=cfg.precheck_scope) if self.sw.critic else None
        self.editor = editor or (RRSIRewriteEditor(llm_propose) if llm_propose is not None else None)
        if constitution is None:
            constitution = domain.rrsi_constitution() if hasattr(domain, "rrsi_constitution") \
                else default_constitution(cfg, self.tax, self.sw)
        if cfg.proposer_protocol == "json_actions":
            if llm_propose is None:
                raise ValueError('Config.proposer_protocol="json_actions" needs llm_propose')
            self.proposer = JsonActionProposer(llm_propose, self.tax, cfg, domain_brief=domain.describe(),
                                               constitution=constitution,
                                               source_exts=getattr(domain, "source_exts", None)
                                               or ('.py', '.txt', '.md', '.json'))
        else:
            self.proposer = Proposer(self.editor, self.tax, cfg, domain_brief=domain.describe(),
                                     constitution=constitution, editable=cfg.editable,
                                     history_mode=self.sw.history_conditioning)
        dom_guards = getattr(domain, "rrsi_guards", ())
        self.guards = list(guards) + list(dom_guards() if callable(dom_guards) else dom_guards)
        self.gates = build_gates(cfg, self.sw, self.guards)
        self.spend = SpendLedger(self.out, self.llms)
        self.trace = RRSITrace(self.out, enabled=bool(cfg.trace), max_text=cfg.trace_max_text)
        if cfg.shadow_monitor:
            self.trace.attach_monitor(domain, llm_task, k=cfg.shadow_monitor_k, workers=cfg.workers)
        write_json(self.out / "config.json", {"config": cfg.dump(), "switches": self.sw.to_json(),
                                              "domain": getattr(domain, "name", "domain"), "K": self.tax.K,
                                              "K_str": self.tax.K_str})

    def log(self, msg: str) -> None:
        if self.verbose:
            print(f"[rrsi:{getattr(self.domain, 'name', 'domain')}] {time.strftime('%H:%M:%S')} {msg}", flush=True)

    def llms(self) -> list[LLM]:
        """Distinct loop backends (task, proposer, critic, analyst); the shadow monitor's model is not one."""
        out: list[LLM] = []
        for l in (self.llm_task, self.llm_propose, self.llm_critic, self.llm_analyst):
            if l is not None and all(l is not x for x in out):
                out.append(l)
        return out

    def _hook(self, event: str, **info) -> None:
        self.spend.checkpoint()                  # every hook follows a persisted step
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
        """Evaluate, retrying once when too many trials are missing (infrastructure)."""
        ev = None
        for attempt in range(2):
            try:
                ev = self.measurer.measure(art, job, self.cfg.k)
            except Exception as e:  # noqa: BLE001 - invalidates only this candidate
                return None, f"evaluation crashed: {e!r}"[:600]
            if ev.missing <= self.cfg.invalid_missing_frac * ev.n_expected:
                return ev, ""
            self.log(f"{job}: {ev.missing}/{ev.n_expected} trials missing{'; retrying once' if attempt == 0 else ''}")
        return None, f"{ev.missing}/{ev.n_expected} trials missing (infrastructure) after a retry"

    def baseline(self, job: str = "base") -> Measurement:
        """Evaluate H_0 and seed the frontier."""
        art = self.seed_artifact
        self.store.put(art)
        ev = self.load_eval(job) if self.eval_path(job).exists() else None
        reused = ev is not None
        if ev is None:
            ev, why = self._measure_valid(art, job)
            if ev is None:
                raise RuntimeError(f"baseline invalid: {why}")
            ev.save(self.eval_path(job))
        self.trace.evaluation(None, "H0", ev, kind="baseline", truth=self.trace.truth(self, art),
                              reused_from_disk=reused, artifact=art.short_id)
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
        self.trace.kept(None, "H0", art, ev.S)
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
            self.spend.checkpoint()
            evals.append(ev)
        cal = _calibrate(evals, z=self.cfg.delta_z, reps=self.cfg.calibration_reps, seed=self.cfg.calibration_seed,
                         small_k_correction=self.cfg.bootstrap_small_k_correction)
        cal["jobs"] = jobs
        write_json(self.out / "calibration.json", cal)
        self.trace.noise(cal)
        self.log(f"calibrated delta={cal['delta']:.5f} (sd_null {cal['sd_null']:.5f}, {cal['method']})")
        if cal.get("warning"):
            warnings.warn(f"rsi.rrsi calibration: {cal['warning']}", RuntimeWarning, stacklevel=2)
        return cal

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

        # b_t, sigma_t, T_t, U_t, E_t, B_t (independent of F_t, so the trace opens with the full loop state)
        budget = edit_budget(t, cfg.T, cfg.b_min, cfg.b_max, cfg.budget_rounding) if sw.budget_anneal \
            else sw.constant_budget or cfg.b_max
        tried = self.history.tried()
        if sw.stall_exploration:
            sigma = stall_flag([x["S"] for x in fr["trajectory"]], t, cfg.w, delta)
            explore = exploration(t, sigma, tried, cfg.m_draft, self.tax.K)
        else:
            sigma, explore = 0, {"sigma": 0, "untried": [], "m_draft": 0, "text": ""}
        prune = self.history.prune_set(t, cfg.n_prune) if sw.prune_directives else []
        hist_rows = self.history.render(cfg.history_render_n, mode=sw.history_conditioning)
        write_json(rdir / "directives.json", {"t": t, "b_t": budget, "sigma_t": sigma, "tried": sorted(tried),
                                              "explore": explore, "prune_set": prune, "delta": delta,
                                              "S_star": fr["S_star"]})
        self.trace.round_start(self, t, fr, delta, budget, sigma, explore, prune, tried)

        # F_t <- Analyze(H_t, D_evolve) on the incumbent's stored evaluation
        traces = build_traces(inc_ev, cfg.n_fail_traces, cfg.n_success_traces)
        if len(traces) < 0.5 * min(len(inc_ev.per_task), cfg.n_fail_traces + cfg.n_success_traces):
            raise RuntimeError(f"only {len(traces)} traces available from {inc['job']}")
        report_path, digests_path = rdir / "analysis_report.json", rdir / "digests.json"
        report_reused = report_path.exists()
        if report_reused:
            report, digests = read_json(report_path), read_json(digests_path, [])
        else:
            prior = read_json(self.out / "global_analysis.json", {})
            extra = {"workdir": rdir} if isinstance(self.analyst, AgenticAnalyst) else {}
            report, digests = self.analyst.analyze(traces, inc_ev, self._task_inputs(traces), prior,
                                                   seed=_seed(cfg.seed, "an", t), **extra)
            write_json(digests_path, digests)
            write_json(report_path, report)
            self.spend.checkpoint()
        write_json(self.out / "global_analysis.json", {"failure_modes": report.get("failure_modes"),
                                                       "success_habits": report.get("success_habits")})
        self.trace.analysis(self, t, report, digests, traces, inc_ev, report_reused)

        # draft and screen m candidates from the same incumbent, then Evaluate(H', D_evolve, k)
        shared = dict(report=report, digests=digests, traces=traces, inc_ev=inc_ev, inc_art=inc_art,
                      budget=budget, explore=explore, prune=prune, hist_rows=hist_rows, delta=delta,
                      S_star=fr["S_star"], sigma=sigma)
        reserved = [bool(sigma and explore["untried"] and v >= cfg.m - cfg.m_draft) for v in range(cfg.m)]
        cands = [self._draft(t, VARIANT_LABELS[v], rdir, reserved[v], **shared) for v in range(cfg.m)]
        live = [c for c in cands if c.gate_failure is None]
        if cfg.eval_parallel > 1 and len(live) > 1:
            with ThreadPoolExecutor(max_workers=cfg.eval_parallel) as ex:
                list(ex.map(lambda c: self._evaluate(t, c, rdir), live))
        else:
            for c in live:
                self._evaluate(t, c, rdir)
        self.trace.evaluations(self, t, cands, inc_ev)

        # Alg. 2; accepted-edit counts from rounds < t (a resumed round may hold its own records)
        counts = self.history.incumbent_component_counts(before_t=t)
        winner, decisions = select_round(cands, inc_ev, fr["S_star"], delta, cfg, counts, self.gates,
                                         taxonomy=self.tax, t=t)
        S_star_before = fr["S_star"]
        write_json(rdir / "decisions.json", [d.to_json() for d in decisions])
        self._record(t, cands, decisions, winner, inc_ev, inc.get("node", "H0"))

        if winner is not None:
            new = _winner_entry(t, winner)
            fr["S_star"] = max(fr["S_star"], winner.ev.S)
            self.log(f"ACCEPTED r{t}{winner.variant} S={winner.ev.S:.4f} S*={fr['S_star']:.4f}")
        else:
            new = dict(inc, t=t + 1)
            self.log(f"no admissible candidate; H_{t + 1} = H_{t}")
        fr["incumbent"] = new
        fr["trajectory"] = [x for x in fr["trajectory"] if x["t"] <= t] + [{k: new.get(k) for k in _TRAJ_KEYS}]
        write_json(rdir / "summary.json", {
            "t": t, "b_t": budget, "sigma_t": sigma, "delta": delta, "S_star": fr["S_star"], "S": new["S"],
            "C": new["C"], "incumbent": new["artifact_id"], "winner": winner.variant if winner else None,
            "n_candidates": len(cands), "n_screened": len(live), "n_untried": len(explore.get("untried") or []),
            "prune_set": [p["component"] for p in prune],
            "decisions": [d.to_json() for d in decisions],
            "gate_failures": {c.variant: c.gate_failure for c in cands if c.gate_failure}})
        if cfg.heldout_monitor and winner is not None:
            self._monitor(t, winner)
        self.trace.selection(self, t, cands, decisions, winner, inc_ev, S_star_before, delta, inc.get("node", "H0"),
                             fr["S_star"], new.get("node"))
        self.frontier.save(fr)                                          # settles round t
        self.trace.state(self, t, fr)
        if winner is not None:
            self.trace.kept(t, f"r{t}{winner.variant}", self.store.get(winner.artifact_id), winner.ev.S)
        self._hook("settled", t=t)

    def _record(self, t, cands, decisions, winner, inc_ev, parent_node) -> None:
        for c, dec in zip(cands, decisions):
            if c.ev is None:
                outcome = c.gate_failure or "not_evaluated"
                if not self.history.has(t, c.variant):
                    self.history.append_candidate(t, c.variant, c.edits, outcome, None, None, False, None, None,
                                                  c.diff_path, c.detail)
            else:
                outcome = _outcome(c, dec, winner)
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

    def _incumbent_node(self) -> str:
        try:
            return self.frontier.load()["incumbent"].get("node") or "H0"
        except Exception:  # noqa: BLE001
            return "H0"

    def _upsert(self, node: Node) -> None:
        """Add a ledger node, or update it in place (keeping its order) when a resumed round records it again."""
        try:
            self.ledger[node.id]
        except KeyError:
            self.ledger.add(node)
            return
        fields = {k: v for k, v in node.to_json().items() if k not in ("id", "seq", "t")}
        fields["metrics"], fields["meta"] = dict(node.metrics), dict(node.meta)
        self.ledger.update(node.id, **fields)

    def _scoreboard_view(self) -> list[dict]:
        """Scoreboard rows shown to the proposer, filtered like the history (``history_conditioning``)."""
        mode, n = self.sw.history_conditioning, self.cfg.scoreboard_n
        if mode == "none":
            return []
        if mode == "accepted_only":
            acc = {(r.get("t"), r.get("variant")) for r in self.history.records()
                   if r.get("accepted") and r.get("edit_id")}
            return [r for r in self.scoreboard.rows() if (r.get("t"), r.get("variant")) in acc][-n:]
        return self.scoreboard.recent(n)

    def _task_inputs(self, traces: dict) -> dict:
        return {tid: self.domain.tasks.get(tid).input for tid in traces if tid in self.domain.tasks.tasks}

    def _traces_text(self, traces: dict, cap_each: int = 1200, max_n: int = 8) -> str:
        parts = []
        for tid in list(traces)[:max_n]:
            task = self.domain.tasks.tasks.get(tid)
            parts.append(render_trace(traces[tid], task.input if task else None, cap=cap_each))
        return "\n\n".join(parts)

    def _draft(self, t, vid, rdir, reserved, *, report, digests, traces, inc_ev, inc_art, budget, explore, prune,
               hist_rows, delta, S_star, sigma) -> Candidate:
        """Propose, critic with bounded repair, tag from the diff, smoke; persisted as ``prep.json``."""
        cfg = self.cfg
        vdir = rdir / vid
        vdir.mkdir(parents=True, exist_ok=True)
        prep_path = vdir / "prep.json"
        prep = read_json(prep_path)
        if prep is not None:
            self.trace.note(t, stage="draft reused from prep.json (resume)", candidate=f"r{t}{vid}",
                            gate_failure=prep.get("gate_failure"))
            if prep.get("gate_failure"):
                return Candidate(vid, prep.get("edits") or [], gate_failure=prep["gate_failure"],
                                 detail=prep.get("detail", ""), diff_path=prep.get("diff_path"),
                                 artifact_id=prep.get("artifact_id"), mechanism=prep.get("mechanism", ""))
            return Candidate(vid, prep["edits"], diff_path=prep["diff_path"], artifact_id=prep["artifact_id"],
                             mechanism=prep.get("mechanism", ""))
        variant_brief = (f"You are variant {vid} of round {t}. {cfg.m} variants are drafted independently from the "
                         f"same incumbent this round and each is evaluated on the full evolve set; the best "
                         f"admissible one becomes H_{t + 1}.")
        directives = self.round_directives(t, vid, budget, reserved, explore, sigma, prune, delta, S_star,
                                           inc_ev.S, traces)
        diff_file = vdir / "diff.patch"
        diff_path = f"r{t}/{vid}/diff.patch"                       # relative to out_dir

        def finish(gate_failure: str, detail: str = "", edits=None, artifact_id=None, mechanism=""):
            dp = diff_path if diff_file.exists() else None
            self.trace.note(t, stage="dropped before evaluation", candidate=f"r{t}{vid}", gate_failure=gate_failure,
                            detail=detail[:600])
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
        if isinstance(self.proposer, JsonActionProposer):
            common.update(traces=traces, task_means=inc_ev.task_means(), task_inputs=self._task_inputs(traces))
        parent = self._incumbent_node()
        cap = self.trace.enabled
        prop = self.proposer.propose(inc_art, seed=_seed(cfg.seed, t, vid, 0), capture=cap, **common)
        self.trace.proposal_turns(self, t, vid, parent, inc_art, prop, 0, reserved, budget)
        write_json(vdir / "proposal.json", {k: v for k, v in prop.items() if k not in _PROP_SKIP})
        if prop["status"] != "done" or prop.get("artifact") is None:
            return finish("no_proposal", str(prop.get("reason") or prop["status"]))
        cand, edits, mech = prop["artifact"], prop["edits"], prop.get("mechanism") or ""

        # the reserved-slot check is stall exploration (proposal side), so it runs even when the critic is ablated
        if self.critic is not None or (reserved and explore.get("untried")):
            review = self.critic.review if self.critic is not None else _no_critic
            verdict = None
            for attempt in range(1 + cfg.repair_rounds):
                diff = inc_art.diff(cand)
                diff_file.write_text(diff)
                verdict = review(diff, mech, prop.get("targets_mode") or "", edits,
                                 seed=_seed(cfg.seed, t, vid, "critic", attempt))
                exchange = dict(getattr(self.critic, "last_exchange", {}) or {}) if self.critic is not None else None
                override = False
                if verdict.get("verdict") == "accept":
                    tagged = [self.tax.normalize(e.get("component"), diff) for e in edits]
                    if reserved and explore.get("untried") and not any(c in explore["untried"] for c in tagged):
                        override = True
                        verdict = {"verdict": "reject", "stage": "reserved_slot",
                                   "reasons": [f"this variant holds a RESERVED EXPLORATION SLOT: at least one edit "
                                               f"must be on a never-exercised component from {explore['untried']}, "
                                               f"judged by the DIFF, and none is"],
                                   "risk_notes": verdict.get("risk_notes")}
                write_json(vdir / f"critic_a{attempt}.json", verdict)
                self.trace.critic(self, t, vid, attempt, verdict, exchange, override)
                if verdict.get("verdict") == "accept" or attempt >= cfg.repair_rounds:
                    break
                prop = self.proposer.propose(
                    inc_art, working=cand, seed=_seed(cfg.seed, t, vid, attempt + 1), capture=cap,
                    repair_brief={"reasons": verdict.get("reasons"), "risk_notes": verdict.get("risk_notes"),
                                  "your_declared_edits": edits}, **common)
                self.trace.proposal_turns(self, t, vid, parent, inc_art, prop, attempt + 1, reserved, budget)
                write_json(vdir / f"proposal_r{attempt + 1}.json",
                           {k: v for k, v in prop.items() if k not in _PROP_SKIP})
                if prop["status"] != "done" or prop.get("artifact") is None:
                    break
                cand, edits, mech = prop["artifact"], prop["edits"], prop.get("mechanism") or mech
            write_json(vdir / "critic.json", verdict)
            if not verdict or verdict.get("verdict") != "accept":
                return finish("critic_reject", str((verdict or {}).get("reasons"))[:600], edits, mechanism=mech)

        diff = inc_art.diff(cand)
        diff_file.write_text(diff)
        for e in edits:
            e["declared_component"] = e.get("component")
            e["component"] = self.tax.normalize(e.get("component"), diff)
        aid = self.store.put(cand)
        self.trace.tagging(self, t, vid, edits, diff)

        if self.sw.smoke and cfg.smoke:
            try:
                err = self.smoke(cand)
            except Exception as ex:  # noqa: BLE001
                err = f"smoke crashed: {ex!r}"
            write_json(vdir / "smoke.json", {"ok": err is None, "error": err, "ids": self.smoke_ids()})
            self.trace.note(t, stage="smoke", candidate=f"r{t}{vid}", ok=err is None, error=err,
                            ids=self.smoke_ids(), rule="liveness only (not a selection rule)")
            if err:
                return finish("smoke_fail", str(err)[:600], edits, aid, mech)
        write_json(prep_path, {"artifact_id": aid, "edits": edits, "diff_path": diff_path, "mechanism": mech})
        self.log(f"{vid}: {len(edits)} edit(s) on {[e['component'] for e in edits]} -> {aid[:10]}")
        self._hook("drafted", t=t, variant=vid)
        return Candidate(vid, edits, diff_path=diff_path, artifact_id=aid, mechanism=mech)

    def round_directives(self, t, vid, budget, reserved, explore, sigma, prune, delta, S_star, S_inc,
                         traces) -> dict:
        """Structured restatement of the round context; delta, S*, S_t and T only with ``proposer_numbers``."""
        d = {"t": t, "T": self.cfg.T, "variant": vid, "b_t": budget, "reserved_slot": reserved,
             "untried": explore.get("untried") or [], "sigma_t": sigma,
             "prune_components": [p["component"] for p in prune], "delta": round(delta, 6),
             "S_star": round(S_star, 6), "S_incumbent": round(S_inc, 6), "m": self.cfg.m,
             "trace_task_ids": list(traces)}
        if not self.cfg.proposer_numbers:
            for k in ("T", "delta", "S_star", "S_incumbent"):
                d.pop(k)
        return d

    def smoke_ids(self) -> list[str]:
        """The domain's ``smoke`` split, else the first ``smoke_n`` evolve tasks."""
        sp = self.domain.tasks.splits
        return list(sp.get("smoke") or sp.get("evolve") or [])[: self.cfg.smoke_n]

    def smoke(self, cand: Artifact) -> Optional[str]:
        """Liveness smoke (not a selection rule): the domain's own smoke, else one run per smoke task."""
        if type(self.domain).smoke is not Domain.smoke:
            return self.domain.smoke(cand, self.llm_task)
        errs, scores = [], []
        for tid in self.smoke_ids():
            tr = self.domain.run(cand, self.domain.tasks.get(tid), seed=0, llm=self.llm_task)
            scores.append(tr.score)
            if tr.error:
                errs.append(f"{tid}: {tr.error}")
        if errs:
            return "; ".join(errs)[:600]
        fail = float(getattr(self.domain, "failure_score", 0.0))
        if self.cfg.smoke_require_score and scores and sum(scores) / len(scores) <= fail:
            return f"smoke: mean score {sum(scores) / len(scores):.4f} is not above the failure score {fail}"
        return None

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
        """Extension: log each new incumbent's holdout score (never read by the loop)."""
        if "holdout" not in self.domain.tasks.splits:
            return
        path = self.out / "heldout_monitor.jsonl"
        rows = read_jsonl(path) if path.exists() else []
        if any(r.get("t") == t + 1 for r in rows):
            return
        ev = Evaluator(self.domain, self.llm_task, workers=self.cfg.workers, allow_sealed=True)
        m = ev.evaluate(self.store.get(winner.artifact_id), "holdout", self.cfg.k)
        append_lines(path, [json.dumps({"t": t + 1, "S_evolve": winner.ev.S, "S_holdout": m.score,
                                        "C_holdout": m.cost}) + "\n"])

    def _variant_dirs(self, t: int) -> list[Path]:
        return sorted(p for p in (self.out / f"r{t}").iterdir() if p.is_dir() and len(p.name) == 1)

    def _load_round_candidates(self, t: int) -> list[Candidate]:
        cands = []
        for vdir in self._variant_dirs(t):
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
        """Remove every round after t (history, attribution, frontier, round dirs; ledger nodes marked truncated)."""
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
        """Re-run Alg. 2 on round t's stored measurements (no new evaluation); call :meth:`truncate` first."""
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
            outcome = _outcome(c, dec, winner)
            self.history.append_candidate(t, c.variant, c.edits, outcome, dec.delta_S, dec.delta_C, c is winner,
                                          dec.S, dec.C, c.diff_path, f"[re-adjudicated delta={delta:.5f}] {dec.reason}")
            self.ledger.update(f"r{t}{c.variant}", status=outcome, meta={"reason": dec.reason, "readjudicated": True})
        if winner is not None:
            new = _winner_entry(t, winner)
        else:
            new = {"t": t + 1, "artifact_id": inc_entry["artifact_id"], "job": inc_entry["job"], "S": inc_ev.S,
                   "C": inc_ev.C, "extra": inc_ev.extra, "variant": inc_entry.get("variant"),
                   "node": inc_entry.get("node")}
        fr["incumbent"] = new
        fr["S_star"] = max(S_star, new["S"])
        fr["trajectory"] = fr["trajectory"][: t + 1] + [{k: new[k] for k in _TRAJ_KEYS}]
        fr.setdefault("readjudications", []).append({"t": t, "delta": delta,
                                                     "winner": winner.variant if winner else None})
        self.frontier.save(fr)
        return decisions

    def reevaluate(self, t: int, variants: Optional[Sequence[str]] = None) -> list:
        """Re-measure round t's stored candidates (after an infrastructure failure), then re-adjudicate."""
        for vdir in self._variant_dirs(t):
            if variants and vdir.name not in variants:
                continue
            prep = read_json(vdir / "prep.json", {})
            if not prep.get("artifact_id") or prep.get("gate_failure"):
                continue
            (vdir / "eval.json").unlink(missing_ok=True)
            ev, _ = self._measure_valid(self.store.get(prep["artifact_id"]), f"r{t}{vdir.name}_re")
            if ev is not None:
                ev.save(vdir / "eval.json")
                ev.save(self.eval_path(ev.job))
        return self.readjudicate(t)
