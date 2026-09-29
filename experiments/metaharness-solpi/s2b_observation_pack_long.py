"""S2b - ObservationPack bill saving on long-horizon sessions (claims audit Q13, retry round 2).

Claim [blog: ObservationPack]: the TB40 sweep of projection settings found V2 (2,048 B head + 1,536 B tail,
2 full sends) inside the -2% quality gate at a 9.1% bill saving (gate: 10%); the paired EdgeBench A/B gave
bill -23.58%. The blog motivates the mechanism with long sessions ("In base Pi, a large file or tool result
reappeared in every later request") and chooses EdgeBench because its 2-12 h trajectories let "context replay,
large tool outputs, cache writes" accumulate.

S2 ran default AgentWorld lengths (4-8 subtasks, ~13 requests per task). Here: the same five families at the
default length AND at the longest length bin used anywhere in this repo (16-20 subtasks, max_turns 200,
S4's longest bin), fresh seeds, both backends; arms: base, V0 (no excerpt, 1 full send), the release default
(1,024 B, 2 sends) and the blog's V2. Everything else as S2 (paired tasks, prefix-cache meter with
cache writes 12.5 x reads; Pi-faithful compaction billing since retry round 2).

Also reported (descriptive, no threshold): per family, the share of large (> 10 KiB) tool results that are
errors (Pi's bash throws on a non-zero exit, so a failing test run is ``isError`` and the release never packs
it), and the base's replayed-large-output read ceiling (S2's ``replay_read_ceiling``).

Preregistered in docs/methods/metaharness-solpi/claims-audit-solpi.md, "Retry round 2: preregistration", P-Q13.

    python experiments/metaharness-solpi/s2b_observation_pack_long.py [--seeds 10] [--seed-offset 10] [--workers 2]
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _agentworld import agg, rel, row, trials_of  # noqa: E402
from _common import fmt, parse_args, pool_map, save, summarize, table  # noqa: E402

import numpy as np  # noqa: E402

from rsi.domains.agentworld import MockAgentLLM, make_domain  # noqa: E402
from rsi.solpi.meter import PRICES  # noqa: E402

ARMS = {"V0_e0_s1": {"excerpt_bytes": 0, "full_sends": 1},
        "release_e1024_s2": {},
        "V2_h2048_t1536_s2": {"excerpt_bytes": 2048 + 1536, "head_frac": 2048 / (2048 + 1536), "full_sends": 2}}
LENGTHS = {"default": {"subtasks": (4, 8)}, "long": {"subtasks": (16, 20), "max_turns": 200}}
FAMILIES = ("repofix", "buildfix", "logtriage", "configfix", "datalookup")
ARGS = None
# render_trace result lines: "    <- <tool>[ ERROR] <chars> chars: ..." (post-compaction history only)
TRACE_RESULT = re.compile(r"^\s*<- (\S+)( ERROR)? (\d+) chars:")


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


def job(spec):
    seed, prof, length = spec
    t0 = time.time()
    kw = dict(LENGTHS[length])
    dom = make_domain(seed=seed, n_train=6, n_final=6, n_accept=0, n_test=0, **kw)
    llm = MockAgentLLM(prof)
    splits = ("evolve", "ood")
    out = {"seed": seed, "backend": prof, "length": length}
    base_trials = trials_of(dom, llm, dom.seed_artifact(), splits=splits)
    base = [row(t) for t in base_trials]
    read = PRICES[llm.prices].read / 1e6
    prof_rows = []
    for t, r in zip(base_trials, base):
        n_err = sum(1 for ln in (t.trace or "").splitlines()
                    if (m := TRACE_RESULT.match(ln)) and m.group(2) and int(m.group(3)) > 10 * 1024)
        prof_rows.append({"family": r["family"], "requests": r["requests"],
                          "n_large": r["oracle"].get("n_large_outputs", 0), "n_large_err": n_err})
    out["base"] = agg(base)
    out["base_by_family"] = {f: agg([r for r in base if r["family"] == f]) for f in FAMILIES}
    out["replay_read_ceiling"] = float(np.mean([r["oracle"].get("replayed_large_outputs", 0) * read / r["cost"]
                                                for r in base if r["cost"] > 0]))
    out["profile"] = large_output_profile(prof_rows)
    for name, params in ARMS.items():
        rs = [row(t) for t in trials_of(dom, llm, dom.harness("observation_pack", observation_pack=params),
                                        splits=splits)]
        out[name] = {**agg(rs), "packed": float(np.mean([r["triggers"].get("observation_pack", {}).get("packed", 0)
                                                         for r in rs])),
                     "recalls": float(np.mean([r["triggers"].get("observation_pack", {}).get("recalls", 0)
                                               for r in rs]))}
        out[f"{name}_by_family"] = {f: agg([r for r in rs if r["family"] == f]) for f in FAMILIES}
    out["seconds"] = time.time() - t0
    return out


def extra(ap):
    ap.add_argument("--seed-offset", type=int, default=10)


def main():
    global ARGS
    ARGS = parse_args(__doc__.splitlines()[0], default_seeds=10, extra=extra)
    seeds = list(range(ARGS.seed_offset, ARGS.seed_offset + ARGS.seeds))
    specs = [(s, p, l) for s in seeds for p in ("A", "B") for l in LENGTHS]
    rows = pool_map(job, specs, ARGS.workers)
    summ = {}
    for p in ("A", "B"):
        for l in LENGTHS:
            rs = [r for r in rows if r["backend"] == p and r["length"] == l]
            cell = {"base_requests": summarize([r["base"]["requests"] for r in rs]),
                    "replay_read_ceiling": summarize([r["replay_read_ceiling"] for r in rs])}
            for a in ARMS:
                cell[a] = {"bill_saving": summarize([-rel(r["base"], r[a], "cost") for r in rs]),
                           "token_saving": summarize([-rel(r["base"], r[a], "tokens") for r in rs]),
                           "quality_change": summarize([rel(r["base"], r[a], "score") for r in rs]),
                           "cache_read_change": summarize([rel(r["base"], r[a], "cache_read") for r in rs]),
                           "cache_write_change": summarize([rel(r["base"], r[a], "cache_write") for r in rs]),
                           "packed_per_task": summarize([r[a]["packed"] for r in rs])}
                cell[a]["by_family_bill_saving"] = {
                    f: summarize([-rel(r["base_by_family"][f], r[f"{a}_by_family"][f], "cost") for r in rs])
                    for f in FAMILIES}
            prof = {}
            for f in FAMILIES:
                prof[f] = {k: float(np.mean([r["profile"][f][k] for r in rs if f in r["profile"]]))
                           for k in ("requests", "large_outputs", "large_error_share")}
            cell["large_output_profile"] = prof
            summ[f"{p}:{l}"] = cell
    # preregistered decision (P-Q13): backend A, long length, V2
    v2 = summ["A:long"]["V2_h2048_t1536_s2"]
    b = v2["bill_saving"]
    q = v2["quality_change"]
    magnitude_ok = b["lo"] <= 0.091 <= b["hi"] or b["lo"] > 0.091
    positive = b["lo"] > 0
    gate = b["mean"] >= 0.10 and q["mean"] >= -0.02
    longer_helps = [-rel(r["base"], r["V2_h2048_t1536_s2"], "cost") for r in rows if r["backend"] == "A" and
                    r["length"] == "long"]
    shorter = {r["seed"]: -rel(r["base"], r["V2_h2048_t1536_s2"], "cost") for r in rows if r["backend"] == "A"
               and r["length"] == "default"}
    diff = summarize([x - shorter[r["seed"]] for x, r in zip(longer_helps, [r for r in rows if r["backend"] == "A"
                                                                           and r["length"] == "long"])])
    verdict = ("bill component REPRODUCED on CPU analogue" if (magnitude_ok and positive) else
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
        "verdict": verdict}, ARGS.out)


if __name__ == "__main__":
    main()
