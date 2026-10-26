"""Live comparison on AgentQA with Claude Haiku: seed (H0) vs Harness² at k = 1 and k = 3 on the same task stream.

Every arm streams the same tasks (AgentQA holdout + ood, shuffled with ``--order-seed``, first ``--n``) with the same
model as agent, improver and selector. The seed arm is free: it is each task's first reference rollout (``base1``),
which Harness² runs anyway and which the k = 1 and k = 3 arms share through the LLM cache. Accuracy is graded against
the evaluation-only labels after each loop has ended; CIs are percentile bootstraps over tasks, paired by task.

Run:  python experiments/harness2/compare_live.py --smoke                  # 1 task at k = 1: formats and cost
      python experiments/harness2/compare_live.py --n 20 --k 1 --cap 6 --out results/harness2/live_k1.json
      python experiments/harness2/merge_live.py   # combine per-arm files into compare_live.json
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rsi.core import get_llm  # noqa: E402
from rsi.core.stats import bootstrap_ci, paired_diff_ci  # noqa: E402
from rsi.domains.agentqa import AgentQADomain  # noqa: E402
from rsi.harness2 import Config, run  # noqa: E402
from rsi.harness2.api import stream_tasks  # noqa: E402

RESULTS = ROOT / "results" / "harness2"


def stream(dom, n: int, order_seed: int):
    tasks = list(stream_tasks(dom, Config(split="holdout+ood")))
    random.Random(order_seed).shuffle(tasks)
    return tasks[:n]


def run_arm(dom, tasks, k: int, cap: float, model: str, cache: Path, out: Path) -> dict:
    llm = get_llm(model, cache_dir=str(cache))
    t0 = time.time()
    res = run(dom, dom.seed_artifact(), llm_task=llm, llm_propose=llm,
              config=Config(k=k, mode="parallel", seed=0, max_usd=cap, report=True, trace=True), out_dir=str(out),
              tasks=tasks)
    recs, rep = res.meta["records"], res.meta["report"]
    stages = [s for r in recs for s in r.stages]
    usage = llm.meter.total()
    saved = getattr(llm, "saved", None)
    return {"k": k, "tasks_done": len(recs), "stop_reason": res.stop_reason, "wall_s": round(time.time() - t0, 1),
            "usd": round(usage.cost_usd, 4), "calls": usage.calls,
            "usd_from_cache": round(saved.total().cost_usd, 4) if saved is not None else None,
            "stages": {"n": len(stages), "fell_back": sum(s.fell_back for s in stages),
                       "kept_base": sum(s.kept_base for s in stages),
                       "errors": sorted({str(s.error)[:120] for s in stages if s.error})[:10],
                       "by_role": {role: sum(s.role == role for s in stages) for role in sorted({s.role for s in stages})}},
            "status": {st: sum(r.status == st for r in recs) for st in sorted({r.status for r in recs})},
            "library_added": sum(bool(r.library_added) for r in recs),
            "report": {k2: v for k2, v in rep.items() if k2 != "per_task"}, "per_task": rep["per_task"]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--k", type=int, nargs="+", default=[1, 3])
    ap.add_argument("--cap", type=float, nargs="+", default=[10.0], help="USD cap per arm, aligned with --k (checked between tasks)")
    ap.add_argument("--model", default="claude:haiku")
    ap.add_argument("--order-seed", type=int, default=0)
    ap.add_argument("--smoke", action="store_true", help="1 task at k = 1")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    if a.smoke:
        a.n, a.k = 1, [1]
    dom = AgentQADomain()
    tasks = stream(dom, a.n, a.order_seed)
    cache = RESULTS / "live_cache"
    arms = {}
    caps = a.cap if len(a.cap) == len(a.k) else [a.cap[0]] * len(a.k)
    for k, cap in zip(a.k, caps):
        arms[f"harness2_k{k}"] = run_arm(dom, tasks, k, cap, a.model, cache, ROOT / "runs" / f"harness2_live_k{k}")
        print(json.dumps({n: {x: v[x] for x in ("tasks_done", "usd", "stop_reason", "stages", "status")}
                          for n, v in arms.items()}, indent=1, default=str), flush=True)
    first = next(iter(arms.values()))
    ids = [t for t in first["per_task"] if all(t in v["per_task"] for v in arms.values())]
    seed = [first["per_task"][t]["base"] for t in ids]
    summary = {"seed (H0 base1)": bootstrap_ci(seed)}
    for name, v in arms.items():
        sub = [v["per_task"][t]["submitted"] for t in ids]
        summary[name] = {"acc": bootstrap_ci(sub), "vs_seed": paired_diff_ci(seed, sub)}
    if {"harness2_k1", "harness2_k3"} <= set(arms):
        summary["k3_minus_k1"] = paired_diff_ci([arms["harness2_k1"]["per_task"][t]["submitted"] for t in ids],
                                                [arms["harness2_k3"]["per_task"][t]["submitted"] for t in ids])
    out = {"experiment": "harness2_compare_live", "model": a.model, "stream": "AgentQA holdout+ood",
           "order_seed": a.order_seed, "n_requested": a.n, "n_common": len(ids), "caps": dict(zip(a.k, caps)),
           "total_usd": round(sum(v["usd"] for v in arms.values()), 4), "summary": summary, "arms": arms}
    path = Path(a.out) if a.out else RESULTS / ("live_smoke.json" if a.smoke else "compare_live.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps({"total_usd": out["total_usd"], "n_common": len(ids), "summary": summary}, indent=1, default=str))


if __name__ == "__main__":
    main()
