"""Live demo: the rsi package improving a real LLM agent harness, end to end, with Claude Haiku.

    python experiments/demo/live_demo.py {metaharness,rrsi,autoresearch} [--suite hard] [--replay-check] [--resume]
"""
from __future__ import annotations

import argparse
import fcntl
import json
import re
import shutil
import signal
import sys
import time
from pathlib import Path

from _demo_common import JOURNAL, RUNS, traces_identical

import rsi
from rsi.core import Budget, CachedLLM, ClaudeCLI, transfer_report
from rsi.domains.agentqa import AgentQADomain, make_suite
from rsi.trace import inspect, load_trace

OUT = RUNS
RESUME = False   # set by --resume
_WRITER_LOCK = None
INFRA = re.compile(r"llm error|agent error|infra:|session limit")
#: the post-run re-score's trial seeds (100, 101): loops and monitors use seeds from 0 (RRSI's loop hashed ones)
RESCORE_K, RESCORE_SEED_OFFSET = 2, 100
NEW_SEED_RECORD = re.compile(r"(rescore|leak_ablation)_s(-?\d+)\.json")
MH_CFG = {"iterations": 4, "k": 2, "history_mode": "full", "seed": 0, "test_splits": ("holdout", "ood"),
          "cost_metric": "tokens", "workers": 4, "validate_timeout_s": 240.0, "shadow_workers": 4}


def _fresh(name: str) -> Path:
    """A new run's directory; with ``--resume`` an unfinished or unclean attempt is archived and replayed from cache."""
    begin_live_write()
    out = OUT / name
    if out.exists():
        summ = out / "summary.json"
        # summaries from before the infrastructure check have no "clean" key and count as finished
        finished = summ.exists() and json.loads(summ.read_text()).get("clean", True) is not False
        if not RESUME or finished:
            hint = "" if finished else " (pass --resume to archive this attempt and continue from the cache)"
            raise SystemExit(f"{out} exists: refusing to overwrite a recorded run{hint}")
        dest = OUT / "_archive" / f"{name}_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        out.rename(dest)
        print(f"archived the unfinished attempt to {dest}", flush=True)
    return out


def begin_live_write() -> None:
    """Hold the demo's writer lock until exit (a second live writer is refused), then drop dangling journal lines."""
    global _WRITER_LOCK
    if _WRITER_LOCK is None:
        OUT.mkdir(parents=True, exist_ok=True)
        f = open(OUT / ".live_writer.lock", "w")
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            f.close()
            raise SystemExit(f"another live demo command holds {OUT / '.live_writer.lock'}: run them one at a time")
        _WRITER_LOCK = f
    drop_unwritten_journal_lines()


def drop_unwritten_journal_lines() -> None:
    """Drop journal lines whose cache entry never appeared (a writer killed between writing the two).

    Safe only under the writer lock, when no other writer can be between the two steps."""
    caches = {c.name: c for c in [*OUT.glob(".cache_*"), *(OUT / "_archive").glob(".cache_*")]}
    for j in OUT.rglob(f"*{JOURNAL}"):
        lines = j.read_text().splitlines()
        kept = []
        for ln in lines:
            cache, _, key = ln.partition("/")
            if cache not in caches or (caches[cache] / key[:2] / f"{key}.json").exists():   # unknown caches stay
                kept.append(ln)
        if kept != lines:
            j.write_text("".join(f"{ln}\n" for ln in kept))


def _haiku(name: str, timeout_s: int = 300, offline: bool = False) -> CachedLLM:
    """The run's cached Haiku; a live one journals its writes into the run directory."""
    return CachedLLM(ClaudeCLI("haiku", timeout_s=timeout_s), OUT / f".cache_{name}", offline=offline,
                     journal=None if offline else OUT / name / JOURNAL)


def _agentqa(suite: str = "easy") -> AgentQADomain:
    if suite == "hard":
        from rsi.domains.agentqa.hard import OOD_HINT, make_hard_suite
        # hard tasks cost ~$0.05 per harness run, so the splits are smaller
        return AgentQADomain(make_hard_suite(n_evolve=8, n_holdout=6, n_ood_per_family=3, seed=0), ood_hint=OOD_HINT)
    return AgentQADomain(make_suite(n_evolve=12, n_holdout=8, n_ood_per_family=2, seed=1))


