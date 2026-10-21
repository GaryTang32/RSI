"""Resumable driver (:func:`drive`) and the public entry point :func:`run`."""
from __future__ import annotations

import tempfile
import time
import traceback
from pathlib import Path
from typing import Callable, Optional, Sequence

from ..core.artifact import Artifact
from ..core.domain import Domain
from ..core.editors import Editor
from ..core.gates import Gate
from ..core.llm import LLM, Usage
from ..core.run import Budget, ImprovementResult
from .config import Config
from .frontier import read_json
from .loop import RRSIRun
from .switches import RegularizerSwitches

MAX_CONSECUTIVE_INFRA = 3


class Killed(BaseException):
    """Raised by test hooks to simulate a hard kill (never swallowed by the driver)."""


def drive(r: RRSIRun, T: Optional[int] = None, start: int = 0, budget: Optional[Budget] = None) -> str:
    """Run rounds until T, a STOP file, the budget, or 3 consecutive infrastructure failures."""
    t0 = time.time()
    resumed = r.frontier.exists()
    r.trace.run_start(r, budget, resumed=resumed)
    try:
        stop = _drive(r, T, start, budget, resumed)
    finally:
        r.spend.checkpoint()
    r.trace.run_end(r, stop, time.time() - t0)
    return stop


def _drive(r: RRSIRun, T: Optional[int], start: int, budget: Optional[Budget], resumed: bool) -> str:
    T = r.cfg.T if T is None else T
    if not r.frontier.exists():
        r.baseline()
    if r.cfg.delta is None and not (r.out / "calibration.json").exists():
        r.calibrate()
    elif r.cfg.delta is not None and not resumed:
        r.trace.noise({}, fixed=float(r.cfg.delta))
    infra = 0
    t = start
    while t < T:
        if (r.out / "STOP").exists() or (budget and budget.stop_dir and Path(budget.stop_dir, "STOP").exists()):
            return "stop_file"
        if budget is not None:
            r.spend.checkpoint()
            why = budget.exhausted(rounds=r.frontier.settled_rounds(), rollouts=r.measurer.n_rollouts,
                                   usd=r.spend.total())
            if why:
                return why
        if r.frontier.settled_rounds() >= t + 1:
            t += 1
            continue
        try:
            r.round(t)
            infra = 0
            t += 1
        except Exception as e:  # noqa: BLE001 - retried; the same round is resume-safe
            infra += 1
            r.log(f"round {t} did not settle ({infra}/{MAX_CONSECUTIVE_INFRA}): {e!r}")
            (r.out / "logs").mkdir(exist_ok=True)
            with open(r.out / "logs" / f"r{t}.err", "a") as f:
                f.write(traceback.format_exc() + "\n")
            if infra >= MAX_CONSECUTIVE_INFRA:
                return "infra_failures"
    return "max_rounds"


def merged_usage(llms: Sequence[LLM]) -> dict:
    """Per-role usage summed over distinct backends (+ ``_total``)."""
    by: dict[str, Usage] = {}
    for l in llms:
        for role, u in l.meter.by_role.items():
            by[role] = by.get(role, Usage()) + u
    out = {k: v.to_dict() for k, v in by.items()}
    out["_total"] = sum(by.values(), Usage()).to_dict()
    return out


def result(r: RRSIRun, stop_reason: str = "") -> ImprovementResult:
    """The :class:`rsi.core.ImprovementResult` of a (possibly resumed) run."""
    fr = r.frontier.load()
    best = r.store.get(fr["incumbent"]["artifact_id"])
    rows = [{"t": 0, "S": fr["trajectory"][0]["S"], "C": fr["trajectory"][0]["C"], "event": "baseline"}]
    for x in fr["trajectory"][1:]:
        summ = read_json(r.out / f"r{x['t'] - 1}" / "summary.json", {})
        rows.append({"t": x["t"], "S": x["S"], "C": x["C"], "S_star": summ.get("S_star"),
                     "b_t": summ.get("b_t"), "sigma_t": summ.get("sigma_t"), "winner": summ.get("winner"),
                     "n_screened": summ.get("n_screened"), "gate_failures": summ.get("gate_failures"),
                     "decisions": [{k: d.get(k) for k in ("variant", "admissible", "S", "delta_S", "delta_C",
                                                           "novelty", "reason")}
                                   for d in summ.get("decisions", [])]})
    critic = r.critic
    has_delta = r.cfg.delta is not None or (r.out / "calibration.json").exists()
    meta = {"frontier": {k: fr[k] for k in ("incumbent", "S_star")},
            "calibration": read_json(r.out / "calibration.json"), "delta": r.delta() if has_delta else None,
            "switches": r.sw.to_json(), "config": r.cfg.dump(), "n_rollouts": r.measurer.n_rollouts,
            "spend": r.spend.summary(), "analyst_mode": r.analyst.mode, "K": r.tax.K, "K_str": r.tax.K_str,
            "critic": None if critic is None else {"reviews": critic.n_reviews,
                                                   "precheck_rejects": critic.n_precheck_rejects,
                                                   "llm_rejects": critic.n_llm_rejects}}
    method = "rrsi" if r.sw.name == "full" else f"rrsi[{r.sw.name}]"
    return ImprovementResult(method=method, baseline=r.seed_artifact, best=best, ledger=r.ledger, trajectory=rows,
                             usage=merged_usage(r.llms()), stop_reason=stop_reason, out_dir=str(r.out), meta=meta)


def run(domain: Domain, seed_artifact: Artifact, *, llm_task: Optional[LLM] = None, llm_propose: Optional[LLM] = None,
        config: Optional[Config] = None, out_dir: Optional[str | Path] = None, llm_critic: Optional[LLM] = None,
        llm_analyst: Optional[LLM] = None, editor: Optional[Editor] = None,
        switches: Optional[RegularizerSwitches] = None, guards: Sequence[Gate] = (), budget: Optional[Budget] = None,
        constitution: Optional[tuple[str, str]] = None, critic_patterns: Sequence[tuple[str, str]] = (),
        hooks: Optional[dict[str, Callable]] = None, verbose: bool = False) -> ImprovementResult:
    """Run RRSI on any :class:`rsi.core.Domain` from H_0; ``out_dir`` makes the run resumable and traced."""
    cfg = config or Config()
    if out_dir is None and cfg.trace:
        cfg = cfg.replace(trace=False)          # without a caller-chosen out_dir the trace is off
    out = Path(out_dir) if out_dir else Path(tempfile.mkdtemp(prefix="rrsi_"))
    r = RRSIRun(domain, seed_artifact, out_dir=out, llm_task=llm_task, llm_propose=llm_propose, llm_critic=llm_critic,
                llm_analyst=llm_analyst, editor=editor, config=cfg, switches=switches, guards=guards,
                constitution=constitution, critic_patterns=critic_patterns, hooks=hooks, verbose=verbose)
    return result(r, drive(r, cfg.T, budget=budget))
