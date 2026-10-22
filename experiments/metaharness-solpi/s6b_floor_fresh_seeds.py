"""S6b - The capability floor, as the blog states it, on fresh seeds; composition losses; loss attribution
(claims audit L4, R7, C1).

Usage: python experiments/metaharness-solpi/s6b_floor_fresh_seeds.py [--seeds 10] [--seed-offset 10] [--workers 2]
"""
from __future__ import annotations

import json
import time

from _agentworld import IDEAS, frozen_variants, kind_counts, metrics, parse_seeded
from _common import fmt, fresh_dir, pool_map, save, summarize, table
from rsi.domains.agentworld import MockAgentLLM, make_domain
from rsi.solpi import Config, GateSpec, compose, run
from rsi.solpi.registry import harness_config

ARMS = {"efficiency_only": GateSpec(mode="efficiency_only"), "dual_gate": GateSpec(mode="aggregate")}
TOL = 0.02
KEYS = ("do_less_admitted", "evolve_stack_change", "test_stack_change", "ood_stack_change",
        "evolve_stack_token_saving", "evolve_stack_cost_saving")


def stack_vs_base(b: dict, c: dict, token_saving: bool = True) -> dict:
    """Per split: base and composed-stack scores, the change, and the savings."""
    out = {}
    for sp in b:
        out[f"{sp}_base_score"] = b[sp]["score"]
        out[f"{sp}_stack_score"] = c[sp]["score"]
        out[f"{sp}_stack_change"] = c[sp]["score"] - b[sp]["score"]
        if token_saving:
            out[f"{sp}_stack_token_saving"] = 1 - c[sp]["tokens"] / b[sp]["tokens"]
        out[f"{sp}_stack_cost_saving"] = 1 - c[sp]["cost"] / b[sp]["cost"]
    return out


def score_change(dom, llm, art, b: dict) -> dict:
    """Score change vs base on ``evolve`` and ``test``."""
    return {sp: metrics(dom, llm, art, splits=(sp,))["score"] - b[sp]["score"] for sp in ("evolve", "test")}


def is_union(best, survivors: list) -> bool:
    return set(harness_config(best.files).get("extensions", {})) == {IDEAS[i].mechanism for i in survivors}


def job(spec):
    arm, seed = spec
    t0 = time.time()
    dom = make_domain(seed=seed, n_train=8, n_final=8, n_accept=2, n_test=8)
    res = run(dom, dom.seed_artifact(), llm_task=MockAgentLLM("A"),
              config=Config(gate=ARMS[arm], firewall=False, n_lineages=10), out_dir=fresh_dir("s6b", f"{arm}_s{seed}"))
    r = res.meta["rounds"][0]
    llm = MockAgentLLM("A")
    base = dom.seed_artifact()
    surv = list(r["survivor_ideas"])
    variants = frozen_variants(dom, r)
    out = {"arm": arm, "seed": seed, "survivors": surv, "variants": {k: v[1] for k, v in variants.items()},
           "do_less_admitted": kind_counts(surv)["do_less"], "admitted_kinds": {i: IDEAS[i].kind for i in surv}}
    b = {sp: metrics(dom, llm, base, splits=(sp,)) for sp in ("evolve", "test", "ood")}
    c = {sp: metrics(dom, llm, res.best, splits=(sp,)) for sp in ("evolve", "test", "ood")}
    out |= stack_vs_base(b, c)
    out["composed_extensions"] = sorted(harness_config(res.best.files).get("extensions", {}))
    out["composed_is_union"] = is_union(res.best, surv)
    out["standalone"] = {i: score_change(dom, llm, variants[i][0], b) for i in surv}
    # leave-one-out attribution of the composed stack
    out["loo"] = {}
    for iid in surv:
        rest = [variants[j][0] for j in surv if j != iid]
        out["loo"][iid] = score_change(dom, llm, compose(base, rest)[0] if rest else base, b)
    out["seconds"] = time.time() - t0
    return out


