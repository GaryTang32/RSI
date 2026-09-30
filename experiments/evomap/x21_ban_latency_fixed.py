"""X21 (retry round 2, P6): ban latency of the spec memory rule when the signal set does not change.

X4(a) measured 5.0 failures before the spec rule (n >= 2, Laplace value (s+1)/(n+2) * age weight < 0.18) excluded a
harmful gene, where the arithmetic gives 4 (1/6 = 0.167 < 0.18 after four failures, 1/5 = 0.2 after three). X4
cycles through the class's tasks, whose signal sets differ, so an edge can miss the Jaccard >= 0.34 key match.
This run repeats X4(a)'s spec arm exactly, except that every cycle uses the SAME task (identical signals).

Preregistered pass: 12 / 12 seeds give exactly 4.

Run: python experiments/evomap/x21_ban_latency_fixed.py [--seeds 12]
"""
from __future__ import annotations

from _common import parse_args, save, summarize
from x4_local_loop import gene_for, gw, make_agent

from rsi.evomap import Config


def ban_latency_fixed(seed: int) -> float:
    dom = gw(seed)
    w = dom.world
    cl = w.classes[seed % len(w.classes)]
    g = gene_for(w, cl, "harmful", w.keywords[cl], "gene_harmful")
    cfg = Config(mode="safe", seed=seed, propose=False, distill=False, outcome_timing="immediate",
                 memory_mode="spec", use_memory=True, failed_capsule_bans=False, failed_capsule_rule="absolute",
                 epigenetic_suppression=False, plateau_override=False)
    ag = make_agent(dom, [g], -3.0, cfg, seed)
    ag.deduper.suppress_min = 10 ** 6
    task = [t for t in dom.tasks.split("evolve") if t.family == cl][0]
    fails = 0
    for _ in range(30):
        cr = ag.cycle(task)
        if cr.gene_id != "gene_harmful":
            return fails
        fails += int(not cr.task_success)
    return float("nan")


def main():
    args = parse_args(__doc__.split("\n")[0], default_seeds=12)
    lat = [ban_latency_fixed(s) for s in range(args.seeds)]
    out = {"config": {"seeds": args.seeds, "rule": "spec", "task": "fixed (identical signals every cycle)",
                      "preregistration": "docs/methods/evomap/claims-audit.md#retry-round-2-preregistration"},
           "raw": lat, "summary": summarize(lat),
           "verdict": {"all_equal_4": all(x == 4 for x in lat), "n_equal_4": sum(x == 4 for x in lat)}}
    save("x21_ban_latency_fixed", out, args.out)
    print(lat, out["verdict"])


if __name__ == "__main__":
    main()
