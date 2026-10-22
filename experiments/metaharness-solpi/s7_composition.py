"""S7 - Independently found mechanisms compose; small losses may accumulate; activation depends on the model.

Usage: python experiments/metaharness-solpi/s7_composition.py [--llm sim|claude:haiku] [--seeds N] [--quick]
"""
from __future__ import annotations

from functools import partial

import numpy as np

from _agentworld import agg, backend, backends, domain, rel, trial_rows
from _common import fmt, parse_args, pool_map, save, summarize, table

MECHS = {"AF": "action_fusion", "OP": "observation_pack", "EPR": "evidence_preserving_reducer",
         "OCC": "online_context_compact"}
FIRE = {"action_fusion": "fused", "observation_pack": "packed", "evidence_preserving_reducer": "applied",
        "online_context_compact": "compactions"}
NAMES = ["base"] + [f"+{k}" for k in MECHS] + ["full"]


def configs(dom) -> dict:
    return {"base": dom.seed_artifact(), **{f"+{k}": dom.harness(m) for k, m in MECHS.items()},
            "full": dom.harness(*MECHS.values())}


def triggers(rs: list[dict]) -> dict:
    """Per mechanism: share of tasks where it fired and mean fires per triggered task."""
    out = {}
    for m, key in FIRE.items():
        fires = [r["triggers"][m].get(key, 0) for r in rs if m in r["triggers"]]
        if fires:
            out[m] = {"task_rate": float(np.mean([f > 0 for f in fires])),
                      "per_triggered_task": float(np.mean([f for f in fires if f > 0])) if any(fires) else 0.0}
    return out


def job(args, spec):
    seed, prof, length = spec
    kw = {"n_train": 3 if args.quick else 6, "n_final": 3 if args.quick else 6}
    if length == "long":
        kw.update(subtasks=(12, 16), train_families=("repofix", "configfix"), heldout_families=(), n_accept=0,
                  n_final=0, n_test=0, max_turns=200)
    dom = domain(args, seed, **kw)
    llm = backend(args, prof)
    out = {"seed": seed, "backend": prof, "length": length}
    for name, art in configs(dom).items():
        rs = trial_rows(dom, llm, art)
        out[name] = {**agg(rs), "triggers": triggers(rs)}
    return out


def cell(rs: list[dict], n: str) -> dict:
    return {"token_saving": summarize([-rel(r["base"], r[n], "tokens") for r in rs]),
            "cost_saving": summarize([-rel(r["base"], r[n], "cost") for r in rs]),
            "score_ratio": summarize([r[n]["score"] / r["base"]["score"] if r["base"]["score"] else float("nan")
                                      for r in rs]),
            "triggers": {m: {k: float(np.mean([r[n]["triggers"].get(m, {}).get(k, 0) for r in rs]))
                             for k in ("task_rate", "per_triggered_task")}
                         for m in MECHS.values() if any(m in r[n]["triggers"] for r in rs)}}


def main():
    args = parse_args(__doc__.splitlines()[0], default_seeds=5)
    profs = backends(args)
    lengths = ["default"] if args.live else ["default", "long"]
    rows = pool_map(partial(job, args), [(s, p, l) for s in range(args.seeds) for p in profs for l in lengths],
                    args.workers)
    summ = {}
    for p in profs:
        for l in lengths:
            rs = [r for r in rows if r["backend"] == p and r["length"] == l]
            summ[f"{p}:{l}"] = {n: cell(rs, n) for n in NAMES}
    each_saves = all(summ[c][f"+{k}"]["token_saving"]["mean"] > 0 for c in summ for k in MECHS)
    full_cheapest = {c: summ[c]["full"]["cost_saving"]["mean"] >= max(summ[c][f"+{k}"]["cost_saving"]["mean"]
                                                                      for k in MECHS) - 1e-9 for c in summ}
    lower_on_b = None
    if "B:default" in summ:
        lower_on_b = summ["B:default"]["full"]["triggers"]["action_fusion"]["per_triggered_task"] < \
            summ["A:default"]["full"]["triggers"]["action_fusion"]["per_triggered_task"]
    a = summ["A:default"]["full"]
    verdict = ("REPRODUCED" if each_saves and all(full_cheapest.values()) else "PARTIAL") + \
        f": full stack tokens {-100 * a['token_saving']['mean']:+.1f}%, cost {-100 * a['cost_saving']['mean']:+.1f}% " \
        f"at {100 * a['score_ratio']['mean']:.1f}% of base success (backend A, default length); full stack cheapest " \
        f"in {sum(full_cheapest.values())}/{len(full_cheapest)} cells; every mechanism saves tokens standalone: " \
        f"{each_saves}" + ("" if lower_on_b is None else
                           f"; Action Fusion fires less per triggered task on backend B: {lower_on_b}") + \
        "; full-stack success ratio per cell: " + \
        ", ".join(f"{c} {summ[c]['full']['score_ratio']['mean']:.3f}" for c in summ) + \
        "; EPR-alone success ratio per cell: " + \
        ", ".join(f"{c} {summ[c]['+EPR']['score_ratio']['mean']:.3f}" for c in summ)
    print(table([[c, n, fmt(summ[c][n]["token_saving"], 3), fmt(summ[c][n]["cost_saving"], 3),
                  fmt(summ[c][n]["score_ratio"], 3)] for c in summ for n in NAMES],
                ["backend:length", "config", "token saving", "cost saving", "success ratio"]))
    print("verdict:", verdict)
    save("s7_composition" + ("_live" if args.live else ""), {
        "claim": "Mechanisms compose (full stack cheapest), small losses may accumulate, activation depends on the "
                 "backend [blog; ye-blog]",
        "config": {"llm": args.llm, "seeds": args.seeds, "lengths": lengths, "backends": profs},
        "per_seed": rows, "summary": summ, "full_cheapest": full_cheapest, "verdict": verdict}, args.out)


if __name__ == "__main__":
    main()
