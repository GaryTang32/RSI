"""Live smoke run (one small end-to-end run with a real LLM; cached; well under $1).

    python experiments/metaharness-solpi/live_smoke.py [--llm claude:haiku] [--parts metaharness,epr,lineage]
                                                      [--cache-dir DIR]
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import RESULTS, candidate_rows, fresh_dir, live_llm, parse_args, save  # noqa: E402

from rsi.core import CachedLLM, ClaudeCLI, Evaluator  # noqa: E402
from rsi.domains.agentworld import MockAgentLLM, make_domain as make_world  # noqa: E402
from rsi.domains.memoclassify import make_domain  # noqa: E402
from rsi.metaharness import Config as MHConfig, run as mh_run  # noqa: E402
from rsi.solpi import (DualGate, GateSpec, Idea, LLMMechanismProposer, LLMReducer, Lineage, SmokeReviewer,  # noqa
                       metrics_from_eval)

ALL_PARTS = {"metaharness", "epr", "lineage"}


def mh_part(llm) -> dict:
    """Meta-Harness with a live RewriteProposer on MemoClassify (scale 0.4, 2 iterations x k = 2)."""
    dom = make_domain(seed=0, scale=0.4)
    res = mh_run(dom, dom.seed_artifact("fewshot_all"), llm_task=dom.make_model("A"), llm_propose=llm,
                 config=MHConfig(iterations=2, k=2, seed=0), out_dir=fresh_dir("live", "metaharness"),
                 baselines=dom.baselines())
    st = res.loop.store
    cands = candidate_rows(st, ("status", "reason", "base_system", "hypothesis"))
    sessions = []
    for d in sorted(st.sessions_dir().glob("iter*")):
        m = json.loads((d / "meta.json").read_text())
        sessions.append({"iteration": m["iteration"], "files_read_by_kind": m["files_read_by_kind"],
                         "view_files": m["view_files"], "view_chars": m["view_chars"], "read_chars": m["read_chars"],
                         "trace_files_rendered": sum("/traces/" in p for p in m["files_read"]),
                         "reports_written": m.get("reports_written", []),
                         "usage": m["usage"], "error": m["error"]})
    print("[metaharness]", res.meta["best_system"], {c["name"]: (c["status"], c["search"]) for c in cands})
    return {"candidates": cands, "best": res.meta["best_system"], "frontier": res.meta["frontier"]["_pareto"],
            "test": {k: v["score"] for k, v in res.meta["final"]["splits"]["test"]["results"].items()},
            "proposer_usage": llm.meter.snapshot(), "iterations": res.trajectory, "sessions": sessions,
            "reports": sorted(p.name for p in st.reports_dir().glob("*.md")),
            "generated_with": "post-audit code (renderer with traces, full traces, all-calls context, skill Step 0/2, "
                              "objective statement), fresh cache"}


def epr_part(llm) -> dict:
    """SoL-Pi's Evidence-Preserving Reducer with a live reducer; every receipt is validated byte-for-byte."""
    from s3_reducer import bench_logs, run_bench
    bench = run_bench(LLMReducer(llm), bench_logs(1))
    bench["usage"] = llm.meter.snapshot()
    print("[epr]", bench["outcomes"], "nonverbatim", bench["nonverbatim"])
    return bench


def lineage_part(llm) -> dict:
    """One SoL-Pi lineage whose mechanism the LLM writes as a code extension, gated on the training screen."""
    world = make_world(seed=0, n_train=2, n_accept=1, n_final=1, n_test=0)
    agent = MockAgentLLM("A")
    ev = Evaluator(world, agent)
    base = world.seed_artifact()
    bm = metrics_from_eval(ev.evaluate(base, "evolve"))
    idea = Idea("L1", "C", "Stop replaying large successful tool outputs in every later request; keep them "
                           "recallable", "", [{}], "none")
    lr = Lineage(idea, evaluator=ev, gate=DualGate(GateSpec()), proposer=LLMMechanismProposer(llm),
                 reviewer=SmokeReviewer(world, agent), base=base, base_metrics=bm, max_iters=2, ralph_max=2).run()
    print("[lineage]", [(it.get("stage"), it.get("outcome"), (it.get("gate") or {}).get("reason"), it.get("error"))
                        for it in lr.iterations])
    return {"frozen": lr.frozen is not None,
            "iterations": [{k: v for k, v in it.items() if k != "metrics"} for it in lr.iterations],
            "code": (lr.frozen.artifact.files if lr.frozen else None), "usage": llm.meter.snapshot()}


