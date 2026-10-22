"""S2 - ObservationPack removes replay of large tool results but keeps exact access by handle.

Usage: python experiments/metaharness-solpi/s2_observation_pack.py [--llm sim|claude:haiku] [--seeds N] [--quick]
"""
from __future__ import annotations

import dataclasses
import random
from functools import partial

import numpy as np

from _agentworld import agg, backend, backends, domain, metrics, rel, replay_read_ceiling, trial_rows, trigger_mean
from _common import fmt, parse_args, pool_map, save, summarize, table
from rsi.solpi.meter import PRICES
from rsi.solpi.obspack import create_observation, read_recall_chunk
from rsi.solpi.runtime import Message

EXCERPTS = (0, 512, 1024, 2048)
SENDS = (1, 2)
V2 = {"excerpt_bytes": 2048 + 1536, "head_frac": 2048 / (2048 + 1536), "full_sends": 2}   # blog's selected point
CONFIGS = {f"e{e}_s{s}": {"excerpt_bytes": e, "full_sends": s} for e in EXCERPTS for s in SENDS} | {"v2": V2}
SWEEP_LABEL = {"v2": "v2_h2048_t1536_s2"}
QUALITY_GATE, BILL_GATE = -0.02, 0.10


def rho1_backend(args, prof: str):
    """The same simulated backend priced with cache writes at the read price (no rewrite premium)."""
    llm = backend(args, prof)
    key = f"{llm.prices}-rho1"
    PRICES.setdefault(key, dataclasses.replace(PRICES[llm.prices], name=key, write=PRICES[llm.prices].read))
    llm.prices = key
    return llm


def job(args, spec):
    seed, prof = spec
    n = 4 if args.quick else 8
    dom = domain(args, seed, n_train=n, n_final=n)
    llm = backend(args, prof)
    base = trial_rows(dom, llm, dom.seed_artifact())
    out = {"seed": seed, "backend": prof, "base": agg(base)}
    for key, params in CONFIGS.items():
        rs = trial_rows(dom, llm, dom.harness("observation_pack", observation_pack=params))
        out[key] = {**agg(rs), "removed_tokens": trigger_mean(rs, "observation_pack", "removed_tokens"),
                    "recalls": trigger_mean(rs, "observation_pack", "recalls")}
    fo = trial_rows(dom, llm, dom.harness("observation_pack", observation_pack={"fail_store": True}))
    out["fail_open"] = {**agg(fo), "fail_open_events": trigger_mean(fo, "observation_pack", "fail_open")}
    if not args.live:
        out["replay_read_ceiling"] = replay_read_ceiling(base, llm.prices)
        l1 = rho1_backend(args, prof)
        out["rho1_base"] = metrics(dom, l1, dom.seed_artifact())
        out["rho1_default"] = metrics(dom, l1, dom.harness("observation_pack"))
    return out


def fidelity(n: int = 300) -> dict:
    rng = random.Random(0)
    ok = eligible = 0
    pages = []
    for i in range(n):
        size = rng.randint(11 * 1024, 120 * 1024)
        alphabet = "abcdefghij \n" + ("éßλ中" if i % 3 == 0 else "")
        text = "".join(rng.choice(alphabet) for _ in range(size))
        if create_observation(Message("tool", text, tool_call_id=f"c{i}", tool_name="bash")) is None:
            continue
        eligible += 1
        data, off, chunks = text.encode(), 0, []
        while True:
            ch = read_recall_chunk(data, off, 16 * 1024 - 512, 400 - 2)
            chunks.append(ch["text"])
            off = ch["next_offset"]
            if ch["eof"]:
                break
        pages.append(len(chunks))
        ok += "".join(chunks) == text
    return {"payloads": eligible, "exact": ok, "fidelity": ok / max(1, eligible), "mean_pages": float(np.mean(pages))}


def sweep_point(rs: list[dict], key: str) -> dict:
    q = summarize([rel(r["base"], r[key], "score") for r in rs])
    bill = summarize([-rel(r["base"], r[key], "cost") for r in rs])
    return {"quality_change": q, "bill_saving": bill,
            "token_saving": summarize([-rel(r["base"], r[key], "tokens") for r in rs]),
            "removed_tokens": summarize([r[key]["removed_tokens"] for r in rs]),
            "in_quality_gate": q["mean"] >= QUALITY_GATE, "in_saving_gate": bill["mean"] >= BILL_GATE}