def _name(method: str, a) -> str:
    return method + ("_hard" if a.suite == "hard" else "")


def _usd(llm) -> float:
    return round(llm.meter.total().cost_usd, 4)


def _print_infra(errs: list[str]) -> None:
    if errs:
        print("INFRA_ERRORS", len(errs), *errs[:5], sep="\n  ")


def infra_errors(out: Path, rep: dict | None = None) -> list[str]:
    """Model calls a run lost to infrastructure, read from structured fields only (never prompts or replies)."""
    found = []
    for e in load_trace(out):
        d, where = e["data"] if isinstance(e["data"], dict) else {}, f"{e['kind']} round {e['round']}"
        s = d.get("summary")
        if e["kind"] in ("eval", "baseline") and isinstance(s, dict) and (s.get("missing") or 0) > 0:
            found.append(f"{e['kind']} {d.get('candidate')}: {s['missing']} trials missing")
        if e["kind"] == "monitor":
            for split, v in (d.get("sealed") or {}).items():
                if isinstance(v, dict) and (v.get("missing") or 0) > 0:
                    found.append(f"monitor {d.get('version')} {split}: {v['missing']} trials missing")
        llm = d.get("llm") if isinstance(d.get("llm"), dict) else {}
        for i, r in enumerate(llm.get("replies") or []):   # RRSI's critic
            if isinstance(r, dict) and r.get("error"):
                found.append(f"{where} {d.get('candidate', '')} reply {i}: {str(r['error'])[:120]}")
        for c in d.get("llm_calls") or []:                  # RRSI's digesters and analyst
            if isinstance(c, dict) and c.get("error"):
                found.append(f"{where} {c.get('role', 'call')}: {str(c['error'])[:120]}")
        gate = d.get("math") if isinstance(d.get("math"), dict) else {}
        if gate.get("gate_failure") == "eval_invalid" or d.get("stage") == "eval_invalid":
            detail = str(gate.get("detail") or d.get("detail"))[:120]
            found.append(f"{where} {d.get('candidate', '')} eval_invalid: {detail}")
        for key in ("error", "status", "reason"):
            if isinstance(text := d.get(key), str) and (m := INFRA.search(text)):
                found.append(f"{where} {key}: {text[m.start():][:120]}")
    # RRSI's calibration repeats and other evaluation jobs are files, not trace events
    for f in sorted((out / "evals").glob("*.json")):
        try:
            miss = json.loads(f.read_text()).get("missing") or 0
        except ValueError:
            continue
        if miss > 0:
            found.append(f"evals/{f.name}: {miss} trials missing")
    for split, row in ((rep or {}).get("splits") or {}).items():
        for arm, v in row.items():
            if (v.get("missing") or 0) > 0:
                found.append(f"re-score {arm} {split}: {v['missing']} trials missing")
    return found


def rescore(dom, llm, arms: dict, seed_offset: int = RESCORE_SEED_OFFSET) -> dict:
    return transfer_report(dom, llm, arms, splits=("evolve", "holdout", "ood"), k=RESCORE_K, workers=4,
                           seed_offset=seed_offset)


def record_path(run_dir: Path, kind: str, seed_offset: int) -> Path:
    """``<kind>.json`` holds trial seeds 100 and 101, ``<kind>_s<N>.json`` seeds N and N + 1."""
    return run_dir / (f"{kind}.json" if seed_offset == RESCORE_SEED_OFFSET else f"{kind}_s{seed_offset}.json")


def record_offsets(run_dir: Path, kind: str) -> list[int]:
    """Seed offsets of the run's ``kind`` records (``rescore`` or ``leak_ablation``)."""
    found = [RESCORE_SEED_OFFSET] if (run_dir / f"{kind}.json").exists() else []
    found += [int(m.group(2)) for p in run_dir.glob(f"{kind}_s*.json") if (m := NEW_SEED_RECORD.fullmatch(p.name))]
    return sorted(found)


