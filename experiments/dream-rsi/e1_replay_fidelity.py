"""E1 - Replay is an exact, near-free simulator.

Claim [paper:§3; Fig.2 "zero-execution-cost off-policy evaluations"; doc: replay "nearly free"]:
replaying the recording policy on its own recorded tree reproduces the online
trajectory exactly (same revealed set, N, k, per-round batches, best) with zero
agent calls, in milliseconds.

For every domain (synthetic worlds, sum-difference, circle packing, Lasso path) and
recording policy (parallel refine, adaptive) we record live searches, then replay the
same policy code (a) in-process, (b) in the subprocess sandbox, (c) on the world
re-imported from the run's rsi.core Ledger (discovery.jsonl), and compare.
Optional ``--ledger PATH`` converts any other method's ledger into a replay world.

    python experiments/dream-rsi/e1_replay_fidelity.py [--llm sim|claude:haiku] [--seeds N] [--quick]
"""
import tempfile
import time

from _common import agent_of, domain_of, fmt, parse_args, pmap, save, summ

from rsi.core import Ledger
from rsi.dream import Config, DiscoveryTree, ReplayEvaluator, run, template_code


GRID = (5, 3)
LIVE_GRID = (3, 1)      # --llm claude:*: 2 policies x 2 rounds x 6 calls = 24 real agent calls


def one(job):
    dom_name, seed, policy, llm = job
    dom = domain_of(dom_name, seed)
    code = template_code(policy)
    g = GRID if llm == "sim" else LIVE_GRID
    cfg = Config(rounds=2, W=4, branch_count=g[0], refine_count=g[1], dream=False, sandbox="inprocess", seed=seed,
                 agent_workers=1 if llm == "sim" else 4)
    out_dir = tempfile.mkdtemp(prefix="e1_")
    t0 = time.time()
    res = run(dom, config=cfg, initial_policy=code, agent=agent_of(dom, llm), out_dir=out_dir)
    online_s = time.time() - t0
    rows = []
    led = Ledger(f"{out_dir}/discovery.jsonl")
    for mode in ("addressable", "earliest"):
        ev_in = ReplayEvaluator(W=4, fallback=g, runner="inprocess", root_mode=mode)
        ev_sb = ReplayEvaluator(W=4, fallback=g, runner="subprocess", root_mode=mode)
        for t, (world, man) in enumerate(zip(res.meta["worlds"], res.meta["manifests"]), 1):
            online = {n.id: n.round for n in world.non_root()}
            back = DiscoveryTree.from_ledger(led, include=lambda n, t=t: n.id.startswith(f"t{t}/"))
            r_in = ev_in.evaluate(code, [world], manifests=res.meta["manifests"])
            r_sb = ev_sb.evaluate(code, [world], manifests=res.meta["manifests"])
            r_lg = ev_in.evaluate(code, [back], manifests=res.meta["manifests"])
            e = r_in.episodes[0]
            same = (e.reveal_round == online and e.N == man["probe_work"] and e.k == man["decision_rounds"]
                    and e.batch_sizes == man["batch_sizes"] and abs((e.best or 0) - man["round_best"]) < 1e-12)
            rows.append({"domain": dom_name, "seed": seed, "policy": policy, "root_mode": mode, "world": t,
                         "N": e.N, "k": e.k, "identical_in_process": same,
                         "identical_sandbox": r_sb.episodes[0].reveal_round == e.reveal_round
                         and r_sb.value == r_in.value,
                         "identical_after_ledger_roundtrip": r_lg.episodes[0].reveal_round == e.reveal_round,
                         "replay_ms_in_process": 1000 * r_in.cpu_s, "replay_ms_sandbox": 1000 * r_sb.wall_s,
                         "online_s_per_world": online_s / len(res.meta["worlds"]),
                         "online_agent_calls": man["agent_calls"], "replay_agent_calls": 0})
    # replay never touches the agent: count agent calls around a replay of the whole pool
    before = res.usage["_cost"]["agent_calls"]
    ReplayEvaluator(W=4, fallback=g, runner="inprocess").evaluate(code, res.meta["worlds"])
    assert res.usage["_cost"]["agent_calls"] == before
    return rows


