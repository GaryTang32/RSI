"""E9 - Selection can overfit past searches; held-out worlds + a noise margin help.

Critique [doc "Choosing the strategy that scores best on past searches can overfit those
searches ... keep some past searches aside for checking, and ignore wins smaller than the
noise"; spec §8.4]: the paper's selector is the argmax of the mean replay score on the
same worlds the developer inspected.

Per repetition: t past worlds H (t = 1, 3, 6) recorded by parallel refine; a dreaming phase
with M - 1 developer revisions starting from pi_1 (the mutator with wide random edits);
select with (a) the paper's argmax over H, or (b) the GuardedSelector (every third world
held out from the developer and from ranking; deploy only if the held-out gain exceeds
max(0.01, 2 * SE)). The selected policy is scored on 20 fresh recorded worlds from the same
generator (its generalization). Reported: train-minus-fresh gap and fresh value vs M.

    python experiments/dream-rsi/e9_overfitting.py [--seeds N] [--quick]
"""
import numpy as np
from _common import figure, parse_args, pmap, save, summ

from rsi.dream import (Config, DevContext, DreamRSILoop, GuardedSelector, ParametricMutator, ReplayEvaluator, Selector,
                       VersionRecord, code_of, parallel_refine)
from rsi.dream.evaluator import PolicyReport, diagnostics
from rsi.domains.discovery import SyntheticConfig, SyntheticDomain

W, GRID = 4, (6, 4)
M_LIST = [2, 4, 8, 16, 32]


def record(seed):
    dom = SyntheticDomain(SyntheticConfig(seed=seed))
    cfg = Config(rounds=1, W=W, branch_count=GRID[0], refine_count=GRID[1], dream=False, sandbox="inprocess",
                 seed=seed, agent_workers=1)
    return DreamRSILoop(dom.as_task(), dom.mock_agent(), config=cfg).run().meta["worlds"][0]


def restrict(rep, idx):
    eps = [rep.episodes[i] for i in idx]
    return PolicyReport(rep.policy_id, rep.label, rep.objective, float(np.mean([rep.per_world[i] for i in idx])),
                        [rep.per_world[i] for i in idx], eps, None, [], diagnostics(eps))


def phase(job):
    rep_i, t, guarded = job
    H = [record(400 + 37 * rep_i + i) for i in range(t)]
    fresh = [record(90000 + 101 * rep_i + j) for j in range(20)]
    ev = ReplayEvaluator(W=W, fallback=GRID, runner="inprocess")
    sel = GuardedSelector() if guarded else Selector()
    dev_idx = sel.dev_worlds(t)
    dev = ParametricMutator(sigma=0.35, n_random=3)
    inc = VersionRecord(0, code_of(parallel_refine()))
    inc.report = ev.evaluate(inc.code, H)
    versions, fresh_v = [inc], [ev.evaluate(inc.code, fresh).value]
    for m in range(1, max(M_LIST)):
        fb = [VersionRecord(v.index, v.code, restrict(v.report, dev_idx), v.change) for v in versions]
        rev = dev.revise(DevContext(1, fb, [], [], inc.code, "eq1", W, first_in_phase=(m == 1)), seed=rep_i * 1000 + m)
        rec = VersionRecord(m, rev.code, ev.evaluate(rev.code, H), rev.change)
        versions.append(rec)
        fresh_v.append(ev.evaluate(rev.code, fresh).value)
    out = []
    for M in M_LIST:
        reps = [v.report for v in versions[:M]]
        s = sel.select(reps)
        v = versions[s.index]
        train = v.report.value
        dev_v = float(np.mean([v.report.per_world[i] for i in dev_idx]))
        out.append({"rep": rep_i, "t": t, "selector": "guarded" if guarded else "argmax", "M": M, "selected": s.index,
                    "train_V": train, "dev_V": dev_v, "fresh_V": fresh_v[s.index], "gap": dev_v - fresh_v[s.index],
                    "oracle_fresh_V": max(fresh_v[:M]), "incumbent_fresh_V": fresh_v[0]})
    return out


