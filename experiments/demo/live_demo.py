"""Live demo: the rsi package improving a real LLM agent harness, end to end, with Claude Haiku.

Each run starts from an untouched seed, uses Claude Haiku both as the frozen task model and
as the proposer, and writes a full trace (``trace.jsonl`` -> ``TRACE.md``) plus a
``summary.json`` that ``experiments/demo/plot_demo.py`` turns into graphs::

    python experiments/demo/live_demo.py metaharness    # AgentQA harness, Meta-Harness, N=4 k=2
    python experiments/demo/live_demo.py rrsi           # AgentQA harness, RRSI, T=4
    python experiments/demo/live_demo.py autoresearch   # tinylm train.py, autoresearch, 10 experiments
    python experiments/demo/live_demo.py metaharness --suite hard   # harder tasks: logic grids + ledgers
    python experiments/demo/live_demo.py rrsi --suite hard
    python experiments/demo/live_demo.py metaharness --replay-check # re-run a finished run from its cache at $0
                                                                    # and check every prompt and score matches

Outputs go to ``demo/runs/<method>/`` with a fresh LLM cache in ``demo/runs/.cache_<method>``
(a re-run with the same cache replays at $0). The seed harness is one direct model call with
the system prompt "You are a helpful assistant."; the grader is exact match, outside the artifact.
Held-out (``holdout``) and out-of-distribution (``ood``) tasks are sealed: the loop never sees
them, only the write-only shadow monitor and the final transfer report do.
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

import rsi  # noqa: E402
from rsi.core import Budget, CachedLLM, ClaudeCLI, transfer_report  # noqa: E402
from rsi.domains.agentqa import AgentQADomain, make_suite  # noqa: E402
from rsi.trace import inspect  # noqa: E402

OUT = ROOT / "demo" / "runs"


def _fresh(name: str) -> Path:
    out = OUT / name
    if out.exists():
        raise SystemExit(f"{out} exists: refusing to overwrite a recorded run")
    return out


def _haiku(name: str, timeout_s: int = 300, offline: bool = False) -> CachedLLM:
    return CachedLLM(ClaudeCLI("haiku", timeout_s=timeout_s), OUT / f".cache_{name}", offline=offline)


def _agentqa(suite: str = "easy"):
    if suite == "hard":
        # 8 practice tasks (logic grids + transaction ledgers); sealed: 6 held-out of the same kinds and
        # 6 OOD (business-day scheduling + chained string transformations)
        from rsi.domains.agentqa.hard import OOD_HINT, make_hard_suite
        # hard tasks cost ~$0.05 per harness run (long puzzles, 60-85 row ledgers), so the splits are smaller
        return AgentQADomain(make_hard_suite(n_evolve=8, n_holdout=6, n_ood_per_family=3, seed=0), ood_hint=OOD_HINT)
    # 12 practice tasks (numeric word problems); sealed: 8 held-out numeric + 8 ood from 4 unseen families
    return AgentQADomain(make_suite(n_evolve=12, n_holdout=8, n_ood_per_family=2, seed=1))


def _name(method: str, a) -> str:
    return method + ("_hard" if a.suite == "hard" else "")


def _usd(llm) -> float:
    return round(llm.meter.total().cost_usd, 4)


def _finish(name: str, out: Path, dom, res, llm, t0: float, setup: dict, k: int = 2) -> None:
    inspect(out)
    before = _usd(llm)
    rep = transfer_report(dom, llm, {"seed": res.baseline, "final": res.best},
                          splits=("evolve", "holdout", "ood"), k=k, workers=4)
    summ = {"run": name, "setup": setup, "stop_reason": res.stop_reason, "wall_s": round(time.time() - t0, 1),
            "trajectory": res.trajectory, "transfer": rep, "usd_total": _usd(llm),
            "usd_transfer_report": round(_usd(llm) - before, 4),
            "seed_files": res.baseline.files, "final_files": res.best.files}
    (out / "summary.json").write_text(json.dumps(summ, indent=1, default=str))
    res.best.to_dir(out / "final_harness", clean=True)
    print(json.dumps({s: {a: v["S"] for a, v in row.items()} for s, row in rep["splits"].items()}, indent=1))
    print("usd", summ["usd_total"])


MH_CFG = {"iterations": 4, "k": 2, "history_mode": "full", "seed": 0, "test_splits": ("holdout", "ood"),
          "cost_metric": "tokens", "workers": 4, "validate_timeout_s": 240.0, "shadow_workers": 4}


def metaharness(a) -> None:
    name = _name("metaharness", a)
    if a.replay_check:
        return replay_check(name, a)
    out, llm, dom, t0 = _fresh(name), _haiku(name), _agentqa(a.suite), time.time()
    res = rsi.improve(dom, method="metaharness", llm_task=llm, llm_propose=llm, config=MH_CFG, out_dir=out,
                      proposer_kind="rewrite")
    _finish(name, out, dom, res, llm, t0, {"method": "metaharness", "suite": a.suite, "config": MH_CFG,
                                           "llm": "claude haiku (task + proposer)"}, k=1 if a.suite == "hard" else 2)


def replay_check(name: str, a) -> None:
    """Re-run a finished Meta-Harness demo with its cache in offline mode (a cache miss is an error, so nothing
    is sent to the model) and compare every proposer prompt and every candidate score with the recorded run."""
    from rsi.trace import load_trace
    src, out = OUT / name, OUT / f"{name}_replay"
    if out.exists():
        raise SystemExit(f"{out} exists")
    llm, dom = _haiku(name, offline=True), _agentqa(a.suite)
    rsi.improve(dom, method="metaharness", llm_task=llm, llm_propose=llm, config=MH_CFG, out_dir=out,
                proposer_kind="rewrite")
    pick = lambda d: ([e["data"].get("prompt") for e in load_trace(d) if e["kind"] == "proposal"],
                      [(e["data"]["candidate"], e["data"]["summary"]["S"]) for e in load_trace(d) if e["kind"] == "eval"])
    (p0, s0), (p1, s1) = pick(src), pick(out)
    rep = {"run": name, "proposals": len(p0), "prompts_identical": p0 == p1, "scores_identical": s0 == s1,
           "cache_misses": llm.misses, "cache_hits": llm.hits, "usd_spent": _usd(llm)}
    (out / "replay_check.json").write_text(json.dumps(rep, indent=1))
    print(json.dumps(rep, indent=1))


def rrsi_run(a) -> None:
    name = _name("rrsi", a)
    out, llm, dom, t0 = _fresh(name), _haiku(name, 400), _agentqa(a.suite), time.time()
    cfg = {"T": 4, "m": 2, "k": 1, "workers": 4, "seed": 0, "analyst": "llm", "calibration_repeats": 3,
           "max_digests": 4, "n_fail_traces": 6, "n_success_traces": 3, "repair_rounds": 2, "max_done_bounces": 2}
    res = rsi.improve(dom, method="rrsi", llm_task=llm, llm_propose=llm, config=cfg, out_dir=out,
                      llm_analyst=llm, budget=Budget(max_usd=a.max_usd, max_wall_s=45 * 60))
    _finish(name, out, dom, res, llm, t0, k=1 if a.suite == "hard" else 2, setup={"method": "rrsi", "suite": a.suite, "config": cfg, "llm": "claude haiku (task, proposer, critic, analyst)"})


def autoresearch(a) -> None:
    from rsi.autoresearch import Config, run
    from rsi.domains.tinylm import TinyLMTask
    name = "autoresearch"
    out, llm, t0 = _fresh(name), _haiku(name), time.time()
    task = TinyLMTask(budget_s=8.0, data_root=OUT / ".cache_tinylm_data")
    task.prepare()
    cfg = Config(max_experiments=10, mode="hardened", keep_rule="strict", reeval_seeds=3, max_usd=a.max_usd,
                 tag="demo-live", seed=0)
    res = run(task, llm_propose=llm, config=cfg, out_dir=out)
    inspect(out)
    summ = {"run": name, "setup": {"method": "autoresearch", "task": "tinylm, 8 s CPU training budget",
                                   "llm": "claude haiku (research agent)", "max_experiments": 10},
            "stop_reason": res.stop_reason, "wall_s": round(time.time() - t0, 1), "trajectory": res.trajectory,
            "meta": res.meta, "usd_total": _usd(llm)}
    (out / "summary.json").write_text(json.dumps(summ, indent=1, default=str))
    print("usd", summ["usd_total"])


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("which", choices=["metaharness", "rrsi", "autoresearch"])
    ap.add_argument("--max-usd", type=float, default=2.5)
    ap.add_argument("--suite", choices=["easy", "hard"], default="easy",
                    help="easy = numeric AgentQA (the first demo); hard = logic grids + ledgers (Meta-Harness, RRSI)")
    ap.add_argument("--replay-check", action="store_true",
                    help="Meta-Harness only: replay a finished run from its cache at $0 and compare it")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    {"metaharness": metaharness, "rrsi": rrsi_run, "autoresearch": autoresearch}[a.which](a)
