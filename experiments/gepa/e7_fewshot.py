"""E7 - Instruction-only optimization vs joint instruction + few-shot demo optimization.

Claims [paper Obs.2, Obs.4][doc "beat MIPROv2 by over 10 points"]: GEPA (instructions only)
beats MIPROv2 in every setting and its prompts are much shorter (up to 9.2x; around
< 33% of MIPROv2's size). Spec E7 expects an equal or higher test score with markedly
shorter prompts.

RuleWorld default world, equal rollout budgets B in {1500, 4000}: GEPA vs FewShotDemoOptimizer
(MIPRO-lite: bootstrapped + labeled demos, grounded instruction proposals from the same
reflection LM without feedback, TPE-like search on validation minibatches) vs its
no-demo variant (MIPROv2-No-Demos analogue). Demos help the simulated task model only by
imitation (p_demo = 0.35 per matching demo) and lengthen the prompt.

    python experiments/gepa/e7_fewshot.py [--llm sim|claude:haiku] [--seeds N] [--quick]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import RESULTS, bar_plot, fmt, paired, parse_args, pool_map, reflection_llm, save, summarize  # noqa

import numpy as np  # noqa: E402

from rsi.core.llm import estimate_tokens  # noqa: E402
from rsi.domains.ruleworld import make_domain  # noqa: E402
from rsi.gepa import Config, FewShotConfig, run, run_fewshot  # noqa: E402

ARMS = ["gepa", "mipro_lite", "mipro_lite_no_demos"]
ARGS = None


def job(spec):
    arm, seed, B = spec
    d = make_domain(seed=seed)
    llm = reflection_llm(ARGS.llm, d.world)
    if arm == "gepa":
        res = run(d, d.seed_artifact(), llm_propose=llm, config=Config(max_metric_calls=B, seed=seed))
    else:
        cfg = FewShotConfig(max_metric_calls=B, seed=seed, n_demo_sets=6 if arm == "mipro_lite" else 1,
                            labeled_demos=arm == "mipro_lite", bootstrap=arm == "mipro_lite")
        res = run_fewshot(d, d.seed_artifact(), llm_propose=llm, config=cfg)
    test = d.expected(res.best, "test")
    comps = [f for f in res.best.files if f.startswith("prompts/")]
    return {"arm": arm, "seed": seed, "B": B, "test": test, "val": res.meta["best_val"],
            "gap": res.meta["best_val"] - test, "chars": sum(len(res.best[c]) for c in comps),
            "tokens": sum(estimate_tokens(res.best[c]) for c in comps),
            "demo_lines": sum(1 for c in comps for l in res.best[c].splitlines() if l.startswith("Example")),
            "rollouts": res.meta["rollouts"]}


def main():
    global ARGS
    ARGS = a = parse_args("E7: instruction-only vs few-shot", default_seeds=30)
    budgets = [400] if a.live else ([1500] if a.quick else [1500, 4000])
    rows = pool_map(job, [(arm, s, B) for B in budgets for arm in ARMS for s in range(a.seeds)], a.workers)
    out = {"experiment": "E7 instruction-only vs few-shot", "llm": a.llm,
           "config": {"budgets": budgets, "seeds": a.seeds}, "summary": {}, "paired": {}, "raw": rows}
    verdicts = []
    for B in budgets:
        by = {arm: sorted([r for r in rows if r["arm"] == arm and r["B"] == B], key=lambda r: r["seed"])
              for arm in ARMS}
        summ = {arm: {k: summarize([r[k] for r in rs]) for k in ("test", "val", "gap", "chars", "tokens",
                                                                   "demo_lines", "rollouts")}
                for arm, rs in by.items()}
        pr = {f"gepa - {arm}": paired([r["test"] for r in by[arm]], [r["test"] for r in by["gepa"]])
              for arm in ARMS[1:]}
        ratio = float(np.median([m["tokens"] / g["tokens"] for g, m in zip(by["gepa"], by["mipro_lite"])]))
        out["summary"][f"B={B}"] = summ
        out["paired"][f"B={B}"] = pr
        out["summary"][f"B={B}"]["mipro_over_gepa_prompt_tokens_median"] = ratio
        p = pr["gepa - mipro_lite"]
        verdicts.append(
            f"[B={B}] test: GEPA {fmt(summ['gepa']['test'])}, MIPRO-lite {fmt(summ['mipro_lite']['test'])}, "
            f"MIPRO-lite-no-demos {fmt(summ['mipro_lite_no_demos']['test'])}; GEPA - MIPRO-lite = "
            f"{p['mean_diff']:+.3f} [{p['lo']:+.3f}, {p['hi']:+.3f}]; prompt tokens GEPA "
            f"{summ['gepa']['tokens']['mean']:.0f} vs MIPRO-lite {summ['mipro_lite']['tokens']['mean']:.0f} "
            f"(median ratio {ratio:.1f}x); gap val-test GEPA {summ['gepa']['gap']['mean']:+.3f} vs MIPRO-lite "
            f"{summ['mipro_lite']['gap']['mean']:+.3f}. Score claim (GEPA >= MIPRO): "
            f"{'REPRODUCED' if p['lo'] > 0 else ('tie' if p['hi'] >= 0 else 'NOT reproduced')}; length claim "
            f"(paper: MIPROv2 prompts ~3x, up to 9.2x longer): GEPA's are {ratio:.1f}x shorter - "
            f"{'REPRODUCED' if ratio >= 3 else ('shorter, but NOT by the paper margin' if ratio > 1 else 'NOT reproduced')}.")
    out["verdict"] = " ".join(verdicts)
    save("e7_fewshot", out, a.out)
    labels = [f"B={B}\n{arm}" for B in budgets for arm in ARMS]
    bar_plot(RESULTS / "e7_fewshot.png", labels, [out["summary"][f"B={B}"][arm]["test"] for B in budgets for arm in ARMS],
             "E7: instruction-only GEPA vs few-shot MIPRO-lite", "true test score")
    print(out["verdict"])


if __name__ == "__main__":
    main()
