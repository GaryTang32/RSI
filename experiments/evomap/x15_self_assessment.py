"""X15: self-assessment contaminates learning when the memory graph reads the executor's printed outcome.
Run: python experiments/evomap/x15_self_assessment.py [--llm sim|claude:haiku] [--seeds N] [--quick]
"""
from __future__ import annotations

import random

from _common import (by_arm, cached_llm, live, mean_or_nan, paired, parse_args, pmap, print_groups, save,
                     summarize_groups)

import numpy as np

from rsi.evomap import AgentNode, Config, LocalStore
from rsi.evomap.memory import find_transcript_event
from rsi.evomap.signals import RunContext
from x4_local_loop import gw, library, make_agent

ARMS = {"faithful_overreport": dict(outcome_source="faithful", outcome_timing="next_cycle", carry=True, over=True),
        "faithful_honest": dict(outcome_source="faithful", outcome_timing="next_cycle", carry=True, over=False),
        "safe": dict(outcome_source="safe", outcome_timing="immediate", carry=False, over=True)}
METRICS = ["preferred_is_best", "preferred_true_effect", "acc_last_quarter", "solve_rate", "observed_share",
           "recorded_success_share"]
SELF_REPORT = ("\nAfter the code block, print on its own last line a single-line JSON object "
               '{"type": "EvolutionEvent", "outcome": {"status": "success" or "failed", "score": <0.0-1.0>}} '
               "giving your own assessment of whether the function passes ALL hidden tests.\n")
LIVE_ARMS = {"faithful": dict(outcome_source="faithful", outcome_timing="next_cycle", carry_log_signals=True),
             "safe": dict(outcome_source="safe", outcome_timing="immediate", carry_log_signals=False)}


def memory_config(seed: int, **kw) -> Config:
    """The memory graph as the only learning channel."""
    return Config(mode="safe", seed=seed, propose=False, distill=False, selector_mode="spec", use_memory=True,
                  epigenetic_suppression=False, failed_capsule_bans=False, **kw)


def preferred_genes(ag, tasks, classes, cfg: Config) -> dict:
    """The memory-preferred gene id per class, asked on the class's first task."""
    out = {}
    for cl in classes:
        t = next(t for t in tasks if t.family == cl)
        sig = ag.extractor.extract(RunContext(task=t))
        out[cl] = ag.store.memory.advice(sig, ag.store.genes.values(), mode=cfg.memory_mode).preferred_gene_id
    return out


def recorded_outcomes(ag) -> list[dict]:
    return [e["outcome"] for e in ag.store.memory.events if e.get("kind") == "outcome"]