def main():
    a = parse_args("E9 overfitting to past worlds", default_seeds=12)
    ts = [1, 3] if a.quick else [1, 3, 6]
    rows = [r for rs in pmap(phase, [(i, t, g) for i in range(a.seeds) for t in ts for g in (False, True)], a.workers)
            for r in rs]
    res = {}
    for t in ts:
        for selname in ("argmax", "guarded"):
            for M in M_LIST:
                rr = [r for r in rows if r["t"] == t and r["selector"] == selname and r["M"] == M]
                res[f"t{t}_{selname}_M{M}"] = {"gap": summ([r["gap"] for r in rr]), "fresh_V": summ([r["fresh_V"] for r in rr]),
                                               "dev_V": summ([r["dev_V"] for r in rr]),
                                               "regret_vs_best_candidate": summ([r["oracle_fresh_V"] - r["fresh_V"] for r in rr]),
                                               "kept_incumbent": float(np.mean([r["selected"] == 0 for r in rr])),
                                               "worse_than_incumbent_fresh": float(np.mean([r["fresh_V"] < r["incumbent_fresh_V"] - 1e-9
                                                                                           for r in rr]))}
    for t in ts:
        print(f"t = {t} past world(s):")
        for selname in ("argmax", "guarded"):
            print(f"   {selname:<8} " + "  ".join(
                f"M={M}: gap {res[f't{t}_{selname}_M{M}']['gap']['mean']:+.3f} fresh {res[f't{t}_{selname}_M{M}']['fresh_V']['mean']:.3f}"
                for M in M_LIST))
    plt, png = figure("e9_overfitting")
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.6))
    for t, ls in zip(ts, ("-", "--", ":")):
        for selname, col in (("argmax", "#C44E52"), ("guarded", "#4C72B0")):
            axes[0].plot(M_LIST, [res[f"t{t}_{selname}_M{M}"]["gap"]["mean"] for M in M_LIST], ls, color=col, marker="o",
                         label=f"{selname}, t={t}")
            axes[1].plot(M_LIST, [res[f"t{t}_{selname}_M{M}"]["fresh_V"]["mean"] for M in M_LIST], ls, color=col,
                         marker="o", label=f"{selname}, t={t}")
    for ax, yl in zip(axes, ("development-world V minus fresh-world V", "fresh-world V of the selected policy")):
        ax.set_xscale("log", base=2)
        ax.set_xlabel("versions per dreaming phase M")
        ax.set_ylabel(yl)
        ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(png, dpi=130)
    grows = all(res[f"t{t}_argmax_M{M_LIST[-1]}"]["gap"]["mean"] > res[f"t{t}_argmax_M{M_LIST[0]}"]["gap"]["mean"] for t in ts)
    narrower = [t for t in ts if t > 1 and res[f"t{t}_guarded_M{M_LIST[-1]}"]["gap"]["mean"] <
                res[f"t{t}_argmax_M{M_LIST[-1]}"]["gap"]["mean"]]
    better_fresh = [t for t in ts if t > 1 and res[f"t{t}_guarded_M{M_LIST[-1]}"]["fresh_V"]["mean"] >=
                    res[f"t{t}_argmax_M{M_LIST[-1]}"]["fresh_V"]["mean"]]
    verdict = (f"overfitting gap grows with M under the paper's argmax: {'YES' if grows else 'NO'} "
               f"(t=1: {res['t1_argmax_M2']['gap']['mean']:+.3f} -> {res[f't1_argmax_M{M_LIST[-1]}']['gap']['mean']:+.3f}); "
               f"guarded selector narrows the gap at M={M_LIST[-1]} for t in {narrower} and matches/improves fresh-world "
               f"value for t in {better_fresh} (it cannot help with a single past world)")
    print(verdict)
    save("e9_overfitting", {"config": {"t": ts, "M": M_LIST, "reps": a.seeds, "fresh_worlds": 20, "W": W, "grid": GRID,
                                       "developer": "ParametricMutator(sigma=0.35, n_random=3)",
                                       "guard": "GuardedSelector(holdout every 3rd world, delta=max(0.01, 2 SE))",
                                       "llm": "not used"},
                            "results": res, "rows": rows, "figure": str(png), "verdict": verdict}, a.out)


if __name__ == "__main__":
    main()
