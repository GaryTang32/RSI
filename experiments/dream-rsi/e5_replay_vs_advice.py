"""E5 - Replay beats written advice (2 x 2 ablation).

Claim [paper:§5.1 Fig.5; doc "Summarizing past searches into written guidance ... did
worse than no guidance at all"]: abstracting history into directional guidance
"over-constrains the search space and impedes diverse exploration"; the digitized
figure shows guidance helping early and losing at equal final budget.

Arms: Fixed, Fixed+Guidance, Dream, Dream+Guidance at equal agent-call budgets. The
guidance summarizer (mock: focus on the historically best directions; ``--llm claude``:
an LLM summary) is injected as ``$direction_guidance`` and steers new roots toward the
advised directions (strength 0.8). Measured: best at 25/50/100% of the budget and the
diversity of directions opened. Domains: synthetic worlds (whose directions deplete
once exploited - an *assumption* of the generator, so this arm is a mechanism check)
and circle packing (real optimisation; directions are mechanism classes).

    python experiments/dream-rsi/e5_replay_vs_advice.py [--llm sim|claude:haiku] [--seeds N] [--quick]
"""
import numpy as np
from _common import agent_of, best_at, developer_of, domain_of, figure, fmt, llm_of, paired, parse_args, pmap, save, summ

from rsi.dream import Config, LLMGuidanceSummarizer, MockGuidanceSummarizer, run

ARMS = {"fixed": (False, False), "fixed+guidance": (False, True), "dream": (True, False),
        "dream+guidance": (True, True)}
SET = {"synthetic": dict(grid=(6, 4), rounds=8, M=6), "circlepack": dict(grid=(5, 3), rounds=5, M=4)}


def one(job):
    dom_name, seed, arm, llm, quick = job
    dream, guidance = ARMS[arm]
    st = SET[dom_name]
    rounds = min(st["rounds"], 4) if quick else st["rounds"]
    per_round = st["grid"][0] * (st["grid"][1] + 1)
    dom = domain_of(dom_name, seed)
    cfg = Config(rounds=40 if dream else rounds, W=4, branch_count=st["grid"][0], refine_count=st["grid"][1],
                 M=st["M"], dream=dream, guidance=guidance, sandbox="inprocess", seed=seed,
                 max_calls=rounds * per_round, agent_workers=1 if llm == "sim" else 4)
    summ_llm = llm_of(llm)
    summarizer = LLMGuidanceSummarizer(summ_llm) if summ_llm is not None else MockGuidanceSummarizer()
    res = run(dom, config=cfg, agent=agent_of(dom, llm), developer=developer_of(llm) if dream else None,
              summarizer=summarizer)
    dirs = [[t.get("direction") for b, t in sorted(w.branch_tags.items()) if w.branches().get(b)]
            for w in res.meta["worlds"]]
    distinct = [len(set(d)) / max(1, len(d)) for d in dirs[1:]] or [1.0]
    allds = [x for d in dirs for x in d if x]
    p = np.array([allds.count(x) for x in set(allds)], float)
    p = p / p.sum() if p.sum() else p
    entropy = float(-(p * np.log(p + 1e-12)).sum())
    return {"domain": dom_name, "seed": seed, "arm": arm, "budget": rounds * per_round,
            "curve": [(0, res.meta["seed_score"])] + [(r["cum_calls"], r["best"]) for r in res.trajectory],
            "final_best": res.meta["best_score"], "distinct_dir_frac_after_r1": float(np.mean(distinct)),
            "direction_entropy": entropy}


