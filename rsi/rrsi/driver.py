"""Sequential driver and the public entry point (mirrors ``rrsi/driver.py`` + ``rrsi.py run``).

:func:`drive` runs baseline + calibration if missing, then rounds ``start..T-1``:

* a round is *settled* once the frontier trajectory has an entry for t+1 (resume rule);
* a file named ``STOP`` in the run directory stops the driver at the next round boundary;
* consecutive infrastructure failures (a round raising) stop the driver after
  ``MAX_CONSECUTIVE_INFRA = 3`` so a broken environment cannot burn the whole budget.
  (The code re-launches the next round; we retry the same round, which is resume-safe.)

:func:`run` is the package's uniform entry point returning :class:`rsi.core.ImprovementResult`.
"""
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
from ..core.run import Budget, ImprovementResult, usd_of
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
    stop = _drive(r, T, start, budget, resumed)
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
            why = budget.exhausted(rounds=r.frontier.settled_rounds(), rollouts=r.measurer.n_rollouts,
                                   usd=usd_of(*_llms(r)))
            if why:
                return why
        if r.frontier.settled_rounds() >= t + 1:
            t += 1
            continue
        try:
            r.round(t)
            infra = 0
            t += 1
        except Exception as e:  # noqa: BLE001 - counted as an infrastructure failure
            infra += 1
            r.log(f"round {t} did not settle ({infra}/{MAX_CONSECUTIVE_INFRA}): {e!r}")
            (r.out / "logs").mkdir(exist_ok=True)
            with open(r.out / "logs" / f"r{t}.err", "a") as f:
                f.write(traceback.format_exc() + "\n")
            if infra >= MAX_CONSECUTIVE_INFRA:
                return "infra_failures"
    return "max_rounds"


def _llms(r: RRSIRun) -> list[LLM]:
    out: list[LLM] = []
    for l in (r.llm_task, r.llm_propose, r.llm_critic, r.llm_analyst):
        if l is not None and all(l is not x for x in out):
            out.append(l)
    return out


def merged_usage(llms: Sequence[LLM]) -> dict:
    """Per-role usage summed over distinct backends (+ ``_total``)."""
    by: dict[str, Usage] = {}
    for l in llms:
        for role, u in l.meter.by_role.items():
            by[role] = by.get(role, Usage()) + u
    out = {k: v.to_dict() for k, v in by.items()}
    tot = Usage()
    for v in by.values():
        tot = tot + v
    out["_total"] = tot.to_dict()
    return out


def result(r: RRSIRun, stop_reason: str = "") -> ImprovementResult:
    """Assemble the :class:`rsi.core.ImprovementResult` of a (possibly resumed) run."""
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
    meta = {"frontier": {k: fr[k] for k in ("incumbent", "S_star")}, "calibration": read_json(r.out / "calibration.json"),
            "delta": r.delta() if (r.cfg.delta is not None or (r.out / "calibration.json").exists()) else None,
            "switches": r.sw.to_json(), "config": r.cfg.dump(), "n_rollouts": r.measurer.n_rollouts,
            "K": r.tax.K, "K_str": r.tax.K_str,
            "critic": None if critic is None else {"reviews": critic.n_reviews, "precheck_rejects": critic.n_precheck_rejects,
                                                   "llm_rejects": critic.n_llm_rejects}}
    method = "rrsi" if r.sw.name == "full" else f"rrsi[{r.sw.name}]"
    return ImprovementResult(method=method, baseline=r.seed_artifact, best=best, ledger=r.ledger, trajectory=rows,
                             usage=merged_usage(_llms(r)), stop_reason=stop_reason, out_dir=str(r.out), meta=meta)


def run(domain: Domain, seed_artifact: Artifact, *, llm_task: Optional[LLM] = None, llm_propose: Optional[LLM] = None,
        config: Optional[Config] = None, out_dir: Optional[str | Path] = None, llm_critic: Optional[LLM] = None,
        llm_analyst: Optional[LLM] = None, editor: Optional[Editor] = None,
        switches: Optional[RegularizerSwitches] = None, guards: Sequence[Gate] = (), budget: Optional[Budget] = None,
        constitution: Optional[tuple[str, str]] = None, critic_patterns: Sequence[tuple[str, str]] = (),
        hooks: Optional[dict[str, Callable]] = None, verbose: bool = False) -> ImprovementResult:
    """Run RRSI on any :class:`rsi.core.Domain` from a seed :class:`rsi.core.Artifact`.

    Parameters
    ----------
    domain, seed_artifact:
        the problem (tasks + locked grader, optional ``components`` taxonomy) and H_0.
    llm_task:
        the frozen model the artifact runs with (``None`` for simulated domains).
    llm_propose:
        the proposer backend (role ``proposer``); also the critic unless ``llm_critic`` is
        given, and the analyst when ``config.analyst == "llm"``.
    config:
        :class:`~rsi.rrsi.config.Config` (defaults = the paper's; ``Config.preset(...)``).
    out_dir:
        run directory (resumable: calling ``run`` again on the same ``out_dir`` continues it). When
        given, the run also writes a per-iteration ``trace.jsonl`` (``Config.trace``; render it with
        :func:`rsi.trace.inspect`) and, for domains with sealed holdout / ood splits, a write-only
        shadow monitor scores every new incumbent there (``Config.shadow_monitor``); see
        :mod:`rsi.rrsi.tracing`. Without ``out_dir`` a temporary directory is used and tracing is off.
    editor:
        any :class:`rsi.core.Editor` (default :class:`~rsi.rrsi.propose.RRSIRewriteEditor`).
    switches:
        :class:`~rsi.rrsi.switches.RegularizerSwitches` for ablations (default: full RRSI).
    guards:
        extra non-compensatory gates (e.g. ``rsi.core.MetricGuard("valid_rate", 0.03)``).
    budget:
        optional :class:`rsi.core.Budget` (usd / rollouts / wall / STOP dir).

    Returns
    -------
    ImprovementResult with ``best`` = the final incumbent H_T, ``trajectory`` = one row per
    settled round, per-role LLM usage, and ``meta`` (delta, calibration, switches, ...).
    """
    cfg = config or Config()
    if out_dir is None and cfg.trace:
        cfg = cfg.replace(trace=False)          # the trace is on by default only for a caller-chosen out_dir
    out = Path(out_dir) if out_dir else Path(tempfile.mkdtemp(prefix="rrsi_"))
    r = RRSIRun(domain, seed_artifact, out_dir=out, llm_task=llm_task, llm_propose=llm_propose, llm_critic=llm_critic,
                llm_analyst=llm_analyst, editor=editor, config=cfg, switches=switches, guards=guards,
                constitution=constitution, critic_patterns=critic_patterns, hooks=hooks, verbose=verbose)
    stop = drive(r, cfg.T, budget=budget)
    return result(r, stop)
