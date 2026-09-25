"""E6 - Overfitting with few examples; a separate validation set helps.

Claim [doc "With few examples it can still overfit; keep a separate validation set"]
[paper App.J]: spec E6 expects small training sets with D_pareto = D_train to show a
larger (validation - test) gap and more copied example-specific content, and a separate
validation set to shrink the gap.

RuleWorld default world; |D_train| in {6, 15, 30, 60}; D_pareto = a separate 30-example
validation split vs D_pareto = D_train; reflection copy rate q (ticket-specific facts
instead of general rules) in {0.15, 0.5}. B = 2000. Reported: true test score, measured
validation score of the returned candidate, gap = measured val - true test, the number of
ticket-specific lines in the final prompts, and prompt length.

    python experiments/gepa/e6_overfitting.py [--llm sim|claude:haiku] [--seeds N] [--quick]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import RESULTS, fmt, paired, parse_args, pool_map, reflection_llm, save, summarize  # noqa: E402

import numpy as np  # noqa: E402

from rsi.domains.ruleworld import ReflectionProfile, make_domain  # noqa: E402
from rsi.gepa import Config, run  # noqa: E402

NTRAIN = [6, 15, 30, 60]
MODES = ["separate_val", "val=train"]
QS = [0.15, 0.5]
ARGS = None


def job(spec):
    n, mode, q, seed, B = spec
    d = make_domain(seed=seed, n_train=n, val_is_train=(mode == "val=train"))
    llm = reflection_llm(ARGS.llm, d.world, profile=ReflectionProfile(q_copy=q)) if ARGS.llm == "sim" else \
        reflection_llm(ARGS.llm)
    res = run(d, d.seed_artifact(), llm_propose=llm, config=Config(max_metric_calls=B, seed=seed))
    test = d.expected(res.best, "test")
    return {"n_train": n, "mode": mode, "q": q, "seed": seed, "test": test, "measured_val": res.meta["best_val"],
            "gap": res.meta["best_val"] - test, "true_train": d.expected(res.best, "evolve"),
            "facts": d.world.count_facts(res.best.files),
            "lines": sum(v.n_lines for v in d.world.views(res.best.files).values()),
            "chars": sum(len(v) for k, v in res.best.files.items())}


def main():
    global ARGS
    ARGS = a = parse_args("E6: overfitting with few examples", default_seeds=30)
    B = 400 if a.live else (1000 if a.quick else 2000)
    qs = QS if not a.live else [None]
    ntr = NTRAIN if not a.live else [6]
    jobs = [(n, m, q, s, B) for q in qs for n in ntr for m in MODES for s in range(a.seeds)]
    rows = pool_map(job, jobs, a.workers)
    summ, pairs = {}, {}
    for q in qs:
        for n in ntr:
            grp = {}
            for m in MODES:
                rs = sorted([r for r in rows if r["q"] == q and r["n_train"] == n and r["mode"] == m],
                            key=lambda r: r["seed"])
                grp[m] = rs
                summ[f"q={q}|n={n}|{m}"] = {k: summarize([r[k] for r in rs]) for k in
                                            ("test", "measured_val", "gap", "true_train", "facts", "lines")}
            pairs[f"q={q}|n={n}"] = {
                "gap(val=train) - gap(separate)": paired([r["gap"] for r in grp["separate_val"]],
                                                         [r["gap"] for r in grp["val=train"]]),
                "test(separate) - test(val=train)": paired([r["test"] for r in grp["val=train"]],
                                                           [r["test"] for r in grp["separate_val"]])}
    q0 = qs[0]
    gaps_vt = [summ[f"q={q0}|n={n}|val=train"]["gap"]["mean"] for n in ntr]
    gaps_sep = [summ[f"q={q0}|n={n}|separate_val"]["gap"]["mean"] for n in ntr]
    facts_vt = [summ[f"q={q0}|n={n}|val=train"]["facts"]["mean"] for n in ntr]
    shrink = [pairs[f"q={q0}|n={n}"]["gap(val=train) - gap(separate)"]["lo"] > 0 for n in ntr]
    out = {"experiment": "E6 overfitting", "llm": a.llm,
           "config": {"budget": B, "seeds": a.seeds, "n_train": ntr, "val_modes": MODES, "copy_rates": qs,
                      "n_val_separate": 30},
           "summary": summ, "paired": pairs, "raw": rows,
           "verdict": (f"q={q0}: gap (measured val - true test) with D_pareto = D_train by |train| {ntr}: "
                       + ", ".join(f"{g:+.3f}" for g in gaps_vt) + "; with a separate val: "
                       + ", ".join(f"{g:+.3f}" for g in gaps_sep) + f"; ticket-specific lines (val=train): "
                       + ", ".join(f"{f:.1f}" for f in facts_vt)
                       + f". Separate val significantly shrinks the gap at |train| = "
                       f"{[n for n, s in zip(ntr, shrink) if s]}. Claim 'few examples overfit; a separate validation "
                       f"set helps': {'REPRODUCED' if shrink and shrink[0] and gaps_vt[0] >= gaps_vt[-1] else 'PARTIAL/NOT reproduced'} "
                       f"(test at |train|={ntr[0]}: separate {fmt(summ[f'q={q0}|n={ntr[0]}|separate_val']['test'])} vs "
                       f"val=train {fmt(summ[f'q={q0}|n={ntr[0]}|val=train']['test'])}).")}
    save("e6_overfitting", out, a.out)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 3, figsize=(12, 3.6))
    for ax, m, lab in zip(axes, ("gap", "test", "facts"), ("measured val - true test", "true test score",
                                                           "ticket-specific lines in prompt")):
        for q in qs:
            for mode in MODES:
                st = [summ[f"q={q}|n={n}|{mode}"][m] for n in ntr]
                ax.errorbar(ntr, [s["mean"] for s in st], yerr=[[s["mean"] - s["lo"] for s in st],
                                                                [s["hi"] - s["mean"] for s in st]],
                            marker="o", capsize=3, label=f"{mode}, q={q}")
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
    print(out["verdict"])


if __name__ == "__main__":
    main()
