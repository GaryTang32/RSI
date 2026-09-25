"""X4: the local loop reuses what worked, bans what fails, explores when stuck (spec §3.3, §4.3-4.5).

(a) ban latency: a library whose only matching gene is harmful; count failures until it is excluded, with each
    rule isolated - spec memory rule (n >= 2, value < 0.18; expected 4), current rule (per-key >= 4, best < 0.15;
    expected 5; 6 with the x1.15 predictive factor, simulated on the memory graph directly), failed-capsule rule
    (>= 2 failed capsules whose trigger overlaps >= 0.6; expected 2) - and with all engine rules on (epigenetic
    hard suppression at boost <= -0.3 fires after 3 failures, before the memory-graph rules).
(b) selection accuracy vs the oracle-best gene: every class has four genes with identical triggers (best, good,
    useless, harmful strategy keys, neutral ids). Arms: ``none`` (pattern score only: no memory graph, no
    learning-history adjustment, no epigenetics, no failed-capsule bans), ``memory_only`` (memory graph advice
    on the spec pattern score), ``full`` (current engine), ``full_drift``.
(c) exploring when stuck: the 'good' gene matches 3 keywords, the 'best' gene only 1 (pattern score prefers the
    worse gene); a weak model (ability -1) so the good gene often fails in streaks. Arms: ``static`` (no drift, no
    plateau override) vs ``drift``, each with the full engine (learning-history penalties + epigenetic suppression)
    and with the pattern score + memory graph only (``pattern_static`` / ``pattern_plateau`` (plateau override ->
    drift after >= 5 non-successes) / ``pattern_drift``).

Run: python experiments/evomap/x4_local_loop.py [--llm sim|claude:haiku] [--seeds N] [--quick]
(``--llm claude:*`` runs part (b) on the katas domain with a live model and the hand-written class genes.)
"""
from __future__ import annotations

import random

from _common import cached_llm, fmt, live, paired, parse_args, pmap, save, summarize

import numpy as np

from rsi.evomap import AgentNode, Clock, Config, Gene, LocalStore, MemoryGraph


def gw(seed):
    from rsi.domains.geneworld import WorldConfig, make_domain
    return make_domain(WorldConfig(seed=seed))


def gene_for(w, cl, kind, sm, gid):
    key = w.class_keys(cl, kind)[0]
    return Gene(id=gid, signals_match=list(sm), strategy=[f"Read the failing {cl} handler.", w.strategy_step(key),
                                                          "Re-run the public check."],
                summary=f"{cl} strategy", validation=["python check.py"], provenance={"oracle_kind": kind})


def make_agent(dom, genes, ability, cfg, seed, over_report=False):
    from rsi.domains.geneworld import GeneWorldModel
    st = LocalStore(node_id=f"x4_{seed}", clock=Clock())
    for g in genes:
        st.upsert_gene(g)
    return AgentNode(f"x4_{seed}", dom, dom.seed_artifact(), llm_task=GeneWorldModel(ability, over_report=over_report),
                     llm_propose=None, config=cfg, store=st)


def library(dom, rng, *, stagnation=False):
    w = dom.world
    genes, oracle = [], {}
    for cl in w.classes:
        kinds = ["best", "good", "useless", "harmful"]
        rng.shuffle(kinds)
        for j, kind in enumerate(kinds):
            gid = f"gene_{cl}_{chr(97 + j)}"
            sm = w.keywords[cl]
            if stagnation:
                if kind == "best":
                    sm = sm[:1]
                elif kind == "good":
                    sm = sm[:3]
                else:
                    continue
            genes.append(gene_for(w, cl, kind, sm, gid))
            if kind == "best":
                oracle[cl] = gid
    return genes, oracle