def main():
    a = parse_args("E5 replay vs written advice", default_seeds=15, extra=lambda ap: ap.add_argument(
        "--domains", default="synthetic,circlepack"))
    doms = a.domains.split(",")
    n_seeds = {"synthetic": a.seeds, "circlepack": max(2, a.seeds // 4)}
    if a.llm != "sim":
        n_seeds = {d: 1 for d in doms}
    out = {}
    plt, png = figure("e5_replay_vs_advice")
    fig, axes = plt.subplots(1, len(doms), figsize=(4.6 * len(doms), 3.5), squeeze=False)
    for i, d in enumerate(doms):
        rows = pmap(one, [(d, s, arm, a.llm, a.quick) for s in range(n_seeds[d]) for arm in ARMS], a.workers)
        budget = rows[0]["budget"]
        res = {"budget": budget}
        by = {arm: sorted([r for r in rows if r["arm"] == arm], key=lambda r: r["seed"]) for arm in ARMS}
        for arm, rs in by.items():
            res[arm] = {f"best_at_{int(f * 100)}pct": summ([best_at(r["curve"], f * budget) for r in rs])
                        for f in (0.25, 0.5, 1.0)}
            res[arm]["distinct_dir_frac"] = summ([r["distinct_dir_frac_after_r1"] for r in rs])
            res[arm]["direction_entropy"] = summ([r["direction_entropy"] for r in rs])
        for base in ("fixed", "dream"):
            for f in (0.25, 0.5, 1.0):
                x = [best_at(r["curve"], f * budget) for r in by[base]]
                y = [best_at(r["curve"], f * budget) for r in by[base + "+guidance"]]
                res[f"{base}_guidance_minus_unguided_at_{int(f * 100)}pct"] = paired(x, y)
        x = [best_at(r["curve"], budget) for r in by["fixed"]]
        y = [best_at(r["curve"], budget) for r in by["dream"]]
        res["dream_minus_fixed_at_budget"] = paired(x, y)
        out[d] = res
        print(f"[{d}] budget {budget}")
        for arm in ARMS:
            print(f"   {arm:<15} best@25% {fmt(res[arm]['best_at_25pct'])} @100% {fmt(res[arm]['best_at_100pct'])} "
                  f"distinct-dirs {res[arm]['distinct_dir_frac']['mean']:.2f}")
        for base in ("fixed", "dream"):
            e, l_ = res[f"{base}_guidance_minus_unguided_at_25pct"], res[f"{base}_guidance_minus_unguided_at_100pct"]
            print(f"   guidance effect on {base}: early {e['mean_diff']:+.4f} [{e['lo']:+.4f},{e['hi']:+.4f}]  final "
                  f"{l_['mean_diff']:+.4f} [{l_['lo']:+.4f},{l_['hi']:+.4f}]")
        ax = axes[0][i]
        grid = np.linspace(0, budget, 50)
        for arm, col, ls in (("fixed", "#C44E52", "-"), ("fixed+guidance", "#C44E52", "--"), ("dream", "#4C72B0", "-"),
                             ("dream+guidance", "#4C72B0", "--")):
            ys = np.array([[best_at(r["curve"], c) for c in grid] for r in by[arm]], float)
            ax.plot(grid, np.nanmean(ys, 0), color=col, ls=ls, label=arm)
        ax.set_title(d, fontsize=9)
        ax.set_xlabel("cumulative agent calls")
        ax.set_ylabel("best score (mean over seeds)")
        ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(png, dpi=130)
    verdict = {}
    for d, r in out.items():
        parts = []
        for base in ("fixed", "dream"):
            fin = r[f"{base}_guidance_minus_unguided_at_100pct"]
            early = r[f"{base}_guidance_minus_unguided_at_25pct"]
            tag = "worse" if fin["hi"] < 0 else ("better" if fin["lo"] > 0 else "no significant difference")
            parts.append(f"{base}+guidance final {tag} ({fin['mean_diff']:+.4f}), early {early['mean_diff']:+.4f}")
        verdict[d] = "; ".join(parts)
        print(f"verdict [{d}]: {verdict[d]}")
    save("e5_replay_vs_advice", {"config": {"arms": ARMS, "settings": SET, "seeds": n_seeds, "guidance_strength": 0.8,
                                            "summarizer": "mock (top-2 directions)" if a.llm == "sim" else a.llm},
                                 "results": out, "figure": str(png), "verdict": verdict}, a.out)


if __name__ == "__main__":
    main()
