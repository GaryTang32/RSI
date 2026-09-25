"""From-scratch, fully traced validation runs of the gene loop (validation/evomap/).

offline  katas (scheme ``audit``: evolve 5, val 5, hub bank 10, holdout 5), KataSimSolver + KataMockProposer,
         safe mode. agent0 runs 12 cycles and publishes to a SafeHub (hidden bank = the sealed ``test`` split);
         agent1 (a second solver, no gene writer) runs 8 cycles, fetches hub genes when nothing local fits,
         quarantines and re-tests them on its own ``val`` tasks, adopts or rejects them and reports back.
live     same domain / hub layout with ClaudeCLI('haiku') as solver, gene writer and hub reference executor,
         8 cycles, then the evolved library vs no library on evolve and the sealed holdout.

Every run starts from the untouched seed harness, a fresh run directory and (live) a fresh LLM cache
(validation/evomap/.cache_<run>); an existing run directory is refused so nothing is resumed by accident.
Writes trace.jsonl + TRACE.md (rsi.trace.inspect) per agent and a summary.json per run.

Run: python experiments/evomap/validate_evomap.py offline|live [--max-usd 2.0]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rsi.core import Budget, CachedLLM, ClaudeCLI, Ledger  # noqa: E402
from rsi.domains.katas import KataMockProposer, KatasDomain, KataSimSolver, seed_harness  # noqa: E402
from rsi.evomap import Config, SafeHub, TaskBank, evaluate_library, run  # noqa: E402
from rsi.evomap.inject import read_library  # noqa: E402
from rsi.evomap.prompts import render_gene  # noqa: E402
from rsi.trace import inspect, load_trace  # noqa: E402

VAL = ROOT / "validation" / "evomap"
SAFE_HINT = (" (it MUST fail before the fix and pass after it, e.g. the public check script). The workspace contains "
             "only solution.py and smoke_test.py. Allowed commands: `python <existing_script>.py` or "
             "`pytest -q <existing_file>.py`; inline code (python -c) and shell syntax are BLOCKED by the command "
             "policy")


def fresh(path: Path) -> Path:
    if path.exists() and any(path.iterdir()):
        raise SystemExit(f"{path} exists and is not empty: refusing to resume a validation run")
    path.mkdir(parents=True, exist_ok=True)
    return path


def usage(llm) -> dict:
    return llm.meter.snapshot()


def hub_summary(hub: SafeHub) -> list[dict]:
    out = []
    for aid in hub.order:
        r = hub.records[aid]
        rep = r.hub_report or {}
        out.append({"asset_id": aid[:24], "gene": r.gene.id, "author": r.author, "status": r.status,
                    "reasons": r.reasons, "U": rep.get("U"), "U_LCB": rep.get("U_LCB"), "delta": rep.get("delta"),
                    "R": rep.get("R"), "n": rep.get("n"), "n_off": rep.get("n_off"),
                    "adoptions": r.adoptions})
    return out


def agent_summary(res, rep) -> dict:
    return {"stop_reason": res.stop_reason, "trajectory": res.trajectory,
            "genes": {g.id: render_gene(g) for g in read_library(res.best)},
            "gene_validation": {g.id: g.validation for g in read_library(res.best)},
            "audit_ok": res.meta["audit"].get("ok"), "rollouts": res.meta["rollouts"],
            "proposer_calls": res.meta["proposer_calls"], "quarantined": res.meta["quarantined"],
            "quarantine_rejected": res.meta["quarantine_rejected"],
            "trace_overhead_usd": res.meta.get("trace_overhead_usd"), "usage": res.usage,
            "transfer": {s: {a: {"S": v["S"], "vs_reference": v.get("vs_reference")} for a, v in row.items()}
                         for s, row in rep["splits"].items()}}


def monitor_rows(run_dir: Path) -> list[dict]:
    return [{"round": e["round"], **e["data"]} for e in load_trace(run_dir) if e["kind"] == "monitor"]


def offline(args) -> None:
    base = fresh(VAL / "offline_katas")
    dom = KatasDomain(scheme="audit")
    h = seed_harness()
    hub = SafeHub(TaskBank(dom, h, KataSimSolver(0.0, name="hub-reference"), split="test", n=24, n_off=4, k=4),
                  seed=0, ledger=Ledger(base / "hub_ledger.jsonl"))
    t0 = time.time()
    solver_a, writer = KataSimSolver(0.0, name="kata-sim-a"), KataMockProposer(0.7)
    cfg_a = Config(cycles=12, mode="safe", seed=0, validation_hint=SAFE_HINT, quarantine_min_tasks=1,
                   trace_baseline_k=4, monitor_k=4)
    res_a = run(dom, h, llm_task=solver_a, llm_propose=writer, config=cfg_a, hub=hub, name="agent0",
                out_dir=base / "agent0")
    solver_b = KataSimSolver(0.0, name="kata-sim-b")
    cfg_b = Config(cycles=8, mode="safe", seed=1, quarantine_min_tasks=1, trace_baseline_k=4, monitor_k=4)
    res_b = run(dom, h, llm_task=solver_b, llm_propose=None, config=cfg_b, hub=hub, name="agent1",
                out_dir=base / "agent1")
    rep_a = evaluate_library(dom, solver_a, res_a, splits=("evolve", "holdout"), k=4, workers=2)
    rep_b = evaluate_library(dom, solver_b, res_b, splits=("evolve", "holdout"), k=4, workers=2)
    for d in ("agent0", "agent1"):
        inspect(base / d)
    out = {"run": "offline_katas", "wall_s": round(time.time() - t0, 1), "hub": hub_summary(hub),
           "hub_metrics": {k: v for k, v in hub.metrics().items() if not isinstance(v, (list, dict))},
           "agent0": agent_summary(res_a, rep_a), "agent1": agent_summary(res_b, rep_b),
           "monitor": {"agent0": monitor_rows(base / "agent0"), "agent1": monitor_rows(base / "agent1")}}
    (base / "summary.json").write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps({k: out[k] for k in ("wall_s", "hub")}, indent=1, default=str)[:4000])
    for a in ("agent0", "agent1"):
        print(a, out[a]["transfer"])


def live(args) -> None:
    base = fresh(VAL / args.name)
    cache = fresh(VAL / f".cache_{args.name}")
    llm = CachedLLM(ClaudeCLI("haiku", timeout_s=240), cache)
    dom = KatasDomain(scheme="audit")
    h = seed_harness()
    hub = SafeHub(TaskBank(dom, h, llm, split="test", n=24, n_off=2, k=2), seed=0,
                  ledger=Ledger(base / "hub_ledger.jsonl"))
    t0 = time.time()
    cfg = Config(cycles=8, mode="safe", seed=0, validation_hint=SAFE_HINT, quarantine_min_tasks=1,
                 trace_baseline_k=2, monitor_k=2, monitor_workers=3)
    res = run(dom, h, llm_task=llm, llm_propose=llm, config=cfg, hub=hub, name="agent0", out_dir=base / "agent0",
              budget=Budget(max_rounds=8, max_usd=args.max_usd))
    loop_s = time.time() - t0
    rep = evaluate_library(dom, llm, res, splits=("evolve", "holdout"), k=2, workers=3)
    inspect(base / "agent0")
    out = {"run": args.name, "wall_s": round(time.time() - t0, 1), "loop_wall_s": round(loop_s, 1),
           "hub": hub_summary(hub), "agent0": agent_summary(res, rep), "monitor": monitor_rows(base / "agent0"),
           "llm_usage_all": usage(llm), "cache": {"hits": llm.hits, "misses": getattr(llm, "misses", None)}}
    (base / "summary.json").write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps({k: out[k] for k in ("wall_s", "hub", "llm_usage_all")}, indent=1, default=str)[:4000])
    print("transfer", out["agent0"]["transfer"])


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("which", choices=["offline", "live"])
    ap.add_argument("--max-usd", type=float, default=2.0)
    ap.add_argument("--name", default="live_katas", help="live run directory under validation/evomap/")
    a = ap.parse_args()
    offline(a) if a.which == "offline" else live(a)
