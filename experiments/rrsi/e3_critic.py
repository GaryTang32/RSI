"""E3 - The leakage critic blocks benchmark-specific fitting before evaluation.

HarnessWorld, full RRSI with the critic on or off, sweeping the share of leaky proposals
(literal leaks that hard-code evolve task ids/answers, and obfuscated hash-keyed leaks,
2:1) and the LLM critic's catch rate for obfuscated leaks (0 = deterministic precheck only).
The precheck is the code's: evolve task ids + the domain's id pattern (``hw-e-\d{3}``), no
answer key. A literal HarnessWorld leak is keyed by evolve task ids, so the precheck stops
all of them (600/600 catalog leaks over 50 worlds, with or without the answer terms);
obfuscated leaks pass it (0/300) and are left to the LLM review.
Measures leaky mechanisms left in the final harness, evaluations spent on candidates that
add a leak, and true evolve / OOD gains.

Confirming outcome (spec E3): critic-on retains ~0 leaky mechanisms and spends no
evaluations on caught leaks; OOD higher than critic-off.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import RESULTS, paired, parse_args, pmap, run_hw, save, strip_curves, summarize  # noqa: E402

from rsi.domains.harnessworld import DEFAULT_SHARES  # noqa: E402
from rsi.rrsi import RegularizerSwitches  # noqa: E402

LEAK_SHARES = (0.05, 0.15, 0.30)
CATCH = (0.0, 0.5, 0.8, 1.0)


def shares_for(leak_share: float) -> dict:
    rest = {k: v for k, v in DEFAULT_SHARES.items() if k not in ("leak", "obfuscated_leak")}
    tot = sum(rest.values())
    out = {k: v / tot * (1.0 - leak_share) for k, v in rest.items()}
    out["leak"], out["obfuscated_leak"] = leak_share * 2 / 3, leak_share / 3
    return out


def main():
    a = parse_args("E3: leakage critic", default_seeds=15)
    T = 8 if a.quick else 20
    jobs = []
    for ls in LEAK_SHARES:
        prop = {"shares": shares_for(ls)}
        for s in range(a.seeds):
            jobs.append({"seed": s, "arm": RegularizerSwitches.full().but(critic=False, name="critic_off"),
                         "label": f"leak={ls}|critic off", "cfg": {"T": T}, "proposer": prop, "llm": a.llm})
            for c in CATCH:
                jobs.append({"seed": s, "arm": "full", "label": f"leak={ls}|critic on, catch={c}", "cfg": {"T": T},
                             "proposer": prop, "critic": {"catch_rate": c}, "llm": a.llm})
    rows = pmap(run_hw, jobs, a.workers if a.llm == "sim" else 1)
    metrics = ("leaks_in_final", "evals_on_leaky", "n_critic_reject", "measured_gain", "evolve_gain", "holdout_gain",
               "ood_gain", "unseen_gain", "token_ratio")
    summ = summarize(rows, metrics=metrics)
    pd = {}
    checks = {}
    for ls in LEAK_SHARES:
        off = f"leak={ls}|critic off"
        on = f"leak={ls}|critic on, catch=0.8"
        pd[str(ls)] = {m: paired(rows, off, on, m) for m in ("leaks_in_final", "evals_on_leaky", "ood_gain", "unseen_gain",
                                                            "measured_gain")}
        checks[f"leak={ls}: fewer leaks retained"] = pd[str(ls)]["leaks_in_final"]["hi"] < 0
        checks[f"leak={ls}: fewer evaluations on leaky candidates"] = pd[str(ls)]["evals_on_leaky"]["hi"] < 0
        checks[f"leak={ls}: OOD not lower"] = pd[str(ls)]["ood_gain"]["lo"] > -0.005
        checks[f"leak={ls}: ~0 leaks retained (catch 1.0)"] = summ[f"leak={ls}|critic on, catch=1.0"]["leaks_in_final"]["mean"] < 0.1
    out = {"experiment": "E3 leakage critic", "config": {"T": T, "seeds": a.seeds, "leak_shares": LEAK_SHARES,
                                                         "catch_rates": CATCH, "llm": a.llm,
                                                         "note": "literal leaks (keyed by evolve task ids) are "
                                                                 "always stopped by the code's task-id / id-pattern "
                                                                 "precheck (no answer denylist); catch_rate applies "
                                                                 "to the LLM review of obfuscated leaks"},
           "summary": summ, "paired_on_minus_off_catch0.8": pd, "checks": checks,
           "verdict": "REPRODUCED" if all(checks.values()) else
           "PARTIAL - not met: " + "; ".join(k for k, v in checks.items() if not v),
           "rows": strip_curves(rows)}
    save("e3_critic", out)
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(1, 3, figsize=(15, 4))
        for ls in LEAK_SHARES:
            labs = [f"leak={ls}|critic off"] + [f"leak={ls}|critic on, catch={c}" for c in CATCH]
            xs = ["off"] + [str(c) for c in CATCH]
            ax[0].plot(xs, [summ[l]["leaks_in_final"]["mean"] for l in labs], "o-", label=f"leak share {ls}")
            ax[1].plot(xs, [summ[l]["evals_on_leaky"]["mean"] for l in labs], "o-", label=f"leak share {ls}")
            ax[2].plot(xs, [summ[l]["ood_gain"]["mean"] * 100 for l in labs], "o-", label=f"leak share {ls}")
        for x, t in zip(ax, ("leaky mechanisms in final harness", "evaluations spent on leaky candidates",
                             "true OOD gain (points)")):
            x.set_title(t)
            x.set_xlabel("critic (catch rate of obfuscated leaks)")
            x.legend(fontsize=8)
        fig.tight_layout()
        fig.savefig(RESULTS / "e3_critic.png", dpi=110)
    except Exception as e:  # noqa: BLE001
        print("figure skipped:", e)
    for k, v in summ.items():
        print(k, {m: round(v[m]["mean"], 3) for m in ("leaks_in_final", "evals_on_leaky", "evolve_gain", "ood_gain")})
    print(out["verdict"])


if __name__ == "__main__":
    main()
