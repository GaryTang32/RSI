"""E5: the minibatch gate (b, acceptance rule): validation share of rollouts, false accepts / rejects vs ground truth.

Run: python experiments/gepa/e5_minibatch.py [--llm sim|claude:haiku] [--seeds N] [--quick]
"""
from __future__ import annotations

from _common import RESULTS, by_seed, ci_err, fmt, parse_args, pool_map, pyplot, reflection_llm, save, summarize
from rsi.domains.ruleworld import make_domain
from rsi.gepa import Config, gate_errors, run

ARMS = {"b=1": dict(minibatch_size=1), "b=3": dict(minibatch_size=3), "b=5": dict(minibatch_size=5),
        "b=10": dict(minibatch_size=10), "b=3,>=": dict(minibatch_size=3, acceptance="improvement_or_equal"),
        "b=3,margin0.1": dict(minibatch_size=3, acceptance="noise_margin", noise_margin=0.1)}
METRICS = ("final_test", "val_share", "minibatch_share", "false_accept_rate", "false_reject_rate", "accept_rate",
           "iterations", "proposals", "accepted")


def job(spec):
    arm, seed, B, llm = spec
    d = make_domain(seed=seed)
    events, cache = [], {}

    def truth(art) -> float:
        if art.id not in cache:
            cache[art.id] = d.expected(art, "test")
        return cache[art.id]

    def cb(event, payload):
        if event in ("candidate_accepted", "candidate_rejected"):
            parent = payload["engine"].state.candidates[payload["parent"]]
            events.append({"accepted": event == "candidate_accepted",
                           "true_gain": truth(payload["child"]) - truth(parent)})

    res = run(d, d.seed_artifact(), llm_propose=reflection_llm(llm, d.world),
              config=Config(max_metric_calls=B, seed=seed, **ARMS[arm]), callbacks=[cb])
    ph, tot = res.meta["rollouts_by_phase"], res.meta["rollouts"]
    return {"arm": arm, "seed": seed, "final_test": d.expected(res.best, "test"),
            "val_share": (ph["seed_val"] + ph["val_reflective"] + ph["val_merge"]) / tot,
            "minibatch_share": (ph["minibatch_parent"] + ph["minibatch_child"]) / tot, "phases": ph,
            "iterations": res.meta["iterations"], "proposals": res.meta["n_proposals"],
            "accepted": res.meta["n_accepted"], **gate_errors(events)}


def plot(summ: dict, arms: list[str], B: int) -> None:
    fig, axes = pyplot().subplots(1, 3, figsize=(12, 3.6))
    for ax, m, lab in zip(axes, ("final_test", "val_share", "false_accept_rate"),
                          ("true test score", "validation share of rollouts", "false-accept rate")):
        st = [summ[k][m] for k in arms]
        ax.bar(range(len(arms)), [s["mean"] for s in st], yerr=ci_err(st), capsize=3)
        ax.set_xticks(range(len(arms)))
        ax.set_xticklabels(arms, rotation=30, fontsize=8)
        ax.set_title(lab, fontsize=9)
        ax.grid(axis="y", alpha=0.3)
    fig.suptitle(f"E5: minibatch gate (RuleWorld, B={B})", fontsize=10)
    fig.tight_layout()
    fig.savefig(RESULTS / "e5_minibatch.png", dpi=130)


def main():
    a = parse_args("E5: minibatch gate", default_seeds=30)
    B = 400 if a.live else (1200 if a.quick else 3000)
    arms = list(ARMS) if not a.live else ["b=3"]
    rows = pool_map(job, [(arm, s, B, a.llm) for arm in arms for s in range(a.seeds)], a.workers)
    summ = {arm: {m: summarize([r[m] for r in by_seed(rows, arm=arm)]) for m in METRICS} for arm in arms}
    sizes = [k for k in arms if "," not in k]
    best_b = max(sizes, key=lambda k: summ[k]["final_test"]["mean"])
    fa = {k: summ[k]["false_accept_rate"]["mean"] for k in sizes}
    fa_mono = len(fa) > 1 and all(x >= y - 0.02 for x, y in zip(list(fa.values()), list(fa.values())[1:]))
    s3 = summ["b=3"]
    ge = summ.get("b=3,>=")
    out = {"experiment": "E5 minibatch gate", "llm": a.llm, "config": {"budget": B, "seeds": a.seeds, "arms": ARMS},
           "summary": summ, "best_b_for_budget": best_b, "raw": rows,
           "verdict": (f"b=3: validation share of rollouts {fmt(s3['val_share'])} (claim '> 50%': "
                       f"{'REPRODUCED' if s3['val_share']['mean'] > 0.5 else 'NOT reproduced'}); "
                       "false-accept rate by b: "
                       + ", ".join(f"{k} {v:.2f}" for k, v in fa.items())
                       + (f" ({'decreasing with b' if fa_mono else 'not monotone in b'})" if len(fa) > 1 else "")
                       + "; false-reject rates: "
                       + ", ".join(f"{k} {summ[k]['false_reject_rate']['mean']:.2f}" for k in fa)
                       + f"; iterations at B={B}: " + ", ".join(f"{k} {summ[k]['iterations']['mean']:.0f}" for k in fa)
                       + f". Best b for this budget: {best_b} ({fmt(summ[best_b]['final_test'])}). "
                       f">= acceptance: test {fmt(ge['final_test']) if ge else '-'}, "
                       f"false-accept {ge['false_accept_rate']['mean'] if ge else float('nan'):.2f}; "
                       f"noise margin 0.1: test "
                       f"{fmt(summ['b=3,margin0.1']['final_test']) if 'b=3,margin0.1' in summ else '-'}.")}
    save("e5_minibatch", out, a.out)
    plot(summ, arms, B)
    print(out["verdict"])


if __name__ == "__main__":
    main()
