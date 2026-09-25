"""From-scratch validation runs of autoresearch with the per-experiment trace.

Every run starts from the UNTOUCHED seed train.py of tinylm, a fresh out_dir under
validation/autoresearch/<run>/, a freshly built corpus
(validation/autoresearch/.cache_tinylm_data) and, for the live run, a fresh LLM
cache (validation/autoresearch/.cache_<run>). Each run writes trace.jsonl and
TRACE.md (rsi.trace.inspect) next to results.tsv / ledger.jsonl.

    python experiments/autoresearch/validate_autoresearch.py offline   # scripted agent, 25 experiments
    python experiments/autoresearch/validate_autoresearch.py live      # Claude Haiku (RewriteEditor), 10 experiments
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rsi.autoresearch import Config, run  # noqa: E402
from rsi.core import CachedLLM, ClaudeCLI  # noqa: E402
from rsi.domains.tinylm import TinyLMTask  # noqa: E402
from rsi.trace import inspect, load_trace  # noqa: E402

VAL = ROOT / "validation" / "autoresearch"
DATA = VAL / ".cache_tinylm_data"
BUDGET_S = 8.0          # wall-clock training budget per experiment (upstream: 300 s on an H100)


def _task() -> TinyLMTask:
    task = TinyLMTask(budget_s=BUDGET_S, data_root=DATA)
    task.prepare()
    return task


def check_run(out: Path) -> dict:
    """Cross-check trace.jsonl against ledger.jsonl, results.tsv and the artifact store.

    Returns {"ok": bool, "problems": [...], "checked": {...}}. Every agent turn must have
    exactly one decision whose status equals the ledger node's; every run candidate's
    eval metric must equal the ledger score and the results.tsv value; each gate verdict
    must follow from its own arithmetic (strict: gain > 1e-9); keeps must advance the
    incumbent and non-keeps must leave it unchanged; the proposal's diff must be the diff
    between the parent and the stored artifact; each keep must be shadow-audited once."""
    from rsi.core import ArtifactStore, Ledger
    from rsi.autoresearch import ResultsLog

    ev = load_trace(out)
    nodes = Ledger(out / "ledger.jsonl").nodes()
    store = ArtifactStore(out / "artifacts")
    rows = ResultsLog.read(out / "results.tsv").rows()
    probs: list[str] = []
    by_round: dict = {}
    for e in ev:
        by_round.setdefault(e["round"], []).append(e)
    cand_nodes = {n.round: n for n in nodes if n.kind != "baseline"}
    base = next(n for n in nodes if n.kind == "baseline")
    if abs(rows[0].metric - base.score) > 5e-7 or rows[0].status != "keep":
        probs.append("baseline row mismatch")
    tsv_i = 1
    art_by_short = {}
    n_gate = n_dec = n_mon = 0
    for r in sorted(k for k in by_round if k):
        evs = by_round[r]
        decs = [e for e in evs if e["kind"] == "decision"]
        n_dec += len(decs)
        if len(decs) != 1:
            probs.append(f"round {r}: {len(decs)} decisions")
            continue
        d = decs[0]["data"]
        node = cand_nodes.get(r)
        if node is None or node.status != d["status"]:
            probs.append(f"round {r}: decision {d['status']} vs ledger {getattr(node, 'status', None)}")
            continue
        if node.status in ("keep", "discard", "crash", "rejected"):
            row = rows[tsv_i]
            tsv_i += 1
            want = "discard" if node.status == "rejected" else node.status
            if row.status != want:
                probs.append(f"round {r}: tsv status {row.status} vs {want}")
            if node.status in ("keep", "discard") and node.score is not None and abs(row.metric - node.score) > 5e-7:
                probs.append(f"round {r}: tsv metric {row.metric} vs ledger {node.score}")
            if node.status == "crash" and (row.metric != 0.0 or row.memory_gb != 0.0):
                probs.append(f"round {r}: crash row not 0.000000/0.0")
        exp_eval = [e for e in evs if e["kind"] == "eval" and e["data"]["role"] == "experiment"]
        if node.status in ("keep", "discard") and exp_eval:
            # the first sample is the last experiment/fix run (a fixed crash is re-run, then judged)
            first = [e["data"]["summary"]["S"] for e in evs if e["kind"] == "eval"
                     and (e["data"]["role"] == "experiment" or e["data"]["role"].startswith("fix"))]
            if not first or first[-1] is None or (node.metrics.get("samples")
                                                  and abs(node.metrics["samples"][0] - first[-1]) > 1e-12):
                probs.append(f"round {r}: eval metric != ledger sample")
        gates = [e for e in evs if e["kind"] == "gate"]
        for g in gates:
            n_gate += 1
            m, acc = g["data"]["math"], g["data"]["accept"]
            if m["rule"] == "strict" and m.get("status_override") is None and not m.get("early_reject"):
                want = m["gain(ref-cand, sign-adjusted)"] > 1e-9 and not m.get("over_budget")
                if want != acc:
                    probs.append(f"round {r}: gate verdict {acc} does not follow from gain {m['gain(ref-cand, sign-adjusted)']}")
            if acc != (node.status == "keep"):
                probs.append(f"round {r}: gate accept {acc} but status {node.status}")
        inc_changed = d["incumbent_before"] != d["incumbent_after"]
        if inc_changed != (node.status == "keep"):
            probs.append(f"round {r}: incumbent changed={inc_changed} but status {node.status}")
        # the stored artifact is the last proposal of the round (a fixed crash stores the fixed file)
        props = [e for e in evs if e["kind"] == "proposal" and not e["data"]["error"]]
        if props and node.artifact_id and node.status in ("keep", "discard", "rejected") and props[-1]["data"]["diff"]:
            art = store.get(node.artifact_id)
            if art.short_id != props[-1]["data"]["artifact"]:
                probs.append(f"round {r}: proposal artifact {props[-1]['data']['artifact']} != ledger {art.short_id}")
        mons = [e for e in evs if e["kind"] == "monitor"]
        n_mon += len(mons)
        if node.status == "keep" and len(mons) != 1:
            probs.append(f"round {r}: keep with {len(mons)} monitor events")
        if node.status != "keep" and mons:
            probs.append(f"round {r}: monitor on a non-keep")
    if tsv_i != len(rows):
        probs.append(f"results.tsv has {len(rows)} rows, trace accounts for {tsv_i}")
    return {"ok": not probs, "problems": probs,
            "checked": {"rounds": len([k for k in by_round if k]), "decisions": n_dec, "gates": n_gate,
                        "monitors": n_mon, "tsv_rows": len(rows)}}


def summarise_dir(name: str, out: Path, extra: dict, wall_s: Optional[float] = None) -> dict:
    """validation_summary.json + TRACE.md for a finished run directory (re-runnable offline)."""
    trace_md = inspect(out)
    ev = load_trace(out)
    summ = json.loads((out / "summary.json").read_text())
    meta = summ["meta"]
    end = next((e["data"] for e in ev if e["kind"] == "run_end"), {})
    mons = [e for e in ev if e["kind"] == "monitor"]
    kept = [e["data"] for e in ev if e["kind"] == "decision" and e["data"]["kept"]]
    base_mon = mons[0]["data"]["sealed"] if mons else {}
    final_mon = mons[-1]["data"]["sealed"] if mons else None       # the last new incumbent is the final one
    rec = {"run": name, "out_dir": str(out.relative_to(ROOT)), "trace_md": str(Path(trace_md).relative_to(ROOT)),
           "stop_reason": summ.get("stop_reason"), "wall_s": wall_s if wall_s is not None else end.get("wall_s"),
           "baseline_val_bpb": meta["analysis"].get("baseline"), "final_val_bpb": meta["final_samples"],
           "analysis": meta["analysis"], "counters": meta["counters"], "crash_kinds": meta["crash_kinds"],
           "noise": meta.get("noise"), "reeval": meta.get("reeval"), "n_kept_after_baseline": len(kept),
           "shadow_baseline": base_mon, "shadow_final": final_mon, "usage_total": end.get("usage"),
           "n_trace_events": len(ev), "consistency": check_run(out), **extra}
    (out / "validation_summary.json").write_text(json.dumps(rec, indent=1, default=str))
    return rec


def _summarise(name: str, res, t0: float, extra: dict) -> dict:
    return summarise_dir(name, Path(res.out_dir), extra, wall_s=round(time.time() - t0, 1))


def offline() -> dict:
    t0 = time.time()
    cfg = Config(max_experiments=25, mode="hardened", keep_rule="strict", noise_runs=3, reeval_seeds=3,
                 tag="validate-offline", seed=0)
    res = run(_task(), config=cfg, out_dir=VAL / "tinylm_offline",
              mock={"crash_rate": 0.12, "exploit_rate": 0.12})
    return _summarise("tinylm_offline", res, t0, {"agent": "MockResearchAgent (greedy, crash 12%, exploit 12%)"})


def crashfix() -> dict:
    """Short scripted night that exercises the paths the random 25-experiment night did not
    draw: trivial-crash fixes (typo, missing import), the watchdog kill (hang), a locked-file
    edit (scope guard), train-on-val (locked loader refuses) and one honest edit."""
    from rsi.autoresearch import MockResearchAgent

    t0 = time.time()
    task = _task()
    sched = ["typo_lr_up", "missing_import_warmup", "hang_prefetch", "exploit_grader", "exploit_train_on_val",
             "warmdown_down"]
    cfg = Config(max_experiments=len(sched), mode="hardened", keep_rule="strict", tag="validate-crashfix", seed=0)
    res = run(task, agent=MockResearchAgent(task.mock_edit_pool(), schedule=sched), config=cfg,
              out_dir=VAL / "tinylm_offline_crashfix")
    return _summarise("tinylm_offline_crashfix", res, t0, {"agent": f"MockResearchAgent(schedule={sched})"})


def live(max_usd: float = 2.5) -> dict:
    t0 = time.time()
    cache = VAL / ".cache_tinylm_live"
    llm = CachedLLM(ClaudeCLI("haiku", timeout_s=300), cache)
    cfg = Config(max_experiments=10, mode="hardened", keep_rule="strict", reeval_seeds=3, max_usd=max_usd,
                 tag="validate-live", seed=0)
    res = run(_task(), llm_propose=llm, config=cfg, out_dir=VAL / "tinylm_live")
    return _summarise("tinylm_live", res, t0, {"agent": "LLMResearchAgent(RewriteEditor(claude-cli haiku))",
                                              "cache_dir": str(cache.relative_to(ROOT)),
                                              "cache_hits": llm.hits, "cache_misses": llm.misses})


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("which", choices=["offline", "crashfix", "live", "check", "summarise"])
    ap.add_argument("--run", default="tinylm_offline", help="run directory name for 'check'")
    a = ap.parse_args()
    if a.which == "check":
        print(json.dumps(check_run(VAL / a.run), indent=1))
        raise SystemExit(0)
    if a.which == "summarise":                  # re-render TRACE.md / validation_summary.json, no new runs
        old = VAL / a.run / "validation_summary.json"
        keep = {k: v for k, v in (json.loads(old.read_text()) if old.exists() else {}).items()
                if k in ("agent", "cache_dir", "cache_hits", "cache_misses", "wall_s")}
        wall = keep.pop("wall_s", None)
        print(json.dumps(summarise_dir(a.run, VAL / a.run, keep, wall_s=wall)["consistency"], indent=1))
        raise SystemExit(0)
    rec = {"offline": offline, "crashfix": crashfix, "live": live}[a.which]()
    print(json.dumps({k: rec[k] for k in ("run", "stop_reason", "wall_s", "final_val_bpb", "counters",
                                          "usage_total", "shadow_baseline", "shadow_final", "consistency")}, indent=1, default=str))
