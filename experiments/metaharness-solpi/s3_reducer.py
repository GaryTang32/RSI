"""S3 - Evidence-Preserving Reducer delegates reading a long log without trusting a fluent summary.

Claims [code: receipt.ts]: a cheap model turns a long diagnostic log into a receipt whose every quote is
verified byte-for-byte against the archived log; invalid receipts (bad JSON, wrong schema/status/hash,
unverifiable quotes, a failing log without failure evidence, not smaller) fall back to the raw output; the
raw log stays readable via ``source_artifact``. Expected: accepted receipts NEVER contain a non-verbatim
quote for any hallucination rate h; fallbacks rise with h; savings positive on long logs; failure logs always
carry failure evidence.

Two parts:
* agent runs: base vs +EPR with reducers {deterministic, MockReducer(h) for h in 0, .1, .3, .6, 1} on
  training + held-out AgentWorld families; per arm: acceptance, fallback reasons, bytes saved, audit of every
  accepted receipt (quotes re-checked against the archive), success and cost vs base;
* log bench: long logs from each family pushed through the reducer pipeline directly (the live mode uses a
  real LLM reducer here: ``--llm claude:haiku``).

    python experiments/metaharness-solpi/s3_reducer.py [--llm sim|claude:haiku] [--seeds N] [--quick]
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _agentworld import agg, domain, rel, row, trials_of  # noqa: E402
from _common import fmt, live_llm, parse_args, pool_map, save, summarize, table  # noqa: E402

import numpy as np  # noqa: E402

from rsi.domains.agentworld import MockAgentLLM  # noqa: E402
from rsi.domains.agentworld.envs import FAMILIES  # noqa: E402
from rsi.solpi.reducer import (DIAGNOSTIC_COMMAND, FAILURE_SIGNAL, ArchiveObject, DeterministicReducer,  # noqa: E402
                               LLMReducer, MockReducer, receipt_text, reducer_input, reducer_instructions, sha256,
                               validate_receipt)

HS = (0.0, 0.1, 0.3, 0.6, 1.0)
ARGS = None


def arms():
    out = {"deterministic": {"reducer": "deterministic"}}
    for h in HS:
        out[f"mock_h{h}"] = {"reducer": "mock", "h": h}
    return out


def job(spec):
    seed, prof = spec
    dom = domain(ARGS, seed, n_train=4 if ARGS.quick else 8, n_final=4 if ARGS.quick else 8)
    llm = MockAgentLLM(prof)
    base = [row(t) for t in trials_of(dom, llm, dom.seed_artifact())]
    out = {"seed": seed, "backend": prof, "base": agg(base)}
    for name, cfg in arms().items():
        rs = [row(t) for t in trials_of(dom, llm, dom.harness("evidence_preserving_reducer",
                                                              evidence_preserving_reducer=cfg))]
        au = [r["audit"].get("epr", {}) for r in rs]
        fb = Counter()
        for a in au:
            fb.update(a.get("fallbacks", {}))
        out[name] = {**agg(rs), "eligible": sum(a.get("eligible", 0) for a in au),
                     "receipts": sum(a.get("receipts", 0) for a in au),
                     "nonverbatim": sum(a.get("nonverbatim", 0) for a in au),
                     "failure_logs": sum(a.get("failure_logs", 0) for a in au),
                     "failure_with_evidence": sum(a.get("failure_with_evidence", 0) for a in au),
                     "bytes_saved": sum(a.get("bytes_saved", 0) for a in au), "fallbacks": dict(fb)}
    return out


def bench_logs(n_per_family: int = 3) -> list[tuple[str, str, bool]]:
    logs = []
    cmds = {"repofix": "pytest", "buildfix": "make", "configfix": "make validate", "logtriage": "make check",
            "datalookup": "pytest tests/test_answers.py"}
    for fam, cls in FAMILIES.items():
        for i in range(n_per_family):
            env = cls(f"bench-{fam}-{i}", seed=i, n_subtasks=3)
            if fam in ("logtriage", "datalookup"):
                env.files["report.txt" if fam == "logtriage" else "answers.txt"] = "1: wrong\n"
            out, code = env.shell(cmds[fam])
            if len(out.encode()) >= 4096 and DIAGNOSTIC_COMMAND.search(cmds[fam]):
                logs.append((cmds[fam], out, code != 0))
    return logs


def run_bench(reducer, logs) -> dict:
    stats = Counter()
    audit = {"accepted": 0, "nonverbatim": 0, "failure_logs": 0, "failure_with_evidence": 0, "bytes_in": 0,
             "bytes_out": 0}
    for cmd, body, is_err in logs:
        a = ArchiveObject(sha256(body), len(body.encode()), len(body), body.count("\n") + 1, "/.solpi/bench")
        resp = reducer.reduce(reducer_instructions(), reducer_input(cmd, is_err, a, body), 2048, body=body,
                              archive=a, is_error=is_err)
        if resp.error or resp.stop_reason not in ("stop", "length"):
            stats["model-error"] += 1
            continue
        ok, v = validate_receipt(resp.text, a, body, is_err)
        if not ok:
            stats[v] += 1
            continue
        txt = receipt_text(cmd, a, v, reducer.provider, reducer.model, resp.input_tokens + resp.output_tokens)
        if len(txt.encode()) >= a.bytes:
            stats["receipt-not-smaller"] += 1
            continue
        stats["accepted"] += 1
        audit["accepted"] += 1
        audit["nonverbatim"] += any(e["quote"] not in body for e in v["evidence"])
        if is_err and FAILURE_SIGNAL.search(body):
            audit["failure_logs"] += 1
            audit["failure_with_evidence"] += any(e["kind"] in ("fatal", "failure") for e in v["evidence"])
        audit["bytes_in"] += a.bytes
        audit["bytes_out"] += len(txt.encode())
    return {"n_logs": len(logs), "outcomes": dict(stats), **audit}


def main():
    global ARGS
    ARGS = parse_args(__doc__.splitlines()[0], default_seeds=5)
    logs = bench_logs(1 if ARGS.live else 3)
    bench = {"deterministic": run_bench(DeterministicReducer(), logs)}
    for h in HS:
        bench[f"mock_h{h}"] = run_bench(MockReducer(h, seed=7), logs * (1 if ARGS.live else 4))
    if ARGS.live:
        llm = live_llm(ARGS.llm)
        bench[f"llm:{ARGS.llm}"] = run_bench(LLMReducer(llm), logs)
        bench[f"llm:{ARGS.llm}"]["usage"] = llm.meter.snapshot()
        rows, summ = [], {}
    else:
        rows = pool_map(job, [(s, p) for s in range(ARGS.seeds) for p in ("A", "B")], ARGS.workers)
        summ = {}
        for p in ("A", "B"):
            rs = [r for r in rows if r["backend"] == p]
            for name in arms():
                acc = [r[name]["receipts"] / max(1, r[name]["eligible"]) for r in rs]
                summ[f"{p}:{name}"] = {
                    "acceptance": summarize(acc),
                    "nonverbatim_in_accepted": int(sum(r[name]["nonverbatim"] for r in rs)),
                    "failure_evidence_rate": (sum(r[name]["failure_with_evidence"] for r in rs) /
                                              sum(r[name]["failure_logs"] for r in rs))
                    if sum(r[name]["failure_logs"] for r in rs) else None,
                    "bytes_saved_per_task": summarize([r[name]["bytes_saved"] / r[name]["n"] for r in rs]),
                    "token_saving": summarize([-rel(r["base"], r[name], "tokens") for r in rs]),
                    "cost_saving": summarize([-rel(r["base"], r[name], "cost") for r in rs]),
                    "success_change": summarize([r[name]["score"] - r["base"]["score"] for r in rs]),
                    "fallbacks": dict(sum((Counter(r[name]["fallbacks"]) for r in rs), Counter()))}
    zero = all(b["nonverbatim"] == 0 for b in bench.values()) and \
        all(v["nonverbatim_in_accepted"] == 0 for v in summ.values())
    fe = all(b["failure_with_evidence"] == b["failure_logs"] for b in bench.values()) and \
        all(v["failure_evidence_rate"] == 1.0 for v in summ.values() if v["failure_evidence_rate"] is not None)
    rates = [bench[f"mock_h{h}"]["accepted"] / max(1, bench[f"mock_h{h}"]["n_logs"]) for h in HS]
    mono = all(a >= b - 1e-9 for a, b in zip(rates, rates[1:]))
    saving = bench["deterministic"]["bytes_out"] < bench["deterministic"]["bytes_in"]
    verdict = ("REPRODUCED" if zero and fe and mono and saving else "PARTIAL") + \
        f": 0 non-verbatim quotes in accepted receipts ({'yes' if zero else 'NO'}); failure logs always carry " \
        f"failure evidence ({'yes' if fe else 'NO'}); bench acceptance falls with h: " + \
        ", ".join(f"h={h}:{r:.2f}" for h, r in zip(HS, rates)) + \
        f"; bytes {bench['deterministic']['bytes_in']} -> {bench['deterministic']['bytes_out']}"
    if ARGS.live:
        lv = bench[f"llm:{ARGS.llm}"]
        verdict += f"; live reducer {ARGS.llm}: {lv['accepted']}/{lv['n_logs']} accepted, outcomes {lv['outcomes']}"
    print(table([[k, v["n_logs"], v.get("accepted", 0), v["nonverbatim"], str(v["outcomes"])]
                 for k, v in bench.items()], ["reducer (bench)", "logs", "accepted", "non-verbatim", "outcomes"]))
    if summ:
        print(table([[k, fmt(v["acceptance"], 2), v["nonverbatim_in_accepted"], fmt(v["token_saving"], 3),
                      fmt(v["cost_saving"], 3), fmt(v["success_change"], 3)] for k, v in summ.items()],
                    ["backend:reducer", "acceptance", "non-verbatim", "token saving", "cost saving",
                     "success change"]))
    print("verdict:", verdict)
    save("s3_reducer" + ("_live" if ARGS.live else ""), {
        "claim": "EPR receipts are verified byte-for-byte; unverifiable receipts fall back; savings on long logs "
                 "[SoL-Pi code]",
        "config": {"llm": ARGS.llm, "seeds": ARGS.seeds, "h": HS}, "bench": bench, "agent_runs": summ,
        "per_seed": rows, "verdict": verdict}, ARGS.out)


if __name__ == "__main__":
    main()
