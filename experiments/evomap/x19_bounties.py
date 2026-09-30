"""X19 (retry round 2, P5): does the top decile still earn from publication when bounties exist?

Claim [snip:BE][abs:BE] (B3): wealth is concentrated among about 10 % of agents, who earn "by repeatedly publishing
assets and benefiting from the platform's high promotion rate, rather than through community reuse or bounty
completion". X7 had no bounties, so the "rather than bounties" part was untested.

Setup (preregistered): X7's base population (40 agents x 30 epochs, 15 % farmers at 8 assets per epoch, naive hub)
plus an external bounty poster (``rsi.evomap.economy.BountyDriver``): B in {1, 4, 16} bounties per epoch of 100
credits each (``BOUNTY_REFERENCE`` in Evolver's ``taskReceiver.js``), each built from a random task's public signals
and family, expiring after 3 epochs (refunded). Honest and inflator agents rank open bounties with the ported
``rankTasks`` (balanced strategy) over their own event history, claim the head, work on a task of that family, and
complete the bounty after a successful solidify with the new capsule's asset_id (self-reported, as ``solidify.js``).

Metrics per seed: top-10 % earned-credit share (agents only; the poster is external), and within the top decile the
share of earned credits from promotion, fetches and bounties. Test: for every B, paired CI lower bound of
(promotion share - bounty share) > 0 and top-10 % share > 0.5.

P5b (review response, preregistered): ``--claimants honest,inflator,farmer`` lets farmers pursue bounties too. A
farmer runs no solve cycle, so once per epoch, after publishing, it claims the head of its ``rankTasks`` ranking
and self-reports completion with the capsule asset_id of a bundle it has just published (nothing checks it, as for
every completion). In P5 the farmers could not claim, so the top decile's 0 % bounty share was structural.
Output: results/evomap/x19b_bounties_farmers.json (P5's x19_bounties.json is kept).

Run: python experiments/evomap/x19_bounties.py [--seeds 10] [--workers 2] [--quick] [--claimants ...]
"""
from __future__ import annotations

import math
from collections import defaultdict

from _common import (DEFAULT_MIX, agent_config, fmt, make_hub, make_world, paired, parse_args, pmap,
                     population_specs, save, summarize)

from rsi.evomap import PopulationSimulator
from rsi.evomap.economy import BountyBoard, BountyDriver

RATES = (1, 4, 16)
FARM_RATE = 8


def one(job):
    seed, B, args = job
    world = make_world(args, seed)
    n, epochs = (24, 10) if args.quick else (40, 30)
    specs = population_specs(n, DEFAULT_MIX, seed, classes=world.domain.tasks.families("evolve"), farm_rate=FARM_RATE)
    hub = make_hub("naive", world)
    drv = BountyDriver(BountyBoard(hub.credits), per_epoch=B, claimant_kinds=tuple(args.claimants.split(",")))
    sim = PopulationSimulator(world.domain, world.harness, hub, specs, config=agent_config("naive", seed),
                              model_factory=world.model_factory, proposer_factory=world.proposer_factory,
                              forge=world.forge, truth=world.truth, seed=seed, bounties=drv)
    sim.run(epochs)
    names = [sp.name for sp in specs]
    earned = hub.credits.earned_all(names)
    top = sorted(names, key=lambda a: -earned[a])[: max(1, math.ceil(0.1 * len(names)))]
    by = defaultdict(float)
    for _, ag, amt, reason in hub.credits.history:
        if ag in top and amt > 0:
            by[reason] += amt
    tot = sum(by.values()) or 1.0
    posted = len(drv.board.tasks)
    done = sum(t.status == "completed" for t in drv.board.tasks.values())
    return {"seed": seed, "B": B, "credit_top10_share": hub.credits.top_share(0.10, names),
            "top10_share_promotion": by["promotion"] / tot, "top10_share_bounty": by["bounty"] / tot,
            "top10_share_fetch": by["fetch"] / tot,
            "top10_are_farmers": sum(a.endswith("farmer") for a in top) / len(top),
            "bounties_completed_by_farmers": sum(1 for e in drv.log if e["agent"].endswith("farmer")),
            "bounties_posted": posted, "bounties_completed": done,
            "bounty_credits_all_agents": sum(a for _, ag, a, r in hub.credits.history if r == "bounty" and ag in names),
            "promotion_credits_all_agents": sum(a for _, ag, a, r in hub.credits.history
                                                if r == "promotion" and ag in names)}


def main():
    args = parse_args(__doc__.split("\n")[0], default_seeds=10,
                      extra=lambda ap: ap.add_argument("--claimants", default="honest,inflator"))
    jobs = [(s, B, args) for B in RATES for s in range(args.seeds)]
    rows = pmap(one, jobs, args.workers)
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
    for B in RATES:
        print(f"== B = {B}")
        for k in keys:
            print(f"  {k:32s} {fmt(summ[B][k])}")
    print("verdict:", v)


if __name__ == "__main__":
    main()
