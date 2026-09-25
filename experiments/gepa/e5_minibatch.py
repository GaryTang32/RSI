"""E5 - The minibatch gate: cheap, and most of the budget goes to validation.

Claim [paper Obs.1]: "The majority of GEPA's rollout budget is spent on validation";
spec E5 expects a validation share > 50%, fewer false accepts with larger b at a higher
per-iteration cost, and a best b for a given budget.

RuleWorld default world, B = 3000: b in {1, 3, 5, 10} with strict acceptance, b = 3 with
>= acceptance and with a noise margin (mean gain > 0.1). For every proposal the ground
truth gain (analytic test score of child minus parent) classifies the gate's decision:
a false accept is an accepted child that is not truly better, a false reject a rejected
child that is truly better.

    python experiments/gepa/e5_minibatch.py [--llm sim|claude:haiku] [--seeds N] [--quick]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import RESULTS, fmt, parse_args, pool_map, reflection_llm, save, summarize  # noqa: E402

import numpy as np  # noqa: E402

from rsi.domains.ruleworld import make_domain  # noqa: E402
from rsi.gepa import Config, gate_errors, run  # noqa: E402

ARMS = {"b=1": dict(minibatch_size=1), "b=3": dict(minibatch_size=3), "b=5": dict(minibatch_size=5),
        "b=10": dict(minibatch_size=10), "b=3,>=": dict(minibatch_size=3, acceptance="improvement_or_equal"),
        "b=3,margin0.1": dict(minibatch_size=3, acceptance="noise_margin", noise_margin=0.1)}
ARGS = None


def job(spec):
    arm, seed, B = spec
    d = make_domain(seed=seed)
    events = []
    cache = {}

    def truth(art):
        if art.id not in cache:
            cache[art.id] = d.expected(art, "test")
        return cache[art.id]

    def cb(event, payload):
        if event in ("candidate_accepted", "candidate_rejected"):
            eng = payload["engine"]
            parent = eng.state.candidates[payload["parent"]]
            events.append({"accepted": event == "candidate_accepted",
                           "true_gain": truth(payload["child"]) - truth(parent)})

    res = run(d, d.seed_artifact(), llm_propose=reflection_llm(ARGS.llm, d.world),
              config=Config(max_metric_calls=B, seed=seed, **ARMS[arm]), callbacks=[cb])
    ph = res.meta["rollouts_by_phase"]
    tot = res.meta["rollouts"]
    ge = gate_errors(events)
    return {"arm": arm, "seed": seed, "final_test": d.expected(res.best, "test"),
            "val_share": (ph["seed_val"] + ph["val_reflective"] + ph["val_merge"]) / tot,
            "minibatch_share": (ph["minibatch_parent"] + ph["minibatch_child"]) / tot, "phases": ph,
            "iterations": res.meta["iterations"], "proposals": res.meta["n_proposals"],
            "accepted": res.meta["n_accepted"], **ge}


def main():
    global ARGS
    ARGS = a = parse_args("E5: minibatch gate", default_seeds=30)
    B = 400 if a.live else (1200 if a.quick else 3000)
    arms = list(ARMS) if not a.live else ["b=3"]
    rows = pool_map(job, [(arm, s, B) for arm in arms for s in range(a.seeds)], a.workers)
    by = {arm: sorted([r for r in rows if r["arm"] == arm], key=lambda r: r["seed"]) for arm in arms}
    summ = {arm: {m: summarize([r[m] for r in rs]) for m in
                  ("final_test", "val_share", "minibatch_share", "false_accept_rate", "false_reject_rate",
                   "accept_rate", "iterations", "proposals", "accepted")} for arm, rs in by.items()}
    best_b = max([k for k in arms if "," not in k], key=lambda k: summ[k]["final_test"]["mean"])
    fa = {k: summ[k]["false_accept_rate"]["mean"] for k in arms if "," not in k}
    fa_mono = all(x >= y - 0.02 for x, y in zip(list(fa.values()), list(fa.values())[1:]))
    s3 = summ["b=3"]
    out = {"experiment": "E5 minibatch gate", "llm": a.llm, "config": {"budget": B, "seeds": a.seeds, "arms": ARMS},
           "summary": summ, "best_b_for_budget": best_b, "raw": rows,
           "verdict": (f"b=3: validation share of rollouts {fmt(s3['val_share'])} (claim '> 50%': "
                       f"{'REPRODUCED' if s3['val_share']['mean'] > 0.5 else 'NOT reproduced'}); false-accept rate by b: "
                       + ", ".join(f"{k} {v:.2f}" for k, v in fa.items())
                       + f" ({'decreasing with b' if fa_mono else 'not monotone in b'}); false-reject rates: "
                       + ", ".join(f"{k} {summ[k]['false_reject_rate']['mean']:.2f}" for k in fa)
                       + f"; iterations at B={B}: " + ", ".join(f"{k} {summ[k]['iterations']['mean']:.0f}" for k in fa)
                       + f". Best b for this budget: {best_b} ({fmt(summ[best_b]['final_test'])}). "
                       f">= acceptance: test {fmt(summ['b=3,>=']['final_test']) if 'b=3,>=' in summ else '-'}, "
                       f"false-accept {summ['b=3,>=']['false_accept_rate']['mean'] if 'b=3,>=' in summ else float('nan'):.2f}; "
                       f"noise margin 0.1: test {fmt(summ['b=3,margin0.1']['final_test']) if 'b=3,margin0.1' in summ else '-'}.")}
    save("e5_minibatch", out, a.out)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 3, figsize=(12, 3.6))
    for ax, m, lab in zip(axes, ("final_test", "val_share", "false_accept_rate"),
                          ("true test score", "validation share of rollouts", "false-accept rate")):
        st = [summ[k][m] for k in arms]
        ax.bar(range(len(arms)), [s["mean"] for s in st],
               yerr=[[s["mean"] - s["lo"] for s in st], [s["hi"] - s["mean"] for s in st]], capsize=3)
        ax.set_xticks(range(len(arms)))
        ax.set_xticklabels(arms, rotation=30, fontsize=8)
        ax.set_title(lab, fontsize=9)
        ax.grid(axis="y", alpha=0.3)
    fig.suptitle(f"E5: minibatch gate (RuleWorld, B={B})", fontsize=10)
    fig.tight_layout()
    fig.savefig(RESULTS / "e5_minibatch.png", dpi=130)
    print(out["verdict"])


if __name__ == "__main__":
    main()
