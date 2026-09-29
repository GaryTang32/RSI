"""S6b - The capability floor, as the blog states it, on fresh seeds; composition losses; loss attribution
(claims audit L4, R7, C1; retry round 2).

Claim [blog, "Capability floors constrain efficiency gains"]: "A cheaper candidate fails if it saves by stopping
early, skipping necessary verification, or removing evidence required to finish the task. ... The gate applies
to one mechanism at a time, so the small losses it permits can accumulate once mechanisms combine: the
assembled harness retains roughly 94% of Pi's average score. What the gate rules out is savings that come from
getting less done." [ye-blog] "an agent can spend fewer tokens by doing less. So capability works as a gate."

S6 (seeds 0-4) scored the floor with a stricter criterion than the blog (composed stack within 2 points). This
script re-tests on FRESH seeds (default 10-19) with preregistered criteria taken from the blog's wording
(claims-audit-solpi.md, "Retry round 2: preregistration", P-L4 / P-R7) and reports the old S6 criterion too:

* per seed and arm (efficiency-only objective vs the dual gate, no firewall - as S6), the protocol's survivors;
* do-less candidates (P14 no-verify, P20 turn cap) admitted;
* every admitted survivor evaluated STANDALONE (base + that one frozen variant) on the ``test`` split: fresh
  tasks of the training families that the gate never saw (does an admitted candidate really lose nothing?);
* the composed stack on ``evolve`` (the gate's screen), ``test`` and ``ood`` (held-out families);
* leave-one-out attribution on ``evolve`` and ``test``: the composed stack minus each survivor (diagnostic).

    python experiments/metaharness-solpi/s6b_floor_fresh_seeds.py [--seeds 10] [--seed-offset 10] [--workers 2]
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _agentworld import agg, row, trials_of  # noqa: E402
from _common import fmt, fresh_dir, parse_args, pool_map, save, summarize, table  # noqa: E402

from rsi.domains.agentworld import MockAgentLLM, make_domain  # noqa: E402
from rsi.solpi import AGENTWORLD_IDEAS, Config, GateSpec, compose, run  # noqa: E402
from rsi.solpi.registry import harness_config  # noqa: E402

ARMS = {"efficiency_only": GateSpec(mode="efficiency_only"), "dual_gate": GateSpec(mode="aggregate")}
IDEAS = {i.id: i for i in AGENTWORLD_IDEAS}
TOL = 0.02
ARGS = None


def frozen_variants(dom, r) -> dict:
    """idea id -> the base harness plus the variant its lineage froze (as S5's no-firewall ablation)."""
    lin = {l["idea"]: l for l in r["lineages"]}
    change = {n.split(":", 1)[0]: n.split(":", 1)[1] for n in r["frozen"]}
    out = {}
    for iid in r["frozen_ideas"]:
        it = [x for x in lin[iid]["iterations"] if x.get("outcome") == "frozen" and x.get("change") == change.get(iid)][-1]
        idea = IDEAS[iid]
        params = idea.grid[it["variant"]] if idea.grid else {}
        out[iid] = (dom.harness(idea.mechanism, **{idea.mechanism: params}), params)
    return out


def score(dom, llm, art, split) -> dict:
    return agg([row(t) for t in trials_of(dom, llm, art, splits=(split,))])


def job(spec):
    arm, seed = spec
    t0 = time.time()
    dom = make_domain(seed=seed, n_train=8, n_final=8, n_accept=2, n_test=8)
    res = run(dom, dom.seed_artifact(), llm_task=MockAgentLLM("A"),
              config=Config(gate=ARMS[arm], firewall=False, n_lineages=10),
              out_dir=fresh_dir("s6b", f"{arm}_s{seed}"))
    r = res.meta["rounds"][0]
    llm = MockAgentLLM("A")
    base = dom.seed_artifact()
    surv = list(r["survivor_ideas"])
    variants = frozen_variants(dom, r)
    out = {"arm": arm, "seed": seed, "survivors": surv,
           "variants": {k: v[1] for k, v in variants.items()},
           "do_less_admitted": sum(IDEAS[i].kind == "do_less" for i in surv),
           "admitted_kinds": {i: IDEAS[i].kind for i in surv}}
    b = {sp: score(dom, llm, base, sp) for sp in ("evolve", "test", "ood")}
    c = {sp: score(dom, llm, res.best, sp) for sp in ("evolve", "test", "ood")}
    for sp in ("evolve", "test", "ood"):
        out[f"{sp}_base_score"] = b[sp]["score"]
        out[f"{sp}_stack_score"] = c[sp]["score"]
        out[f"{sp}_stack_change"] = c[sp]["score"] - b[sp]["score"]
        out[f"{sp}_stack_token_saving"] = 1 - c[sp]["tokens"] / b[sp]["tokens"]
        out[f"{sp}_stack_cost_saving"] = 1 - c[sp]["cost"] / b[sp]["cost"]
    # R7: the composed harness is the union of the survivors' extensions
    comp_ext = set(harness_config(res.best.files).get("extensions", {}))
    out["composed_extensions"] = sorted(comp_ext)
    out["composed_is_union"] = comp_ext == {IDEAS[i].mechanism for i in surv}
    # standalone survivors on evolve (the gate's screen) and on the unseen test split
    out["standalone"] = {}
    for iid in surv:
        art = variants[iid][0]
        out["standalone"][iid] = {sp: score(dom, llm, art, sp)["score"] - b[sp]["score"] for sp in ("evolve", "test")}
    # leave-one-out attribution of the composed stack
    out["loo"] = {}
    for iid in surv:
        rest = [variants[j][0] for j in surv if j != iid]
        art = compose(base, rest)[0] if rest else base
        out["loo"][iid] = {sp: score(dom, llm, art, sp)["score"] - b[sp]["score"] for sp in ("evolve", "test")}
    out["seconds"] = time.time() - t0
    return out


def extra(ap):
    ap.add_argument("--seed-offset", type=int, default=10)


def main():
    global ARGS
    ARGS = parse_args(__doc__.splitlines()[0], default_seeds=10, extra=extra)
    seeds = list(range(ARGS.seed_offset, ARGS.seed_offset + ARGS.seeds))
    rows = pool_map(job, [(a, s) for s in seeds for a in ARMS], ARGS.workers)
    by = {a: sorted([r for r in rows if r["arm"] == a], key=lambda r: r["seed"]) for a in ARMS}
    keys = ("do_less_admitted", "evolve_stack_change", "test_stack_change", "ood_stack_change",
            "evolve_stack_token_saving", "evolve_stack_cost_saving")
    summ = {a: {k: summarize([r[k] for r in by[a]]) for k in keys} for a in ARMS}
    dg, eo = by["dual_gate"], by["efficiency_only"]
    n = len(seeds)
    # ---- preregistered P-L4
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
    # ---- preregistered P-R7
    union = sum(r["composed_is_union"] for r in dg)
    accum = [r for r in dg if r["survivors"] and
             r["evolve_stack_change"] < min(v["evolve"] for v in r["standalone"].values()) - 0.005]
    retention = [r["evolve_stack_score"] / r["evolve_base_score"] for r in dg]
    r7_pass = union == n and len(accum) >= 3
    # ---- diagnostic: which survivor's removal restores the most success (dual gate)
    attrib = {}
    for r in dg:
        for i, v in r["loo"].items():
            attrib.setdefault(i, []).append({sp: v[sp] - r[f"{sp}_stack_change"] for sp in ("evolve", "test")})
    attrib_s = {i: {sp: summarize([x[sp] for x in xs]) for sp in ("evolve", "test")} | {"in_stack_seeds": len(xs)}
                for i, xs in attrib.items()}
    verdict = (f"P-L4 {'PASS' if l4_pass else 'FAIL'} ({json.dumps({k: v for k, v in p_l4.items() if 'failures' not in k}, default=str)}); "
               f"old S6 criterion (composed evolve change >= -2 pts): {'met' if old_s6 else 'NOT met'} "
               f"({fmt(summ['dual_gate']['evolve_stack_change'])}); "
               f"P-R7 {'PASS' if r7_pass else 'FAIL'} (union {union}/{n}; accumulation in {len(accum)}/{n} seeds; "
               f"composed retention {fmt(summarize(retention))})")
    print(table([[a] + [fmt(summ[a][k], 3) for k in keys] for a in ARMS], ["arm"] + list(keys)))
    print(json.dumps(attrib_s, indent=0, default=str)[:3000])
    print("verdict:", verdict)
    save("s6b_floor_fresh_seeds", {
        "claim": "L4/R7/C1: the floor rejects per-mechanism savings from doing less; one-at-a-time gating lets small "
                 "losses accumulate in composition [blog Capability floors; ye-blog]",
        "config": {"seeds": seeds, "tolerance": TOL, "prereg": "claims-audit-solpi.md P-L4, P-R7, D-LOO"},
        "per_seed": rows, "summary": summ, "p_l4": p_l4, "p_l4_pass": l4_pass, "old_s6_criterion_met": old_s6,
        "p_r7": {"union": f"{union}/{n}", "accumulation_seeds": [r["seed"] for r in accum],
                 "retention": summarize(retention)}, "p_r7_pass": r7_pass,
        "loo_attribution": attrib_s, "verdict": verdict}, ARGS.out)


if __name__ == "__main__":
    main()
