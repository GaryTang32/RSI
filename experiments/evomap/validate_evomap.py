"""From-scratch, fully traced validation runs of the gene loop on the katas (written to validation/evomap/).
Run: python experiments/evomap/validate_evomap.py offline|live [--max-usd 2.0] [--name live_katas]
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from _common import KATA_SAFE_HINT, ROOT

from rsi.core import Budget, CachedLLM, ClaudeCLI, Ledger
from rsi.domains.katas import KataMockProposer, KatasDomain, KataSimSolver, seed_harness
from rsi.evomap import Config, SafeHub, TaskBank, evaluate_library, read_library, render_gene, run
from rsi.trace import inspect, load_trace

VAL = ROOT / "validation" / "evomap"
SPLITS = ("evolve", "holdout")


def fresh(path: Path) -> Path:
    if path.exists() and any(path.iterdir()):
        raise SystemExit(f"{path} exists and is not empty: refusing to resume a validation run")
    path.mkdir(parents=True, exist_ok=True)
    return path


def audit_hub(dom, harness, reference, base: Path, k: int) -> SafeHub:
    """A SafeHub whose hidden bank is the sealed ``test`` split."""
    return SafeHub(TaskBank(dom, harness, reference, split="test", n=24, n_off=k, k=k), seed=0,
                   ledger=Ledger(base / "hub_ledger.jsonl"))


def hub_summary(hub: SafeHub) -> list[dict]:
    out = []
    for aid in hub.order:
        r = hub.records[aid]
        rep = r.hub_report or {}
        out.append({"asset_id": aid[:24], "gene": r.gene.id, "author": r.author, "status": r.status,
                    "reasons": r.reasons, **{k: rep.get(k) for k in ("U", "U_LCB", "delta", "R", "n", "n_off")},
                    "adoptions": r.adoptions})
    return out


def agent_summary(res, rep) -> dict:
    lib = read_library(res.best)
    return {"stop_reason": res.stop_reason, "trajectory": res.trajectory,
            "genes": {g.id: render_gene(g) for g in lib}, "gene_validation": {g.id: g.validation for g in lib},
            "audit_ok": res.meta["audit"].get("ok"),
            **{k: res.meta[k] for k in ("rollouts", "proposer_calls", "quarantined", "quarantine_rejected")},
            "trace_overhead_usd": res.meta.get("trace_overhead_usd"), "usage": res.usage,
            "transfer": {s: {a: {"S": v["S"], "vs_reference": v.get("vs_reference")} for a, v in row.items()}
                         for s, row in rep["splits"].items()}}


def monitor_rows(run_dir: Path) -> list[dict]:
    return [{"round": e["round"], **e["data"]} for e in load_trace(run_dir) if e["kind"] == "monitor"]


def write_summary(base: Path, out: dict, show: tuple) -> None:
    (base / "summary.json").write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps({k: out[k] for k in show}, indent=1, default=str)[:4000])


def offline() -> None:
    """agent0 (solver + mock writer) publishes to the hub; agent1 (no writer) quarantines and re-tests its genes."""
    base = fresh(VAL / "offline_katas")
    dom = KatasDomain(scheme="audit")
    h = seed_harness()
    hub = audit_hub(dom, h, KataSimSolver(0.0, name="hub-reference"), base, 4)
    t0 = time.time()
    solver_a, writer = KataSimSolver(0.0, name="kata-sim-a"), KataMockProposer(0.7)
    cfg_a = Config(cycles=12, mode="safe", seed=0, validation_hint=KATA_SAFE_HINT, quarantine_min_tasks=1,
                   trace_baseline_k=4, monitor_k=4)
    res_a = run(dom, h, llm_task=solver_a, llm_propose=writer, config=cfg_a, hub=hub, name="agent0",
                out_dir=base / "agent0")
    solver_b = KataSimSolver(0.0, name="kata-sim-b")
    cfg_b = Config(cycles=8, mode="safe", seed=1, quarantine_min_tasks=1, trace_baseline_k=4, monitor_k=4)
    res_b = run(dom, h, llm_task=solver_b, llm_propose=None, config=cfg_b, hub=hub, name="agent1",
                out_dir=base / "agent1")
    rep_a = evaluate_library(dom, solver_a, res_a, splits=SPLITS, k=4, workers=2)
    rep_b = evaluate_library(dom, solver_b, res_b, splits=SPLITS, k=4, workers=2)
    for d in ("agent0", "agent1"):
        inspect(base / d)
    out = {"run": "offline_katas", "wall_s": round(time.time() - t0, 1), "hub": hub_summary(hub),
           "hub_metrics": {k: v for k, v in hub.metrics().items() if not isinstance(v, (list, dict))},
           "agent0": agent_summary(res_a, rep_a), "agent1": agent_summary(res_b, rep_b),
           "monitor": {"agent0": monitor_rows(base / "agent0"), "agent1": monitor_rows(base / "agent1")}}
    write_summary(base, out, ("wall_s", "hub"))
    for a in ("agent0", "agent1"):
        print(a, out[a]["transfer"])


def live(name: str, max_usd: float) -> None:
    """ClaudeCLI('haiku') as solver, gene writer and hub reference executor, with a fresh LLM cache."""
    base = fresh(VAL / name)
    cache = fresh(VAL / f".cache_{name}")
    llm = CachedLLM(ClaudeCLI("haiku", timeout_s=240), cache)
    dom = KatasDomain(scheme="audit")
    h = seed_harness()
    hub = audit_hub(dom, h, llm, base, 2)
    t0 = time.time()
    cfg = Config(cycles=8, mode="safe", seed=0, validation_hint=KATA_SAFE_HINT, quarantine_min_tasks=1,
                 trace_baseline_k=2, monitor_k=2, monitor_workers=3)
    res = run(dom, h, llm_task=llm, llm_propose=llm, config=cfg, hub=hub, name="agent0", out_dir=base / "agent0",
              budget=Budget(max_rounds=8, max_usd=max_usd))
    loop_s = time.time() - t0
    rep = evaluate_library(dom, llm, res, splits=SPLITS, k=2, workers=3)
    inspect(base / "agent0")
    out = {"run": name, "wall_s": round(time.time() - t0, 1), "loop_wall_s": round(loop_s, 1),
           "hub": hub_summary(hub), "agent0": agent_summary(res, rep), "monitor": monitor_rows(base / "agent0"),
           "llm_usage_all": llm.meter.snapshot(), "cache": {"hits": llm.hits, "misses": getattr(llm, "misses", None)}}
    write_summary(base, out, ("wall_s", "hub", "llm_usage_all"))
    print("transfer", out["agent0"]["transfer"])


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("which", choices=["offline", "live"])
    ap.add_argument("--max-usd", type=float, default=2.0)
    ap.add_argument("--name", default="live_katas", help="live run directory under validation/evomap/")
    a = ap.parse_args()
    if a.which == "offline":
        offline()
    else:
        live(a.name, a.max_usd)


if __name__ == "__main__":
    main()
