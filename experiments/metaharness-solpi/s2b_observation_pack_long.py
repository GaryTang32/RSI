"""S2b - ObservationPack bill saving on long-horizon sessions (claims audit Q13, retry round 2).

Usage: python experiments/metaharness-solpi/s2b_observation_pack_long.py [--seeds 10] [--seed-offset 10] [--workers 2]
"""
from __future__ import annotations

import re
import time

import numpy as np

from _agentworld import agg, parse_seeded, rel, replay_read_ceiling, row, trial_rows, trials_of, trigger_mean
from _common import fmt, pool_map, save, summarize, table
from rsi.domains.agentworld import MockAgentLLM, make_domain

ARMS = {"V0_e0_s1": {"excerpt_bytes": 0, "full_sends": 1},
        "release_e1024_s2": {},
        "V2_h2048_t1536_s2": {"excerpt_bytes": 2048 + 1536, "head_frac": 2048 / (2048 + 1536), "full_sends": 2}}
V2 = "V2_h2048_t1536_s2"
LENGTHS = {"default": {"subtasks": (4, 8)}, "long": {"subtasks": (16, 20), "max_turns": 200}}
FAMILIES = ("repofix", "buildfix", "logtriage", "configfix", "datalookup")
# render_trace result lines: "    <- <tool>[ ERROR] <chars> chars: ..." (post-compaction history only)
TRACE_RESULT = re.compile(r"^\s*<- (\S+)( ERROR)? (\d+) chars:")


def large_errors(trace: str | None) -> int:
    """Error tool results over 10 KiB in a rendered trace."""
    return sum(1 for ln in (trace or "").splitlines()
               if (m := TRACE_RESULT.match(ln)) and m.group(2) and int(m.group(3)) > 10 * 1024)


def large_output_profile(rows: list[dict]) -> dict:
    out = {}
    for fam in FAMILIES:
        rs = [r for r in rows if r["family"] == fam]
        if rs:
            out[fam] = {"requests": float(np.mean([r["requests"] for r in rs])),
                        "large_outputs": float(np.mean([r["n_large"] for r in rs])),
                        "large_error_share": float(np.sum([r["n_large_err"] for r in rs]) /
                                                   max(1, np.sum([r["n_large"] + r["n_large_err"] for r in rs])))}
    return out


def by_family(rows: list[dict]) -> dict:
    return {f: agg([r for r in rows if r["family"] == f]) for f in FAMILIES}


def job(spec):
    seed, prof, length = spec
    t0 = time.time()
    dom = make_domain(seed=seed, n_train=6, n_final=6, n_accept=0, n_test=0, **LENGTHS[length])
    llm = MockAgentLLM(prof)
    base_trials = trials_of(dom, llm, dom.seed_artifact())
    base = [row(t) for t in base_trials]
    profile = [{"family": r["family"], "requests": r["requests"], "n_large": r["oracle"].get("n_large_outputs", 0),
                "n_large_err": large_errors(t.trace)} for t, r in zip(base_trials, base)]
    out = {"seed": seed, "backend": prof, "length": length, "base": agg(base), "base_by_family": by_family(base),
           "replay_read_ceiling": replay_read_ceiling(base, llm.prices), "profile": large_output_profile(profile)}
    for name, params in ARMS.items():
        rs = trial_rows(dom, llm, dom.harness("observation_pack", observation_pack=params))
        out[name] = {**agg(rs), "packed": trigger_mean(rs, "observation_pack", "packed"),
                     "recalls": trigger_mean(rs, "observation_pack", "recalls")}
        out[f"{name}_by_family"] = by_family(rs)
    out["seconds"] = time.time() - t0
    return out


def arm_cell(rs: list[dict], a: str) -> dict:
    cell = {"bill_saving": summarize([-rel(r["base"], r[a], "cost") for r in rs]),
            "token_saving": summarize([-rel(r["base"], r[a], "tokens") for r in rs]),
            "quality_change": summarize([rel(r["base"], r[a], "score") for r in rs]),
            "cache_read_change": summarize([rel(r["base"], r[a], "cache_read") for r in rs]),
            "cache_write_change": summarize([rel(r["base"], r[a], "cache_write") for r in rs]),
            "packed_per_task": summarize([r[a]["packed"] for r in rs])}
    cell["by_family_bill_saving"] = {
        f: summarize([-rel(r["base_by_family"][f], r[f"{a}_by_family"][f], "cost") for r in rs]) for f in FAMILIES}
    return cell


def main():
    args, seeds = parse_seeded(__doc__.splitlines()[0], default_seeds=10)
    rows = pool_map(job, [(s, p, l) for s in seeds for p in ("A", "B") for l in LENGTHS], args.workers)
    summ = {}
    for p in ("A", "B"):
        for l in LENGTHS:
            rs = [r for r in rows if r["backend"] == p and r["length"] == l]
            summ[f"{p}:{l}"] = {
                "base_requests": summarize([r["base"]["requests"] for r in rs]),
                "replay_read_ceiling": summarize([r["replay_read_ceiling"] for r in rs]),
                **{a: arm_cell(rs, a) for a in ARMS},
                "large_output_profile": {f: {k: float(np.mean([r["profile"][f][k] for r in rs if f in r["profile"]]))
                                             for k in ("requests", "large_outputs", "large_error_share")}
                                         for f in FAMILIES}}
    # preregistered decision (P-Q13): backend A, long length, V2
    b, q = summ["A:long"][V2]["bill_saving"], summ["A:long"][V2]["quality_change"]
    magnitude_ok = b["lo"] <= 0.091 <= b["hi"] or b["lo"] > 0.091
    gate = b["mean"] >= 0.10 and q["mean"] >= -0.02

    def a_rows(length):
        return [r for r in rows if r["backend"] == "A" and r["length"] == length]

    shorter = {r["seed"]: -rel(r["base"], r[V2], "cost") for r in a_rows("default")}
    diff = summarize([-rel(r["base"], r[V2], "cost") - shorter[r["seed"]] for r in a_rows("long")])
    verdict = ("bill component REPRODUCED on CPU analogue" if magnitude_ok and b["lo"] > 0 else
               "bill component NOT REPRODUCED") + \
        f": V2 on long sessions (backend A) bill saving {fmt(b)} (paper 9.1%), quality change {fmt(q)}; " \
        f"reaches the 10% gate: {gate}; long minus default (paired by seed) {fmt(diff)}"
    print(table([[c, a, fmt(summ[c][a]["bill_saving"]), fmt(summ[c][a]["token_saving"]),
                  fmt(summ[c][a]["quality_change"]), fmt(summ[c][a]["cache_write_change"])]
                 for c in summ for a in ARMS], ["backend:length", "arm", "bill saving", "token saving",
                                                "quality change", "cache-write change"]))
    for c in summ:
        print(c, "base requests", fmt(summ[c]["base_requests"]), "replay ceiling", fmt(summ[c]["replay_read_ceiling"]),
              {f: {k: round(v, 3) for k, v in d.items()} for f, d in summ[c]["large_output_profile"].items()})
    print("verdict:", verdict)
    save("s2b_observation_pack_long", {
        "claim": "Q13: ObservationPack V2 saves ~9.1% of the bill inside the quality gate (TB40); long sessions are "
                 "the paper's setting [blog]",
        "config": {"seeds": seeds, "arms": ARMS, "lengths": LENGTHS, "prereg": "claims-audit-solpi.md P-Q13"},
        "per_seed": rows, "summary": summ, "long_minus_default_A_V2": diff, "reaches_gate_A_long_V2": gate,
        "verdict": verdict}, args.out)


if __name__ == "__main__":
    main()
