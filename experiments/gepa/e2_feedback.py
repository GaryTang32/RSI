"""E2 - Text feedback ablation: rich vs symptom vs score-only mu_f.

Claim [paper 3 (argued, never ablated)][doc "works best when feedback includes text
explaining failures"]: textual feedback is the key learning signal; spec E2 expects
rich > symptom > score_only in both speed and final score.

RuleWorld (default world), GEPA at equal budget with the grader's feedback at three
levels (``rich`` names the violated rule, ``symptom`` names only the failing property,
``score_only`` gives no text) plus ScoreOnlyReflection (the engine strips the grader text
of the rich domain; inputs, outputs and traces remain). Also: rich feedback that
reports only the first failing property per module (compiler-style).

    python experiments/gepa/e2_feedback.py [--llm sim|claude:haiku] [--seeds N] [--quick]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (RESULTS, fmt, paired, parse_args, plot_curves, pool_map, reflection_llm, save,  # noqa: E402
                     summarize)

import numpy as np  # noqa: E402

from rsi.domains.ruleworld import make_domain  # noqa: E402
from rsi.gepa import Config, curve_at, gepa_curve, rollouts_to_target, run  # noqa: E402

ARMS = {"rich": ("rich", "full", False), "rich_first_error": ("rich", "full", True),
        "symptom": ("symptom", "full", False), "score_only": ("score_only", "full", False),
        "ScoreOnlyReflection(rich domain)": ("rich", "score_only", False)}
GRID = [100, 200, 400, 800, 1600, 3000]
ARGS = None


def job(spec):
    arm, seed, B = spec
    fb, mode, first = ARMS[arm]
    d = make_domain(seed=seed, feedback=fb, first_error_only=first)
    res = run(d, d.seed_artifact(), llm_propose=reflection_llm(ARGS.llm, d.world),
              config=Config(max_metric_calls=B, seed=seed, feedback=mode))
    oracle = d.expected(d.oracle_artifact(), "test")
    curve = gepa_curve(res, lambda a: d.expected(a, "test"))
    vcurve = [(r, res.state.agg_scores()[b]) for r, b in res.state.best_history]
    ev = [e.get("event") for e in res.state.trace]
    return {"arm": arm, "seed": seed, "final_test": curve[-1][1], "best_val": res.meta["best_val"],
            "at": curve_at(curve, GRID), "val_at": curve_at(vcurve, GRID),
            "to80": rollouts_to_target(curve, 0.8 * oracle), "accept_rate": ev.count("accepted") / max(
                1, ev.count("accepted") + ev.count("rejected")), "n_candidates": res.meta["n_candidates"]}


def main():
    global ARGS
    ARGS = a = parse_args("E2: feedback ablation", default_seeds=30)
    B = 400 if a.live else (1200 if a.quick else 3000)
    grid = [g for g in GRID if g <= B]
    arms = list(ARMS) if not a.live else ["rich", "score_only"]
    rows = pool_map(job, [(arm, s, B) for s in range(a.seeds) for arm in arms], a.workers)
    by = {}
    for r in rows:
        by.setdefault(r["arm"], []).append(r)
    for rs in by.values():
        rs.sort(key=lambda r: r["seed"])
    summ = {arm: {"final_test": summarize([r["final_test"] for r in rs]),
                  "best_val": summarize([r["best_val"] for r in rs]),
                  "test_at_budget": {str(b): summarize([r["at"][j] for r in rs]) for j, b in enumerate(GRID)
                                     if b <= B},
                  "rollouts_to_80pct_oracle": summarize([r["to80"] for r in rs]),
                  "reached_80pct": float(np.mean([r["to80"] is not None for r in rs])),
                  "accept_rate": summarize([r["accept_rate"] for r in rs])} for arm, rs in by.items()}
    pairs = {}
    order = [x for x in ("rich", "symptom", "score_only") if x in by]
    for x, y in zip(order, order[1:]):
        pairs[f"{x} - {y}"] = paired([r["final_test"] for r in by[y]], [r["final_test"] for r in by[x]])
    if "ScoreOnlyReflection(rich domain)" in by:
        pairs["rich - ScoreOnlyReflection"] = paired([r["final_test"] for r in by["ScoreOnlyReflection(rich domain)"]],
                                                     [r["final_test"] for r in by["rich"]])
    means = [summ[x]["final_test"]["mean"] for x in order]
    mono = all(p > q for p, q in zip(means, means[1:]))
    sig = {k: bool(v.get("lo", -1) > 0) for k, v in pairs.items()}
    speed = [summ[x]["test_at_budget"][str(grid[len(grid) // 2])]["mean"] for x in order]
    rich_best = all(sig.get(k, False) for k in pairs if k.startswith("rich -"))
    out = {"experiment": "E2 text-feedback ablation", "llm": a.llm, "config": {"budget": B, "seeds": a.seeds},
           "summary": summ, "paired_final_test": pairs, "significant": sig, "raw": rows,
           "verdict": (f"Final true test at B={B}: " + ", ".join(f"{x} {fmt(summ[x]['final_test'])}" for x in summ)
                       + ". Paired differences: " + "; ".join(
                           f"{k} {v['mean_diff']:+.3f} [{v['lo']:+.3f}, {v['hi']:+.3f}]" for k, v in pairs.items())
                       + f". At {grid[len(grid) // 2]} rollouts: "
                       + ", ".join(f"{x} {v:.3f}" for x, v in zip(order, speed))
                       + f". Claim rich > symptom > score_only: ordering of means "
                       f"{'holds' if mono else 'does NOT hold'}; rich text beats the text-free arms "
                       f"{'significantly (REPRODUCED)' if rich_best else '(not significant)'}; symptom vs score_only "
                       f"{'significant' if sig.get('symptom - score_only') else 'NOT significant'}. The primary paper "
                       "never ran this ablation; it is argued, not measured there.")}
    save("e2_feedback", out, a.out)
    plot_curves(RESULTS / "e2_feedback.png", grid, {k: [r["at"][: len(grid)] for r in v] for k, v in by.items()},
                "E2: feedback level (mu_f) vs true test score", logx=True)
    print(out["verdict"])


if __name__ == "__main__":
    main()
