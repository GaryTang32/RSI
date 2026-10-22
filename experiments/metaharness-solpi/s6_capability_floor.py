"""S6 - The capability floor blocks "saving by doing less".

Usage: python experiments/metaharness-solpi/s6_capability_floor.py [--llm sim|claude:haiku] [--seeds N] [--quick]
"""
from __future__ import annotations

from functools import partial

from _agentworld import kind_counts, metrics
from _common import fmt, fresh_dir, live_llm, paired, parse_args, pool_map, save, summarize, table
from rsi.domains.agentworld import MockAgentLLM, make_domain
from rsi.solpi import Config, GateSpec, run

ARMS = {"efficiency_only": GateSpec(mode="efficiency_only"), "dual_gate": GateSpec(mode="aggregate"),
        "dual_gate+composition_check": GateSpec(mode="aggregate")}
KEYS = ("do_less_admitted", "general_admitted", "evolve_success_change", "evolve_token_saving", "evolve_cost_saving",
        "ood_success_change", "ood_token_saving", "ood_cost_saving")


def job(args, spec):
    arm, seed = spec
    q = args.quick or args.live
    dom = make_domain(seed=seed, n_train=3 if q else 8, n_final=3 if q else 8, n_accept=2, n_test=0)
    res = run(dom, dom.seed_artifact(), llm_task=MockAgentLLM("A"),
              llm_propose=live_llm(args.llm) if args.live else None,
              config=Config(gate=ARMS[arm], firewall=False, n_lineages=3 if args.live else 10,
                            validate_composition=arm.endswith("composition_check")),
              out_dir=fresh_dir("s6", f"{arm}_s{seed}_{args.llm.replace(':', '_')}"))
    r = res.meta["rounds"][0]
    kinds = kind_counts(r["survivor_ideas"])
    out = {"arm": arm, "seed": seed, "survivors": r["survivor_ideas"],
           "dropped_by_composition_check": r.get("dropped_by_composition_check", []),
           "do_less_admitted": kinds["do_less"], "general_admitted": kinds["general"]}
    llm = MockAgentLLM("A")
    for split in ("evolve", "ood"):
        b = metrics(dom, llm, dom.seed_artifact(), splits=(split,))
        c = metrics(dom, llm, res.best, splits=(split,))
        out[f"{split}_success_base"], out[f"{split}_success"] = b["score"], c["score"]
        out[f"{split}_success_change"] = c["score"] - b["score"]
        out[f"{split}_token_saving"] = 1 - c["tokens"] / b["tokens"]
        out[f"{split}_cost_saving"] = 1 - c["cost"] / b["cost"]
    return out


def main():
    args = parse_args(__doc__.splitlines()[0], default_seeds=5)
    rows = pool_map(partial(job, args), [(a, s) for s in range(args.seeds) for a in ARMS], args.workers)
    by = {a: sorted([r for r in rows if r["arm"] == a], key=lambda r: r["seed"]) for a in ARMS}
    summ = {a: {k: summarize([r[k] for r in by[a]]) for k in KEYS} for a in ARMS}
    cmp = {k: paired([r[k] for r in by["dual_gate"]], [r[k] for r in by["efficiency_only"]])
           for k in ("evolve_success_change", "ood_success_change")}
    eo, dg, cc = summ["efficiency_only"], summ["dual_gate"], summ["dual_gate+composition_check"]
    naive_less = eo["do_less_admitted"]["mean"] > 0 and eo["evolve_success_change"]["mean"] < -0.02
    floor_ok = dg["do_less_admitted"]["mean"] == 0 and dg["evolve_success_change"]["mean"] >= -0.02
    comp_ok = cc["evolve_success_change"]["mean"] >= -0.02
    verdict = ("REPRODUCED" if naive_less and floor_ok else "PARTIAL" if floor_ok else "NOT REPRODUCED") + \
        f": efficiency-only admits {eo['do_less_admitted']['mean']:.1f} do-less shortcuts " \
        f"(success change {fmt(eo['evolve_success_change'])} train, " \
        f"{fmt(eo['ood_success_change'])} held-out); the dual gate admits " \
        f"{dg['do_less_admitted']['mean']:.1f} (success change " \
        f"{fmt(dg['evolve_success_change'])} train, {fmt(dg['ood_success_change'])} " \
        f"held-out) while keeping {dg['general_admitted']['mean']:.1f} general mechanisms. With the " \
        f"(inferred) composition re-check: {cc['do_less_admitted']['mean']:.1f} do-less kept, success change " \
        f"{fmt(cc['evolve_success_change'])} train / {fmt(cc['ood_success_change'])} held-out, tokens " \
        f"-{100 * cc['evolve_token_saving']['mean']:.0f}% ({'floor holds for the stack' if comp_ok else 'still below'})"
    print(table([[a] + [fmt(summ[a][k], 3) for k in KEYS] for a in ARMS], ["arm"] + list(KEYS)))
    print("verdict:", verdict)
    save("s6_capability_floor" + ("_live" if args.live else ""), {
        "claim": "The capability floor blocks saving by doing less [blog; ye-blog]",
        "config": {"llm": args.llm, "seeds": args.seeds}, "per_seed": rows, "summary": summ, "paired": cmp,
        "verdict": verdict}, args.out)


if __name__ == "__main__":
    main()