# ----------------------------------------------------------------------------- (a) ban latency
def ban_latency(seed, rule):
    dom = gw(seed)
    w = dom.world
    cl = w.classes[seed % len(w.classes)]
    g = gene_for(w, cl, "harmful", w.keywords[cl], "gene_harmful")
    all_rules = rule == "engine_all_rules"
    cfg = Config(mode="safe", seed=seed, propose=False, distill=False, outcome_timing="immediate",
                 memory_mode="spec" if rule == "spec" else "current",
                 use_memory=rule in ("spec", "current") or all_rules,
                 failed_capsule_bans=rule == "failed_capsule" or all_rules, failed_capsule_rule="absolute",
                 epigenetic_suppression=all_rules, plateau_override=False)
    ag = make_agent(dom, [g], -3.0, cfg, seed)
    ag.deduper.suppress_min = 10 ** 6 if not all_rules else ag.deduper.suppress_min
    tasks = [t for t in dom.tasks.split("evolve") if t.family == cl]
    fails = 0
    for i in range(30):
        cr = ag.cycle(tasks[i % len(tasks)])
        if cr.gene_id != "gene_harmful":
            return fails
        fails += int(not cr.task_success)
    return float("nan")


def ban_latency_predictive(n_max=20):
    mg = MemoryGraph(clock=Clock())
    for n in range(1, n_max):
        mg.record_outcome(signals=["a", "b"], gene_id="g", status="failed", score=0.2, predictive={"trend": -0.06})
        if "g" in mg.advice(["a", "b"]).banned_gene_ids:
            return n
    return float("nan")


# ----------------------------------------------------------------------------- (b)/(c) selection accuracy
ARMS_B = {"none": dict(selector_mode="spec", use_memory=False, epigenetic_suppression=False,
                        failed_capsule_bans=False),
          "memory_only": dict(selector_mode="spec", use_memory=True, epigenetic_suppression=False,
                              failed_capsule_bans=False),
          "full": dict(), "full_drift": dict(drift=True)}
_PAT = dict(selector_mode="spec", use_memory=True, epigenetic_suppression=False, failed_capsule_bans=False)
ARMS_C = {"full_static": dict(drift=False, plateau_override=False), "full_drift": dict(drift=True, plateau_override=True),
          "pattern_static": dict(_PAT, drift=False, plateau_override=False),
          "pattern_plateau": dict(_PAT, drift=False, plateau_override=True),
          "pattern_drift": dict(_PAT, drift=True, plateau_override=True)}