def main():
    a = parse_args("E1 replay fidelity", default_seeds=4, extra=lambda ap: (
        ap.add_argument("--ledger", default=None, help="an rsi.core ledger.jsonl from any method to convert"),
        ap.add_argument("--lower-is-better", action="store_true")))
    doms = {"synthetic": a.seeds, "sumdiff": max(1, a.seeds // 2), "circlepack": 1, "lasso": 1}
    if a.quick:
        doms = {"synthetic": 2, "sumdiff": 1, "circlepack": 1}
    if a.llm != "sim":   # live: one real-task world pair with the LLM agent (synthetic worlds need no LLM)
        doms = {"sumdiff": 1}
    jobs = [(d, s, p, a.llm) for d, n in doms.items() for s in range(n) for p in ("parallel_refine", "adaptive")]
    rows = [r for rs in pmap(one, jobs, a.workers) for r in rs]
    n = len(rows)
    ok_in = sum(r["identical_in_process"] for r in rows)
    ok_sb = sum(r["identical_sandbox"] for r in rows)
    ok_lg = sum(r["identical_after_ledger_roundtrip"] for r in rows)
    ms_in = summ([r["replay_ms_in_process"] for r in rows])
    ms_sb = summ([r["replay_ms_sandbox"] for r in rows])
    by_dom = {}
    for d in doms:
        rr = [r for r in rows if r["domain"] == d]
        by_dom[d] = {"episodes": len(rr), "identical": sum(r["identical_in_process"] for r in rr),
                     "online_s_per_world": summ([r["online_s_per_world"] for r in rr]),
                     "replay_ms_in_process": summ([r["replay_ms_in_process"] for r in rr]),
                     "speedup_online_over_replay": summ([r["online_s_per_world"] * 1000 / max(1e-6, r["replay_ms_in_process"])
                                                          for r in rr])}
    ext = None
    if a.ledger:
        led = Ledger(a.ledger)
        w = DiscoveryTree.from_ledger(led, lower_is_better=a.lower_is_better)
        rep = ReplayEvaluator(W=4, runner="inprocess").evaluate(template_code("adaptive"), [w])
        ext = {"path": a.ledger, "attempts": w.size, "branches": w.trace_branch_count, "depth": w.trace_refine_count,
               "adaptive_V": rep.value, "N": rep.episodes[0].N}
        print(f"external ledger -> world with {w.size} attempts / {w.trace_branch_count} branches; adaptive V={rep.value:.4f}")
    print(f"identical replays: in-process {ok_in}/{n}, sandbox {ok_sb}/{n}, after ledger round trip {ok_lg}/{n}")
    print(f"replay ms/episode: in-process {fmt(ms_in, 2)}; subprocess sandbox {fmt(ms_sb, 2)}")
    for d, v in by_dom.items():
        print(f"  {d:<11} online {fmt(v['online_s_per_world'], 3)} s/world vs replay {fmt(v['replay_ms_in_process'], 2)} ms"
              f" -> x{v['speedup_online_over_replay']['mean']:.0f}")
    verdict = ("REPRODUCED: every replay of the recording policy is identical to its live rollout "
               f"({ok_in}/{n} in-process, {ok_sb}/{n} sandboxed, {ok_lg}/{n} via the ledger), zero agent calls, "
               f"~{ms_in['mean']:.1f} ms per episode in-process") if ok_in == n and ok_sb == n and ok_lg == n else \
        f"PARTIAL: {ok_in}/{n} identical in-process, {ok_sb}/{n} sandboxed, {ok_lg}/{n} via ledger"
    save("e1_replay_fidelity", {"config": {"domains": doms, "policies": ["parallel_refine", "adaptive"],
                                           "grid": "%dx%d" % (GRID if a.llm == "sim" else LIVE_GRID), "W": 4,
                                           "rounds": 2, "llm": a.llm},
                                "rows": rows, "by_domain": by_dom, "replay_ms_in_process": ms_in,
                                "replay_ms_sandbox": ms_sb, "external_ledger": ext, "verdict": verdict}, a.out)
    print(verdict)


if __name__ == "__main__":
    main()
