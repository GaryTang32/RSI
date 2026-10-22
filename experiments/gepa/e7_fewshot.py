"""E7: instruction-only GEPA vs few-shot MIPRO-lite (with and without demos): test score and prompt length.

Run: python experiments/gepa/e7_fewshot.py [--llm sim|claude:haiku] [--seeds N] [--quick]
"""
from __future__ import annotations

import numpy as np

from _common import (RESULTS, bar_plot, by_seed, fmt, fmt_diff, paired, parse_args, pool_map, reflection_llm, save,
                     summarize)
from rsi.core.llm import estimate_tokens
from rsi.domains.ruleworld import make_domain
from rsi.gepa import Config, FewShotConfig, run, run_fewshot

ARMS = ["gepa", "mipro_lite", "mipro_lite_no_demos"]


def job(spec):
    arm, seed, B, llm = spec
    d = make_domain(seed=seed)
    propose = reflection_llm(llm, d.world)
    if arm == "gepa":
        res = run(d, d.seed_artifact(), llm_propose=propose, config=Config(max_metric_calls=B, seed=seed))
    else:
        demos = arm == "mipro_lite"
        cfg = FewShotConfig(max_metric_calls=B, seed=seed, n_demo_sets=6 if demos else 1, labeled_demos=demos,
                            bootstrap=demos)
        res = run_fewshot(d, d.seed_artifact(), llm_propose=propose, config=cfg)
    test = d.expected(res.best, "test")
    prompts = [res.best[f] for f in res.best.files if f.startswith("prompts/")]
    return {"arm": arm, "seed": seed, "B": B, "test": test, "val": res.meta["best_val"],
            "gap": res.meta["best_val"] - test, "chars": sum(len(p) for p in prompts),
            "tokens": sum(estimate_tokens(p) for p in prompts),
            "demo_lines": sum(1 for p in prompts for line in p.splitlines() if line.startswith("Example")),
            "rollouts": res.meta["rollouts"]}


def main():
    a = parse_args("E7: instruction-only vs few-shot", default_seeds=30)
    budgets = [400] if a.live else ([1500] if a.quick else [1500, 4000])
    rows = pool_map(job, [(arm, s, B, a.llm) for B in budgets for arm in ARMS for s in range(a.seeds)], a.workers)
    out = {"experiment": "E7 instruction-only vs few-shot", "llm": a.llm,
           "config": {"budgets": budgets, "seeds": a.seeds}, "summary": {}, "paired": {}, "raw": rows}
    verdicts = []
    for B in budgets:
        by = {arm: by_seed(rows, arm=arm, B=B) for arm in ARMS}
        summ = {arm: {k: summarize([r[k] for r in rs]) for k in ("test", "val", "gap", "chars", "tokens",
                                                                   "demo_lines", "rollouts")}
                for arm, rs in by.items()}
        pr = {f"gepa - {arm}": paired([r["test"] for r in by[arm]], [r["test"] for r in by["gepa"]])
              for arm in ARMS[1:]}
        ratio = float(np.median([m["tokens"] / g["tokens"] for g, m in zip(by["gepa"], by["mipro_lite"])]))
        out["summary"][f"B={B}"] = summ
        out["paired"][f"B={B}"] = pr
        summ["mipro_over_gepa_prompt_tokens_median"] = ratio
        p = pr["gepa - mipro_lite"]
        score = "REPRODUCED" if p["lo"] > 0 else ("tie" if p["hi"] >= 0 else "NOT reproduced")
        length = ("REPRODUCED" if ratio >= 3 else
                  "shorter, but NOT by the paper margin" if ratio > 1 else "NOT reproduced")
        verdicts.append(
            f"[B={B}] test: GEPA {fmt(summ['gepa']['test'])}, MIPRO-lite {fmt(summ['mipro_lite']['test'])}, "
            f"MIPRO-lite-no-demos {fmt(summ['mipro_lite_no_demos']['test'])}; GEPA - MIPRO-lite = "
            f"{fmt_diff(p)}; prompt tokens GEPA "
            f"{summ['gepa']['tokens']['mean']:.0f} vs MIPRO-lite {summ['mipro_lite']['tokens']['mean']:.0f} "
            f"(median ratio {ratio:.1f}x); gap val-test GEPA {summ['gepa']['gap']['mean']:+.3f} vs MIPRO-lite "
            f"{summ['mipro_lite']['gap']['mean']:+.3f}. Score claim (GEPA >= MIPRO): {score}; length claim "
            f"(paper: MIPROv2 prompts ~3x, up to 9.2x longer): GEPA's are {ratio:.1f}x shorter - {length}.")
    out["verdict"] = " ".join(verdicts)
    save("e7_fewshot", out, a.out)
    labels = [f"B={B}\n{arm}" for B in budgets for arm in ARMS]
    stats = [out["summary"][f"B={B}"][arm]["test"] for B in budgets for arm in ARMS]
    bar_plot(RESULTS / "e7_fewshot.png", labels, stats, "E7: instruction-only GEPA vs few-shot MIPRO-lite",
             "true test score")
    print(out["verdict"])


if __name__ == "__main__":
    main()
