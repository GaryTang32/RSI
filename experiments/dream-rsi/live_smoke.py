"""Live smoke: one small end-to-end Dream-RSI run with a real LLM.

Claude (``claude -p``, default haiku) is BOTH the frozen discovery agent (Listing-1
exploration prompt, rewrites the program) and the policy developer (Listing-2 prompt,
rewrites the exploration-policy code from replay feedback). Everything LLM-written runs
sandboxed: candidate programs through the domain's subprocess evaluator, policies in
the subprocess PrefixGuard sandbox. Responses are cached under ``.rsi_cache/dream-rsi``
so a rerun is free.

Budget: 2 live searches on a 3 x 2 grid (<= 6 agent calls each), M = 2 (one developer
revision) -> about 13-15 LLM calls, typically well under $1 and ~5 minutes.

    python experiments/dream-rsi/live_smoke.py --llm claude:haiku [--domain sumdiff|circlepack|lasso]
"""
import json
import tempfile
import time

from _common import CACHE, domain_of, llm_of, parse_args, save

from rsi.dream import Config, EditorAgent, LLMPolicyDeveloper, run, static_check


def main():
    a = parse_args("Dream-RSI live smoke", default_seeds=1, extra=lambda ap: ap.add_argument(
        "--domain", default="sumdiff"))
    llm = llm_of(a.llm)
    dom = domain_of(a.domain, 0, sandboxed=True)
    if llm is None:
        print("[offline dry run: mock agent + mock developer; use --llm claude:haiku for the live smoke]")
        agent, dev = dom.mock_agent(), None
    else:
        agent = EditorAgent(llm, editable=[dom.program_file])
        dev = LLMPolicyDeveloper(llm)
    cfg = Config(rounds=2, W=3, branch_count=3, refine_count=1, M=2, sandbox="subprocess", seed=0, dream_last=False)
    out_dir = tempfile.mkdtemp(prefix="dream_live_")
    t0 = time.time()
    res = run(dom, config=cfg, agent=agent, developer=dev, llm_propose=None, out_dir=out_dir)
    wall = time.time() - t0
    usage = {}
    if llm is not None:
        usage = llm.meter.snapshot()
    d = res.trajectory[0].get("dream", {})
    nodes = [n for w in res.meta["worlds"] for n in w.non_root()]
    fails = {}
    for n in nodes:
        fails[n.fail_class] = fails.get(n.fail_class, 0) + 1
    final_ok = static_check(res.meta["policy"]).ok
    summary = {
        "domain": a.domain, "llm": a.llm, "wall_s": round(wall, 1), "out_dir": out_dir,
        "seed_score": res.meta["seed_score"], "best_score": res.meta["best_score"],
        "per_round": [{k: r[k] for k in ("iteration", "calls", "round_best", "best", "N", "k", "plan")}
                      for r in res.trajectory],
        "attempt_outcomes": fails, "dream_values": d.get("values"), "dream_selected": d.get("selected"),
        "developer_change": d.get("changes"), "final_policy_passes_static_check": final_ok,
        "deployed_policy_changed": res.trajectory[-1]["policy"] != res.trajectory[0]["policy"],
        "usage_by_role": usage, "cost": res.usage.get("_cost"),
        "usd": sum(v.get("cost_usd", 0.0) for k, v in usage.items() if k != "_total"),
    }
    print(json.dumps({k: v for k, v in summary.items() if k not in ("usage_by_role", "cost")}, indent=1, default=str))
    verdict = (f"live run completed: best {summary['best_score']:.4f} from seed {summary['seed_score']:.4f} in "
               f"{sum(r['calls'] for r in summary['per_round'])} agent calls; developer revision "
               f"{'selected' if d.get('selected') else 'not selected (incumbent kept)'}; "
               f"${summary['usd']:.3f}, {wall:.0f}s")
    print(verdict)
    save("live_smoke" if llm is not None else "live_smoke_dryrun",
         {"config": {"rounds": 2, "grid": [3, 1], "W": 3, "M": 2, "cache": str(CACHE)}, "summary": summary,
          "verdict": verdict}, a.out)


if __name__ == "__main__":
    main()
