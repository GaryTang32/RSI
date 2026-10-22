"""E9: argmax selection on past searches overfits them; held-out worlds plus a noise margin (GuardedSelector) help.

    python experiments/dream-rsi/e9_overfitting.py [--llm sim|claude:haiku] [--seeds N] [--quick]
"""
import numpy as np
from _common import developer_of, domain_of, figure, parse_args, pmap, record_world, sandbox_of, save, save_figure, summ

from rsi.dream import (DevContext, GuardedSelector, ReplayEvaluator, Selector, VersionRecord, code_of,
                       parallel_refine)
from rsi.dream.evaluator import PolicyReport, diagnostics

W, GRID = 4, (6, 4)
M_LIST = [2, 4, 8, 16, 32]


def record(seed):
    return record_world(domain_of("synthetic", seed), W, GRID, seed)


def restrict(rep, idx):
    if not rep.episodes:  # rejected revision: nothing to restrict
        return rep
    eps = [rep.episodes[i] for i in idx]
    return PolicyReport(rep.policy_id, rep.label, rep.objective, float(np.mean([rep.per_world[i] for i in idx])),
                        [rep.per_world[i] for i in idx], eps, None, [], diagnostics(eps))


def phase(job):
    """One dreaming phase on t past worlds; for every M, the selected version's value on 20 fresh worlds."""
    rep_i, t, guarded, llm, m_list = job
    H = [record(400 + 37 * rep_i + i) for i in range(t)]
    fresh = [record(90000 + 101 * rep_i + j) for j in range(20)]
    ev = ReplayEvaluator(W=W, fallback=GRID, runner=sandbox_of(llm))
    sel = GuardedSelector() if guarded else Selector()
    dev_idx = sel.dev_worlds(t)
    dev = developer_of(llm, sigma=0.35, n_random=3)
    inc = VersionRecord(0, code_of(parallel_refine()))
    inc.report = ev.evaluate(inc.code, H)
    versions, fresh_v = [inc], [ev.evaluate(inc.code, fresh).value]
    for m in range(1, max(m_list)):
        fb = [VersionRecord(v.index, v.code, restrict(v.report, dev_idx), v.change) for v in versions]
        rev = dev.revise(DevContext(1, fb, [], [], inc.code, "eq1", W, first_in_phase=(m == 1)), seed=rep_i * 1000 + m)
        if not rev.ok:  # a rejected LLM revision is a candidate that can never win
            rec = VersionRecord(m, inc.code, None, f"rejected: {rev.error}")
            rec.report = PolicyReport("", "rejected", "rejected", float("-inf"), [float("-inf")] * t, [], None, [], {})
            versions.append(rec)
            fresh_v.append(float("-inf"))
            continue
        versions.append(VersionRecord(m, rev.code, ev.evaluate(rev.code, H), rev.change))
        fresh_v.append(ev.evaluate(rev.code, fresh).value)
    out = []
    for M in m_list:
        s = sel.select([v.report for v in versions[:M]])
        v = versions[s.index]
        dev_v = float(np.mean([v.report.per_world[i] for i in dev_idx]))
        out.append({"rep": rep_i, "t": t, "selector": "guarded" if guarded else "argmax", "M": M, "selected": s.index,
                    "train_V": v.report.value, "dev_V": dev_v, "fresh_V": fresh_v[s.index],
                    "gap": dev_v - fresh_v[s.index], "oracle_fresh_V": max(fresh_v[:M]),
                    "incumbent_fresh_V": fresh_v[0]})
    return out


def main():
    a = parse_args("E9 overfitting to past worlds", default_seeds=12)
    ts = [1, 3] if a.quick else [1, 3, 6]
    m_list = M_LIST if a.llm == "sim" else [2, 4]  # live LLM developer: bound the number of developer calls
    jobs = [(i, t, g, a.llm, m_list) for i in range(a.seeds) for t in ts for g in (False, True)]
    rows = [r for rs in pmap(phase, jobs, a.workers) for r in rs]
    res = {}
    for t in ts:
        for selname in ("argmax", "guarded"):
            for M in m_list:
                rr = [r for r in rows if r["t"] == t and r["selector"] == selname and r["M"] == M]
                res[f"t{t}_{selname}_M{M}"] = {
                    "gap": summ([r["gap"] for r in rr]), "fresh_V": summ([r["fresh_V"] for r in rr]),
                    "dev_V": summ([r["dev_V"] for r in rr]),
                    "regret_vs_best_candidate": summ([r["oracle_fresh_V"] - r["fresh_V"] for r in rr]),
                    "kept_incumbent": float(np.mean([r["selected"] == 0 for r in rr])),
                    "worse_than_incumbent_fresh": float(np.mean([r["fresh_V"] < r["incumbent_fresh_V"] - 1e-9
                                                                for r in rr]))}
    mean = {k: {m: v[m]["mean"] for m in ("gap", "fresh_V")} for k, v in res.items()}
    for t in ts:
        print(f"t = {t} past world(s):")
        for selname in ("argmax", "guarded"):
            row = {M: mean[f"t{t}_{selname}_M{M}"] for M in m_list}
            print(f"   {selname:<8} " + "  ".join(f"M={M}: gap {row[M]['gap']:+.3f} fresh {row[M]['fresh_V']:.3f}"
                                                for M in m_list))
    plt, png = figure("e9_overfitting")
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.6))
    for t, ls in zip(ts, ("-", "--", ":")):
        for selname, col in (("argmax", "#C44E52"), ("guarded", "#4C72B0")):
            for ax, key in zip(axes, ("gap", "fresh_V")):
                ax.plot(m_list, [mean[f"t{t}_{selname}_M{M}"][key] for M in m_list], ls, color=col, marker="o",
                        label=f"{selname}, t={t}")
    for ax, yl in zip(axes, ("development-world V minus fresh-world V", "fresh-world V of the selected policy")):
        ax.set_xscale("log", base=2)
        ax.set(xlabel="versions per dreaming phase M", ylabel=yl)
        ax.legend(fontsize=7)
    save_figure(fig, png)
    first, last = m_list[0], m_list[-1]
    grows = all(mean[f"t{t}_argmax_M{last}"]["gap"] > mean[f"t{t}_argmax_M{first}"]["gap"] for t in ts)
    narrower = [t for t in ts if t > 1 and mean[f"t{t}_guarded_M{last}"]["gap"] < mean[f"t{t}_argmax_M{last}"]["gap"]]
    better_fresh = [t for t in ts if t > 1 and mean[f"t{t}_guarded_M{last}"]["fresh_V"] >=
                    mean[f"t{t}_argmax_M{last}"]["fresh_V"]]
    verdict = (f"overfitting gap grows with M under the paper's argmax: {'YES' if grows else 'NO'} "
               f"(t=1: {mean['t1_argmax_M2']['gap']:+.3f} -> {mean[f't1_argmax_M{last}']['gap']:+.3f}); "
               f"guarded selector narrows the gap at M={last} for t in {narrower} and matches/improves fresh-world "
               f"value for t in {better_fresh} (it cannot help with a single past world)")
    print(verdict)
    developer = "ParametricMutator(sigma=0.35, n_random=3)" if a.llm == "sim" else a.llm
    save("e9_overfitting", {"config": {"t": ts, "M": m_list, "reps": a.seeds, "fresh_worlds": 20, "W": W, "grid": GRID,
                                       "developer": developer,
                                       "guard": "GuardedSelector(holdout every 3rd world, delta=max(0.01, 2 SE))",
                                       "llm": a.llm},
                            "results": res, "rows": rows, "figure": str(png), "verdict": verdict}, a.out)


if __name__ == "__main__":
    main()
