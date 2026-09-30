"""E17 - Fig. 3b re-read on the paper's own compute axis (claims audit Q15, retry round 2).

Q15: "the two trajectories diverge markedly: Dream-RSI consistently achieves superior downstream performance
while requiring substantially lower cumulative compute across both Gemini-3.1-Pro and Gemini-3.7-Flash"
[paper:§4.1 p.8]. The paper's x-axis is "cumulative discovery compute" = cumulative discovery-agent calls
(Fig. 3 caption), not rounds, tokens or wall-clock; the y-axis is the arithmetic-mean held-out runtime.

1. **The paper's exact data.** The arXiv source (``Main_Text/Figures/scaling_lasso.tex``) holds the plotted
   points. Two readings of "consistently superior ... while requiring lower cumulative compute":
   A = at the same round (the numbered markers), B = at the same cumulative compute (the Fixed curve linearly
   interpolated at each Dream marker, as the plotted lines connect them). The source's own comments are
   quoted: for Flash, Fixed "Iter 1--2 have no downstream evaluation" and Dream "Iter 1 has no downstream
   evaluation", although values are plotted there.
2. **Our E3 on the same axis.** E3's saved curves (best-so-far search score vs cumulative agent calls, Dream
   with more, cheaper rounds up to Fixed's budget) re-read with reading B at every Dream round end that Fixed's
   curve covers (the preregistered T2 criterion: CI lower bound > 0 at every point).

    python experiments/dream-rsi/e17_fig3b_axis.py [--source <scaling_lasso.tex>]
"""
import argparse
import json
import re
from pathlib import Path

import numpy as np

from _common import RESULTS, paired, save

#: exact values from the arXiv source (scaling_lasso.tex); used when --source is not given
PAPER = {
    "pro": {"fixed": [(110, 5267.0500), (220, 5365.4000), (330, 5295.2333), (440, 4691.0833), (550, 3587.0667)],
            "dream": [(110, 5267.0500), (119, 5349.1500), (147, 5368.9000), (234, 5705.4333), (317, 2931.0000)]},
    "flash": {"fixed": [(640, 2735.6), (1280, 2988.3), (1920, 3011.1333), (2560, 2909.1833), (3200, 2516.6833)],
              "dream": [(640, 2735.6), (976, 2394.9167), (1230, 2378.0500), (1529, 2327.7000), (1879, 2350.5833)]},
}
SOURCE_COMMENTS = {"flash_fixed": "Iter 1--2 have no downstream evaluation",
                   "flash_dream": "Iter 1 has no downstream evaluation",
                   "pro_dream": "Iter 1 shares the same initialization",
                   "metric": "arithmetic mean runtime over six real downstream tasks; Lower is better."}


def parse_source(tex: str) -> dict:
    tables = {}
    for body, name in re.findall(r"\\pgfplotstableread\{\s*compute avg iter\s*(.*?)\}\\(\w+)", tex, re.S):
        tables[name] = [(float(a), float(b)) for a, b, _ in re.findall(r"([\d.]+)\s+([\d.]+)\s+(\d+)", body)]
    return {"pro": {"fixed": tables["probaseline"], "dream": tables["proours"]},
            "flash": {"fixed": tables["flashbaseline"], "dream": tables["flashours"]}}


def paper_readings(data: dict) -> dict:
    out = {}
    for model, d in data.items():
        fx, dr = d["fixed"], d["dream"]
        xs, ys = [c for c, _ in fx], [v for _, v in fx]
        pts = []
        for r in range(1, len(dr)):              # rounds 2..5
            c, v = dr[r]
            fa = fx[r][1]                                      # reading A: same round
            fb = float(np.interp(c, xs, ys))                   # reading B: same cumulative calls
            pts.append({"round": r + 1, "dream_calls": c, "fixed_calls_same_round": fx[r][0], "dream_ms": v,
                        "A_fixed_ms": fa, "A_dream_better": v < fa, "A_diff_ms": v - fa,
                        "B_fixed_ms_interp": fb, "B_dream_better": v < fb, "B_diff_ms": v - fb,
                        "cheaper_same_round": c < fx[r][0]})
        out[model] = {"points": pts, "A_consistently_superior": all(p["A_dream_better"] for p in pts),
                      "B_consistently_superior": all(p["B_dream_better"] for p in pts),
                      "always_cheaper": all(p["cheaper_same_round"] for p in pts),
                      "A_worse_rounds": [p["round"] for p in pts if not p["A_dream_better"]],
                      "B_worse_rounds": [p["round"] for p in pts if not p["B_dream_better"]]}
    return out


