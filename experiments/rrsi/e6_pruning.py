"""E6: prune directives remove decaying machinery that stopped helping (HarnessWorld, prune on vs off).

Run: python experiments/rrsi/e6_pruning.py [--quick] [--seeds N]
"""
from _common import (figure, mean_curves, pairs, parse_args, pmap, print_means, run_hw, save, strip_curves, summarize,
                     verdict)

from rsi.rrsi import RegularizerSwitches

# decaying mechanisms help until a superseding one is present, then only cost tokens
WORLD = {"n_decaying": 14, "decaying_cost": (0.08, 0.15), "decaying_effect": (0.20, 0.04),
         "decaying_components": ("context_mgmt", "output_plumbing", "config", "prompt")}
SHARES = {"generic": 0.20, "structural": 0.12, "narrow": 0.04, "leak": 0.06, "obfuscated_leak": 0.02, "null": 0.12,
          "costly": 0.08, "harmful": 0.14, "decaying": 0.22}


def main():
    a = parse_args("E6: pruning directives", default_seeds=50)
    T = 10 if a.quick else 30
    arms = {"prune directives on": RegularizerSwitches.full(),
            "prune directives off": RegularizerSwitches.full().but(prune_directives=False, name="no_prune")}
    jobs = [{"seed": s, "arm": sw, "label": lab, "cfg": {"T": T}, "world": WORLD, "proposer": {"shares": SHARES},
             "llm": a.llm} for s in range(a.seeds) for lab, sw in arms.items()]
    rows = pmap(run_hw, jobs, a.procs)
    metrics = ("dead_in_final", "prune_accepted", "token_ratio", "n_mechanisms", "evolve_gain", "ood_gain",
               "unseen_gain")
    summ = summarize(rows, metrics=metrics)
    pd = pairs(rows, "prune directives off", "prune directives on", metrics)
    checks = {"prune edits accepted when on": summ["prune directives on"]["prune_accepted"]["mean"] > 0.5,
              "fewer dead mechanisms with pruning": pd["dead_in_final"]["hi"] < 0,
              "fewer tokens with pruning": pd["token_ratio"]["hi"] < 0}
    out = {"experiment": "E6 pruning", "config": {"T": T, "seeds": a.seeds, "world": WORLD, "shares": SHARES,
                                                  "n_prune": 4, "llm": a.llm},
           "summary": summ, "paired_on_minus_off": pd, "checks": checks, "verdict": verdict(checks),
           "curves": mean_curves(rows), "rows": strip_curves(rows)}
    save("e6_pruning", out)

    def draw(ax):
        for lab, c in out["curves"].items():
            ax[0].plot([x / c["tokens"][0] for x in c["tokens"]], label=lab)
            ax[1].plot([x - c["ood"][0] for x in c["ood"]], label=lab)
        ax[0].set_title("tokens per trial / H_0")
        ax[1].set_title("true OOD gain")
        for x in ax:
            x.set_xlabel("round")
            x.legend(fontsize=8)
    figure("e6_pruning", draw, 2, figsize=(12, 4))
    print_means(summ, metrics)
    print(out["verdict"])


if __name__ == "__main__":
    main()