def recorded_seeds(run_dir: Path) -> set[int]:
    """Every trial seed a run's files record under ``seeds``, except in the new-seed records."""
    found: set[int] = set()

    def walk(x) -> None:
        if isinstance(x, dict):
            for k, v in x.items():
                if k == "seeds" and isinstance(v, list):
                    found.update(s for s in v if isinstance(s, int))
                else:
                    walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    for p in run_dir.rglob("*.json"):
        if NEW_SEED_RECORD.fullmatch(p.name) or NEW_SEED_RECORD.fullmatch(p.name.replace("_check.json", ".json")):
            continue
        text = p.read_text(errors="replace")
        if '"seeds"' in text:
            try:
                walk(json.loads(text))
            except ValueError:
                pass
    return found


def new_seed_problem(run_dir: Path, record: str, seed_offset: int) -> str | None:
    """Why ``record`` at ``seed_offset`` would reuse trial seeds another evaluation of the run used, or None.

    A re-score and a leak check may share an offset (the leak check's final arm then replays the re-score's)."""
    first = RESCORE_SEED_OFFSET + RESCORE_K
    if seed_offset < first:
        return (f"--seed-offset must be at least {first}: seeds below it were used by the loops, the monitors or the "
                f"recorded re-score")
    hit = sorted(s for s in recorded_seeds(run_dir) if seed_offset <= s < seed_offset + RESCORE_K)
    if hit:
        return f"--seed-offset {seed_offset}: trial seed {hit[0]} was used by an evaluation of the run (its loop)"
    kind = NEW_SEED_RECORD.fullmatch(record).group(1)
    for f in sorted(run_dir.glob("*_s*.json")):
        m = NEW_SEED_RECORD.fullmatch(f.name)
        if not m or f.name == record:
            continue
        rec = json.loads(f.read_text())
        lo = int(rec.get("seed_offset", m.group(2)))
        hi = lo + int(rec.get("k", RESCORE_K))
        if lo < seed_offset + RESCORE_K and seed_offset < hi and not (lo == seed_offset and m.group(1) != kind):
            return f"--seed-offset {seed_offset} overlaps the trial seeds {lo}..{hi - 1} of {f.name}"
    return None


def _monitor_usd(out: Path) -> float:
    """Spend metered outside the run's main LLM (RRSI's shadow monitor)."""
    end = [e for e in load_trace(out) if e["kind"] == "run_end"]
    sp = (end[-1]["data"].get("spend") or {}) if end else {}
    return float(sp.get("shadow_monitor_usd") or 0.0)


def _finish(name: str, out: Path, dom, res, llm, t0: float, setup: dict) -> None:
    inspect(out)
    before, h0, m0 = _usd(llm), llm.hits, llm.misses
    llm.journal = out / f"rescore.{JOURNAL}"   # the post-run re-score is its own attempt (<run>/rescore)
    rep = rescore(dom, llm, {"seed": res.baseline, "final": res.best})
    errs = infra_errors(out, rep)
    monitor = _monitor_usd(out)
    summ = {"run": name, "setup": setup, "stop_reason": res.stop_reason, "wall_s": round(time.time() - t0, 1),
            "trajectory": res.trajectory, "transfer": rep,
            "usd_total": round(_usd(llm) + monitor, 4), "usd_shadow_monitor": round(monitor, 4),
            "usd_transfer_report": round(_usd(llm) - before, 4),
            # hits: calls replayed from an earlier attempt or repeated prompts (forked validation children excluded)
            "cache": {"loop_hits": h0, "loop_misses": m0, "rescore_hits": llm.hits - h0,
                      "rescore_misses": llm.misses - m0},
            "infra_errors": errs, "clean": not errs,
            "seed_files": res.baseline.files, "final_files": res.best.files}
    (out / "summary.json").write_text(json.dumps(summ, indent=1, default=str))
    res.best.to_dir(out / "final_harness", clean=True)
    print(json.dumps({s: {a: v["S"] for a, v in row.items()} for s, row in rep["splits"].items()}, indent=1))
    print("usd", summ["usd_total"], "cache", summ["cache"])
    _print_infra(errs)


def metaharness(a) -> None:
    name = _name("metaharness", a)
    if a.replay_check:
        return replay_check(name, a)
    out, llm, dom, t0 = _fresh(name), _haiku(name), _agentqa(a.suite), time.time()
    res = rsi.improve(dom, method="metaharness", llm_task=llm, llm_propose=llm, config=MH_CFG, out_dir=out,
                      proposer_kind="rewrite")
    _finish(name, out, dom, res, llm, t0, {"method": "metaharness", "suite": a.suite, "config": MH_CFG,
                                           "llm": "claude haiku (task + proposer)"})


