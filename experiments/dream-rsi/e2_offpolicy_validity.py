"""E2 - Off-policy replay ranks policies usefully (Track A, synthetic worlds with ground truth).

Claim [paper:Fig.2; §3]: one recorded search lets many candidate policies be evaluated
off-policy. The paper never validates replay against online outcomes (critique §8.3);
the synthetic generator lets us: the *true* value of a policy is its mean online Eq.-1
score over fresh worlds (Monte Carlo, the policy's own unclipped plan), the *replay*
value its mean Eq.-1 score over K worlds recorded by parallel refine (6 x 5 grid).
We report the Spearman rank correlation across a ~20-policy zoo, stratified by whether
a policy's plan fits inside the recorded support, and repeat with context coupling
(outcomes depend on how many sibling attempts the agent read), which biases replay.

With ``--llm claude:haiku`` two policies written by the LLM developer (Listing-2 prompt, a
short dreaming chain on the recorded worlds) join the zoo; like every LLM-written policy they
only ever run in the subprocess sandbox.

    python experiments/dream-rsi/e2_offpolicy_validity.py [--llm sim|claude:haiku] [--seeds K] [--quick]
"""
import numpy as np
from _common import figure, fmt, llm_policies, parse_args, pmap, runner_for, save, spearman, summ

from rsi.dream import (Config, DreamRSILoop, Eq1Objective, ReplayEvaluator, adaptive, code_of, parallel_refine,
                       rules)
from rsi.domains.discovery import SyntheticConfig, SyntheticDomain

W, GRID = 4, (6, 4)
OBJ = dict(beta1=0.01, beta2=0.005)


def zoo():
    z = {"parallel_refine": code_of(parallel_refine())}
    for w, r in ((2, 4), (4, 2), (6, 1), (3, 3), (6, 2)):
        z[f"grid_{w}x{r}"] = code_of(rules(plan_w=w, plan_r=r))
    for g in (0.005, 0.02, 0.05):
        z[f"gainstop_{g}"] = code_of(rules(gain_stop=g))
    z["deepen_top1"] = code_of(rules(top_k=1))
    z["deepen_top2"] = code_of(rules(top_k=2))
    z["serial"] = code_of(rules(cap=1))
    z["stop_after_2"] = code_of(rules(max_rounds=2))
    for b in (0.2, 0.6, 1.0):
        z[f"adaptive_b{b}"] = code_of(adaptive(default_beta=b))
    z["adaptive_lean"] = code_of(adaptive(patience_lo=0.5, patience_hi=1.0, prune_lo=0.1, prune_hi=0.3))
    # plans beyond the recording grid (out of support)
    z["deeper_6x8"] = code_of(rules(plan_w=6, plan_r=8, top_k=2))
    z["wider_10x4"] = code_of(rules(plan_w=10, plan_r=4))
    z["deep_serial_2x9"] = code_of(rules(plan_w=2, plan_r=9, top_k=1))
    return z


def online_value(job):
    name, code, seed, coupling = job
    dom = SyntheticDomain(SyntheticConfig(seed=seed, context_coupling=coupling))
    # the TRUE online value of the policy's OWN plan (a replay-validity study, not an arm comparison): no
    # per-round call cap, so plans beyond the recording grid really spend their extra probes online
    cfg = Config(rounds=1, W=W, branch_count=GRID[0], refine_count=GRID[1], dream=False, sandbox=runner_for(name),
                 seed=seed, hard_max_branch=12, hard_max_refine=12, agent_workers=1, round_budget=None)
    loop = DreamRSILoop(dom.as_task(), dom.mock_agent(), config=cfg, initial_policy=code)
    res = loop.run()
    r = res.trajectory[0]
    return name, seed, r["round_best"] - OBJ["beta1"] * r["N"] + OBJ["beta2"] * r["N"] / max(1, r["k"])


def record(seed, coupling):
    dom = SyntheticDomain(SyntheticConfig(seed=seed, context_coupling=coupling))
    cfg = Config(rounds=1, W=W, branch_count=GRID[0], refine_count=GRID[1], dream=False, sandbox="inprocess",
                 seed=seed, agent_workers=1)
    return DreamRSILoop(dom.as_task(), dom.mock_agent(), config=cfg).run().meta["worlds"][0]


def evaluators(runner):
    ev = ReplayEvaluator(Eq1Objective(normalize=False, support="clip", **OBJ), W=W, fallback=GRID, runner=runner,
                         hard_max=(12, 12))
    ev_nr = ReplayEvaluator(Eq1Objective(normalize=False, support="no_reward", **OBJ), W=W, fallback=GRID,
                            runner=runner, hard_max=(12, 12))
    return ev, ev_nr


def study(policies, K, n_true, coupling, workers, worlds=None):
    worlds = worlds or [record(100 + i, coupling) for i in range(K)]
    evs = {r: evaluators(r) for r in ("inprocess", "subprocess")}
    replay, support, replay_nr = {}, {}, {}
    for name, code in policies.items():
        ev, ev_nr = evs[runner_for(name)]
        rep = ev.evaluate(code, worlds)
        replay[name] = rep.value
        replay_nr[name] = ev_nr.evaluate(code, worlds).value
        support[name] = not any(e.out_of_support for e in rep.episodes)
    jobs = [(n, c, 1000 + s, coupling) for n, c in policies.items() for s in range(n_true)]
    true: dict[str, list] = {}
    for name, seed, v in pmap(online_value, jobs, workers):
        true.setdefault(name, []).append(v)
    tv = {n: float(np.mean(v)) for n, v in true.items()}
    names = list(policies)
    ins = [n for n in names if support[n]]
    out = [n for n in names if not support[n]]
    res = {"replay": replay, "replay_no_reward": replay_nr, "true": tv, "true_ci": {n: summ(true[n]) for n in names},
           "in_support": support,
           "spearman_all": spearman([replay[n] for n in names], [tv[n] for n in names]),
           "spearman_all_no_reward": spearman([replay_nr[n] for n in names], [tv[n] for n in names]),
           "spearman_in_support": spearman([replay[n] for n in ins], [tv[n] for n in ins]),
           "bias_in_support": float(np.mean([replay[n] - tv[n] for n in ins])),
           "bias_out_of_support": float(np.mean([replay[n] - tv[n] for n in out])) if out else None,
           "top1_replay": max(names, key=lambda n: replay[n]), "top1_true": max(names, key=lambda n: tv[n]),
           "regret_of_replay_pick": max(tv.values()) - tv[max(names, key=lambda n: replay[n])]}
    return res


