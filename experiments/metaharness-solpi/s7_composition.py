"""S7 - Independently found mechanisms compose; small losses may accumulate; activation depends on the model.

Claims [blog: Results; ye-blog; sec:pengqian Q9]: in the add-one ablation every mechanism reduces tokens
standalone and the full stack is cheapest (EdgeBench: -44.7..-49.0% tokens, about -33% cost at ~94% of Pi's
score, on two backends); mechanisms trigger less on the second backend (Action Fusion 70.6 vs 13.5 fires per
triggered task; OCC on 92% vs 33% of tasks) but still pay.

Configurations: base, +AF, +OP, +EPR, +OCC, full stack; backends A (search) and B (other habits); training +
held-out families, default task lengths plus a long-task bin (12-16 subtasks) where OCC can matter.

    python experiments/metaharness-solpi/s7_composition.py [--llm sim|claude:haiku] [--seeds N] [--quick]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _agentworld import agg, backend, domain, rel, row, trials_of  # noqa: E402
from _common import fmt, parse_args, pool_map, save, summarize, table  # noqa: E402

import numpy as np  # noqa: E402

MECHS = {"AF": "action_fusion", "OP": "observation_pack", "EPR": "evidence_preserving_reducer",
         "OCC": "online_context_compact"}
FIRE = {"action_fusion": "fused", "observation_pack": "packed", "evidence_preserving_reducer": "applied",
        "online_context_compact": "compactions"}
ARGS = None


def configs(dom):
    out = {"base": dom.seed_artifact()}
    for k, m in MECHS.items():
        out[f"+{k}"] = dom.harness(m)
    out["full"] = dom.harness(*MECHS.values())
    return out


def job(spec):
    seed, prof, length = spec
    kw = {"n_train": 3 if ARGS.quick else 6, "n_final": 3 if ARGS.quick else 6}
    if length == "long":
        kw.update(subtasks=(12, 16), train_families=("repofix", "configfix"), heldout_families=(), n_accept=0,
                  n_final=0, n_test=0, max_turns=200)
    dom = domain(ARGS, seed, **kw)
    llm = backend(ARGS, prof)
    out = {"seed": seed, "backend": prof, "length": length}
    for name, art in configs(dom).items():
        rs = [row(t) for t in trials_of(dom, llm, art)]
        a = agg(rs)
        trig = {}
        for m, key in FIRE.items():
            fires = [r["triggers"].get(m, {}).get(key, 0) for r in rs if m in r["triggers"]]
            if fires:
                trig[m] = {"task_rate": float(np.mean([f > 0 for f in fires])),
                           "per_triggered_task": float(np.mean([f for f in fires if f > 0])) if any(fires) else 0.0}
        out[name] = {**a, "triggers": trig}
    return out


def main():
    global ARGS
    ARGS = parse_args(__doc__.splitlines()[0], default_seeds=5)
    profs = ["A"] if ARGS.live else ["A", "B"]
    lengths = ["default"] if ARGS.live else ["default", "long"]
    rows = pool_map(job, [(s, p, l) for s in range(ARGS.seeds) for p in profs for l in lengths], ARGS.workers)
    names = ["base"] + [f"+{k}" for k in MECHS] + ["full"]
    summ = {}
    for p in profs:
        for l in lengths:
            rs = [r for r in rows if r["backend"] == p and r["length"] == l]
            cell = {}
            for n in names:
                cell[n] = {"token_saving": summarize([-rel(r["base"], r[n], "tokens") for r in rs]),
                           "cost_saving": summarize([-rel(r["base"], r[n], "cost") for r in rs]),
                           "score_ratio": summarize([r[n]["score"] / r["base"]["score"] if r["base"]["score"]
                                                     else float("nan") for r in rs]),
                           "triggers": {m: {k: float(np.mean([r[n]["triggers"].get(m, {}).get(k, 0) for r in rs]))
                                            for k in ("task_rate", "per_triggered_task")}
                                        for m in MECHS.values() if any(m in r[n]["triggers"] for r in rs)}}
            summ[f"{p}:{l}"] = cell
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
                  fmt(summ[c][n]["score_ratio"], 3)] for c in summ for n in names],
                ["backend:length", "config", "token saving", "cost saving", "success ratio"]))
    print("verdict:", verdict)
    save("s7_composition" + ("_live" if ARGS.live else ""), {
        "claim": "Mechanisms compose (full stack cheapest), small losses may accumulate, activation depends on the "
                 "backend [blog; ye-blog]",
        "config": {"llm": ARGS.llm, "seeds": ARGS.seeds, "lengths": lengths, "backends": profs},
        "per_seed": rows, "summary": summ, "full_cheapest": full_cheapest, "verdict": verdict}, ARGS.out)


if __name__ == "__main__":
    main()