def _subset_equal(old, new) -> bool:
    """``new`` reproduces ``old``: equal, except that a dict may carry keys ``old`` predates."""
    if isinstance(old, dict) and isinstance(new, dict):
        return all(k in new and _subset_equal(v, new[k]) for k, v in old.items())
    return old == new


def replay_check(name: str, a) -> None:
    """Replay a finished Meta-Harness run from its cache offline and compare it with the recording.

    The first passing check is recorded in ``<run>_replay/``; later ones replay into a scratch directory and must
    also reproduce that record. ``--record`` refreshes it from a check that passes."""
    src, rec = OUT / name, OUT / f"{name}_replay"
    first = not (rec / "replay_check.json").exists()
    out = rec if first else OUT / f"{name}_replaycheck"   # same depth as the run: paths never reach a prompt
    if out.exists():   # left by a killed check
        shutil.rmtree(out)
    keep = False
    old_term = signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))   # so `kill`/`timeout` still clean up
    try:
        keep = _replay_compare(name, a, src, rec, out, first)
    finally:
        signal.signal(signal.SIGTERM, old_term)
        if out != rec or not keep:
            shutil.rmtree(out, ignore_errors=True)
    if not keep:
        raise SystemExit(1)


def _scores_beyond_practice(d: Path) -> dict:
    """The seed's score, every shadow-monitor reading and every final test result of a Meta-Harness run."""
    ev = load_trace(d)
    return {"baseline": [(e["data"].get("artifact"), e["data"]["summary"]["S"]) for e in ev if e["kind"] == "baseline"],
            "monitor": [(e["data"].get("version"), e["data"].get("artifact"), e["data"].get("decision_score"),
                         {sp: v.get("S") for sp, v in sorted((e["data"].get("sealed") or {}).items())})
                        for e in ev if e["kind"] == "monitor"],
            "test": {str(p.relative_to(d)): (json.loads(p.read_text()).get("score"),
                                             json.loads(p.read_text()).get("per_unit"))
                     for p in sorted((d / "store" / "results").glob("*/*/test.json"))}}


def _prompts_and_scores(d: Path) -> tuple[list, list]:
    # one proposer call yields k candidates: the trace stores its prompt on the first, a placeholder on the rest
    ev = load_trace(d)
    return ([e["data"].get("prompt") for e in ev
             if e["kind"] == "proposal" and e["data"].get("prompt") != "(same proposer call as the first candidate)"],
            [(e["data"]["candidate"], e["data"]["summary"]["S"]) for e in ev if e["kind"] == "eval"])


def _replay_compare(name: str, a, src: Path, rec: Path, out: Path, first: bool) -> bool:
    """Replay ``name`` into ``out`` and compare; True when the check passes (and, for the first, is recorded)."""
    llm, dom = _haiku(name, offline=True), _agentqa(a.suite)
    rsi.improve(dom, method="metaharness", llm_task=llm, llm_propose=llm, config=MH_CFG, out_dir=out,
                proposer_kind="rewrite")
    (p0, s0), (p1, s1) = _prompts_and_scores(src), _prompts_and_scores(out)
    # traces keep at most 60,000 characters of a prompt; zero cache misses proves the whole prompts (hash-keyed)
    b0, b1 = _scores_beyond_practice(src), _scores_beyond_practice(out)
    rep = {"run": name, "proposer_calls": len(p0), "candidates_scored": len(s0), "prompts_identical": p0 == p1,
           "scores_identical": s0 == s1, "traces_identical": traces_identical(src, out),
           "cache_misses": llm.misses, "cache_hits": llm.hits, "usd_spent": _usd(llm),
           "beyond_practice": {"baseline": len(b0["baseline"]),
                               "monitor_readings": sum(len(m[3]) for m in b0["monitor"]),
                               "test_results": len(b0["test"]), "identical": b0 == b1}}
    print(json.dumps(rep, indent=1))
    t, bp = rep["traces_identical"], rep["beyond_practice"]
    ok = (rep["prompts_identical"] and rep["scores_identical"] and not rep["cache_misses"]
          and t["files"] > 0 and t["identical"] == t["files"] and not t["missing_in_replay"]
          and not t["extra_in_replay"] and bp["identical"] and bp["baseline"] == 1 and bp["test_results"] > 0)
    if first:
        if ok:
            (rec / "replay_check.json").write_text(json.dumps(rep, indent=1))
            print("recorded", rec / "replay_check.json")
        else:
            print("FAILED: nothing recorded")
        return ok
    old = json.loads((rec / "replay_check.json").read_text())
    same = all(k in rep and _subset_equal(v, rep[k]) for k, v in old.items())
    print("MATCH: reproduces the run and its recorded replay check" if ok and same else "MISMATCH" if ok else "FAILED")
    if ok and a.record:
        if t["files"] < (old.get("traces_identical") or {}).get("files", 0):
            print("not refreshed: the record has more traces than this run now holds")
            return False
        (rec / "replay_check.json").write_text(json.dumps(rep, indent=1))
        print("refreshed", rec / "replay_check.json")
        return True
    return ok and same


