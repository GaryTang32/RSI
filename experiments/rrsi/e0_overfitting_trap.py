"""E0 - the overview's "See the overfitting trap" toy, reproduced exactly.

Runs the page's own simulator (ported bit-for-bit from its JavaScript; verified against
Node in tests/test_rrsi_toy.py) at the page defaults: 60 rounds x 200 runs, 35% tricks,
noise 2.0 pts, critic catches 80%. Reports the four curves (plain/guarded x practice/
unseen), per-run final values with 95% bootstrap CIs, the guard ablations (critic only,
noise margin only), RRSI's z = 2 band instead of the demo's half-sd band, and one-factor
sensitivity sweeps over the three sliders.

The toy is LLM-free: ``--llm`` is accepted for interface uniformity and ignored.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import RESULTS, parse_args, save  # noqa: E402

from rsi.core import paired_diff_ci, summarize_runs  # noqa: E402
from rsi.rrsi.toy import simulate, sweep  # noqa: E402


def finals_summary(r) -> dict:
    return {k: summarize_runs(v) for k, v in r.finals.items()}


def main():
    a = parse_args("E0: overview overfitting-trap toy", default_seeds=200)
    runs = 200 if not a.quick else 50
    base = simulate(0.35, 0.02, 0.8, runs=runs)
    arms = {
        "page_defaults": base,
        "critic_only (band 0)": simulate(0.35, 0.02, 0.8, runs=runs, band_z=0.0),
        "noise_margin_only (no critic)": simulate(0.35, 0.02, 0.8, runs=runs, critic=False),
        "rrsi_band_z2": simulate(0.35, 0.02, 0.8, runs=runs, band_z=2.0),
    }
    rows = sweep({"p_trick": [0.0, 0.1, 0.2, 0.35, 0.5, 0.6], "sigma": [0.0, 0.01, 0.02, 0.03, 0.04],
                  "catch_rate": [0.0, 0.25, 0.5, 0.8, 1.0]}, runs=runs)
    diff_unseen = paired_diff_ci(base.finals["plain_unseen"], base.finals["guarded_unseen"])
    diff_practice = paired_diff_ci(base.finals["plain_practice"], base.finals["guarded_practice"])
    last = base.last()
    reproduced = last["guarded_unseen"] > last["plain_unseen"] and last["guarded_practice"] < last["plain_practice"]
    out = {
        "experiment": "E0 overfitting trap (overview toy, exact port)",
        "config": {"rounds": 60, "runs": runs, "p_trick": 0.35, "sigma": 0.02, "catch_rate": 0.8,
                   "band": "0.5 * sigma * sqrt(2) (demo)", "llm": "none (LLM-free toy)"},
        "page_readout": {"plain": f"Practice {last['plain_practice']:+.1f} pts, unseen {last['plain_unseen']:+.1f} pts, "
                                  f"{last['changes_kept_plain']:.1f} changes kept",
                         "guarded": f"Practice {last['guarded_practice']:+.1f} pts, unseen {last['guarded_unseen']:+.1f} "
                                    f"pts, {last['changes_kept_guarded']:.1f} changes kept",
                         "verdict": base.verdict()},
        "curves": {"plain_practice": base.plain_ev, "plain_unseen": base.plain_ho, "guarded_practice": base.reg_ev,
                   "guarded_unseen": base.reg_ho},
        "arms": {k: {"last": r.last(), "finals_ci": finals_summary(r), "curves": {
            "plain_practice": r.plain_ev, "plain_unseen": r.plain_ho, "guarded_practice": r.reg_ev,
            "guarded_unseen": r.reg_ho}} for k, r in arms.items()},
        "paired_guarded_minus_plain": {"unseen_pts": diff_unseen, "practice_pts": diff_practice},
        "sensitivity": rows,
        "verdict": {"claim": "plain loop: practice up, unseen ~flat; guarded loop: practice lower, unseen higher",
                    "reproduced": bool(reproduced),
                    "exact_match_to_page_js": "yes (see tests/test_rrsi_toy.py: max |diff| ~1e-15, identical accept counts)",
                    "note": "the toy is the overview author's, not RRSI's method or data; with RRSI's z=2 band the "
                            "guarded loop keeps fewer changes (see arms.rrsi_band_z2)"},
    }
    save("e0_overfitting_trap", out)
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(1, 3, figsize=(15, 4.2))
        xs = list(range(len(base.plain_ev)))
        ax[0].plot(xs, base.plain_ev, "--", color="0.5", label="Plain loop, practice")
        ax[0].plot(xs, base.plain_ho, "-", color="#d9730d", label="Plain loop, unseen")
        ax[0].plot(xs, base.reg_ev, "--", color="#2f6fdf", label="Guarded loop, practice")
        ax[0].plot(xs, base.reg_ho, "-", color="#2f6fdf", label="Guarded loop, unseen")
        ax[0].set_xlabel("round")
        ax[0].set_ylabel("score change (points)")
        ax[0].set_title("Overview toy (page defaults, 200 runs)")
        ax[0].legend(fontsize=8)
        names = list(arms)
        ax[1].bar(range(len(names)), [arms[n].last()["guarded_unseen"] for n in names], color="#2f6fdf")
        ax[1].axhline(base.last()["plain_unseen"], color="#d9730d", ls="--", label="plain loop, unseen")
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
        fig.tight_layout()
        fig.savefig(RESULTS / "e0_overfitting_trap.png", dpi=110)
    except Exception as e:  # noqa: BLE001
        print("figure skipped:", e)
    print(out["page_readout"])


if __name__ == "__main__":
    main()