def main():
    args, seeds = parse_seeded(__doc__.splitlines()[0], default_seeds=10)
    rows = pool_map(job, [(a, s) for s in seeds for a in ARMS], args.workers)
    by = {a: sorted([r for r in rows if r["arm"] == a], key=lambda r: r["seed"]) for a in ARMS}
    summ = {a: {k: summarize([r[k] for r in by[a]]) for k in KEYS} for a in ARMS}
    dg, eo = by["dual_gate"], by["efficiency_only"]
    n = len(seeds)
    # P-L4 (preregistered)
    l4_1 = sum(r["do_less_admitted"] == 0 for r in dg)
    l4_2 = sum(r["do_less_admitted"] >= 1 for r in eo)
    l4_2_loss = summ["efficiency_only"]["evolve_stack_change"]["mean"]
    pairs = [(r["seed"], i, v["test"]) for r in dg for i, v in r["standalone"].items()]
    l4_3 = sum(v >= -TOL for _, _, v in pairs)
    p_l4 = {"L4-1 dual gate admits no do-less (seeds)": f"{l4_1}/{n}",
            "L4-2 efficiency-only admits >=1 do-less (seeds)": f"{l4_2}/{n}",
            "L4-2 efficiency-only composed evolve change": l4_2_loss,
            "L4-3 admitted survivors within 2 pts standalone on the unseen test split": f"{l4_3}/{len(pairs)}",
            "L4-3 failures": [(s, i, round(v, 4)) for s, i, v in pairs if v < -TOL]}
    l4_pass = l4_1 == n and l4_2 >= n - 1 and l4_2_loss < -TOL and l4_3 >= 0.95 * len(pairs)
    old_s6 = summ["dual_gate"]["evolve_stack_change"]["mean"] >= -TOL
    # P-R7 (preregistered)
    union = sum(r["composed_is_union"] for r in dg)
    accum = [r for r in dg if r["survivors"] and
             r["evolve_stack_change"] < min(v["evolve"] for v in r["standalone"].values()) - 0.005]
    retention = [r["evolve_stack_score"] / r["evolve_base_score"] for r in dg]
    r7_pass = union == n and len(accum) >= 3
    # diagnostic: which survivor's removal restores the most success (dual gate)
    attrib = {}
    for r in dg:
        for i, v in r["loo"].items():
            attrib.setdefault(i, []).append({sp: v[sp] - r[f"{sp}_stack_change"] for sp in ("evolve", "test")})
    attrib_s = {i: {sp: summarize([x[sp] for x in xs]) for sp in ("evolve", "test")} | {"in_stack_seeds": len(xs)}
                for i, xs in attrib.items()}
    shown = {k: v for k, v in p_l4.items() if "failures" not in k}
    verdict = (f"P-L4 {'PASS' if l4_pass else 'FAIL'} ({json.dumps(shown, default=str)}); "
               f"old S6 criterion (composed evolve change >= -2 pts): {'met' if old_s6 else 'NOT met'} "
               f"({fmt(summ['dual_gate']['evolve_stack_change'])}); "
               f"P-R7 {'PASS' if r7_pass else 'FAIL'} (union {union}/{n}; accumulation in {len(accum)}/{n} seeds; "
               f"composed retention {fmt(summarize(retention))})")
    print(table([[a] + [fmt(summ[a][k], 3) for k in KEYS] for a in ARMS], ["arm"] + list(KEYS)))
    print(json.dumps(attrib_s, indent=0, default=str)[:3000])
    print("verdict:", verdict)
    save("s6b_floor_fresh_seeds", {
        "claim": "L4/R7/C1: the floor rejects per-mechanism savings from doing less; one-at-a-time gating lets small "
                 "losses accumulate in composition [blog Capability floors; ye-blog]",
        "config": {"seeds": seeds, "tolerance": TOL, "prereg": "claims-audit-solpi.md P-L4, P-R7, D-LOO"},
        "per_seed": rows, "summary": summ, "p_l4": p_l4, "p_l4_pass": l4_pass, "old_s6_criterion_met": old_s6,
        "p_r7": {"union": f"{union}/{n}", "accumulation_seeds": [r["seed"] for r in accum],
                 "retention": summarize(retention)}, "p_r7_pass": r7_pass,
        "loo_attribution": attrib_s, "verdict": verdict}, args.out)


if __name__ == "__main__":
    main()
