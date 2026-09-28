"""S2 - ObservationPack removes replay of large tool results but keeps exact access by handle.

Claims [code; blog:ObservationPack]: after its first 2 provider requests a large (>10 KiB) successful tool
result is replaced by a stable placeholder; the bytes stay recallable page by page (exact fidelity);
storage errors fail open. The blog's sweep (excerpt size x full sends) under gates "bill saving >= 10%" and
"quality change >= -2%" found only one configuration (V2: 1536 B / 2 sends, -0.8% quality, 9.1% saving)
inside the quality gate - itself just below the saving gate.

Here: base vs ObservationPack configurations {excerpt 0, 512, 1024, 2048 B} x {1, 2 full sends} plus the blog's
exact V2 point (2,048 B head + 1,536 B tail, 2 sends) on training + held-out AgentWorld families (paired tasks),
recall fidelity on 300 synthetic payloads (incl. multi-byte UTF-8) and a fault-injection arm (every store write
fails).

Why the 10% bill gate is out of reach here (claims audit Q13: parameter or domain?), measured per backend:
* ``bill_decomposition``: cache-read / cache-write token changes of the release default vs base;
* ``replay_read_ceiling``: the base's cache-read cost of large outputs replayed after their first 2 sends
  (``oracle.replayed_large_outputs`` x read price) / base bill = the most any packing can save before paying for
  the prefix rewrite at the placeholder swap;
* ``rho1``: the release default re-priced with cache writes at the read price (rho = 1), i.e. without the
  rewrite premium.

    python experiments/metaharness-solpi/s2_observation_pack.py [--llm sim|claude:haiku] [--seeds N] [--quick]
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _agentworld import agg, backend, domain, rel, row, trials_of  # noqa: E402
from _common import fmt, parse_args, pool_map, save, summarize, table  # noqa: E402

import numpy as np  # noqa: E402

import dataclasses  # noqa: E402

from rsi.solpi import ObservationPack  # noqa: E402
from rsi.solpi.meter import PRICES  # noqa: E402
from rsi.solpi.obspack import create_observation, read_recall_chunk  # noqa: E402
from rsi.solpi.runtime import Message  # noqa: E402

EXCERPTS = (0, 512, 1024, 2048)
SENDS = (1, 2)
V2 = {"excerpt_bytes": 2048 + 1536, "head_frac": 2048 / (2048 + 1536), "full_sends": 2}   # blog's selected point
ARGS = None


def rho1_backend(prof: str):
    """The same simulated backend priced with cache writes at the read price (no rewrite premium)."""
    llm = backend(ARGS, prof)
    key = f"{llm.prices}-rho1"
    if key not in PRICES:
        PRICES[key] = dataclasses.replace(PRICES[llm.prices], name=key, write=PRICES[llm.prices].read)
    llm.prices = key
    return llm


def job(spec):
    seed, prof = spec
    dom = domain(ARGS, seed, n_train=4 if ARGS.quick else 8, n_final=4 if ARGS.quick else 8)
    llm = backend(ARGS, prof)
    out = {"seed": seed, "backend": prof}
    base = [row(t) for t in trials_of(dom, llm, dom.seed_artifact())]
    out["base"] = agg(base)
    for e in EXCERPTS:
        for sends in SENDS:
            rs = [row(t) for t in trials_of(dom, llm, dom.harness(
                "observation_pack", observation_pack={"excerpt_bytes": e, "full_sends": sends}))]
            a = agg(rs)
            out[f"e{e}_s{sends}"] = {**a, "removed_tokens": float(np.mean([r["triggers"].get(
                "observation_pack", {}).get("removed_tokens", 0) for r in rs])),
                "recalls": float(np.mean([r["triggers"].get("observation_pack", {}).get("recalls", 0) for r in rs]))}
    rs = [row(t) for t in trials_of(dom, llm, dom.harness("observation_pack", observation_pack=V2))]
    out["v2"] = {**agg(rs), "removed_tokens": float(np.mean([r["triggers"].get(
        "observation_pack", {}).get("removed_tokens", 0) for r in rs])),
        "recalls": float(np.mean([r["triggers"].get("observation_pack", {}).get("recalls", 0) for r in rs]))}
    fo = [row(t) for t in trials_of(dom, llm, dom.harness("observation_pack", observation_pack={"fail_store": True}))]
    out["fail_open"] = {**agg(fo), "fail_open_events": float(np.mean([r["triggers"].get(
        "observation_pack", {}).get("fail_open", 0) for r in fo]))}
    if not ARGS.live:
        read = PRICES[llm.prices].read / 1e6
        out["replay_read_ceiling"] = float(np.mean([r["oracle"].get("replayed_large_outputs", 0) * read / r["cost"]
                                                    for r in base if r["cost"] > 0]))
        l1 = rho1_backend(prof)
        out["rho1_base"] = agg([row(t) for t in trials_of(dom, l1, dom.seed_artifact())])
        out["rho1_default"] = agg([row(t) for t in trials_of(dom, l1, dom.harness("observation_pack"))])
    return out


def fidelity(n: int = 300) -> dict:
    rng = random.Random(0)
    ok = eligible = 0
    pages = []
    for i in range(n):
        size = rng.randint(11 * 1024, 120 * 1024)
        alphabet = "abcdefghij \n" + ("éßλ中" if i % 3 == 0 else "")
        text = "".join(rng.choice(alphabet) for _ in range(size))
        obs = create_observation(Message("tool", text, tool_call_id=f"c{i}", tool_name="bash"))
        if obs is None:
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


def main():
    global ARGS
    ARGS = parse_args(__doc__.splitlines()[0], default_seeds=5)
    profs = ["A"] if ARGS.live else ["A", "B"]
    rows = pool_map(job, [(s, p) for s in range(ARGS.seeds) for p in profs], ARGS.workers)
    sweep = {}
    for p in profs:
        rs = [r for r in rows if r["backend"] == p]
        for e in EXCERPTS:
            for sends in SENDS:
                key = f"e{e}_s{sends}"
                q = [rel(r["base"], r[key], "score") for r in rs]
                bill = [-rel(r["base"], r[key], "cost") for r in rs]
                tok = [-rel(r["base"], r[key], "tokens") for r in rs]
                qs, bs = summarize(q), summarize(bill)
                sweep[f"{p}:{key}"] = {"quality_change": qs, "bill_saving": bs, "token_saving": summarize(tok),
                                       "removed_tokens": summarize([r[key]["removed_tokens"] for r in rs]),
                                       "in_quality_gate": qs["mean"] >= -0.02, "in_saving_gate": bs["mean"] >= 0.10}
        q, bill, tok = ([rel(r["base"], r["v2"], "score") for r in rs], [-rel(r["base"], r["v2"], "cost") for r in rs],
                        [-rel(r["base"], r["v2"], "tokens") for r in rs])
        sweep[f"{p}:v2_h2048_t1536_s2"] = {"quality_change": summarize(q), "bill_saving": summarize(bill),
                                           "token_saving": summarize(tok),
                                           "removed_tokens": summarize([r["v2"]["removed_tokens"] for r in rs]),
                                           "in_quality_gate": summarize(q)["mean"] >= -0.02,
                                           "in_saving_gate": summarize(bill)["mean"] >= 0.10}
        if not ARGS.live:
            dec = {"cache_read_change": summarize([rel(r["base"], r["e1024_s2"], "cache_read") for r in rs]),
                   "cache_write_change": summarize([rel(r["base"], r["e1024_s2"], "cache_write") for r in rs]),
                   "replay_read_ceiling": summarize([r["replay_read_ceiling"] for r in rs]),
                   "rho1_bill_saving": summarize([-rel(r["rho1_base"], r["rho1_default"], "cost") for r in rs]),
                   "recalls_per_task": summarize([r["e1024_s2"]["recalls"] for r in rs])}
            sweep[f"{p}:bill_decomposition"] = dec
        fo = [r["fail_open"] for r in rs]
        sweep[f"{p}:fail_open"] = {"quality_change": summarize([rel(r["base"], r["fail_open"], "score") for r in rs]),
                                   "fail_open_events": summarize([f["fail_open_events"] for f in fo])}
    fid = fidelity()
    a_keys = [k for k in sweep if k.startswith("A:e") or k.startswith("A:v2")]
    passing_q = [k for k in a_keys if sweep[k]["in_quality_gate"]]
    passing_both = [k for k in passing_q if sweep[k]["in_saving_gate"]]
    fo_ok = abs(sweep["A:fail_open"]["quality_change"]["mean"]) <= 0.01 and \
        sweep["A:fail_open"]["fail_open_events"]["mean"] > 0
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
    save("s2_observation_pack" + ("_live" if ARGS.live else ""), {
        "claim": "ObservationPack removes replay but keeps exact access; only some sweep points pass the quality gate; "
                 "fail-open [SoL-Pi code/blog]",
        "config": {"llm": ARGS.llm, "seeds": ARGS.seeds, "excerpts": EXCERPTS, "sends": SENDS,
                   "gates": {"quality": -0.02, "bill_saving": 0.10}},
        "per_seed": rows, "sweep": sweep, "recall_fidelity": fid, "verdict": verdict}, ARGS.out)


if __name__ == "__main__":
    main()
