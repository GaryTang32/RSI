"""S2 - ObservationPack removes replay of large tool results but keeps exact access by handle.

Claims [code; blog:ObservationPack]: after its first 2 provider requests a large (>10 KiB) successful tool
result is replaced by a stable placeholder; the bytes stay recallable page by page (exact fidelity);
storage errors fail open. The blog's sweep (excerpt size x full sends) under gates "bill saving >= 10%" and
"quality change >= -2%" found only one configuration (V2: 1536 B / 2 sends, -0.8% quality, 9.1% saving)
inside the quality gate - itself just below the saving gate.

Here: base vs ObservationPack configurations {excerpt 0, 512, 1024, 2048 B} x {1, 2 full sends} on training +
held-out AgentWorld families (paired tasks), plus recall fidelity on 300 synthetic payloads (incl. multi-byte
UTF-8) and a fault-injection arm (every store write fails).

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

from rsi.solpi import ObservationPack  # noqa: E402
from rsi.solpi.obspack import create_observation, read_recall_chunk  # noqa: E402
from rsi.solpi.runtime import Message  # noqa: E402

EXCERPTS = (0, 512, 1024, 2048)
SENDS = (1, 2)
ARGS = None


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
    fo = [row(t) for t in trials_of(dom, llm, dom.harness("observation_pack", observation_pack={"fail_store": True}))]
    out["fail_open"] = {**agg(fo), "fail_open_events": float(np.mean([r["triggers"].get(
        "observation_pack", {}).get("fail_open", 0) for r in fo]))}
    return out


def fidelity(n: int = 300) -> dict:
    rng = random.Random(0)
    ok = 0
    pages = []
    for i in range(n):
        size = rng.randint(11 * 1024, 120 * 1024)
        alphabet = "abcdefghij \n" + ("éßλ中" if i % 3 == 0 else "")
        text = "".join(rng.choice(alphabet) for _ in range(size // 2))
        obs = create_observation(Message("tool", text, tool_call_id=f"c{i}", tool_name="bash"))
        if obs is None:
            continue
        data, off, chunks = text.encode(), 0, []
        while True:
            ch = read_recall_chunk(data, off, 16 * 1024 - 512, 400 - 2)
            chunks.append(ch["text"])
            off = ch["next_offset"]
            if ch["eof"]:
                break
        pages.append(len(chunks))
        ok += "".join(chunks) == text
    return {"payloads": n, "exact": ok, "fidelity": ok / n, "mean_pages": float(np.mean(pages))}


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
        fo = [r["fail_open"] for r in rs]
        sweep[f"{p}:fail_open"] = {"quality_change": summarize([rel(r["base"], r["fail_open"], "score") for r in rs]),
                                   "fail_open_events": summarize([f["fail_open_events"] for f in fo])}
    fid = fidelity()
    a_keys = [k for k in sweep if k.startswith("A:e")]
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
    print(table([[k, fmt(v["quality_change"]), fmt(v["bill_saving"]), fmt(v["token_saving"])]
                 for k, v in sweep.items() if "fail_open" not in k],
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
