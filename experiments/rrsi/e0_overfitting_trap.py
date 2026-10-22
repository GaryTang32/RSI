"""E0: the overview's overfitting-trap toy (exact port of its JavaScript), guard ablations and slider sweeps; LLM-free.

Run: python experiments/rrsi/e0_overfitting_trap.py [--quick]
"""
from _common import figure, parse_args, save

from rsi.core import paired_diff_ci, summarize_runs
from rsi.rrsi.toy import simulate, sweep

PAGE = (0.35, 0.02, 0.8)                   # page defaults: p_trick, sigma, catch_rate


def curves(r) -> dict:
    return {"plain_practice": r.plain_ev, "plain_unseen": r.plain_ho, "guarded_practice": r.reg_ev,
            "guarded_unseen": r.reg_ho}


def main():
    a = parse_args("E0: overview overfitting-trap toy", default_seeds=200)
    runs = 200 if not a.quick else 50
    base = simulate(*PAGE, runs=runs)
    arms = {"page_defaults": base, "critic_only (band 0)": simulate(*PAGE, runs=runs, band_z=0.0),
            "noise_margin_only (no critic)": simulate(*PAGE, runs=runs, critic=False),
            "rrsi_band_z2": simulate(*PAGE, runs=runs, band_z=2.0)}
    rows = sweep({"p_trick": [0.0, 0.1, 0.2, 0.35, 0.5, 0.6], "sigma": [0.0, 0.01, 0.02, 0.03, 0.04],
                  "catch_rate": [0.0, 0.25, 0.5, 0.8, 1.0]}, runs=runs)
    fin = base.finals
    last = base.last()
    reproduced = last["guarded_unseen"] > last["plain_unseen"] and last["guarded_practice"] < last["plain_practice"]
    out = {
        "experiment": "E0 overfitting trap (overview toy, exact port)",
        "config": {"rounds": 60, "runs": runs, "p_trick": 0.35, "sigma": 0.02, "catch_rate": 0.8,
                   "band": "0.5 * sigma * sqrt(2) (demo)", "llm": "none (LLM-free toy)"},
        "page_readout": {
            "plain": f"Practice {last['plain_practice']:+.1f} pts, unseen {last['plain_unseen']:+.1f} pts, "
                     f"{last['changes_kept_plain']:.1f} changes kept",
            "guarded": f"Practice {last['guarded_practice']:+.1f} pts, unseen {last['guarded_unseen']:+.1f} pts, "
                       f"{last['changes_kept_guarded']:.1f} changes kept",
            "verdict": base.verdict()},
        "curves": curves(base),
        "arms": {k: {"last": r.last(), "finals_ci": {f: summarize_runs(v) for f, v in r.finals.items()},
                     "curves": curves(r)} for k, r in arms.items()},
        "paired_guarded_minus_plain": {"unseen_pts": paired_diff_ci(fin["plain_unseen"], fin["guarded_unseen"]),
                                       "practice_pts": paired_diff_ci(fin["plain_practice"], fin["guarded_practice"])},
        "sensitivity": rows,
        "verdict": {"claim": "plain loop: practice up, unseen ~flat; guarded loop: practice lower, unseen higher",
                    "reproduced": bool(reproduced),
                    "exact_match_to_page_js": "yes (see tests/rrsi/test_rrsi_toy.py: max |diff| ~1e-15, "
                                              "identical accept counts)",
                    "note": "the toy is the overview author's, not RRSI's method or data; with RRSI's z=2 band the "
                            "guarded loop keeps fewer changes (see arms.rrsi_band_z2)"},
    }
    save("e0_overfitting_trap", out)

    def draw(ax):
        xs = list(range(len(base.plain_ev)))
        for ys, style, color, label in ((base.plain_ev, "--", "0.5", "Plain loop, practice"),
                                        (base.plain_ho, "-", "#d9730d", "Plain loop, unseen"),
                                        (base.reg_ev, "--", "#2f6fdf", "Guarded loop, practice"),
                                        (base.reg_ho, "-", "#2f6fdf", "Guarded loop, unseen")):
            ax[0].plot(xs, ys, style, color=color, label=label)
        ax[0].set_xlabel("round")
        ax[0].set_ylabel("score change (points)")
        ax[0].set_title("Overview toy (page defaults, 200 runs)")
        ax[0].legend(fontsize=8)
        names = list(arms)
        ax[1].bar(range(len(names)), [arms[n].last()["guarded_unseen"] for n in names], color="#2f6fdf")
        ax[1].axhline(last["plain_unseen"], color="#d9730d", ls="--", label="plain loop, unseen")
        ax[1].set_xticks(range(len(names)))
        ax[1].set_xticklabels([n.split(" ")[0] for n in names], rotation=20, fontsize=8)
        ax[1].set_title("Guarded loop unseen gain by guard (pts)")
        ax[1].legend(fontsize=8)
        for fac, col in (("p_trick", "#9b51e0"), ("sigma", "#27ae60"), ("catch_rate", "#eb5757")):
            rs = [r for r in rows if r["factor"] == fac]
            ax[2].plot([r["value"] / max(x["value"] for x in rs) for r in rs],
                       [r["guarded_unseen"] - r["plain_unseen"] for r in rs], "o-", color=col, label=fac)
        ax[2].axhline(0, color="k", lw=0.5)
        ax[2].set_xlabel("slider value (normalised to its max)")
        ax[2].set_title("Guarded minus plain, unseen (pts)")
        ax[2].legend(fontsize=8)
    figure("e0_overfitting_trap", draw, 3, figsize=(15, 4.2))
    print(out["page_readout"])


if __name__ == "__main__":
    main()
