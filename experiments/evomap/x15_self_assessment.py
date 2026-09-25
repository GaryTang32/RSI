"""X15: self-assessment contaminates learning when the memory graph reads the executor's printed outcome.

Claim (spec §4.11 / X15, inferred from the engine): the memory graph takes the outcome of cycle t-1 from the last
one-line EvolutionEvent JSON in the transcript (most plausibly the host LLM's own self-report), else from an
error-delta heuristic. An over-confident executor (always prints outcome.score 0.9) therefore steers gene
preference toward genes that do not help; the safe OutcomeInferrer (measured solidify outcome only) does not.

Setup: X4(b)'s library (per class: best / good / useless / harmful genes with identical triggers); the memory graph
is the only learning channel (pattern score, no learning-history adjustment, no epigenetics, no failed-capsule
bans). Arms: ``faithful_overreport`` (transcript first, one cycle late, executor prints success),
``faithful_honest`` (transcript first but the executor prints nothing -> the §4.11 heuristic), ``safe``
(measured outcome, immediate). Metrics: share of classes whose memory-preferred gene is the oracle best, mean true
effect of the preferred genes, selection accuracy in the last quarter, solve rate.

Run: python experiments/evomap/x15_self_assessment.py [--seeds N] [--quick]   (simulation only: the contamination
mechanism needs a controllable over-reporting executor; with --llm claude:* the script notes that and exits.)
"""
from __future__ import annotations

import random

from _common import fmt, live, paired, parse_args, pmap, save, summarize

import numpy as np

from rsi.evomap import Config
from x4_local_loop import gw, library, make_agent

ARMS = {"faithful_overreport": dict(outcome_source="faithful", outcome_timing="next_cycle", carry=True, over=True),
        "faithful_honest": dict(outcome_source="faithful", outcome_timing="next_cycle", carry=True, over=False),
        "safe": dict(outcome_source="safe", outcome_timing="immediate", carry=False, over=True)}


def one(job):
    seed, arm, args = job
    a = ARMS[arm]
    rng = random.Random(f"x15-{seed}")
    dom = gw(seed)
    genes, oracle = library(dom, rng)
    cfg = Config(mode="safe", seed=seed, propose=False, distill=False, selector_mode="spec", use_memory=True,
                 epigenetic_suppression=False, failed_capsule_bans=False, outcome_source=a["outcome_source"],
                 outcome_timing=a["outcome_timing"], carry_log_signals=a["carry"])
    ag = make_agent(dom, genes, 0.0, cfg, seed, over_report=a["over"])
    tasks = dom.tasks.split("evolve")
    T = 120 if args.quick else 360
    hits = []
    for _ in range(T):
        task = rng.choice(tasks)
        cr = ag.cycle(task)
        hits.append(cr.gene_id == oracle[task.family])
    ag.flush()
    w = dom.world
    pref_ok, pref_eff = [], []
    for cl in w.classes:
        t = next(t for t in tasks if t.family == cl)
        from rsi.evomap.signals import RunContext
        sig = ag.extractor.extract(RunContext(task=t))
        adv = ag.store.memory.advice(sig, ag.store.genes.values(), mode=cfg.memory_mode)
        pid = adv.preferred_gene_id
        pref_ok.append(pid == oracle[cl])
        if pid:
            kind = ag.store.genes[pid].provenance["oracle_kind"]
            key = w.class_keys(cl, kind)[0]
            pref_eff.append(w.effects[key][cl])
    notes = [e.get("outcome", {}).get("note") for e in ag.store.memory.events if e.get("kind") == "outcome"]
    return {"seed": seed, "arm": arm, "preferred_is_best": float(np.mean(pref_ok)),
            "preferred_true_effect": float(np.mean(pref_eff)) if pref_eff else float("nan"),
            "acc_last_quarter": float(np.mean(hits[-T // 4:])),
            "solve_rate": float(np.mean([r.task_success for r in ag.results[-T // 4:]])),
            "observed_share": float(np.mean([n == "evolutionevent_observed" for n in notes])) if notes else 0.0,
            "recorded_success_share": float(np.mean([e["outcome"]["status"] == "success" for e in
                                                     ag.store.memory.events if e.get("kind") == "outcome"]))}


METRICS = ["preferred_is_best", "preferred_true_effect", "acc_last_quarter", "solve_rate", "observed_share",
           "recorded_success_share"]


def main():
    args = parse_args(__doc__.split("\n")[0], default_seeds=12)
    if live(args):
        print("X15 needs a controllable over-reporting executor; it runs in simulation only.")
        return
    rows = pmap(one, [(s, a, args) for a in ARMS for s in range(args.seeds)], args.workers)
    by = {a: [r for r in rows if r["arm"] == a] for a in ARMS}
    summ = {a: {m: summarize([r[m] for r in by[a]]) for m in METRICS} for a in ARMS}
    pd = {a: {m: paired([r[m] for r in by[a]], [r[m] for r in by["safe"]])
              for m in ("preferred_true_effect", "preferred_is_best", "acc_last_quarter", "solve_rate")}
          for a in ("faithful_overreport", "faithful_honest")}
    out = {"config": {"seeds": args.seeds}, "raw": rows, "summary": summ, "paired_safe_minus": pd,
           "verdict": {"overreport_contaminates_preference": pd["faithful_overreport"]["preferred_true_effect"]["lo"] > 0,
                       "heuristic_also_contaminates_preference":
                           pd["faithful_honest"]["preferred_true_effect"]["lo"] > 0,
                       "faithful_records_only_successes": summ["faithful_overreport"]["recorded_success_share"]["mean"],
                       "safe_recorded_success_share": summ["safe"]["recorded_success_share"]["mean"]}}
    save("x15_self_assessment", out, args.out)
    for a in ARMS:
        print(f"== {a}")
        for m in METRICS:
            print(f"  {m:24s} {fmt(summ[a][m])}")
    print("verdict:", out["verdict"])


if __name__ == "__main__":
    main()
