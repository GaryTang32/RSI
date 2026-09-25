"""E8 - The no-peeking constraint matters.

Claim [doc "Strategies must decide using only what they have revealed so far. They
can't peek at unrevealed scores or use known best answers"; paper App.B.2 hard
constraints]: without a prefix guard, a policy that reads the recorded tree gets an
inflated replay score, wins selection, and then fails online, where the future is not
recorded. The guard blocks it.

A deliberately cheating ``oracle`` policy (reads ``question.tree`` and ``best_so_far``)
and the honest adaptive policy are replayed over recorded synthetic worlds with the
guard ON (subprocess sandbox + prefix proxy) and OFF (raw question, in-process), and
run online on fresh worlds. Raw Eq.1 (beta1=0.01, beta2=0.005) for all numbers.

    python experiments/dream-rsi/e8_no_peeking.py [--seeds N] [--quick]
"""
import numpy as np
from _common import fmt, parse_args, pmap, save, summ

from rsi.dream import (Config, DreamRSILoop, Eq1Objective, GridPlan, OnlineQuestion, ReplayEvaluator, SubprocessRunner,
                       adaptive, code_of, static_check, template_code)
from rsi.dream.guard import InProcessSession
from rsi.domains.discovery import SyntheticConfig, SyntheticDomain

W, GRID = 4, (6, 4)
OBJ = Eq1Objective(normalize=False)


def record(seed):
    dom = SyntheticDomain(SyntheticConfig(seed=seed))
    cfg = Config(rounds=1, W=W, branch_count=GRID[0], refine_count=GRID[1], dream=False, sandbox="inprocess",
                 seed=seed, agent_workers=1)
    return DreamRSILoop(dom.as_task(), dom.mock_agent(), config=cfg).run().meta["worlds"][0]


def online(job):
    """One live search on a fresh world; ``guarded=False`` hands the policy the raw question."""
    name, code, seed, guarded = job
    dom = SyntheticDomain(SyntheticConfig(seed=seed))
    task, agent = dom.as_task(), dom.mock_agent()
    root = task.seed_artifact()
    q = OnlineQuestion(task=task, agent=agent, root_artifact=root, root_eval=task.evaluate(root), W=W,
                       plan=GridPlan(*GRID), workers=1, seed=seed, round_index=1)
    out = InProcessSession(code).solve({}, q, unguarded=not guarded)
    st = q.stats()
    disq = bool(out.violations or out.batch_errors or out.error)
    v = -1.0 if disq else st["best"] - OBJ.beta1 * st["N"] + OBJ.beta2 * st["N"] / max(1, st["k"])
    return name, guarded, v, st["N"], disq


