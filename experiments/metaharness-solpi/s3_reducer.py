"""S3 - Evidence-Preserving Reducer delegates reading a long log without trusting a fluent summary.

Usage: python experiments/metaharness-solpi/s3_reducer.py [--llm sim|claude:haiku] [--seeds N] [--quick]
"""
from __future__ import annotations

from collections import Counter
from functools import partial

from _agentworld import agg, domain, metrics, rel, trial_rows
from _common import fmt, live_llm, parse_args, pool_map, save, summarize, table
from rsi.domains.agentworld import MockAgentLLM
from rsi.domains.agentworld.envs import FAMILIES
from rsi.solpi.reducer import (DIAGNOSTIC_COMMAND, FAILURE_SIGNAL, ArchiveObject, DeterministicReducer, LLMReducer,
                               MockReducer, receipt_text, reducer_input, reducer_instructions, sha256, validate_receipt)

HS = (0.0, 0.1, 0.3, 0.6, 1.0)
ARMS = {"deterministic": {"reducer": "deterministic"}} | {f"mock_h{h}": {"reducer": "mock", "h": h} for h in HS}
EPR_COUNTS = ("eligible", "receipts", "nonverbatim", "failure_logs", "failure_with_evidence", "bytes_saved")
BENCH_COMMANDS = {"repofix": "pytest", "buildfix": "make", "configfix": "make validate", "logtriage": "make check",
                  "datalookup": "pytest tests/test_answers.py"}


def job(args, spec):
    seed, prof = spec
    n = 4 if args.quick else 8
    dom = domain(args, seed, n_train=n, n_final=n)
    llm = MockAgentLLM(prof)
    out = {"seed": seed, "backend": prof, "base": metrics(dom, llm, dom.seed_artifact())}
    for name, cfg in ARMS.items():
        rs = trial_rows(dom, llm, dom.harness("evidence_preserving_reducer", evidence_preserving_reducer=cfg))
        audits = [r["audit"].get("epr", {}) for r in rs]
        fallbacks = Counter()
        for a in audits:
            fallbacks.update(a.get("fallbacks", {}))
        out[name] = {**agg(rs), **{k: sum(a.get(k, 0) for a in audits) for k in EPR_COUNTS},
                     "fallbacks": dict(fallbacks)}
    return out


def bench_logs(n_per_family: int = 3) -> list[tuple[str, str, bool]]:
    logs = []
    for fam, cls in FAMILIES.items():
        cmd = BENCH_COMMANDS[fam]
        for i in range(n_per_family):
            env = cls(f"bench-{fam}-{i}", seed=i, n_subtasks=3)
            if fam in ("logtriage", "datalookup"):
                env.files["report.txt" if fam == "logtriage" else "answers.txt"] = "1: wrong\n"
            out, code = env.shell(cmd)
            if len(out.encode()) >= 4096 and DIAGNOSTIC_COMMAND.search(cmd):
                logs.append((cmd, out, code != 0))
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


def arm_summary(rs: list[dict], name: str) -> dict:
    failure_logs = sum(r[name]["failure_logs"] for r in rs)
    return {"acceptance": summarize([r[name]["receipts"] / max(1, r[name]["eligible"]) for r in rs]),
            "nonverbatim_in_accepted": int(sum(r[name]["nonverbatim"] for r in rs)),
            "failure_evidence_rate": (sum(r[name]["failure_with_evidence"] for r in rs) / failure_logs)
            if failure_logs else None,
            "bytes_saved_per_task": summarize([r[name]["bytes_saved"] / r[name]["n"] for r in rs]),
            "token_saving": summarize([-rel(r["base"], r[name], "tokens") for r in rs]),
            "cost_saving": summarize([-rel(r["base"], r[name], "cost") for r in rs]),
            "success_change": summarize([r[name]["score"] - r["base"]["score"] for r in rs]),
            "fallbacks": dict(sum((Counter(r[name]["fallbacks"]) for r in rs), Counter()))}


def main():
    args = parse_args(__doc__.splitlines()[0], default_seeds=5)
    logs = bench_logs(1 if args.live else 3)
    bench = {"deterministic": run_bench(DeterministicReducer(), logs)}
    for h in HS:
        bench[f"mock_h{h}"] = run_bench(MockReducer(h, seed=7), logs * (1 if args.live else 4))
    rows, summ = [], {}
    if args.live:
        llm, key = live_llm(args.llm), f"llm:{args.llm}"
        bench[key] = run_bench(LLMReducer(llm), logs)
        bench[key]["usage"] = llm.meter.snapshot()
    else:
        rows = pool_map(partial(job, args), [(s, p) for s in range(args.seeds) for p in ("A", "B")], args.workers)
        for p in ("A", "B"):
            rs = [r for r in rows if r["backend"] == p]
            summ |= {f"{p}:{name}": arm_summary(rs, name) for name in ARMS}
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
    if args.live:
        lv = bench[f"llm:{args.llm}"]
        verdict += f"; live reducer {args.llm}: {lv['accepted']}/{lv['n_logs']} accepted, outcomes {lv['outcomes']}"
    print(table([[k, v["n_logs"], v.get("accepted", 0), v["nonverbatim"], str(v["outcomes"])]
                 for k, v in bench.items()], ["reducer (bench)", "logs", "accepted", "non-verbatim", "outcomes"]))
    if summ:
        print(table([[k, fmt(v["acceptance"], 2), v["nonverbatim_in_accepted"], fmt(v["token_saving"], 3),
                      fmt(v["cost_saving"], 3), fmt(v["success_change"], 3)] for k, v in summ.items()],
                    ["backend:reducer", "acceptance", "non-verbatim", "token saving", "cost saving",
                     "success change"]))
    print("verdict:", verdict)
    save("s3_reducer" + ("_live" if args.live else ""), {
        "claim": "EPR receipts are verified byte-for-byte; unverifiable receipts fall back; savings on long logs "
                 "[SoL-Pi code]",
        "config": {"llm": args.llm, "seeds": args.seeds, "h": HS}, "bench": bench, "agent_runs": summ,
        "per_seed": rows, "verdict": verdict}, args.out)


if __name__ == "__main__":
    main()