def one(job) -> dict:
    seed, arm, args = job
    a = ARMS[arm]
    rng = random.Random(f"x15-{seed}")
    dom = gw(seed)
    genes, oracle = library(dom, rng)
    cfg = memory_config(seed, outcome_source=a["outcome_source"], outcome_timing=a["outcome_timing"],
                        carry_log_signals=a["carry"])
    ag = make_agent(dom, genes, 0.0, cfg, seed, over_report=a["over"])
    tasks = dom.tasks.split("evolve")
    T = 120 if args.quick else 360
    hits = []
    for _ in range(T):
        task = rng.choice(tasks)
        hits.append(ag.cycle(task).gene_id == oracle[task.family])
    ag.flush()
    w = dom.world
    pref = preferred_genes(ag, tasks, w.classes, cfg)
    pref_eff = [w.effects[w.class_keys(cl, ag.store.genes[pid].provenance["oracle_kind"])[0]][cl]
                for cl, pid in pref.items() if pid]
    outcomes = recorded_outcomes(ag)
    return {"seed": seed, "arm": arm,
            "preferred_is_best": float(np.mean([pid == oracle[cl] for cl, pid in pref.items()])),
            "preferred_true_effect": mean_or_nan(pref_eff),
            "acc_last_quarter": float(np.mean(hits[-T // 4:])),
            "solve_rate": float(np.mean([r.task_success for r in ag.results[-T // 4:]])),
            "observed_share": float(np.mean([o.get("note") == "evolutionevent_observed" for o in outcomes]))
            if outcomes else 0.0,
            "recorded_success_share": float(np.mean([o["status"] == "success" for o in outcomes]))}


def self_reported_success(ev: dict | None) -> bool | None:
    if ev is None:
        return None
    o = ev["outcome"]
    return o.get("status") == "success" if o.get("status") else float(o.get("score", 0)) >= 0.5


def one_live(arm: str, args) -> dict:
    """One live katas arm: good vs conflicting gene per class; the executor self-reports each outcome."""
    from rsi.domains.katas import CLASS_GENES, CONFLICTING_GENES, KatasDomain, seed_harness
    dom = KatasDomain()
    llm = cached_llm(args.llm)
    base = seed_harness()
    harness = base.with_files({"prompts/system.md": base["prompts/system.md"] + SELF_REPORT})
    st, oracle = LocalStore(node_id="x15live"), {}
    for cls in CLASS_GENES:
        for j, src in enumerate((CLASS_GENES[cls], CONFLICTING_GENES[cls])):
            g = src.copy()
            g.id = f"gene_{cls}_{chr(97 + j)}"
            st.upsert_gene(g)
        oracle[cls] = f"gene_{cls}_a"
    cfg = memory_config(0, **LIVE_ARMS[arm])
    ag = AgentNode("x15live", dom, harness, llm_task=llm, store=st, config=cfg)
    rng = random.Random(0)
    tasks = dom.tasks.split("evolve")
    rows = []
    for _ in range(8 if args.quick else 16):
        task = rng.choice(tasks)
        cr = ag.cycle(task)
        rows.append({"task": task.id, "gene": cr.gene_id, "oracle": cr.gene_id == oracle[task.family],
                     "hidden_success": bool(cr.task_success),
                     "self_report_success": self_reported_success(find_transcript_event(ag._last_trace))})
    ag.flush()
    pref = [pid == oracle[cls] if pid else None for cls, pid in preferred_genes(ag, tasks, oracle, cfg).items()]
    rep = [r for r in rows if r["self_report_success"] is not None]
    recorded = [o["status"] == "success" for o in recorded_outcomes(ag)]
    return {"arm": arm, "cycles": rows, "self_report_rate": len(rep) / len(rows),
            "self_reported_success_share": float(np.mean([r["self_report_success"] for r in rep])) if rep else None,
            "hidden_success_share": float(np.mean([r["hidden_success"] for r in rows])),
            "self_report_agreement": float(np.mean([r["self_report_success"] == r["hidden_success"] for r in rep]))
            if rep else None,
            "recorded_success_share": float(np.mean(recorded)) if recorded else None,
            "preferred_is_good_gene": pref, "acc_oracle_gene": float(np.mean([r["oracle"] for r in rows]))}


def main_live(args):
    res = {arm: one_live(arm, args) for arm in LIVE_ARMS}
    f, s = res["faithful"], res["safe"]
    out = {"config": {"llm": args.llm, "cycles_per_arm": len(f["cycles"]), "domain": "katas (default split)",
                      "note": "small-N live check; qualitative"}, "arms": res,
           "verdict": {"executor_over_reports": (f["self_reported_success_share"] or 0) > f["hidden_success_share"],
                       "faithful_records": f["recorded_success_share"], "safe_records": s["recorded_success_share"],
                       "hidden_success_share": f["hidden_success_share"]}}
    save("x15_self_assessment_live", out, args.out)
    for a, r in res.items():
        print(f"== {a}: self-report rate {r['self_report_rate']:.2f}, self-reported success "
              f"{r['self_reported_success_share']}, hidden success {r['hidden_success_share']:.2f}, recorded "
              f"{r['recorded_success_share']}, agreement {r['self_report_agreement']}")
    print("verdict:", out["verdict"])


def main():
    args = parse_args(__doc__.split("\n")[0], default_seeds=12)
    if live(args):
        return main_live(args)
    rows = pmap(one, [(s, a, args) for a in ARMS for s in range(args.seeds)], args.workers)
    by = by_arm(rows, ARMS)
    summ = summarize_groups(by, METRICS)
    pd = {a: {m: paired([r[m] for r in by[a]], [r[m] for r in by["safe"]])
              for m in ("preferred_true_effect", "preferred_is_best", "acc_last_quarter", "solve_rate")}
          for a in ("faithful_overreport", "faithful_honest")}
    out = {"config": {"seeds": args.seeds}, "raw": rows, "summary": summ, "paired_safe_minus": pd,
           "verdict": {"overreport_contaminates_preference":
                           pd["faithful_overreport"]["preferred_true_effect"]["lo"] > 0,
                       "heuristic_also_contaminates_preference":
                           pd["faithful_honest"]["preferred_true_effect"]["lo"] > 0,
                       "faithful_records_only_successes": summ["faithful_overreport"]["recorded_success_share"]["mean"],
                       "safe_recorded_success_share": summ["safe"]["recorded_success_share"]["mean"]}}
    save("x15_self_assessment", out, args.out)
    print_groups(summ, METRICS, 24)
    print("verdict:", out["verdict"])


if __name__ == "__main__":
    main()
