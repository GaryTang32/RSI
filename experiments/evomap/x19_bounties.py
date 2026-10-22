"""X19 (retry round 2, P5): does the top decile still earn from publication when bounties exist?
Run: python experiments/evomap/x19_bounties.py [--seeds 10] [--workers 2] [--quick] [--claimants honest,inflator,farmer]
"""
from __future__ import annotations

import math
from collections import defaultdict

from _common import (DEFAULT_MIX, agent_config, make_hub, make_world, paired, parse_args, pmap, population_specs,
                     print_groups, save, simulator, summarize)

from rsi.evomap.economy import BountyBoard, BountyDriver

RATES = (1, 4, 16)
FARM_RATE = 8


def one(job) -> dict:
    seed, B, args = job
    world = make_world(args, seed)
    n, epochs = (24, 10) if args.quick else (40, 30)
    specs = population_specs(n, DEFAULT_MIX, seed, classes=world.domain.tasks.families("evolve"), farm_rate=FARM_RATE)
    hub = make_hub("naive", world)
    drv = BountyDriver(BountyBoard(hub.credits), per_epoch=B, claimant_kinds=tuple(args.claimants.split(",")))
    simulator(world, hub, specs, agent_config("naive", seed), seed, bounties=drv).run(epochs)
    names = [sp.name for sp in specs]
    earned = hub.credits.earned_all(names)
    top = sorted(names, key=lambda a: -earned[a])[: max(1, math.ceil(0.1 * len(names)))]
    history = hub.credits.history
    by = defaultdict(float)             # the top decile's earned credits by reason
    for _, ag, amt, reason in history:
        if ag in top and amt > 0:
            by[reason] += amt
    tot = sum(by.values()) or 1.0
    return {"seed": seed, "B": B, "credit_top10_share": hub.credits.top_share(0.10, names),
            "top10_share_promotion": by["promotion"] / tot, "top10_share_bounty": by["bounty"] / tot,
            "top10_share_fetch": by["fetch"] / tot,
            "top10_are_farmers": sum(a.endswith("farmer") for a in top) / len(top),
            "bounties_completed_by_farmers": sum(1 for e in drv.log if e["agent"].endswith("farmer")),
            "bounties_posted": len(drv.board.tasks),
            "bounties_completed": sum(t.status == "completed" for t in drv.board.tasks.values()),
            "bounty_credits_all_agents": sum(a for _, ag, a, r in history if r == "bounty" and ag in names),
            "promotion_credits_all_agents": sum(a for _, ag, a, r in history if r == "promotion" and ag in names)}


def main():
    args = parse_args(__doc__.split("\n")[0], default_seeds=10,
                      extra=lambda ap: ap.add_argument("--claimants", default="honest,inflator"))
    rows = pmap(one, [(s, B, args) for B in RATES for s in range(args.seeds)], args.workers)
    keys = [k for k in rows[0] if k not in ("seed", "B")]
    summ = {B: {k: summarize([r[k] for r in rows if r["B"] == B]) for k in keys} for B in RATES}
    v = {}
    for B in RATES:
        rs = [r for r in rows if r["B"] == B]
        d = paired([r["top10_share_bounty"] for r in rs], [r["top10_share_promotion"] for r in rs])
        v[f"B{B}"] = {"promotion_minus_bounty_share": d, "top10_share": summ[B]["credit_top10_share"],
                      "pass": bool(d["lo"] > 0 and summ[B]["credit_top10_share"]["mean"] > 0.5)}
    v["rather_than_bounties_holds_for_all_B"] = all(v[f"B{B}"]["pass"] for B in RATES)
    v["promotion_gt_bounty_for_all_B"] = all(v[f"B{B}"]["promotion_minus_bounty_share"]["lo"] > 0 for B in RATES)
    v["top10_gt_half_for_all_B"] = all(v[f"B{B}"]["top10_share"]["mean"] > 0.5 for B in RATES)
    out = {"config": {"seeds": args.seeds, "rates": RATES, "claimant_kinds": args.claimants.split(","),
                      "amount": 100, "farm_rate": FARM_RATE,
                      "population": "24x10 (quick)" if args.quick else "40 agents x 30 epochs",
                      "preregistration": "docs/methods/evomap/claims-audit.md#retry-round-2-preregistration"},
           "raw": rows, "summary": {str(B): s for B, s in summ.items()}, "verdict": v}
    save("x19b_bounties_farmers" if "farmer" in args.claimants else "x19_bounties", out, args.out)
    print_groups({f"B = {B}": s for B, s in summ.items()}, keys, 32)
    print("verdict:", v)


if __name__ == "__main__":
    main()
