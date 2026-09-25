"""X1-X3: how experience should be represented and combined (Strategy Genes paper [snip:SG][sec:mpx]).

X1  compact genes carry experience better than long skill documents: none / Gene (~200 tok) / Skill (~2,000 tok,
    same content embedded in documentation) / Skill fragment truncated to the Gene's budget; plus the paper's
    progressive construction (keywords -> +summary -> +strategy).
X2  failure warnings work best as compact AVOID items: strategy only / AVOID only / strategy + AVOID / strategy +
    appended raw failure log.
X3  one targeted gene beats compositions: single / two complementary / two conflicting.

Domain: the 25 katas with hidden unit-test graders. Offline the solver is :class:`KataSimSolver`, whose response to
guidance is a set of KNOBS (hint gain, dilution inside long documents, length and composition penalties, penalty
for contradictory advice) - the offline numbers are a mechanism check of the pipeline, NOT evidence for the
claims. ``--llm claude:haiku`` runs the same arms with a real model (one command away; costs ~225 calls at k=1).

Run: python experiments/evomap/x1_representation.py [--llm sim|claude:haiku] [--seeds K] [--quick]
"""
from __future__ import annotations

from _common import cached_llm, fmt, live, paired, parse_args, save, summarize

import numpy as np

from rsi.core import Evaluator
from rsi.core.llm import estimate_tokens
from rsi.domains.katas import (CLASS_GENES, COMPLEMENTARY_GENES, CONFLICTING_GENES, FAILURE_LOGS, KATAS,
                               KataSimSolver, KatasDomain, seed_harness)
from rsi.evomap import render_gene as _render_gene, render_skill


def render_gene(g, parts=("keywords", "summary", "strategy", "avoid"), **kw):
    return _render_gene(g, parts, show_id=False, **kw)   # ids can carry content; keep ablations clean
from rsi.evomap.prompts import truncate_tokens


def arm_texts(arm: str, cls: str) -> dict:
    g = CLASS_GENES[cls]
    if arm == "none":
        return {}
    if arm == "gene":
        return {"genes/active/0.md": render_gene(g)}
    if arm == "skill":
        return {f"skills/{g.id}/SKILL.md": render_skill(g)}
    if arm == "skill_fragment":
        return {f"skills/{g.id}/SKILL.md": truncate_tokens(render_skill(g), estimate_tokens(render_gene(g)))}
    if arm == "keywords_only":
        return {"genes/active/0.md": render_gene(g, ("keywords",))}
    if arm == "keywords_summary":
        return {"genes/active/0.md": render_gene(g, ("keywords", "summary"))}
    if arm == "keywords_summary_strategy":
        return {"genes/active/0.md": render_gene(g, ("keywords", "summary", "strategy"))}
    if arm == "avoid_only":
        return {"genes/active/0.md": render_gene(g, ("keywords", "avoid"))}
    if arm == "strategy_appended_log":
        return {"genes/active/0.md": render_gene(g, ("keywords", "summary", "strategy"), failure_log=FAILURE_LOGS[cls])}
    if arm == "two_complementary":
        return {"genes/active/0.md": render_gene(g), "genes/active/1.md": render_gene(COMPLEMENTARY_GENES[cls])}
    if arm == "two_conflicting":
        return {"genes/active/0.md": render_gene(g), "genes/active/1.md": render_gene(CONFLICTING_GENES[cls])}
    raise KeyError(arm)


GROUPS = {"X1": ["none", "gene", "skill", "skill_fragment", "keywords_only", "keywords_summary",
                 "keywords_summary_strategy"],
          "X2": ["none", "keywords_summary_strategy", "avoid_only", "gene", "strategy_appended_log"],
          "X3": ["gene", "two_complementary", "two_conflicting"]}


def evaluate_arm(dom, llm, arm, k, katas):
    ev = Evaluator(dom, llm, workers=4 if not isinstance(llm, KataSimSolver) else 2)
    per_task, toks = {}, []
    base = seed_harness()
    for cls in sorted({kt.cls for kt in katas}):
        texts = arm_texts(arm, cls)
        art = base.with_files(texts)
        toks.append(sum(estimate_tokens(t) for t in texts.values()))
        tasks = [dom.tasks.get(kt.id) for kt in katas if kt.cls == cls]
        res = ev.evaluate(art, tasks, k, label=arm)
        per_task.update(res.task_scores())
    return per_task, float(np.mean(toks))


def main():
    args = parse_args(__doc__.split("\n")[0], default_seeds=5)
    k = args.seeds
    dom = KatasDomain()
    llm = cached_llm(args.llm) if live(args) else KataSimSolver(0.0)
    katas = [kt for i, kt in enumerate(KATAS) if not args.quick or i % 5 < 2]
    arms = sorted({a for g in GROUPS.values() for a in g}, key=lambda a: [a in g for g in GROUPS.values()].index(True))
    res, toks = {}, {}
    for arm in arms:
        res[arm], toks[arm] = evaluate_arm(dom, llm, arm, k, katas)
        print(f"  {arm:28s} pass {np.mean(list(res[arm].values())):.3f} injected tokens {toks[arm]:.0f}", flush=True)
    ids = sorted(res["none"])
    summ = {a: {**summarize([res[a][i] for i in ids]), "injected_tokens": toks[a]} for a in arms}
    vs_none = {a: paired([res["none"][i] for i in ids], [res[a][i] for i in ids]) for a in arms if a != "none"}
    vs_gene = {a: paired([res["gene"][i] for i in ids], [res[a][i] for i in ids]) for a in arms if a != "gene"}
    out = {"config": {"llm": args.llm, "k": k, "n_katas": len(katas), "solver": "KataSimSolver (knobs)" if not live(args)
                      else args.llm, "groups": GROUPS}, "per_task": res, "summary": summ, "paired_vs_none": vs_none,
           "paired_vs_gene": vs_gene}
    m = {a: summ[a]["mean"] for a in arms}
    out["verdict"] = {
        "X1_gene_ge_none": m["gene"] >= m["none"], "X1_gene_gt_skill": m["gene"] > m["skill"],
        "X1_gene_gt_matched_fragment": m["gene"] > m["skill_fragment"],
        "X1_progressive": [m["keywords_only"], m["keywords_summary"], m["keywords_summary_strategy"]],
        "X2_avoid_only_ge_appended_log": m["avoid_only"] >= m["strategy_appended_log"],
        "X3_single_ge_complementary": m["gene"] >= m["two_complementary"],
        "X3_single_ge_conflicting": m["gene"] >= m["two_conflicting"],
        "X3_complementary_worse_than_conflicting (paper)": m["two_complementary"] < m["two_conflicting"],
        "note": ("offline: the solver's response to guidance is a set of knobs, so these directions restate the knobs;"
                 " run with --llm claude:haiku for evidence") if not live(args) else "live model",
    }
    save("x1_representation" + ("" if not live(args) else "_live"), out, args.out)
    for a in arms:
        print(f"  {a:28s} {fmt(summ[a])}  tokens {toks[a]:.0f}")
    print("verdict:", out["verdict"])


if __name__ == "__main__":
    main()