def main():
    args = parse_args(__doc__.splitlines()[0], default_seeds=5)
    profs = backends(args)
    rows = pool_map(partial(job, args), [(s, p) for s in range(args.seeds) for p in profs], args.workers)
    sweep = {}
    for p in profs:
        rs = [r for r in rows if r["backend"] == p]
        for key in CONFIGS:
            sweep[f"{p}:{SWEEP_LABEL.get(key, key)}"] = sweep_point(rs, key)
        if not args.live:
            sweep[f"{p}:bill_decomposition"] = {
                "cache_read_change": summarize([rel(r["base"], r["e1024_s2"], "cache_read") for r in rs]),
                "cache_write_change": summarize([rel(r["base"], r["e1024_s2"], "cache_write") for r in rs]),
                "replay_read_ceiling": summarize([r["replay_read_ceiling"] for r in rs]),
                "rho1_bill_saving": summarize([-rel(r["rho1_base"], r["rho1_default"], "cost") for r in rs]),
                "recalls_per_task": summarize([r["e1024_s2"]["recalls"] for r in rs])}
        sweep[f"{p}:fail_open"] = {"quality_change": summarize([rel(r["base"], r["fail_open"], "score") for r in rs]),
                                   "fail_open_events": summarize([r["fail_open"]["fail_open_events"] for r in rs])}
    fid = fidelity()
    a_keys = [k for k in sweep if k.startswith(("A:e", "A:v2"))]
    passing_q = [k for k in a_keys if sweep[k]["in_quality_gate"]]
    passing_both = [k for k in passing_q if sweep[k]["in_saving_gate"]]
    fail_open = sweep["A:fail_open"]
    fo_ok = abs(fail_open["quality_change"]["mean"]) <= 0.01 and fail_open["fail_open_events"]["mean"] > 0
    only_some = 0 < len(passing_q) < len(a_keys)
    verdict = ("REPRODUCED" if only_some and fid["fidelity"] == 1.0 and fo_ok else "PARTIAL") + \
        f": recall fidelity {100 * fid['fidelity']:.0f}% over {fid['payloads']} payloads; fail-open " \
        f"{'ok' if fo_ok else 'NOT ok'}; {len(passing_q)}/{len(a_keys)} configurations inside the -2% quality gate " \
        f"({', '.join(k[2:] for k in passing_q)}), {len(passing_both)} also inside the 10% bill-saving gate" + \
        ("" if only_some else " - the quality gate does not discriminate between configurations here")
    if "A:bill_decomposition" in sweep:
        d = sweep["A:bill_decomposition"]
        verdict += (f". Bill gate out of reach by construction (domain, not parameters): replayed large outputs are "
                    f"only {100 * d['replay_read_ceiling']['mean']:.1f}% of the base bill as cache reads; packing "
                    f"cuts cache reads {100 * d['cache_read_change']['mean']:+.0f}% but re-writes the prefix after "
                    f"each swap (cache writes {100 * d['cache_write_change']['mean']:+.0f}%, priced 12.5x); without "
                    f"that premium (rho = 1) the same packing saves "
                    f"{100 * d['rho1_bill_saving']['mean']:.1f}% of the bill")
    print(table([[k, fmt(v["quality_change"]), fmt(v["bill_saving"]), fmt(v["token_saving"])]
                 for k, v in sweep.items() if "fail_open" not in k and "decomposition" not in k],
                ["backend:config", "quality change", "bill saving", "token saving"]))
    print("verdict:", verdict)
    save("s2_observation_pack" + ("_live" if args.live else ""), {
        "claim": "ObservationPack removes replay but keeps exact access; only some sweep points pass the quality gate; "
                 "fail-open [SoL-Pi code/blog]",
        "config": {"llm": args.llm, "seeds": args.seeds, "excerpts": EXCERPTS, "sends": SENDS,
                   "gates": {"quality": QUALITY_GATE, "bill_saving": BILL_GATE}},
        "per_seed": rows, "sweep": sweep, "recall_fidelity": fid, "verdict": verdict}, args.out)


if __name__ == "__main__":
    main()
