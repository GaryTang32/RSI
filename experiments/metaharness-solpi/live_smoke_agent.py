"""Live smoke of the paper's proposer type: a coding agent (``claude -p`` with haiku) over the history directory.

    python experiments/metaharness-solpi/live_smoke_agent.py [--llm claude:haiku] [--iterations 2] [--max-budget-usd X]
    python experiments/metaharness-solpi/live_smoke_agent.py --reanalyze   # $0: re-score the stored transcripts
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import RESULTS, RUNS, candidate_rows, fresh_dir, parse_args, save  # noqa: E402

from rsi.core import ClaudeCLI  # noqa: E402
from rsi.domains.memoclassify import make_domain  # noqa: E402
from rsi.metaharness import AgentProposer, Config, ExperienceStore, run  # noqa: E402
from rsi.metaharness.proposer import files_touched, prototype_activity  # noqa: E402

BASELINES = ("no_memory", "fewshot_all")


def trace_candidates(paths: list[str]) -> list[str]:
    return sorted({p.split("/")[1] for p in paths if "/traces/" in p})


def reanalyze(out: dict, store: ExperienceStore) -> dict:
    """Recompute every session's read/search/prototype accounting from its stored tool calls ($0)."""
    view = store.view("full")
    for sess in out["sessions"]:
        m = json.loads((store.sessions_dir() / f"iter{sess['iteration']:03d}" / "meta.json").read_text())
        calls = (m.get("proposer_meta") or {}).get("tool_calls", [])
        read, searched = files_touched(calls, view)
        sess.update(files_read=read, n_files_read=len(read), files_searched=searched,
                    trace_candidates_read=trace_candidates(read),
                    files_read_by_kind={
                        "code": sum("/src/" in p for p in read), "traces": sum("/traces/" in p for p in read),
                        "scores": sum(p.endswith("scores.json") for p in read),
                        "other": sum(not p.startswith("candidates/") for p in read)},
                    read_chars=sum(len(view.get(p, "")) for p in read), prototype=prototype_activity(calls),
                    accounting="recomputed from the stored tool calls")
    return out


def verdict(out: dict) -> str:
    cands, sessions = out["candidates"], out["sessions"]
    evald = [c for c in cands if c["status"] == "evaluated" and c["name"] not in BASELINES]
    fa = next(c["search"] for c in cands if c["name"] == "fewshot_all")
    n_prop = sum(c["name"] not in BASELINES for c in cands)
    return (f"coding-agent proposer ran live: {len(evald)} of {n_prop} candidates evaluated (best search "
            f"{max([c['search'] for c in evald] or [float('nan')]):.3f} vs fewshot_all {fa:.3f}); files read per "
            f"iteration {[s['n_files_read'] for s in sessions]} of {[s['view_files'] for s in sessions]} in the "
            f"view, traces of {[len(s['trace_candidates_read']) for s in sessions]} candidates; tools "
            f"{[s['tool_summary'] for s in sessions]}; Python runs {[s['prototype']['python_runs'] for s in sessions]}"
            f", prototype scripts {[len(s['prototype']['scripts_written']) for s in sessions]}; reports "
            f"{out['reports']}; cost ${out['total_usd']:.3f}")


def session_row(m: dict) -> dict:
    pm = m.get("proposer_meta") or {}
    return {"iteration": m["iteration"], "error": m["error"], "usage": m["usage"],
            "seconds": m["seconds"], "view_files": m["view_files"], "view_chars": m["view_chars"],
            "files_read": m["files_read"], "n_files_read": m["n_files_read"],
            "files_read_by_kind": m["files_read_by_kind"], "read_chars": m["read_chars"],
            "trace_candidates_read": trace_candidates(m["files_read"]),
            "files_searched": pm.get("files_searched"), "tool_summary": pm.get("tool_summary"),
            "prototype_files": pm.get("prototype_files"), "bash_commands": [
                c["input"].get("command") for c in pm.get("tool_calls", []) if c["name"] == "Bash"][:40],
            "transcript_available": pm.get("transcript_available"),
            "reports_written": m.get("reports_written")}


def main():
    args = parse_args(__doc__.splitlines()[0], default_seeds=1, extra=lambda ap: (
        ap.add_argument("--iterations", type=int, default=2),
        ap.add_argument("--max-budget-usd", type=float, default=0.6),
        ap.add_argument("--timeout", type=float, default=900.0),
        ap.add_argument("--reanalyze", action="store_true")))
    if args.reanalyze:
        path = RESULTS / "live_smoke_agent.json"
        out = reanalyze(json.loads(path.read_text()), ExperienceStore(RUNS / "live" / "metaharness_agent" / "store"))
        out["verdict"] = verdict(out)
        print(out["verdict"])
        save("live_smoke_agent", out, args.out)
        return
    if not args.live:
        args.llm = "claude:haiku"
    _, _, model = args.llm.partition(":")
    cli = ClaudeCLI(model or "haiku", timeout_s=args.timeout, retries=1)
    # prototype=True: Read/Edit/Write/Glob/Grep + Bash restricted to python3 and read-only commands
    prop = AgentProposer(cli, timeout_s=args.timeout, prototype=True, max_budget_usd=args.max_budget_usd)
    dom = make_domain(seed=0, scale=0.4)
    t0 = time.time()
    res = run(dom, dom.seed_artifact("fewshot_all"), llm_task=dom.make_model("A"), proposer=prop,
              config=Config(iterations=args.iterations, k=2, seed=0, proposer_timeout_s=args.timeout),
              out_dir=fresh_dir("live", "metaharness_agent"), baselines=dom.baselines())
    st = res.loop.store
    cands = candidate_rows(st, ("status", "reason", "base_system", "hypothesis", "base_fallback"))
    sessions = [session_row(json.loads((d / "meta.json").read_text())) for d in sorted(st.sessions_dir().glob("iter*"))]
    final = (res.meta.get("final") or {}).get("splits", {}).get("test", {}).get("results", {})
    out = {"llm": args.llm, "proposer": "AgentProposer(prototype=True)", "config": {
        "iterations": args.iterations, "k": 2, "scale": 0.4, "max_budget_usd_per_session": args.max_budget_usd,
        "timeout_s": args.timeout}, "candidates": cands, "sessions": sessions, "best": res.meta["best_system"],
        "frontier": res.meta["frontier"]["_pareto"], "test": {k: v["score"] for k, v in final.items()},
        "reports": sorted(p.name for p in st.reports_dir().glob("*")), "total_usd": cli.meter.total().cost_usd,
        "proposer_usage": cli.meter.snapshot(), "seconds": time.time() - t0}
    out = reanalyze(out, st)
    out["verdict"] = verdict(out)
    print(out["verdict"])
    save("live_smoke_agent", out, args.out)


if __name__ == "__main__":
    main()