def selection(job):
    seed, part, arm, args = job
    rng = random.Random(f"x4-{seed}")
    if live(args):
        return selection_live(seed, arm, args)
    dom = gw(seed)
    genes, oracle = library(dom, rng, stagnation=part == "c")
    kw = (ARMS_B if part == "b" else ARMS_C)[arm]
    cfg = Config(mode="safe", seed=seed, propose=False, distill=False, **kw)
    ag = make_agent(dom, genes, 0.0 if part == "b" else -1.0, cfg, seed)
    tasks = dom.tasks.split("evolve")
    T = 120 if args.quick else 360
    hits = []
    for t in range(T):
        task = rng.choice(tasks)
        cr = ag.cycle(task)
        hits.append(cr.gene_id == oracle[task.family])
    q = hits[-T // 4:]
    found = {}
    for task_hit, r in zip(hits, ag.results):
        found[r.family] = found.get(r.family, False) or task_hit
    return {"seed": seed, "part": part, "arm": arm, "acc_last_quarter": float(np.mean(q)),
            "acc_first_quarter": float(np.mean(hits[: T // 4])), "discovered_share": float(np.mean(list(found.values()))),
            "solve_rate": float(np.mean([r.task_success for r in ag.results[-T // 4:]]))}


def selection_live(seed, arm, args):
    from rsi.domains.katas import CONFLICTING_GENES, CLASS_GENES, KatasDomain, seed_harness
    dom = KatasDomain()
    llm = cached_llm(args.llm)
    genes, oracle = [], {}
    for cls in CLASS_GENES:
        for j, (src, best) in enumerate(((CLASS_GENES[cls], True), (CONFLICTING_GENES[cls], False))):
            g = src.copy()
            g.id = f"gene_{cls}_{chr(97 + j)}"
            genes.append(g)
            if best:
                oracle[cls] = g.id
    st = LocalStore(node_id="x4live")
    for g in genes:
        st.upsert_gene(g)
    ag = AgentNode("x4live", dom, seed_harness(), llm_task=llm, store=st,
                   config=Config(mode="safe", seed=seed, propose=False, distill=False, **ARMS_B[arm]))
    rng = random.Random(seed)
    tasks = dom.tasks.split("evolve")
    hits = []
    for t in range(12):
        task = rng.choice(tasks)
        cr = ag.cycle(task)
        hits.append(cr.gene_id == oracle[task.family])
    return {"seed": seed, "part": "b", "arm": arm, "acc_last_quarter": float(np.mean(hits[-4:])),
            "acc_first_quarter": float(np.mean(hits[:4])), "discovered_share": float("nan"),
            "solve_rate": float(np.mean([r.task_success for r in ag.results]))}


def main():
    args = parse_args(__doc__.split("\n")[0], default_seeds=12)
    out = {"config": {"llm": args.llm, "seeds": args.seeds}}
    if not live(args):
        rules = ("spec", "current", "failed_capsule", "engine_all_rules")
        lat = {rule: [ban_latency(s, rule) for s in range(args.seeds)] for rule in rules}
        lat["current_predictive"] = [ban_latency_predictive()]
        out["ban_latency"] = {k: summarize(v) for k, v in lat.items()}
        out["ban_latency_raw"] = lat
    parts = {"b": ["full", "none"]} if live(args) else {"b": list(ARMS_B), "c": list(ARMS_C)}
    jobs = [(s, p, a, args) for p, arms in parts.items() for a in arms for s in range(args.seeds)]
    rows = pmap(selection, jobs, args.workers)
    out["raw"] = rows
    summ = {f"{p}_{a}": {k: summarize([r[k] for r in rows if r["part"] == p and r["arm"] == a])
                         for k in ("acc_first_quarter", "acc_last_quarter", "discovered_share", "solve_rate")}
            for p, arms in parts.items() for a in arms}
    out["summary"] = summ

    def col(p, a, k="acc_last_quarter"):
        return [r[k] for r in rows if r["part"] == p and r["arm"] == a]
    v = {"learning_raises_accuracy_full_vs_none": paired(col("b", "none"), col("b", "full"))}
    v["learning_raises_accuracy"] = v["learning_raises_accuracy_full_vs_none"]["lo"] > 0
    if not live(args):
        bl = out["ban_latency"]
        v.update({"ban_latency_spec_eq_4": bl["spec"]["mean"] == 4, "ban_latency_current_eq_5": bl["current"]["mean"] == 5,
                  "ban_latency_current_predictive_eq_6": bl["current_predictive"]["mean"] == 6,
                  "ban_latency_failed_capsule_mean": bl["failed_capsule"]["mean"],
                  "ban_latency_engine_all_rules_mean": bl["engine_all_rules"]["mean"],
                  "memory_graph_alone_raises_accuracy": paired(col("b", "none"), col("b", "memory_only"))["lo"] > 0,
                  "accuracy_rises_toward_oracle": summ["b_full"]["acc_last_quarter"]["mean"] >
                  summ["b_full"]["acc_first_quarter"]["mean"],
                  "full_engine_escapes_without_drift": summ["c_full_static"]["discovered_share"]["mean"],
                  "pattern_plateau_discovers_more": paired(col("c", "pattern_static", "discovered_share"),
                                                           col("c", "pattern_plateau", "discovered_share"))["lo"] > 0,
                  "pattern_drift_discovers_more": paired(col("c", "pattern_static", "discovered_share"),
                                                         col("c", "pattern_drift", "discovered_share"))["lo"] > 0,
                  "pattern_drift_paired_acc": paired(col("c", "pattern_static"), col("c", "pattern_drift")),
                  "pattern_drift_paired_solve": paired(col("c", "pattern_static", "solve_rate"),
                                                       col("c", "pattern_drift", "solve_rate"))})
    out["verdict"] = v
    save("x4_local_loop", out, args.out)
    if "ban_latency" in out:
        for k, s in out["ban_latency"].items():
            print(f"  ban latency {k:20s} {fmt(s, 2)}")
    for k, s in summ.items():
        print(f"  {k:16s} first {fmt(s['acc_first_quarter'])} last {fmt(s['acc_last_quarter'])} "
              f"found {fmt(s['discovered_share'])} solve {fmt(s['solve_rate'])}")
    print("verdict:", v)


if __name__ == "__main__":
    main()
