"""E9 - Stall exploration escapes prompt-only collapse.

HarnessWorld where non-structural mechanisms carry only weak gains (effect scale 0.25) and
the remaining real gains sit in structural components (client_tool / skill / memory /
subagent), with a proposer strongly biased toward ``prompt`` edits (prompt collapse) and
rarely proposing structural machinery on its own. Full RRSI with stall exploration (sigma_t
over a w = 3 window, reserved slot for never-exercised components) on vs off.

Confirming outcome (spec E9): with exploration on, broader component coverage |T_t|/|K|, more
accepted structural mechanisms and escape from the plateau (higher final score).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import RESULTS, mean_curves, paired, parse_args, pmap, run_hw, save, strip_curves, summarize  # noqa: E402

from rsi.rrsi import RegularizerSwitches  # noqa: E402

SETUPS = {
    # mild collapse: weak (not zero) non-structural gains; the proposer still sometimes drafts structural edits
    "mild": ({"nonstructural_effect_scale": 0.25},
             {"component_bias": {"prompt": 8.0, "control_flow": 2.0},
              "shares": {"generic": 0.30, "structural": 0.03, "narrow": 0.06, "leak": 0.0, "obfuscated_leak": 0.0,
                         "null": 0.25, "costly": 0.10, "harmful": 0.16, "decaying": 0.10}}),
    # strict collapse (the spec's premise): real gains ONLY in structural components and a proposer that almost
    # never drafts structural machinery on its own
    "strict": ({"nonstructural_effect_scale": 0.0},
               {"component_bias": {"prompt": 20.0, "control_flow": 5.0, "client_tool": 0.05, "skill": 0.05,
                                   "memory": 0.05, "subagent": 0.05},
                "shares": {"generic": 0.30, "structural": 0.15, "narrow": 0.0, "leak": 0.0, "obfuscated_leak": 0.0,
                           "null": 0.20, "costly": 0.10, "harmful": 0.15, "decaying": 0.10}}),
}


def main():
    a = parse_args("E9: stall exploration", default_seeds=30)
    T = 10 if a.quick else 20
    arms = {"exploration on": RegularizerSwitches.full(),
            "exploration off": RegularizerSwitches.full().but(stall_exploration=False, name="no_explore")}
    jobs = [{"seed": s, "arm": sw, "label": f"{setup}|{lab}", "cfg": {"T": T}, "world": world, "proposer": prop,
             "llm": a.llm} for setup, (world, prop) in SETUPS.items() for s in range(a.seeds)
            for lab, sw in arms.items()]
    rows = pmap(run_hw, jobs, a.workers if a.llm == "sim" else 1)
    metrics = ("coverage", "accepted_structural", "structural_in_final", "evolve_gain", "holdout_gain", "ood_gain",
               "unseen_gain", "token_ratio")
    summ = summarize(rows, metrics=metrics)
    pd = {setup: {m: paired(rows, f"{setup}|exploration off", f"{setup}|exploration on", m) for m in metrics}
          for setup in SETUPS}
    checks = {}
    for setup in SETUPS:
        checks[f"{setup}: broader coverage"] = pd[setup]["coverage"]["lo"] > 0
        checks[f"{setup}: more accepted structural mechanisms"] = pd[setup]["accepted_structural"]["lo"] > 0
        checks[f"{setup}: higher final true score (plateau escape)"] = pd[setup]["evolve_gain"]["lo"] > 0
    out = {"experiment": "E9 stall exploration", "config": {"T": T, "seeds": a.seeds, "setups": SETUPS, "w": 3,
                                                            "m_draft": 1, "llm": a.llm},
           "summary": summ, "paired_on_minus_off": pd, "checks": checks,
           "verdict": "REPRODUCED" if all(checks.values()) else
           "PARTIAL - not met: " + "; ".join(k for k, v in checks.items() if not v),
           "curves": mean_curves(rows), "rows": strip_curves(rows)}
    save("e9_exploration", out)
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(1, 2, figsize=(12, 4))
        for lab, c in out["curves"].items():
            ax[0].plot([x - c["evolve"][0] for x in c["evolve"]], "--" if "off" in lab else "-", label=lab)
        ax[0].set_xlabel("round")
        ax[0].set_ylabel("true evolve gain vs H_0")
        ax[0].legend(fontsize=8)
        ax[0].set_title("Escape from prompt-only collapse")
        labs = list(summ)
        ax[1].bar(range(len(labs)), [summ[l]["coverage"]["mean"] for l in labs])
        ax[1].set_xticks(range(len(labs)))
        ax[1].set_xticklabels(labs, fontsize=7, rotation=10)
        ax[1].set_title("component coverage |T_T| / |K|")
        fig.tight_layout()
        fig.savefig(RESULTS / "e9_exploration.png", dpi=110)
    except Exception as e:  # noqa: BLE001
        print("figure skipped:", e)
    for k, v in summ.items():
        print(k, {m: round(v[m]["mean"], 3) for m in metrics})
    print(out["verdict"])


if __name__ == "__main__":
    main()
