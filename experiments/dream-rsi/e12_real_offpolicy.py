"""E12: E2's replay-validity design on real tasks; does replay rank E2's policy zoo like their true online value?

    python experiments/dream-rsi/e12_real_offpolicy.py [--domains sumdiff,autocorr] [--workers 2] [--quality-only]
"""
import numpy as np
from _common import (domain_of, live_search, online_eq1, paired, parse_args, pmap, record_world, save, spearman,
                     summ)

from e2_offpolicy_validity import zoo
from rsi.dream import Eq1Objective, ReplayEvaluator

W, GRID, K, N_TRUE = 6, (6, 4), 5, 40


def record(job):
    dom_name, seed = job
    return record_world(domain_of(dom_name, seed), W, GRID, seed, trace=False)


def online(job):
    dom_name, name, code, seed, b1, b2 = job
    r = live_search(domain_of(dom_name, seed), W, GRID, seed, code, hard_max_branch=12, hard_max_refine=12,
                    round_budget=None, trace=False).trajectory[0]
    return name, seed, online_eq1(r, b1, b2), r["round_best"], r["N"]


def study(dom_name, workers, quality_only=False):
    """beta1/beta2 scaled by the recorded parallel-refine gain G_d (0 with ``quality_only``)."""
    worlds = pmap(record, [(dom_name, 100 + i) for i in range(K)], workers)
    G = float(np.mean([w.ceiling - w.root_score for w in worlds]))
    b1, b2 = (0.0, 0.0) if quality_only else (0.01 * G, 0.005 * G)
    ev = ReplayEvaluator(Eq1Objective(beta1=b1, beta2=b2, normalize=False, support="no_reward"), W=W, fallback=GRID,
                         runner="inprocess", hard_max=(12, 12))
    pol = zoo()
    replay, support = {}, {}
    for n, c in pol.items():
        rep = ev.evaluate(c, worlds)
        replay[n] = rep.value
        support[n] = not any(e.out_of_support for e in rep.episodes)
    true, best_q, probes = {}, {}, {}
    for n, s, v, q, N in pmap(online, [(dom_name, n, c, 1000 + s, b1, b2) for n, c in pol.items()
                                       for s in range(N_TRUE)], workers):
        true.setdefault(n, {})[s] = v
        best_q.setdefault(n, {})[s] = q
        probes.setdefault(n, {})[s] = N
    tv = {n: float(np.mean(list(true[n].values()))) for n in pol}
    ins = [n for n in pol if support[n]]
    pick = max(pol, key=lambda n: replay[n])
    seeds = sorted(true["parallel_refine"])
    vs_pi1 = paired([true["parallel_refine"][s] for s in seeds], [true[pick][s] for s in seeds])
    q_vs_pi1 = paired([best_q["parallel_refine"][s] for s in seeds], [best_q[pick][s] for s in seeds])
    rng = np.random.default_rng(0)  # bootstrap CI of the in-support Spearman, resampling policies
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
    def extra(ap):
        ap.add_argument("--domains", default="sumdiff,autocorr")
        ap.add_argument("--quality-only", action="store_true", help="E12q: beta1 = beta2 = 0")

    a = parse_args("E12 replay validity on real tasks", default_seeds=K, extra=extra)
    out = {}
    for d in a.domains.split(","):
        r = out[d] = study(d, a.workers, quality_only=a.quality_only)
        print(f"[{d}] Spearman in-support {r['spearman_in_support']:.3f} {r['spearman_in_support_ci']}, all "
              f"{r['spearman_all']:.3f}; replay pick {r['replay_pick']} (true best {r['true_best']}, regret "
              f"{r['regret_of_replay_pick']:.4g}); pick - pi1 online {r['pick_minus_parallel_refine_online_value']}",
              flush=True)
    holds = all(out[d]["passes_rho"] for d in out)
    failing = ", ".join(d for d in out if not out[d]["passes_rho"])

    def config(objective):
        return {"W": W, "recording_grid": GRID, "recorded_worlds": K, "true_value_searches": N_TRUE,
                "objective": objective, "policies": list(zoo())}

    if a.quality_only:
        verdict = ("L1 stays REPRODUCED (quality-only rho >= 0.7 in both domains)" if holds else
                   "L1 -> PARTIAL (quality-only rho < 0.7 in " + failing + ")")
        save("e12q_real_offpolicy_quality", {"config": config("quality only: raw Eq.1 with beta1 = beta2 = 0, "
                                                              "support=no_reward"), "results": out, "verdict": verdict})
    else:
        verdict = ("L1 -> REPRODUCED" if holds and all(out[d]["pick_beats_pi1"] for d in out) else
                   "L1 stays PARTIAL" + ("" if holds else " (Spearman < 0.7 in " + failing + ")"))
        save("e12_real_offpolicy", {"config": config("raw Eq.1, beta scaled by G_d, support=no_reward"),
                                    "results": out, "verdict": verdict})
    print(verdict)


if __name__ == "__main__":
    main()
