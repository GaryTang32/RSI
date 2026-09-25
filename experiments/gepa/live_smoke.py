"""Live smoke run: GEPA end-to-end with a real reflection LM.

The reflection LM is ``claude -p`` (default haiku) behind ``CachedLLM`` (cache under
``.rsi_cache/gepa``, so a re-run replays for free); the task models stay simulated and
deterministic (RuleWorld's rule-following model, AgentQA's SimModel), so the run checks
that a real model reads GEPA's verbatim meta-prompt + side_info, returns a fenced
instruction that parses, and that its rewrites are gated, validated and logged.
Small by design: RuleWorld B = 300 rollouts and AgentQA (two-module harness) B = 80,
guarded by ``Budget(max_usd=0.8, max_wall_s=900)``. ``--llm sim`` runs the same pipeline
with the offline mocks.

    python experiments/gepa/live_smoke.py --llm claude:haiku
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import parse_args, reflection_llm, save  # noqa: E402

from rsi.core import Budget  # noqa: E402
from rsi.domains.agentqa import AgentQADomain, SimModel, make_suite  # noqa: E402
from rsi.domains.ruleworld import make_domain  # noqa: E402
from rsi.gepa import Config, run, two_module_harness  # noqa: E402


def main():
    a = parse_args("live smoke run of GEPA", default_seeds=1,
                   extra=lambda ap: (ap.add_argument("--B", type=int, default=300),
                                     ap.add_argument("--B-agentqa", type=int, default=80),
                                     ap.add_argument("--max-usd", type=float, default=0.8)))
    t0 = time.time()
    out = {"experiment": "live smoke", "llm": a.llm}
    # ---- RuleWorld: real reflection LM, simulated rule-following task model
    d = make_domain(seed=0, feedback="rich")
    llm = reflection_llm(a.llm, d.world)
    res = run(d, d.seed_artifact(), llm_propose=llm, config=Config(max_metric_calls=a.B, seed=0),
              budget=Budget(max_usd=a.max_usd, max_wall_s=900), verbose=True)
    st = res.state
    ev = [e.get("event") for e in st.trace]
    out["ruleworld"] = {
        "budget": a.B, "stop_reason": res.stop_reason, "rollouts": res.meta["rollouts"],
        "iterations": res.meta["iterations"], "events": {k: ev.count(k) for k in set(ev)},
        "seed_val": res.meta["seed_val"], "best_val": res.meta["best_val"],
        "seed_test_true": d.expected(res.baseline, "test"), "best_test_true": d.expected(res.best, "test"),
        "oracle_test_true": d.expected(d.oracle_artifact(), "test"),
        "rejected_outputs": [e["rejected_outputs"] for e in st.trace if "rejected_outputs" in e],
        "rules_detected": {m: {a_: [c for c, _ in r] for a_, r in v.rules.items()}
                           for m, v in d.world.views(res.best.files).items()},
        "ticket_facts_in_best": d.world.count_facts(res.best.files),
        "best_prompts": {k: v[:2500] for k, v in res.best.files.items()}, "usage": res.usage,
        "ledger_nodes": len(res.ledger)}
    # ---- AgentQA two-module harness: real reflection LM, SimModel task model
    suite = make_suite(n_evolve=8, n_val=8, n_holdout=10, n_ood_per_family=3, seed=0)
    dom = AgentQADomain(suite)
    llm2 = reflection_llm(a.llm, kind="agentqa")
    spent = float(res.usage.get("_total", {}).get("cost_usd", 0.0))
    res2 = run(dom, two_module_harness(), llm_task=SimModel(suite), llm_propose=llm2,
               config=Config(max_metric_calls=a.B_agentqa, seed=0, workers=4),
               budget=Budget(max_usd=max(0.05, a.max_usd - spent), max_wall_s=600), verbose=True,
               report_splits=("holdout", "ood"))
    rep = res2.meta["report"]["splits"]
    out["agentqa"] = {"budget": a.B_agentqa, "stop_reason": res2.stop_reason, "rollouts": res2.meta["rollouts"],
                      "seed_val": res2.meta["seed_val"], "best_val": res2.meta["best_val"],
                      "holdout": {k: v["S"] for k, v in rep["holdout"].items()},
                      "ood": {k: v["S"] for k, v in rep["ood"].items()},
                      "best_prompts": {k: v[:2500] for k, v in res2.best.files.items() if k.startswith("prompts/")},
                      "usage": res2.usage}
    out["total_cost_usd"] = spent + float(res2.usage.get("_total", {}).get("cost_usd", 0.0))
    out["wall_s"] = time.time() - t0
    r = out["ruleworld"]
    out["verdict"] = (f"RuleWorld: true test {r['seed_test_true']:.3f} -> {r['best_test_true']:.3f} (oracle "
                      f"{r['oracle_test_true']:.3f}) with {r['events'].get('accepted', 0)} accepted of "
                      f"{r['events'].get('accepted', 0) + r['events'].get('rejected', 0)} proposals in "
                      f"{r['rollouts']} rollouts; AgentQA holdout {out['agentqa']['holdout']} / OOD "
                      f"{out['agentqa']['ood']}; cost ${out['total_cost_usd']:.3f}, {out['wall_s']:.0f} s.")
    save("live_smoke" if a.live else "live_smoke_sim", out, a.out)
    print(out["verdict"])


if __name__ == "__main__":
    main()