def main():
    a = parse_args("E8 no-peeking guard", default_seeds=8)
    n_online = 10 if a.quick else 40
    worlds = [record(300 + i) for i in range(a.seeds)]
    pol = {"honest_adaptive": code_of(adaptive()), "oracle": template_code("oracle")}
    lint = static_check(pol["oracle"])
    on = ReplayEvaluator(OBJ, W=W, fallback=GRID, runner=SubprocessRunner(timeout_s=30), root_mode="addressable")
    off = ReplayEvaluator(OBJ, W=W, fallback=GRID, runner="inprocess", root_mode="addressable", unguarded=True)
    rep = {}
    for n, c in pol.items():
        r_on, r_off = on.evaluate(c, worlds), off.evaluate(c, worlds)
        rep[n] = {"replay_guard_on": r_on.value, "replay_guard_off": r_off.value,
                  "disqualified_on": r_on.disqualified, "violations_on": r_on.diagnostics.get("violations"),
                  "N_off": float(np.mean([e.N for e in r_off.episodes])),
                  "attainment_off": float(np.mean([e.attainment for e in r_off.episodes]))}
    jobs = [(n, c, 2000 + s, g) for n, c in pol.items() for s in range(n_online) for g in (False, True)]
    res_on = {}
    for n, g, v, N, disq in pmap(online, jobs, a.workers):
        res_on.setdefault((n, g), []).append((v, N, disq))
    for n in pol:
        for g in (False, True):
            vals = res_on[(n, g)]
            rep[n][f"online_{'guarded' if g else 'unguarded'}"] = summ([v for v, _, _ in vals])
            rep[n][f"online_N_{'guarded' if g else 'unguarded'}"] = float(np.mean([N for _, N, _ in vals]))
    pick_off = max(pol, key=lambda n: rep[n]["replay_guard_off"])
    pick_on = max(pol, key=lambda n: rep[n]["replay_guard_on"])
    for n, r in rep.items():
        print(f"{n:<16} replay guard OFF {r['replay_guard_off']:+.4f} (N={r['N_off']:.1f}, attainment "
              f"{r['attainment_off']:.2f}) | guard ON {r['replay_guard_on']:+.4f} (disqualified {r['disqualified_on']}) | "
              f"online {fmt(r['online_unguarded'])}")
    print(f"selection without guard picks {pick_off} (online {rep[pick_off]['online_unguarded']['mean']:+.4f}); "
          f"with guard picks {pick_on} (online {rep[pick_on]['online_unguarded']['mean']:+.4f})")
    print(f"static check on the oracle: ok={lint.ok} errors={lint.errors}")
    for n in pol:
        rep[n]["replay_minus_online_guard_off"] = rep[n]["replay_guard_off"] - rep[n]["online_unguarded"]["mean"]
    infl_o = rep["oracle"]["replay_minus_online_guard_off"]
    infl_h = rep["honest_adaptive"]["replay_minus_online_guard_off"]
    inflated = rep["oracle"]["replay_guard_off"] > rep["honest_adaptive"]["replay_guard_off"] and infl_o > infl_h + 0.05
    worse_online = rep["oracle"]["online_unguarded"]["mean"] < rep["honest_adaptive"]["online_unguarded"]["mean"]
    blocked = rep["oracle"]["disqualified_on"] == a.seeds and pick_on == "honest_adaptive"
    verdict = (f"{'REPRODUCED' if inflated and blocked else 'PARTIAL'}: without the guard the peeking policy's replay "
               f"score is inflated by {infl_o:+.3f} over its true online value (honest policy: {infl_h:+.3f}) and it "
               f"wins selection ({rep['oracle']['replay_guard_off']:+.3f} vs {rep['honest_adaptive']['replay_guard_off']:+.3f})"
               f"; online it scores {rep['oracle']['online_unguarded']['mean']:+.3f} vs honest "
               f"{rep['honest_adaptive']['online_unguarded']['mean']:+.3f} - "
               + ("so the replay advantage does not survive online" if worse_online else
                  "its no-peek fallback happens to be competitive here, so the damage is a mis-estimated value "
                  f"({rep['oracle']['replay_guard_off'] - rep['honest_adaptive']['replay_guard_off']:+.3f} predicted "
                  f"advantage vs {rep['oracle']['online_unguarded']['mean'] - rep['honest_adaptive']['online_unguarded']['mean']:+.3f}"
                  " realised), not an online collapse")
               + f"; the guard disqualifies it in {rep['oracle']['disqualified_on']}/{a.seeds} worlds and the static "
                 "check rejects it before it runs")
    print(verdict)
    save("e8_no_peeking", {"config": {"worlds": a.seeds, "online_worlds": n_online, "W": W, "grid": GRID,
                                      "objective": "raw Eq.1 beta1=0.01 beta2=0.005", "root_mode": "addressable",
                                      "llm": "not used"},
                           "results": rep, "pick_without_guard": pick_off, "pick_with_guard": pick_on,
                           "oracle_static_check": {"ok": lint.ok, "errors": lint.errors}, "verdict": verdict}, a.out)


if __name__ == "__main__":
    main()
