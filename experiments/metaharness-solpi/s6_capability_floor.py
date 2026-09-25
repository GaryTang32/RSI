"""S6 - The capability floor blocks "saving by doing less".

Claim [blog: Capability floors; ye-blog "Choosing an Objective"]: an efficiency objective alone rewards doing
less (skipping verification, stopping early); a predeclared capability floor rejects such candidates while
keeping real efficiency mechanisms.

Protocols on the same idea pool and training families (no firewall, to isolate the training gate):
efficiency-only objective vs the dual gate (2% capability tolerance). Metrics: do-less shortcuts admitted,
composed stack's success and token/cost savings on the training screen and on the held-out families.

    python experiments/metaharness-solpi/s6_capability_floor.py [--llm sim|claude:haiku] [--seeds N] [--quick]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _agentworld import agg, row, trials_of  # noqa: E402
from _common import fmt, fresh_dir, live_llm, paired, parse_args, pool_map, save, summarize, table  # noqa: E402

from rsi.domains.agentworld import MockAgentLLM, make_domain  # noqa: E402
from rsi.solpi import AGENTWORLD_IDEAS, Config, GateSpec, run  # noqa: E402

ARMS = {"efficiency_only": GateSpec(mode="efficiency_only"), "dual_gate": GateSpec(mode="aggregate"),
        "dual_gate+composition_check": GateSpec(mode="aggregate")}
KIND = {i.id: i.kind for i in AGENTWORLD_IDEAS}
ARGS = None


def job(spec):
    arm, seed = spec
    q = ARGS.quick or ARGS.live
    dom = make_domain(seed=seed, n_train=3 if q else 8, n_final=3 if q else 8, n_accept=2, n_test=0)
    res = run(dom, dom.seed_artifact(), llm_task=MockAgentLLM("A"),
              llm_propose=live_llm(ARGS.llm) if ARGS.live else None,
              config=Config(gate=ARMS[arm], firewall=False, n_lineages=3 if ARGS.live else 10,
                            validate_composition=arm.endswith("composition_check")),
              out_dir=fresh_dir("s6", f"{arm}_s{seed}_{ARGS.llm.replace(':', '_')}"))
    r = res.meta["rounds"][0]
    out = {"arm": arm, "seed": seed, "survivors": r["survivor_ideas"],
           "dropped_by_composition_check": r.get("dropped_by_composition_check", []),
           "do_less_admitted": sum(KIND.get(i) == "do_less" for i in r["survivor_ideas"]),
           "general_admitted": sum(KIND.get(i) == "general" for i in r["survivor_ideas"])}
    llm = MockAgentLLM("A")
    for split in ("evolve", "ood"):
        b = agg([row(t) for t in trials_of(dom, llm, dom.seed_artifact(), splits=(split,))])
        c = agg([row(t) for t in trials_of(dom, llm, res.best, splits=(split,))])
        out[f"{split}_success_base"], out[f"{split}_success"] = b["score"], c["score"]
        out[f"{split}_success_change"] = c["score"] - b["score"]
        out[f"{split}_token_saving"] = 1 - c["tokens"] / b["tokens"]
        out[f"{split}_cost_saving"] = 1 - c["cost"] / b["cost"]
    return out


def main():
    global ARGS
    ARGS = parse_args(__doc__.splitlines()[0], default_seeds=5)
    rows = pool_map(job, [(a, s) for s in range(ARGS.seeds) for a in ARMS], ARGS.workers)
    by = {a: sorted([r for r in rows if r["arm"] == a], key=lambda r: r["seed"]) for a in ARMS}
    keys = ("do_less_admitted", "general_admitted", "evolve_success_change", "evolve_token_saving",
            "evolve_cost_saving", "ood_success_change", "ood_token_saving", "ood_cost_saving")
    summ = {a: {k: summarize([r[k] for r in by[a]]) for k in keys} for a in ARMS}
    cmp = {k: paired([r[k] for r in by["dual_gate"]], [r[k] for r in by["efficiency_only"]])
           for k in ("evolve_success_change", "ood_success_change")}
    naive_less = summ["efficiency_only"]["do_less_admitted"]["mean"] > 0 and \
        summ["efficiency_only"]["evolve_success_change"]["mean"] < -0.02
    floor_ok = summ["dual_gate"]["do_less_admitted"]["mean"] == 0 and \
        summ["dual_gate"]["evolve_success_change"]["mean"] >= -0.02
    cc = summ["dual_gate+composition_check"]
    comp_ok = cc["evolve_success_change"]["mean"] >= -0.02
    verdict = ("REPRODUCED" if naive_less and floor_ok else "PARTIAL" if floor_ok else "NOT REPRODUCED") + \
        f": efficiency-only admits {summ['efficiency_only']['do_less_admitted']['mean']:.1f} do-less shortcuts " \
        f"(success change {fmt(summ['efficiency_only']['evolve_success_change'])} train, " \
        f"{fmt(summ['efficiency_only']['ood_success_change'])} held-out); the dual gate admits " \
        f"{summ['dual_gate']['do_less_admitted']['mean']:.1f} (success change " \
        f"{fmt(summ['dual_gate']['evolve_success_change'])} train, {fmt(summ['dual_gate']['ood_success_change'])} " \
        f"held-out) while keeping {summ['dual_gate']['general_admitted']['mean']:.1f} general mechanisms. With the " \
        f"(inferred) composition re-check: {cc['do_less_admitted']['mean']:.1f} do-less kept, success change " \
        f"{fmt(cc['evolve_success_change'])} train / {fmt(cc['ood_success_change'])} held-out, tokens " \
        f"-{100 * cc['evolve_token_saving']['mean']:.0f}% ({'floor holds for the stack' if comp_ok else 'still below'})"
    print(table([[a] + [fmt(summ[a][k], 3) for k in keys] for a in ARMS], ["arm"] + list(keys)))
    print("verdict:", verdict)
    save("s6_capability_floor" + ("_live" if ARGS.live else ""), {
        "claim": "The capability floor blocks saving by doing less [blog; ye-blog]",
        "config": {"llm": ARGS.llm, "seeds": ARGS.seeds}, "per_seed": rows, "summary": summ, "paired": cmp,
        "verdict": verdict}, ARGS.out)


if __name__ == "__main__":
    main()