def part_usd(p: dict, key: str) -> float:
    u = p.get(key) or {}
    return float(sum(v.get("cost_usd", 0.0) for r, v in u.items() if isinstance(v, dict) and not r.startswith("_")))


def make_verdict(out: dict, spent: dict) -> str:
    pm, pe, pl = (out["parts"].get(k, {}) for k in ("metaharness", "epr", "lineage"))
    mh_c = [c for c in pm.get("candidates", []) if c.get("status") == "evaluated" and c["name"] not in
            ("no_memory", "fewshot_all")]
    fa = next((c["search"] for c in pm.get("candidates", []) if c["name"] == "fewshot_all"), float("nan"))
    best_c = max([c["search"] for c in mh_c] or [float("nan")])
    tr = [s.get("trace_files_rendered", 0) for s in pm.get("sessions", [])]
    return (f"live end-to-end OK: Meta-Harness evaluated {len(mh_c)} haiku-written candidates (best "
            f"{pm.get('best')}; best candidate search {best_c:.3f} vs fewshot_all {fa:.3f}; trace files "
            f"rendered per iteration {tr}; reports {pm.get('reports')}); EPR accepted "
            f"{pe.get('accepted')}/{pe.get('n_logs')} haiku receipts with {pe.get('nonverbatim')} "
            f"non-verbatim quotes; LLM lineage "
            f"{'froze a mechanism' if pl.get('frozen') else 'did not pass the gate'}; "
            f"cost of all parts ${out['total_usd']:.3f} (this run ${sum(spent.values()):.3f})")


def main():
    args = parse_args(__doc__.splitlines()[0], default_seeds=1, extra=lambda ap: (
        ap.add_argument("--parts", default="metaharness,epr,lineage"),
        ap.add_argument("--cache-dir", default=None, help="cache for the Meta-Harness proposer (default: shared)")))
    if not args.live:
        args.llm = "claude:haiku"
        args.live = True
    parts = set(args.parts.split(","))
    path = Path(args.out) if args.out else RESULTS / "live_smoke.json"
    old = json.loads(path.read_text()) if path.exists() else {}
    out = {"llm": args.llm, "parts": dict(old.get("parts", {}))}
    t0 = time.time()
    spent = {}
    for name, part in (("metaharness", mh_part), ("epr", epr_part), ("lineage", lineage_part)):
        if name not in parts:
            continue
        if name == "metaharness" and args.cache_dir:
            _, _, model = args.llm.partition(":")
            llm = CachedLLM(ClaudeCLI(model or "haiku", timeout_s=300.0), args.cache_dir)
        else:
            llm = live_llm(args.llm)
        out["parts"][name] = part(llm)
        spent[name] = llm.meter.total().cost_usd
    out["spend_this_run_usd"] = spent
    out["parts_regenerated"] = sorted(parts)
    out["parts_kept_from"] = old.get("generated_at") if parts != ALL_PARTS else None
    pm, pe, pl = (out["parts"].get(k, {}) for k in ("metaharness", "epr", "lineage"))
    out["total_usd"] = part_usd(pm, "proposer_usage") + part_usd(pe, "usage") + part_usd(pl, "usage")
    out["seconds"] = time.time() - t0
    out["verdict"] = make_verdict(out, spent)
    print(out["verdict"])
    save("live_smoke", out, args.out)


if __name__ == "__main__":
    main()
