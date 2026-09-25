"""Live smoke run (one small end-to-end run with a real LLM; cached; well under $1).

1. Meta-Harness on MemoClassify: haiku as the proposer (RewriteProposer over the rendered full history),
   frozen MemoLM-A as the base model, 2 iterations x k = 2, then the one-time test finalisation.
2. SoL-Pi Evidence-Preserving Reducer: haiku as the reducer on long diagnostic logs from each AgentWorld family,
   every receipt validated byte-for-byte (accepted / fallback reasons / audit).
3. SoL-Pi lineage with an LLM implementer: haiku writes ONE mechanism as a code extension against the runtime
   hook API for a free-form idea; the lineage validates it on the training screen with the dual gate.

    python experiments/metaharness-solpi/live_smoke.py [--llm claude:haiku]
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import fresh_dir, live_llm, parse_args, save  # noqa: E402

from rsi.core import Evaluator  # noqa: E402
from rsi.domains.agentworld import MockAgentLLM, make_domain as make_world  # noqa: E402
from rsi.domains.memoclassify import make_domain  # noqa: E402
from rsi.metaharness import Config as MHConfig, run as mh_run  # noqa: E402
from rsi.solpi import (DualGate, GateSpec, Idea, LLMMechanismProposer, LLMReducer, Lineage, SmokeReviewer,  # noqa
                       metrics_from_eval)


def main():
    args = parse_args(__doc__.splitlines()[0], default_seeds=1)
    if not args.live:
        args.llm = "claude:haiku"
        args.live = True
    out = {"llm": args.llm, "parts": {}}
    t0 = time.time()
    # ---- 1. Meta-Harness with a live proposer
    llm = live_llm(args.llm)
    dom = make_domain(seed=0, scale=0.4)
    res = mh_run(dom, dom.seed_artifact("fewshot_all"), llm_task=dom.make_model("A"), llm_propose=llm,
                 config=MHConfig(iterations=2, k=2, seed=0), out_dir=fresh_dir("live", "metaharness"),
                 baselines=dom.baselines())
    st = res.loop.store
    cands = [{"name": n, **{k: st.meta(n).get(k) for k in ("status", "reason", "base_system", "hypothesis")},
              "search": (st.scores(n) or {}).get("score"), "context": (st.scores(n) or {}).get("context_cost")}
             for n in st.names()]
    out["parts"]["metaharness"] = {
        "candidates": cands, "best": res.meta["best_system"], "frontier": res.meta["frontier"]["_pareto"],
        "test": {k: v["score"] for k, v in res.meta["final"]["splits"]["test"]["results"].items()},
        "proposer_usage": llm.meter.snapshot(), "iterations": res.trajectory}
    print("[metaharness]", res.meta["best_system"], {c["name"]: (c["status"], c["search"]) for c in cands})
    # ---- 2. EPR with a live reducer
    from s3_reducer import bench_logs, run_bench
    llm2 = live_llm(args.llm)
    logs = bench_logs(1)
    bench = run_bench(LLMReducer(llm2), logs)
    bench["usage"] = llm2.meter.snapshot()
    out["parts"]["epr"] = bench
    print("[epr]", bench["outcomes"], "nonverbatim", bench["nonverbatim"])
    # ---- 3. one LLM-implemented lineage
    llm3 = live_llm(args.llm)
    world = make_world(seed=0, n_train=2, n_accept=1, n_final=1, n_test=0)
    agent = MockAgentLLM("A")
    ev = Evaluator(world, agent)
    base = world.seed_artifact()
    bm = metrics_from_eval(ev.evaluate(base, "evolve"))
    idea = Idea("L1", "C", "Stop replaying large successful tool outputs in every later request; keep them "
                           "recallable", "", [{}], "none")
    lin = Lineage(idea, evaluator=ev, gate=DualGate(GateSpec()), proposer=LLMMechanismProposer(llm3),
                  reviewer=SmokeReviewer(world, agent), base=base, base_metrics=bm, max_iters=2, ralph_max=2)
    lr = lin.run()
    out["parts"]["lineage"] = {"frozen": lr.frozen is not None,
                               "iterations": [{k: v for k, v in it.items() if k != "metrics"} for it in lr.iterations],
                               "code": (lr.frozen.artifact.files if lr.frozen else None),
                               "usage": llm3.meter.snapshot()}
    print("[lineage]", [(it.get("stage"), it.get("outcome"), (it.get("gate") or {}).get("reason"), it.get("error"))
                        for it in lr.iterations])
    usd = sum(m.meter.total().cost_usd for m in (llm, llm2, llm3))
    out["total_usd"] = usd
    out["seconds"] = time.time() - t0
    out["verdict"] = (f"live end-to-end OK: Meta-Harness evaluated {sum(c['status'] == 'evaluated' for c in cands) - 2} "
                      f"haiku-written candidates (best {res.meta['best_system']}); EPR accepted {bench['accepted']}/"
                      f"{bench['n_logs']} haiku receipts with {bench['nonverbatim']} non-verbatim quotes; LLM lineage "
                      f"{'froze a mechanism' if lr.frozen else 'did not pass the gate'}; cost ${usd:.3f}")
    print(out["verdict"])
    save("live_smoke", out)


if __name__ == "__main__":
    main()