def main():
    a = parse_args("E2 off-policy validity", default_seeds=5)
    n_true = 20 if a.quick else 60
    pol = zoo()
    if a.llm != "sim":   # LLM-written policies join the zoo (dreamed on the same recorded worlds)
        rec_worlds = [record(100 + i, 0.0) for i in range(a.seeds)]
        pol.update(llm_policies(a.llm, rec_worlds, n=2, W=W, fallback=GRID, hard_max=(12, 12),
                                objective=Eq1Objective(normalize=False, **OBJ)))
    base = study(pol, a.seeds, n_true, 0.0, a.workers)
    coup = study(pol, a.seeds, n_true, 0.6, a.workers)
    # replay estimate vs number of recorded worlds (sampling noise of the simulator itself)
    by_k = {}
    for k in ([1, 3] if a.quick else [1, 2, 5, 10]):
        rs = []
        for rep in range(3):
            worlds = [record(5000 + 100 * rep + i, 0.0) for i in range(k)]
            evs = {r: evaluators(r)[0] for r in ("inprocess", "subprocess")}
            vals = {n: evs[runner_for(n)].evaluate(c, worlds).value for n, c in pol.items()}
            rs.append(spearman([vals[n] for n in pol], [base["true"][n] for n in pol]))
        by_k[k] = summ(rs)
    print(f"Spearman(replay, true) all={base['spearman_all']:.3f}  in-support={base['spearman_in_support']:.3f}")
    print(f"bias in-support {base['bias_in_support']:+.4f}, out-of-support {base['bias_out_of_support']:+.4f} "
          f"(clip); Spearman all with the no-reward support rule = {base['spearman_all_no_reward']:.3f}")
    print(f"replay pick {base['top1_replay']} vs true best {base['top1_true']} (regret {base['regret_of_replay_pick']:.4f})")
    print(f"with context coupling: Spearman all={coup['spearman_all']:.3f}, in-support bias {coup['bias_in_support']:+.4f}")
    for k, s in by_k.items():
        print(f"  K={k} recorded worlds: Spearman {fmt(s, 3)}")
    plt, png = figure("e2_offpolicy_validity")
    fig, axes = plt.subplots(1, 2, figsize=(9, 3.8))
    for ax, st, title in ((axes[0], base, "independent outcomes"), (axes[1], coup, "context coupling 0.6")):
        for n in pol:
            c = "#4C72B0" if st["in_support"][n] else "#C44E52"
            ax.scatter(st["true"][n], st["replay"][n], c=c, s=18)
        lo = min(min(st["true"].values()), min(st["replay"].values()))
        hi = max(max(st["true"].values()), max(st["replay"].values()))
        ax.plot([lo, hi], [lo, hi], "k:", lw=1)
        ax.set_xlabel("true online Eq.1 value")
        ax.set_ylabel(f"replay value ({a.seeds} worlds)")
        ax.set_title(f"{title}: rho={st['spearman_all']:.2f} (blue in-support, red out)", fontsize=8)
    fig.tight_layout()
    fig.savefig(png, dpi=130)
    ok = base["spearman_in_support"] > 0.7
    verdict = (f"REPRODUCED for in-support policies: replay ranks them like their true online value "
               f"(Spearman {base['spearman_in_support']:.2f}; mean bias {base['bias_in_support']:+.3f}). Caveats measured: "
               f"(1) plans beyond the recorded grid are mis-estimated - clipping them to the record hides the extra "
               f"probes they spend online, so replay OVER-estimates them here (bias {base['bias_out_of_support']:+.3f}); "
               f"the Listing-2 rule 'out-of-support plans earn no replay reward' fixes the ranking "
               f"(all-policy Spearman {base['spearman_all']:.2f} -> {base['spearman_all_no_reward']:.2f}); "
               f"(2) when outcomes depend on how many sibling attempts the agent read, replay is biased "
               f"(in-support bias {coup['bias_in_support']:+.3f}, Spearman {coup['spearman_all']:.2f}); "
               f"(3) the replay-optimal policy is not the true optimum (regret {base['regret_of_replay_pick']:.3f}).") \
        if ok else f"NOT reproduced: in-support Spearman {base['spearman_in_support']:.2f}"
    save("e2_offpolicy_validity", {"config": {"W": W, "recording_grid": GRID, "recorded_worlds": a.seeds,
                                              "true_value_worlds": n_true, "objective": OBJ | {"normalize": False},
                                              "policies": list(pol),
                                              "llm": a.llm if a.llm != "sim" else "not used (replay only)",
                                              "llm_policies": {n: c for n, c in pol.items() if n.startswith("llm_")}},
                                   "independent": base, "context_coupling_0.6": coup,
                                   "spearman_vs_recorded_worlds": by_k, "figure": str(png), "verdict": verdict}, a.out)
    print(verdict)


if __name__ == "__main__":
    main()