def e3_reading_b(path: Path) -> dict:
    d = json.loads(path.read_text())
    out = {}
    for dom in d["results"]:
        rows = [r for r in d["raw"] if r["domain"] == dom and r["arm"] in ("fixed", "dream")]
        fx = {r["seed"]: r for r in rows if r["arm"] == "fixed"}
        dr = {r["seed"]: r for r in rows if r["arm"] == "dream"}
        seeds = sorted(set(fx) & set(dr))
        R = min(len(dr[s]["curve"]) for s in seeds)
        pts = []
        for r in range(2, R):                    # curve[0] = (0, seed); curve[1] = round 1 (shared)
            a, b = [], []
            for s in seeds:
                c, v = dr[s]["curve"][r]
                fxc = fx[s]["curve"]
                if c > fxc[-1][0]:
                    break
                a.append(float(np.interp(c, [p[0] for p in fxc], [p[1] for p in fxc])))
                b.append(v)
            if len(a) == len(seeds):
                pts.append({"dream_round": r, **paired(a, b)})
        out[dom] = {"points": pts, "consistently_superior": bool(pts) and all(p["lo"] > 0 for p in pts),
                    "n_points": len(pts), "n_seeds": len(seeds),
                    "share_points_ci_above_0": float(np.mean([p["lo"] > 0 for p in pts])) if pts else None,
                    "share_points_ci_below_0": float(np.mean([p["hi"] < 0 for p in pts])) if pts else None}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=None, help="scaling_lasso.tex from the arXiv source")
    ap.add_argument("--e3", default=str(RESULTS / "e3_dream_vs_fixed.json"))
    a = ap.parse_args()
    data = PAPER
    if a.source:
        parsed = parse_source(Path(a.source).read_text())
        assert parsed == {m: {k: [tuple(p) for p in v] for k, v in d.items()} for m, d in PAPER.items()}, parsed
    paper = paper_readings(data)
    e3 = e3_reading_b(Path(a.e3))
    decision = {"paper_contradicts_itself": any(p["A_worse_rounds"] and p["B_worse_rounds"] for p in paper.values()),
                "rule": "Q15 stays CONTRADICTED (root cause e) iff the paper's own exact data show Dream worse than "
                        "Fixed at >= 1 post-round-1 point under both readings for either model"}
    for m, p in paper.items():
        print(f"[paper {m}] A worse at rounds {p['A_worse_rounds']}, B worse at rounds {p['B_worse_rounds']}, "
              f"always cheaper {p['always_cheaper']}")
    for dom, r in e3.items():
        print(f"[E3 {dom}] reading B: {r['n_points']} points, CI>0 at {r['share_points_ci_above_0']}, CI<0 at "
              f"{r['share_points_ci_below_0']}; consistently superior {r['consistently_superior']}")
    save("e17_fig3b_axis", {"config": {"axis": "cumulative discovery-agent calls (Fig. 3 caption)",
                                       "source": "arXiv 2609.14858v1 source, Main_Text/Figures/scaling_lasso.tex",
                                       "readings": {"A": "same round", "B": "same cumulative calls, Fixed linearly "
                                                    "interpolated"}},
                            "source_comments": SOURCE_COMMENTS, "paper": paper, "e3_reading_b": e3,
                            "decision": decision})


if __name__ == "__main__":
    main()
