"""E6: overfitting with few training examples, D_pareto = D_train vs a separate validation set (RuleWorld).

Run: python experiments/gepa/e6_overfitting.py [--llm sim|claude:haiku] [--seeds N] [--quick]
"""
from __future__ import annotations

from _common import (RESULTS, by_seed, ci_err, fmt, paired, parse_args, pool_map, pyplot, reflection_llm, save,
                     summarize)
from rsi.domains.ruleworld import ReflectionProfile, make_domain
from rsi.gepa import Config, run

NTRAIN = [6, 15, 30, 60]
MODES = ["separate_val", "val=train"]
QS = [0.15, 0.5]


def job(spec):
    n, mode, q, seed, B, llm = spec
    d = make_domain(seed=seed, n_train=n, val_is_train=(mode == "val=train"))
    propose = reflection_llm(llm, d.world, profile=ReflectionProfile(q_copy=q))
    res = run(d, d.seed_artifact(), llm_propose=propose, config=Config(max_metric_calls=B, seed=seed))
    test = d.expected(res.best, "test")
    return {"n_train": n, "mode": mode, "q": q, "seed": seed, "test": test, "measured_val": res.meta["best_val"],
            "gap": res.meta["best_val"] - test, "true_train": d.expected(res.best, "evolve"),
            "facts": d.world.count_facts(res.best.files),
            "lines": sum(v.n_lines for v in d.world.views(res.best.files).values()),
            "chars": sum(len(v) for v in res.best.files.values())}


def plot(summ: dict, qs: list, ntr: list[int], B: int) -> None:
    fig, axes = pyplot().subplots(1, 3, figsize=(12, 3.6))
    for ax, m, lab in zip(axes, ("gap", "test", "facts"), ("measured val - true test", "true test score",
                                                           "ticket-specific lines in prompt")):
        for q in qs:
            for mode in MODES:
                st = [summ[f"q={q}|n={n}|{mode}"][m] for n in ntr]
                ax.errorbar(ntr, [s["mean"] for s in st], yerr=ci_err(st), marker="o", capsize=3,
                            label=f"{mode}, q={q}")
        ax.set_xscale("log")
        ax.set_xticks(ntr)
        ax.set_xticklabels([str(n) for n in ntr])
        ax.set_xlabel("|D_train|")
        ax.set_title(lab, fontsize=9)
        ax.grid(alpha=0.3)
    axes[0].legend(fontsize=7)
    fig.suptitle(f"E6: overfitting (RuleWorld, B={B})", fontsize=10)
    fig.tight_layout()
    fig.savefig(RESULTS / "e6_overfitting.png", dpi=130)


def main():
    a = parse_args("E6: overfitting with few examples", default_seeds=30)
    B = 400 if a.live else (1000 if a.quick else 2000)
    qs = QS if not a.live else [None]
    ntr = NTRAIN if not a.live else [6]
    jobs = [(n, m, q, s, B, a.llm) for q in qs for n in ntr for m in MODES for s in range(a.seeds)]
    rows = pool_map(job, jobs, a.workers)
    summ, pairs = {}, {}
    for q in qs:
        for n in ntr:
            grp = {m: by_seed(rows, q=q, n_train=n, mode=m) for m in MODES}
            for m, rs in grp.items():
                summ[f"q={q}|n={n}|{m}"] = {k: summarize([r[k] for r in rs]) for k in
                                            ("test", "measured_val", "gap", "true_train", "facts", "lines")}
            col = lambda m, k: [r[k] for r in grp[m]]
            pairs[f"q={q}|n={n}"] = {
                "gap(val=train) - gap(separate)": paired(col("separate_val", "gap"), col("val=train", "gap")),
                "test(separate) - test(val=train)": paired(col("val=train", "test"), col("separate_val", "test"))}
    q0 = qs[0]
    mean = lambda n, mode, k: summ[f"q={q0}|n={n}|{mode}"][k]["mean"]
    gaps_vt = [mean(n, "val=train", "gap") for n in ntr]
    gaps_sep = [mean(n, "separate_val", "gap") for n in ntr]
    facts_vt = [mean(n, "val=train", "facts") for n in ntr]
    shrink = [pairs[f"q={q0}|n={n}"]["gap(val=train) - gap(separate)"]["lo"] > 0 for n in ntr]
    repro = shrink and shrink[0] and gaps_vt[0] >= gaps_vt[-1]
    out = {"experiment": "E6 overfitting", "llm": a.llm,
           "config": {"budget": B, "seeds": a.seeds, "n_train": ntr, "val_modes": MODES, "copy_rates": qs,
                      "n_val_separate": 30},
           "summary": summ, "paired": pairs, "raw": rows,
           "verdict": (f"q={q0}: gap (measured val - true test) with D_pareto = D_train by |train| {ntr}: "
                       + ", ".join(f"{g:+.3f}" for g in gaps_vt) + "; with a separate val: "
                       + ", ".join(f"{g:+.3f}" for g in gaps_sep) + "; ticket-specific lines (val=train): "
                       + ", ".join(f"{f:.1f}" for f in facts_vt)
                       + f". Separate val significantly shrinks the gap at |train| = "
                       f"{[n for n, s in zip(ntr, shrink) if s]}. Claim 'few examples overfit; a separate validation "
                       f"set helps': {'REPRODUCED' if repro else 'PARTIAL/NOT reproduced'} "
                       f"(test at |train|={ntr[0]}: separate {fmt(summ[f'q={q0}|n={ntr[0]}|separate_val']['test'])} vs "
                       f"val=train {fmt(summ[f'q={q0}|n={ntr[0]}|val=train']['test'])}).")}
    save("e6_overfitting", out, a.out)
    plot(summ, qs, ntr, B)
    print(out["verdict"])


if __name__ == "__main__":
    main()
