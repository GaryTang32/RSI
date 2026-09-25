"""E6 - Pruning removes machinery that stopped helping.

HarnessWorld with a richer pool of *decaying* mechanisms: each helps until a superseding
mechanism (a dedicated generic / structural one) is also present, after which it only
costs tokens. Full RRSI with the prune directives B_t on or off (everything else equal),
T = 30 rounds. B_t follows the code exactly: tried components whose best measured gain in
the last n_prune = 4 rounds is <= 0, handed to the proposer with the accepted machinery;
removal is an ordinary edit that must pass the critic and Algorithm 2.

Confirming outcome (spec E6): with pruning on, dead mechanisms are removed and tokens are
lower; with it off they accumulate.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import RESULTS, mean_curves, paired, parse_args, pmap, run_hw, save, strip_curves, summarize  # noqa: E402

from rsi.rrsi import RegularizerSwitches  # noqa: E402

WORLD = {"n_decaying": 14, "decaying_cost": (0.08, 0.15), "decaying_effect": (0.20, 0.04),
         "decaying_components": ("context_mgmt", "output_plumbing", "config", "prompt")}
SHARES = {"generic": 0.20, "structural": 0.12, "narrow": 0.04, "leak": 0.06, "obfuscated_leak": 0.02, "null": 0.12,
          "costly": 0.08, "harmful": 0.14, "decaying": 0.22}


def main():
    a = parse_args("E6: pruning directives", default_seeds=30)
    T = 10 if a.quick else 30
    arms = {"prune directives on": RegularizerSwitches.full(),
            "prune directives off": RegularizerSwitches.full().but(prune_directives=False, name="no_prune")}
    jobs = [{"seed": s, "arm": sw, "label": lab, "cfg": {"T": T}, "world": WORLD, "proposer": {"shares": SHARES},
             "llm": a.llm} for s in range(a.seeds) for lab, sw in arms.items()]
    rows = pmap(run_hw, jobs, a.workers if a.llm == "sim" else 1)
    metrics = ("dead_in_final", "prune_accepted", "token_ratio", "n_mechanisms", "evolve_gain", "ood_gain",
               "unseen_gain")
    summ = summarize(rows, metrics=metrics)
    pd = {m: paired(rows, "prune directives off", "prune directives on", m) for m in metrics}
    checks = {"prune edits accepted when on": summ["prune directives on"]["prune_accepted"]["mean"] > 0.5,
              "fewer dead mechanisms with pruning": pd["dead_in_final"]["hi"] < 0,
              "fewer tokens with pruning": pd["token_ratio"]["hi"] < 0}
    out = {"experiment": "E6 pruning", "config": {"T": T, "seeds": a.seeds, "world": WORLD, "shares": SHARES,
                                                  "n_prune": 4, "llm": a.llm},
           "summary": summ, "paired_on_minus_off": pd, "checks": checks,
           "verdict": "REPRODUCED" if all(checks.values()) else
           "PARTIAL - not met: " + "; ".join(k for k, v in checks.items() if not v),
           "curves": mean_curves(rows), "rows": strip_curves(rows)}
    save("e6_pruning", out)
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(1, 2, figsize=(12, 4))
        for lab, c in out["curves"].items():
            ax[0].plot([x / c["tokens"][0] for x in c["tokens"]], label=lab)
            ax[1].plot([x - c["ood"][0] for x in c["ood"]], label=lab)
        ax[0].set_title("tokens per trial / H_0")
        ax[1].set_title("true OOD gain")
        for x in ax:
            x.set_xlabel("round")
            x.legend(fontsize=8)
        fig.tight_layout()
        fig.savefig(RESULTS / "e6_pruning.png", dpi=110)
    except Exception as e:  # noqa: BLE001
        print("figure skipped:", e)
    for k, v in summ.items():
        print(k, {m: round(v[m]["mean"], 3) for m in metrics})
    print(out["verdict"])


if __name__ == "__main__":
    main()
