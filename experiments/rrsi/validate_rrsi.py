"""From-scratch validation runs of RRSI with a full per-iteration trace.

Each run starts from the UNTOUCHED seed artifact of its domain, in a fresh run directory
under ``validation/rrsi/<run>/`` (and, for live runs, a fresh LLM cache
``validation/rrsi/.cache_<run>``), traces every step (``trace.jsonl`` -> ``TRACE.md``),
audits every step against the paper's formulas (``audit.json``, :mod:`rsi.rrsi.audit`),
and scores H_0 vs the final harness on evolve / holdout / ood with
:func:`rsi.core.transfer_report` (``summary.json``)::

    python experiments/rrsi/validate_rrsi.py offline_agentqa        # SimModel + AgentQAMockLLM, T=8 m=2 k=2
    python experiments/rrsi/validate_rrsi.py offline_harnessworld   # HarnessWorld + its mock LLM, T=10
    python experiments/rrsi/validate_rrsi.py live_agentqa --max-usd 2.0   # haiku everywhere, T=4 m=2 k=1

Refuses to reuse an existing run directory or cache (``--resume`` continues an interrupted
run in the same directories).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from rsi.core import Budget, CachedLLM, ClaudeCLI, transfer_report  # noqa: E402
from rsi.rrsi import Config, run  # noqa: E402
from rsi.rrsi.audit import audit_run  # noqa: E402
from rsi.trace import inspect, load_trace  # noqa: E402

VAL = Path(os.environ.get("RRSI_VALIDATION_DIR") or ROOT / "validation" / "rrsi")   # env override: dry runs


def _fresh(path: Path, resume: bool) -> Path:
    if path.exists() and not resume:
        raise SystemExit(f"{path} exists: refusing to reuse it (delete it, or pass --resume)")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _usage_summary(res, extra_llms=()) -> dict:
    tot = res.usage.get("_total", {})
    return {"loop_usage": res.usage, "loop_usd": round(tot.get("cost_usd", 0.0), 4),
            "loop_calls": tot.get("calls", 0)}


def _round_table(out: Path) -> list[dict]:
    """Compact per-round view read back from the trace (for RUNS.md)."""
    rows = []
    ev = load_trace(out)
    for t in sorted({e["round"] for e in ev if e["round"] is not None}):
        es = [e for e in ev if e["round"] == t]
        rs = next((e["data"] for e in es if e["kind"] == "round_start"), {})
        dec = next((e["data"] for e in es if e["kind"] == "decision"), {})
        cands = []
        for g in [e["data"] for e in es if e["kind"] == "gate"]:
            c = g["candidate"]
            props = [e["data"] for e in es if e["kind"] == "proposal" and e["data"]["candidate"] == c]
            crit = [e["data"] for e in es if e["kind"] == "critic" and e["data"]["candidate"] == c]
            tag = next((e["data"] for e in es if e["kind"] == "note" and e["data"].get("stage") == "tagging"
                        and e["data"]["candidate"] == c), {})
            fin = [p for p in props if str(p.get("outcome", "")).startswith("done")]
            m = g.get("math") or {}
            cands.append({"candidate": c, "claimed": (fin[-1] if fin else (props[-1] if props else {})).get("change"),
                          "components": [r.get("normalized") for r in tag.get("edits") or []],
                          "files": tag.get("files_in_diff"), "proposer_calls": len(props),
                          "critic": [("accept" if x.get("accept") else "reject") + f"({x.get('stage')})" for x in crit],
                          "admissible": g.get("accept"), "reason": g.get("reason"),
                          "S": m.get("S_prime"), "dS": m.get("dS"), "dC": m.get("dC"), "nu": m.get("nu"),
                          "branch": m.get("branch"), "gate_failure": m.get("gate_failure")})
        mon = [e["data"] for e in es if e["kind"] == "monitor"]
        rows.append({"t": t, "b_t": rs.get("b_t"), "sigma_t": rs.get("sigma_t"), "S_t": rs.get("incumbent", {}).get("S"),
                     "S_star": rs.get("S_star"), "delta": rs.get("delta"), "untried": rs.get("untried_U_t"),
                     "reserved": rs.get("reserved_variants"), "prune": [p["component"] for p in rs.get("prune_B_t") or []],
                     "candidates": cands, "kept": dec.get("kept"), "why": dec.get("why"),
                     "monitor": {s: v.get("S") for m_ in mon for s, v in m_.get("sealed", {}).items()}})
    return rows


def finish(name: str, out: Path, dom, res, task_llm, k_transfer: int, setup: dict, t0: float,
           extra: dict | None = None) -> dict:
    md = inspect(out)
    audit = audit_run(out, domain=dom)
    rep = transfer_report(dom, task_llm, {"H0": res.baseline, "final": res.best}, k=k_transfer, workers=4)
    expected = None
    if hasattr(dom, "expected"):
        expected = {s: {a: dom.expected(x, s) for a, x in (("H0", res.baseline), ("final", res.best))}
                    for s in ("evolve", "holdout", "ood")}
    summ = {"run": name, "setup": setup, "stop_reason": res.stop_reason, "rounds": len(res.trajectory) - 1,
            "trajectory": [{k: r.get(k) for k in ("t", "S", "C", "S_star", "b_t", "sigma_t", "winner")}
                           for r in res.trajectory],
            "seed_vs_final": {s: {a: {"S": v["S"], "C": v["C"]} for a, v in row.items()}
                              for s, row in rep["splits"].items()},
            "paired_final_vs_H0": {s: row["final"]["vs_reference"] for s, row in rep["splits"].items()},
            "analytic_expected": expected, "delta": res.meta.get("delta"), "calibration": res.meta.get("calibration"),
            "usage": _usage_summary(res), "critic": res.meta.get("critic"), "n_rollouts": res.meta.get("n_rollouts"),
            "audit_summary": audit["summary"],
            "audit_failures": [r for r in audit["checks"] if r["status"] in ("fail", "unverifiable")],
            "rounds_table": _round_table(out), "final_files": sorted(res.best.files), "trace_md": md,
            "wall_s": round(time.time() - t0, 1), **(extra or {})}
    (out / "summary.json").write_text(json.dumps(summ, indent=1, default=str))
    res.best.to_dir(out / "final_harness", clean=True)
    print(json.dumps({k: summ[k] for k in ("run", "stop_reason", "rounds", "seed_vs_final", "audit_summary",
                                            "usage", "wall_s")}, indent=1, default=str)[:4000])
    return summ


def offline_agentqa(a) -> dict:
    from rsi.domains.agentqa import AgentQADomain, SimModel, make_suite
    from rsi.rrsi.mocks import AgentQAMockLLM
    name = "offline_agentqa"
    out = _fresh(VAL / name, a.resume)
    suite = make_suite(seed=0)
    dom = AgentQADomain(suite)
    task, prop = SimModel(suite), AgentQAMockLLM()
    cfg = Config(T=8, m=2, k=2, workers=2, seed=0)
    t0 = time.time()
    res = run(dom, AgentQADomain.seed_artifact(), llm_task=task, llm_propose=prop, config=cfg, out_dir=out,
              verbose=True)
    setup = {"domain": "agentqa make_suite(seed=0) (20 evolve numeric, 20 holdout numeric, 24 ood in 4 unseen "
                       "families)", "seed_artifact": "AgentQADomain.seed_artifact() (one direct model call)",
             "task_model": task.name, "proposer_and_critic": prop.name, "analyst": "heuristic (no analyst LLM)",
             "config": {"T": 8, "m": 2, "k": 2}, "llm_cache": "none (deterministic mocks, nothing to cache)"}
    return finish(name, out, dom, res, task, 2, setup, t0)


def offline_harnessworld(a) -> dict:
    from rsi.domains.harnessworld import HarnessWorldMockLLM, make_domain
    name = "offline_harnessworld"
    out = _fresh(VAL / name, a.resume)
    dom = make_domain(seed=0)
    llm = HarnessWorldMockLLM(dom.world)
    cfg = Config(T=10, m=2, k=2, workers=2, seed=0, analyst="llm")
    t0 = time.time()
    res = run(dom, dom.seed_artifact(), llm_propose=llm, config=cfg, out_dir=out, verbose=True)
    setup = {"domain": "harnessworld make_domain(seed=0) (100 evolve / 100 holdout / 100 ood tasks, 16 rubric "
                       "criteria each)", "seed_artifact": "HarnessWorldDomain.seed_artifact()",
             "task_model": "simulated frozen policy (STRONG) inside the domain",
             "proposer_critic_digester_analyst": llm.name, "analyst": "LLM path (mock digesters + aggregator)",
             "config": {"T": 10, "m": 2, "k": 2}, "llm_cache": "none (deterministic mocks)"}
    return finish(name, out, dom, res, None, 2, setup, t0)


def live_agentqa(a) -> dict:
    from rsi.domains.agentqa import AgentQADomain, make_suite
    name = "live_agentqa"
    out = _fresh(VAL / name, a.resume)
    cache = _fresh(VAL / f".cache_{name}", a.resume)
    suite = make_suite(n_evolve=12, n_holdout=12, n_ood_per_family=3, seed=0)
    dom = AgentQADomain(suite)
    llm = CachedLLM(ClaudeCLI("haiku", timeout_s=400), cache)            # proposer, critic, analyst
    task = CachedLLM(ClaudeCLI("haiku", timeout_s=200), cache)           # frozen task model (separate meter)
    cfg = Config(T=4, m=2, k=1, workers=4, seed=0, analyst="llm", calibration_repeats=3, max_digests=4,
                 n_fail_traces=6, n_success_traces=3, repair_rounds=2, max_done_bounces=2)
    budget = Budget(max_usd=a.max_usd, max_wall_s=a.max_wall_min * 60)
    t0 = time.time()
    res = run(dom, AgentQADomain.seed_artifact(), llm_task=task, llm_propose=llm, llm_analyst=llm, config=cfg,
              out_dir=out, budget=budget, verbose=True)
    task_before = task.meter.total().cost_usd
    setup = {"domain": "agentqa make_suite(n_evolve=12, n_holdout=12, n_ood_per_family=3, seed=0)",
             "seed_artifact": "AgentQADomain.seed_artifact() ('You are a helpful assistant.', one direct call)",
             "task_model": "CachedLLM(ClaudeCLI('haiku')) - frozen", "proposer_critic_analyst":
             "CachedLLM(ClaudeCLI('haiku'))", "cache": str(cache) + " (fresh)",
             "config": {"T": 4, "m": 2, "k": 1, "calibration_repeats": 3, "analyst": "llm", "max_digests": 4,
                        "n_fail_traces": 6, "n_success_traces": 3, "repair_rounds": 2, "max_done_bounces": 2},
             "budget": {"max_usd": a.max_usd, "max_wall_min": a.max_wall_min}}
    mon = None
    try:
        from rsi.rrsi.loop import RRSIRun  # noqa: F401
        mon_ev = [e for e in load_trace(out) if e["kind"] == "run_end"]
        mon = mon_ev[-1]["data"].get("shadow_monitor_usage") if mon_ev else None
    except Exception:  # noqa: BLE001
        pass
    summ = finish(name, out, dom, res, task, 1, setup, t0, extra={"shadow_monitor_usage": mon})
    transfer_usd = task.meter.total().cost_usd - task_before
    summ["usage"]["transfer_report_usd"] = round(transfer_usd, 4)
    summ["usage"]["shadow_monitor_usd"] = round(((mon or {}).get("_total") or {}).get("cost_usd", 0.0), 4)
    summ["usage"]["total_live_usd"] = round(summ["usage"]["loop_usd"] + transfer_usd
                                            + summ["usage"]["shadow_monitor_usd"], 4)
    summ["cache_hits_misses"] = {"search": [llm.hits, llm.misses], "task": [task.hits, task.misses]}
    (out / "summary.json").write_text(json.dumps(summ, indent=1, default=str))
    print(json.dumps(summ["usage"], indent=1, default=str)[:3000])
    return summ


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run", choices=["offline_agentqa", "offline_harnessworld", "live_agentqa"])
    ap.add_argument("--resume", action="store_true", help="continue an interrupted run in the same directories")
    ap.add_argument("--max-usd", type=float, default=2.0)
    ap.add_argument("--max-wall-min", type=float, default=35.0)
    a = ap.parse_args()
    {"offline_agentqa": offline_agentqa, "offline_harnessworld": offline_harnessworld,
     "live_agentqa": live_agentqa}[a.run](a)


if __name__ == "__main__":
    main()
