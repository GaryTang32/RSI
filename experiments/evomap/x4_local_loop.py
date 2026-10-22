"""X4: the local loop reuses what worked, bans what fails, explores when stuck (spec §3.3, §4.3-4.5).
Run: python experiments/evomap/x4_local_loop.py [--llm sim|claude:haiku] [--seeds N] [--quick]
"""
from __future__ import annotations

import random

from _common import cached_llm, fmt, live, paired, parse_args, pmap, save, summarize

import numpy as np

from rsi.evomap import AgentNode, Clock, Config, Gene, LocalStore, MemoryGraph

PATTERN_MEMORY = dict(selector_mode="spec", use_memory=True, epigenetic_suppression=False, failed_capsule_bans=False)
ARMS_B = {"none": {**PATTERN_MEMORY, "use_memory": False}, "memory_only": PATTERN_MEMORY, "full": {},
          "full_drift": {"drift": True}}
ARMS_C = {"full_static": dict(drift=False, plateau_override=False),
          "full_drift": dict(drift=True, plateau_override=True),
          "pattern_static": dict(PATTERN_MEMORY, drift=False, plateau_override=False),
          "pattern_plateau": dict(PATTERN_MEMORY, drift=False, plateau_override=True),
          "pattern_drift": dict(PATTERN_MEMORY, drift=True, plateau_override=True)}
# part (c): the best gene matches fewer keywords than the good one, so the pattern score prefers the worse gene
STAGNATION_KEYWORDS = {"best": 1, "good": 3}


def gw(seed: int):
    from rsi.domains.geneworld import WorldConfig, make_domain
    return make_domain(WorldConfig(seed=seed))


def gene_for(w, cl: str, kind: str, sm, gid: str) -> Gene:
    key = w.class_keys(cl, kind)[0]
    return Gene(id=gid, signals_match=list(sm), strategy=[f"Read the failing {cl} handler.", w.strategy_step(key),
                                                          "Re-run the public check."],
                summary=f"{cl} strategy", validation=["python check.py"], provenance={"oracle_kind": kind})


def store_with(node_id: str, genes, clock: Clock | None = None) -> LocalStore:
    st = LocalStore(node_id=node_id, clock=clock)
    for g in genes:
        st.upsert_gene(g)
    return st


def make_agent(dom, genes, ability: float, cfg: Config, seed: int, over_report: bool = False) -> AgentNode:
    from rsi.domains.geneworld import GeneWorldModel
    return AgentNode(f"x4_{seed}", dom, dom.seed_artifact(), llm_task=GeneWorldModel(ability, over_report=over_report),
                     llm_propose=None, config=cfg, store=store_with(f"x4_{seed}", genes, Clock()))


def library(dom, rng: random.Random, *, stagnation: bool = False) -> tuple[list[Gene], dict]:
    """Per class four genes with identical triggers (best / good / useless / harmful keys, neutral ids)."""
    w = dom.world
    genes, oracle = [], {}
    for cl in w.classes:
        kinds = ["best", "good", "useless", "harmful"]
        rng.shuffle(kinds)
        for j, kind in enumerate(kinds):
            if stagnation and kind not in STAGNATION_KEYWORDS:
                continue
            gid = f"gene_{cl}_{chr(97 + j)}"
            sm = w.keywords[cl][:STAGNATION_KEYWORDS[kind]] if stagnation else w.keywords[cl]
            genes.append(gene_for(w, cl, kind, sm, gid))
            if kind == "best":
                oracle[cl] = gid
    return genes, oracle


def katas_library() -> tuple[list[Gene], dict]:
    """Per kata class the hand-written gene (the oracle) and a conflicting gene with identical triggers."""
    from rsi.domains.katas import CLASS_GENES, CONFLICTING_GENES
    genes, oracle = [], {}
    for cls in CLASS_GENES:
        for j, src in enumerate((CLASS_GENES[cls], CONFLICTING_GENES[cls])):
            g = src.copy()
            g.id = f"gene_{cls}_{chr(97 + j)}"
            genes.append(g)
        oracle[cls] = genes[-2].id
    return genes, oracle


def oracle_hits(ag: AgentNode, tasks, oracle: dict, rng: random.Random, n: int) -> list[bool]:
    """Run ``n`` cycles on random tasks; per cycle, whether the selected gene was the class's oracle gene."""
    hits = []
    for _ in range(n):
        task = rng.choice(tasks)
        hits.append(ag.cycle(task).gene_id == oracle[task.family])
    return hits


def ban_latency(seed: int, rule: str) -> float:
    """Failures before a lone harmful gene is excluded under one ban rule (or all engine rules); NaN if never."""
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
    if not all_rules:
        ag.deduper.suppress_min = 10 ** 6
    tasks = [t for t in dom.tasks.split("evolve") if t.family == cl]
    fails = 0
    for i in range(30):
        cr = ag.cycle(tasks[i % len(tasks)])
        if cr.gene_id != "gene_harmful":
            return fails
        fails += int(not cr.task_success)
    return float("nan")


