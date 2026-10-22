"""E3: the leakage critic blocks benchmark-specific fitting before evaluation (leak share x catch rate sweep).

Run: python experiments/rrsi/e3_critic.py [--quick] [--seeds N]
"""
from _common import figure, pairs, parse_args, pmap, print_means, run_hw, save, strip_curves, summarize, verdict

from rsi.domains.harnessworld import DEFAULT_SHARES
from rsi.rrsi import RegularizerSwitches

LEAK_SHARES = (0.05, 0.15, 0.30)
CATCH = (0.0, 0.5, 0.8, 1.0)       # LLM-critic catch rate of obfuscated leaks (0 = precheck only)


def shares_for(leak_share: float) -> dict:
    """Default shares rescaled to 1 - leak_share; the leaks split 2:1 literal : obfuscated."""
    rest = {k: v for k, v in DEFAULT_SHARES.items() if k not in ("leak", "obfuscated_leak")}
    tot = sum(rest.values())
    out = {k: v / tot * (1.0 - leak_share) for k, v in rest.items()}
    out["leak"], out["obfuscated_leak"] = leak_share * 2 / 3, leak_share / 3
    return out


def main():
    a = parse_args("E3: leakage critic", default_seeds=15)
    T = 8 if a.quick else 20
    off = RegularizerSwitches.full().but(critic=False, name="critic_off")
    jobs = []
    for ls in LEAK_SHARES:
        prop = {"shares": shares_for(ls)}
        for s in range(a.seeds):
            jobs.append({"seed": s, "arm": off, "label": f"leak={ls}|critic off", "cfg": {"T": T}, "proposer": prop,
                         "llm": a.llm})
            jobs += [{"seed": s, "arm": "full", "label": f"leak={ls}|critic on, catch={c}", "cfg": {"T": T},
                      "proposer": prop, "critic": {"catch_rate": c}, "llm": a.llm} for c in CATCH]
    rows = pmap(run_hw, jobs, a.procs)
    summ = summarize(rows, metrics=("leaks_in_final", "evals_on_leaky", "n_critic_reject", "measured_gain",
                                    "evolve_gain", "holdout_gain", "ood_gain", "unseen_gain", "token_ratio"))
    pd, checks = {}, {}
    for ls in LEAK_SHARES:
        d = pd[str(ls)] = pairs(rows, f"leak={ls}|critic off", f"leak={ls}|critic on, catch=0.8",
                                ("leaks_in_final", "evals_on_leaky", "ood_gain", "unseen_gain", "measured_gain"))
        checks[f"leak={ls}: fewer leaks retained"] = d["leaks_in_final"]["hi"] < 0
        checks[f"leak={ls}: fewer evaluations on leaky candidates"] = d["evals_on_leaky"]["hi"] < 0
        checks[f"leak={ls}: OOD not lower"] = d["ood_gain"]["lo"] > -0.005
        checks[f"leak={ls}: ~0 leaks retained (catch 1.0)"] = \
            summ[f"leak={ls}|critic on, catch=1.0"]["leaks_in_final"]["mean"] < 0.1
    out = {"experiment": "E3 leakage critic", "config": {"T": T, "seeds": a.seeds, "leak_shares": LEAK_SHARES,
                                                         "catch_rates": CATCH, "llm": a.llm,
                                                         "note": "literal leaks (keyed by evolve task ids) are "
                                                                 "always stopped by the code's task-id / id-pattern "
                                                                 "precheck (no answer denylist); catch_rate applies "
                                                                 "to the LLM review of obfuscated leaks"},
           "summary": summ, "paired_on_minus_off_catch0.8": pd, "checks": checks, "verdict": verdict(checks),
           "rows": strip_curves(rows)}
    save("e3_critic", out)

    def draw(ax):
        for ls in LEAK_SHARES:
            labs = [f"leak={ls}|critic off"] + [f"leak={ls}|critic on, catch={c}" for c in CATCH]
            xs = ["off"] + [str(c) for c in CATCH]
            ax[0].plot(xs, [summ[lab]["leaks_in_final"]["mean"] for lab in labs], "o-", label=f"leak share {ls}")
            ax[1].plot(xs, [summ[lab]["evals_on_leaky"]["mean"] for lab in labs], "o-", label=f"leak share {ls}")
            ax[2].plot(xs, [summ[lab]["ood_gain"]["mean"] * 100 for lab in labs], "o-", label=f"leak share {ls}")
        for x, t in zip(ax, ("leaky mechanisms in final harness", "evaluations spent on leaky candidates",
                             "true OOD gain (points)")):
            x.set_title(t)
            x.set_xlabel("critic (catch rate of obfuscated leaks)")
            x.legend(fontsize=8)
    figure("e3_critic", draw, 3, figsize=(15, 4))
    print_means(summ, ("leaks_in_final", "evals_on_leaky", "evolve_gain", "ood_gain"))
    print(out["verdict"])


if __name__ == "__main__":
    main()
