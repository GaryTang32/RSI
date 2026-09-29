"""E12 - Does replay rank policies like their true online value on REAL tasks? (claims audit L1, retry round 2)

E2 validated replay only on synthetic worlds (Spearman 0.92 in support); the one real-task check was a single
recorded world (t = 1), where the replay-selected frugal policy found less online. This repeats E2's design on
two real CPU tasks with their mock agents: K = 5 worlds recorded by parallel refine (6 x 4 grid, W = 6), E2's
20-policy zoo (3 plans are out of support), replay value = raw Eq. 1 on the recorded worlds with
``support="no_reward"``, true value = the same raw Eq. 1 averaged over 40 fresh online searches that run the
policy's own plan. beta1 and beta2 are scaled by the task's recorded parallel-refine gain G_d (mean of
ceiling - root over the recorded worlds), so that the quality/cost trade-off matches the normalized Eq. 1.

Preregistered (claims-audit §6): per domain, Spearman over in-support policies >= 0.7; L1 -> REPRODUCED iff
that holds in both domains AND the replay pick beats parallel refine online (paired CI lower bound > 0) in both.

    python experiments/dream-rsi/e12_real_offpolicy.py [--domains sumdiff,autocorr] [--workers 2]
"""
import numpy as np

from _common import domain_of, paired, parse_args, pmap, save, spearman, summ

from e2_offpolicy_validity import zoo
from rsi.dream import Config, DreamRSILoop, Eq1Objective, ReplayEvaluator

W, GRID, K, N_TRUE = 6, (6, 4), 5, 40


def record(job):
    dom_name, seed = job
    dom = domain_of(dom_name, seed)
    cfg = Config(rounds=1, W=W, branch_count=GRID[0], refine_count=GRID[1], dream=False, sandbox="inprocess",
                 seed=seed, agent_workers=1, trace=False)
    return DreamRSILoop(dom.as_task(), dom.mock_agent(), config=cfg).run().meta["worlds"][0]


def online(job):
    dom_name, name, code, seed, b1, b2 = job
    dom = domain_of(dom_name, seed)
    cfg = Config(rounds=1, W=W, branch_count=GRID[0], refine_count=GRID[1], dream=False, sandbox="inprocess",
                 seed=seed, hard_max_branch=12, hard_max_refine=12, agent_workers=1, round_budget=None, trace=False)
    r = DreamRSILoop(dom.as_task(), dom.mock_agent(), config=cfg, initial_policy=code).run().trajectory[0]
    return name, seed, r["round_best"] - b1 * r["N"] + b2 * r["N"] / max(1, r["k"]), r["round_best"], r["N"]


def study(dom_name, workers):
    worlds = pmap(record, [(dom_name, 100 + i) for i in range(K)], workers)
    G = float(np.mean([w.ceiling - w.root_score for w in worlds]))
    b1, b2 = 0.01 * G, 0.005 * G
    ev = ReplayEvaluator(Eq1Objective(beta1=b1, beta2=b2, normalize=False, support="no_reward"), W=W, fallback=GRID,
                         runner="inprocess", hard_max=(12, 12))
    pol = zoo()
    replay, support = {}, {}
    for n, c in pol.items():
        rep = ev.evaluate(c, worlds)
        replay[n] = rep.value
        support[n] = not any(e.out_of_support for e in rep.episodes)
    jobs = [(dom_name, n, c, 1000 + s, b1, b2) for n, c in pol.items() for s in range(N_TRUE)]
    true, best_q, probes = {}, {}, {}
    for n, s, v, q, N in pmap(online, jobs, workers):
        true.setdefault(n, {})[s] = v
        best_q.setdefault(n, {})[s] = q
        probes.setdefault(n, {})[s] = N
    tv = {n: float(np.mean(list(true[n].values()))) for n in pol}
    ins = [n for n in pol if support[n]]
    pick = max(pol, key=lambda n: replay[n])
    seeds = sorted(true["parallel_refine"])
    vs_pi1 = paired([true["parallel_refine"][s] for s in seeds], [true[pick][s] for s in seeds])
    q_vs_pi1 = paired([best_q["parallel_refine"][s] for s in seeds], [best_q[pick][s] for s in seeds])
    # bootstrap CI of the in-support Spearman over policies (resampling policies)
    rng = np.random.default_rng(0)
    boots = []
    for _ in range(2000):
        idx = rng.choice(len(ins), len(ins), replace=True)
        xs, ys = [replay[ins[i]] for i in idx], [tv[ins[i]] for i in idx]
        if len(set(xs)) > 1 and len(set(ys)) > 1:
            boots.append(spearman(xs, ys))
    rho_in = spearman([replay[n] for n in ins], [tv[n] for n in ins])
    return {"G": G, "beta1": b1, "beta2": b2, "replay": replay, "true": tv, "in_support": support,
            "true_ci": {n: summ(list(true[n].values())) for n in pol},
            "spearman_in_support": rho_in,
            "spearman_in_support_ci": [float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))],
            "spearman_all": spearman([replay[n] for n in pol], [tv[n] for n in pol]),
            "replay_pick": pick, "true_best": max(pol, key=lambda n: tv[n]),
            "regret_of_replay_pick": max(tv.values()) - tv[pick],
            "pick_minus_parallel_refine_online_value": vs_pi1,
            "pick_minus_parallel_refine_online_best_score": q_vs_pi1,
            "pick_probes_vs_parallel_refine": [float(np.mean(list(probes[pick].values()))),
                                               float(np.mean(list(probes["parallel_refine"].values())))],
            "passes_rho": rho_in >= 0.7, "pick_beats_pi1": vs_pi1["lo"] > 0}


def main():
    a = parse_args("E12 replay validity on real tasks", default_seeds=K, extra=lambda ap: ap.add_argument(
        "--domains", default="sumdiff,autocorr"))
    out = {}
    for d in a.domains.split(","):
        out[d] = study(d, a.workers)
        r = out[d]
        print(f"[{d}] Spearman in-support {r['spearman_in_support']:.3f} {r['spearman_in_support_ci']}, all "
              f"{r['spearman_all']:.3f}; replay pick {r['replay_pick']} (true best {r['true_best']}, regret "
              f"{r['regret_of_replay_pick']:.4g}); pick - pi1 online {r['pick_minus_parallel_refine_online_value']}",
              flush=True)
    holds = all(out[d]["passes_rho"] for d in out)
    verdict = ("L1 -> REPRODUCED" if holds and all(out[d]["pick_beats_pi1"] for d in out) else
               "L1 stays PARTIAL" + ("" if holds else " (Spearman < 0.7 in " +
                                     ", ".join(d for d in out if not out[d]["passes_rho"]) + ")"))
    save("e12_real_offpolicy", {"config": {"W": W, "recording_grid": GRID, "recorded_worlds": K,
                                           "true_value_searches": N_TRUE, "objective": "raw Eq.1, beta scaled by G_d, "
                                           "support=no_reward", "policies": list(zoo())},
                                "results": out, "verdict": verdict})
    print(verdict)


if __name__ == "__main__":
    main()