def ban_latency_predictive(n_max: int = 20) -> float:
    """The current rule with the x1.15 predictive factor, simulated on the memory graph directly."""
    mg = MemoryGraph(clock=Clock())
    for n in range(1, n_max):
        mg.record_outcome(signals=["a", "b"], gene_id="g", status="failed", score=0.2, predictive={"trend": -0.06})
        if "g" in mg.advice(["a", "b"]).banned_gene_ids:
            return n
    return float("nan")


def selection(job) -> dict:
    """Selection accuracy vs the oracle-best gene: part (b) learning arms, part (c) exploring when stuck."""
    seed, part, arm, args = job
    if live(args):
        return selection_live(seed, arm, args)
    rng = random.Random(f"x4-{seed}")
    dom = gw(seed)
    genes, oracle = library(dom, rng, stagnation=part == "c")
    cfg = Config(mode="safe", seed=seed, propose=False, distill=False, **(ARMS_B if part == "b" else ARMS_C)[arm])
    ag = make_agent(dom, genes, 0.0 if part == "b" else -1.0, cfg, seed)
    T = 120 if args.quick else 360
    hits = oracle_hits(ag, dom.tasks.split("evolve"), oracle, rng, T)
    found = {}
    for task_hit, r in zip(hits, ag.results):
        found[r.family] = found.get(r.family, False) or task_hit
    return {"seed": seed, "part": part, "arm": arm, "acc_last_quarter": float(np.mean(hits[-T // 4:])),
            "acc_first_quarter": float(np.mean(hits[: T // 4])),
            "discovered_share": float(np.mean(list(found.values()))),
            "solve_rate": float(np.mean([r.task_success for r in ag.results[-T // 4:]]))}


def selection_live(seed: int, arm: str, args) -> dict:
    from rsi.domains.katas import KatasDomain, seed_harness
    dom = KatasDomain()
    llm = cached_llm(args.llm)
    genes, oracle = katas_library()
    ag = AgentNode("x4live", dom, seed_harness(), llm_task=llm, store=store_with("x4live", genes),
                   config=Config(mode="safe", seed=seed, propose=False, distill=False, **ARMS_B[arm]))
    hits = oracle_hits(ag, dom.tasks.split("evolve"), oracle, random.Random(seed), 12)
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
    rows = pmap(selection, [(s, p, a, args) for p, arms in parts.items() for a in arms for s in range(args.seeds)],
                args.workers)
    out["raw"] = rows
    summ = out["summary"] = {
        f"{p}_{a}": {k: summarize([r[k] for r in rows if r["part"] == p and r["arm"] == a])
                     for k in ("acc_first_quarter", "acc_last_quarter", "discovered_share", "solve_rate")}
        for p, arms in parts.items() for a in arms}

    def col(p, a, k="acc_last_quarter"):
        return [r[k] for r in rows if r["part"] == p and r["arm"] == a]

    def gain_c(a, k="acc_last_quarter"):
        return paired(col("c", "pattern_static", k), col("c", a, k))

    v = {"learning_raises_accuracy_full_vs_none": paired(col("b", "none"), col("b", "full"))}
    v["learning_raises_accuracy"] = v["learning_raises_accuracy_full_vs_none"]["lo"] > 0
    if not live(args):
        bl = out["ban_latency"]
        v.update({"ban_latency_spec_eq_4": bl["spec"]["mean"] == 4,
                  "ban_latency_current_eq_5": bl["current"]["mean"] == 5,
                  "ban_latency_current_predictive_eq_6": bl["current_predictive"]["mean"] == 6,
                  "ban_latency_failed_capsule_mean": bl["failed_capsule"]["mean"],
                  "ban_latency_engine_all_rules_mean": bl["engine_all_rules"]["mean"],
                  "memory_graph_alone_raises_accuracy": paired(col("b", "none"), col("b", "memory_only"))["lo"] > 0,
                  "accuracy_rises_toward_oracle": summ["b_full"]["acc_last_quarter"]["mean"] >
                  summ["b_full"]["acc_first_quarter"]["mean"],
                  "full_engine_escapes_without_drift": summ["c_full_static"]["discovered_share"]["mean"],
                  "pattern_plateau_discovers_more": gain_c("pattern_plateau", "discovered_share")["lo"] > 0,
                  "pattern_drift_discovers_more": gain_c("pattern_drift", "discovered_share")["lo"] > 0,
                  "pattern_drift_paired_acc": gain_c("pattern_drift"),
                  "pattern_drift_paired_solve": gain_c("pattern_drift", "solve_rate")})
    out["verdict"] = v
    save("x4_local_loop", out, args.out)
    for k, s in out.get("ban_latency", {}).items():
        print(f"  ban latency {k:20s} {fmt(s, 2)}")
    for k, s in summ.items():
        print(f"  {k:16s} first {fmt(s['acc_first_quarter'])} last {fmt(s['acc_last_quarter'])} "
              f"found {fmt(s['discovered_share'])} solve {fmt(s['solve_rate'])}")
    print("verdict:", v)


if __name__ == "__main__":
    main()