def rrsi_run(a) -> None:
    name = _name("rrsi", a)
    out, llm, dom, t0 = _fresh(name), _haiku(name, 400), _agentqa(a.suite), time.time()
    cfg = {"T": 4, "m": 2, "k": 1, "workers": 4, "seed": 0, "analyst": "llm", "calibration_repeats": 3,
           "max_digests": 4, "n_fail_traces": 6, "n_success_traces": 3, "repair_rounds": 2, "max_done_bounces": 2}
    res = rsi.improve(dom, method="rrsi", llm_task=llm, llm_propose=llm, config=cfg, out_dir=out,
                      llm_analyst=llm, budget=Budget(max_usd=a.max_usd, max_wall_s=45 * 60))
    _finish(name, out, dom, res, llm, t0, {"method": "rrsi", "suite": a.suite, "config": cfg,
                                           "llm": "claude haiku (task, proposer, critic, analyst)"})


def autoresearch(a) -> None:
    from rsi.autoresearch import Config, run
    from rsi.domains.tinylm import TinyLMTask
    name = "autoresearch"
    out, llm, t0 = _fresh(name), _haiku(name), time.time()
    task = TinyLMTask(budget_s=8.0, data_root=OUT / ".cache_tinylm_data")
    task.prepare()
    cfg = Config(max_experiments=10, mode="hardened", keep_rule="strict", reeval_seeds=3, max_usd=a.max_usd,
                 tag="demo-live", seed=0)
    res = run(task, llm_propose=llm, config=cfg, out_dir=out)
    inspect(out)
    errs = infra_errors(out)   # a "crash" row in results.tsv is a graded outcome, not a lost call
    summ = {"run": name, "setup": {"method": "autoresearch", "task": "tinylm, 8 s CPU training budget",
                                   "llm": "claude haiku (research agent)", "max_experiments": 10},
            "stop_reason": res.stop_reason, "wall_s": round(time.time() - t0, 1), "trajectory": res.trajectory,
            "meta": res.meta, "usd_total": _usd(llm), "cache": {"loop_hits": llm.hits, "loop_misses": llm.misses},
            "infra_errors": errs, "clean": not errs}
    (out / "summary.json").write_text(json.dumps(summ, indent=1, default=str))
    print("usd", summ["usd_total"])
    _print_infra(errs)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("which", choices=["metaharness", "rrsi", "autoresearch"])
    ap.add_argument("--max-usd", type=float, default=2.5)
    ap.add_argument("--suite", choices=["easy", "hard"], default="easy",
                    help="easy = numeric AgentQA (the first demo); hard = logic grids + ledgers (Meta-Harness, RRSI)")
    ap.add_argument("--replay-check", action="store_true",
                    help="Meta-Harness only: replay a finished run from its cache at $0 and compare it")
    ap.add_argument("--record", action="store_true",
                    help="with --replay-check: refresh <run>_replay/replay_check.json from a check that passes")
    ap.add_argument("--resume", action="store_true",
                    help="archive an unfinished or unclean attempt of this run and restart it from its cache")
    a = ap.parse_args()
    RESUME = a.resume
    OUT.mkdir(parents=True, exist_ok=True)
    {"metaharness": metaharness, "rrsi": rrsi_run, "autoresearch": autoresearch}[a.which](a)
