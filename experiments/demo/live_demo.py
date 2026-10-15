"""Live demo: the rsi package improving a real LLM agent harness, end to end, with Claude Haiku.

Each run starts from an untouched seed, uses Claude Haiku both as the frozen task model and
as the proposer, and writes a full trace (``trace.jsonl`` -> ``TRACE.md``) plus a
``summary.json`` that ``experiments/demo/plot_demo.py`` turns into graphs::

    python experiments/demo/live_demo.py metaharness    # AgentQA harness, Meta-Harness, N=4 k=2
    python experiments/demo/live_demo.py rrsi           # AgentQA harness, RRSI, T=4
    python experiments/demo/live_demo.py autoresearch   # tinylm train.py, autoresearch, 10 experiments
    python experiments/demo/live_demo.py metaharness --suite hard   # harder tasks: logic grids + ledgers
    python experiments/demo/live_demo.py rrsi --suite hard
    python experiments/demo/live_demo.py metaharness --replay-check # re-run a finished run from its cache at $0 and
                # compare its proposer prompts, practice scores and traces, seed score, monitor readings and test results

Outputs go to ``demo/runs/<method>/`` with a fresh LLM cache in ``demo/runs/.cache_<method>``
(a re-run with the same cache replays at $0). The seed harness is one direct model call with
the system prompt "You are a helpful assistant."; the grader is exact match, outside the artifact.
Held-out (``holdout``) and out-of-distribution (``ood``) tasks are sealed: the loop never sees
them, only the write-only shadow monitor and the final transfer report do.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import rsi  # noqa: E402
from rsi.core import Budget, CachedLLM, ClaudeCLI, transfer_report  # noqa: E402
from rsi.domains.agentqa import AgentQADomain, make_suite  # noqa: E402
from rsi.trace import inspect  # noqa: E402

OUT = ROOT / "demo" / "runs"


RESUME = False   # set by --resume


def _fresh(name: str) -> Path:
    """The run directory for a new run. An existing one is refused, except with ``--resume`` when it holds an
    unfinished or unclean attempt: that attempt moves to ``runs/_archive/<name>_<UTC time>`` (its log stays where the
    chain wrote it) and the run restarts, replaying every call its cache already holds for $0."""
    out = OUT / name
    if out.exists():
        summ = out / "summary.json"
        # a finished run (summary written, and not marked unclean) is never replaced; summaries from before the
        # infrastructure check have no "clean" key and count as finished
        finished = summ.exists() and json.loads(summ.read_text()).get("clean", True) is not False
        if not RESUME or finished:
            raise SystemExit(f"{out} exists: refusing to overwrite a recorded run"
                             + ("" if finished else " (pass --resume to archive this attempt and continue from the cache)"))
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        dest = OUT / "_archive" / f"{name}_{stamp}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        out.rename(dest)
        print(f"archived the unfinished attempt to {dest}", flush=True)
    drop_unwritten_journal_lines()
    return out


def drop_unwritten_journal_lines(root: Optional[Path] = None) -> int:
    """A journal line is written just before its cache entry appears, so an attempt killed in between lists an entry
    it never wrote; a later attempt that writes the entry would then share it. Every live writer (a run, a
    ``--record``) calls this before it starts, when no other live writer is running (the demo runs one job at a
    time), so any line without an entry is such a leftover: drop them all. Returns how many lines it dropped."""
    root = OUT if root is None else root
    caches = {c.name: c for c in [*root.glob(".cache_*"), *(root / "_archive").glob(".cache_*")]}
    dropped = 0
    for j in root.rglob(f"*{JOURNAL}"):
        lines = j.read_text().splitlines()
        kept = []
        for ln in lines:
            cache, _, key = ln.partition("/")
            if cache in caches and not (caches[cache] / key[:2] / f"{key}.json").exists():
                continue   # an entry that never appeared (an unknown cache is left alone)
            kept.append(ln)
        if kept != lines:
            j.write_text("".join(f"{ln}\n" for ln in kept))
            dropped += len(lines) - len(kept)
    return dropped


#: an attempt's cache-write journal (every cache entry it paid for; spend.py attributes entries by it)
JOURNAL = "cache_writes.txt"


def _haiku(name: str, timeout_s: int = 300, offline: bool = False) -> CachedLLM:
    """The run's cached Haiku. A live one journals its writes into the run directory (it moves with an archived
    attempt); an offline replay writes nothing."""
    return CachedLLM(ClaudeCLI("haiku", timeout_s=timeout_s), OUT / f".cache_{name}", offline=offline,
                     journal=None if offline else OUT / name / JOURNAL)


def _agentqa(suite: str = "easy"):
    if suite == "hard":
        # 8 practice tasks (logic grids + transaction ledgers); sealed: 6 held-out of the same kinds and
        # 6 OOD (business-day scheduling + chained string transformations)
        from rsi.domains.agentqa.hard import OOD_HINT, make_hard_suite
        # hard tasks cost ~$0.05 per harness run (long puzzles, 60-85 row ledgers), so the splits are smaller
        return AgentQADomain(make_hard_suite(n_evolve=8, n_holdout=6, n_ood_per_family=3, seed=0), ood_hint=OOD_HINT)
    # 12 practice tasks (numeric word problems); sealed: 8 held-out numeric + 8 ood from 4 unseen families
    return AgentQADomain(make_suite(n_evolve=12, n_holdout=8, n_ood_per_family=2, seed=1))


def _name(method: str, a) -> str:
    return method + ("_hard" if a.suite == "hard" else "")


def _usd(llm) -> float:
    return round(llm.meter.total().cost_usd, 4)


INFRA = re.compile(r"llm error|agent error|infra:|session limit")


def infra_errors(out: Path, rep: Optional[dict] = None) -> list[str]:
    """Model calls lost to infrastructure anywhere in a run, read from structured fields only (never from prompts or
    replies, so a harness that merely mentions such text is not flagged):

    - trials missing from loop evaluations, shadow-monitor readings and the post-run re-score;
    - every recorded model-call error of RRSI's critic (``llm.replies[].error``) and of its digesters and analyst
      (``llm_calls[].error``): any error there is a failed call (timeout, usage cap, CLI error, offline cache miss);
    - candidates RRSI dropped as ``eval_invalid`` (too many trials lost), and trials missing from any of RRSI's
      evaluation files (``evals/*.json``, which include its calibration repeats);
    - infrastructure errors on proposals, analyses, gates and other events (``error`` / ``status`` / ``reason``).

    A run with any of these is not a clean result."""
    from rsi.trace import load_trace
    found = []

    def hit(where: str, text) -> None:
        if isinstance(text, str) and (m := INFRA.search(text)):
            found.append(f"{where}: {text[m.start():][:120]}")

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
        for i, r in enumerate(llm.get("replies") or []):
            if isinstance(r, dict) and r.get("error"):
                found.append(f"{where} {d.get('candidate', '')} reply {i}: {str(r['error'])[:120]}")
        for c in d.get("llm_calls") or []:
            if isinstance(c, dict) and c.get("error"):
                found.append(f"{where} {c.get('role', 'call')}: {str(c['error'])[:120]}")
        math = d.get("math") if isinstance(d.get("math"), dict) else {}
        if math.get("gate_failure") == "eval_invalid" or d.get("stage") == "eval_invalid":
            found.append(f"{where} {d.get('candidate', '')} eval_invalid: {str(math.get('detail') or d.get('detail'))[:120]}")
        for key in ("error", "status", "reason"):
            hit(f"{where} {key}", d.get(key))
    # RRSI's calibration repeats and other evaluation jobs are files, not trace events
    for f in sorted((out / "evals").glob("*.json")) if (out / "evals").is_dir() else []:
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


#: the post-run re-score draws fresh trials: shadow monitors and Meta-Harness's loop score with trial seeds 0, 1, ...
#: (RRSI's loop uses hashed per-job seeds), so these seeds (100, 101) are samples no loop decision and no monitor
#: reading used
RESCORE_K, RESCORE_SEED_OFFSET = 2, 100


def rescore(dom, llm, arms: dict, splits=("evolve", "holdout", "ood"), seed_offset: int = RESCORE_SEED_OFFSET) -> dict:
    return transfer_report(dom, llm, arms, splits=splits, k=RESCORE_K, workers=4, seed_offset=seed_offset)


NEW_SEED_RECORD = re.compile(r"(rescore|leak_ablation)_s(-?\d+)\.json")


def recorded_seeds(run_dir: Path) -> set[int]:
    """Every trial seed a run's files record under ``seeds`` (RRSI's loop: its hashed per-job seeds), apart from the
    new-seed records, whose seeds :func:`new_seed_problem` checks separately."""
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


def new_seed_problem(run_dir: Path, record: str, seed_offset: int) -> Optional[str]:
    """Why a re-score or leak check with ``seed_offset`` would not draw new trial seeds, or None.

    A re-score uses trial seeds ``seed_offset .. seed_offset + RESCORE_K - 1`` and the LLM cache is keyed on the seed,
    so any seed another evaluation of the run already used replays its samples. Meta-Harness's loop and the monitors
    use seeds from 0 and the recorded re-score and leak check use 100 and 101, so new seeds start at 102. RRSI's loop
    uses hashed seeds, which its evaluation files record (:func:`recorded_seeds`); none of them may fall in the range.
    Nor may the seeds of an existing ``rescore_s<N>``/``leak_ablation_s<N>`` record, except that a re-score and a
    leak check may share an offset, as the recorded pair does (the leak check's final arm then replays the
    re-score's)."""
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
    """Spend a run metered outside its main LLM (RRSI's shadow monitor has its own meter)."""
    from rsi.trace import load_trace
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
            # hits = lookups answered from the cache at $0: calls replayed from an earlier attempt, plus repeats of
            # prompts this run already sent. Calls made inside forked interface-validation children are not counted.
            "cache": {"loop_hits": h0, "loop_misses": m0, "rescore_hits": llm.hits - h0,
                      "rescore_misses": llm.misses - m0},
            "infra_errors": errs, "clean": not errs,
            "seed_files": res.baseline.files, "final_files": res.best.files}
    (out / "summary.json").write_text(json.dumps(summ, indent=1, default=str))
    res.best.to_dir(out / "final_harness", clean=True)
    print(json.dumps({s: {a: v["S"] for a, v in row.items()} for s, row in rep["splits"].items()}, indent=1))
    print("usd", summ["usd_total"], "cache", summ["cache"])
    if errs:
        print("INFRA_ERRORS", len(errs), *errs[:5], sep="\n  ")


MH_CFG = {"iterations": 4, "k": 2, "history_mode": "full", "seed": 0, "test_splits": ("holdout", "ood"),
          "cost_metric": "tokens", "workers": 4, "validate_timeout_s": 240.0, "shadow_workers": 4}


def metaharness(a) -> None:
    name = _name("metaharness", a)
    if a.replay_check:
        return replay_check(name, a)
    out, llm, dom, t0 = _fresh(name), _haiku(name), _agentqa(a.suite), time.time()
    res = rsi.improve(dom, method="metaharness", llm_task=llm, llm_propose=llm, config=MH_CFG, out_dir=out,
                      proposer_kind="rewrite")
    _finish(name, out, dom, res, llm, t0, {"method": "metaharness", "suite": a.suite, "config": MH_CFG,
                                           "llm": "claude haiku (task + proposer)"})


def traces_identical(src: Path, out: Path) -> dict:
    """Byte-compare every candidate's execution traces (model replies, tool output with sandbox paths and timeouts)
    between a recorded run and its replay."""
    rel = lambda d: {p.relative_to(d): p.read_bytes() for p in (d / "store" / "candidates").glob("*/eval/*/traces/*")}
    a, b = rel(src), rel(out)
    # the replay writes every trace afresh, so a trace only it has means the recorded run lost one
    return {"files": len(a), "identical": sum(1 for k in a if b.get(k) == a[k]), "missing_in_replay": len(set(a) - set(b)),
            "extra_in_replay": len(set(b) - set(a))}


def _subset_equal(old, new) -> bool:
    """``new`` reproduces ``old``: equal, except that a dict may carry keys ``old`` predates."""
    if isinstance(old, dict) and isinstance(new, dict):
        return all(k in new and _subset_equal(v, new[k]) for k, v in old.items())
    return old == new


def replay_check(name: str, a) -> None:
    """Re-run a finished Meta-Harness demo with its cache in offline mode (a cache miss is an error, so nothing
    is sent to the model) and compare with the recorded run every proposer prompt, every candidate's practice score,
    every candidate execution trace, the seed's score, every shadow-monitor reading and every final test result. The first check that passes is recorded in ``<run>_replay/``; a failing or interrupted one is
    removed, so the next check starts over. Later checks replay into a scratch directory (removed afterwards) and must
    also reproduce that record, trace counts included. ``--record`` refreshes the record from a check that passes on
    its own (e.g. to add a field a newer check computes), never with fewer traces than it records."""
    import shutil
    import signal
    from rsi.trace import load_trace
    src, rec = OUT / name, OUT / f"{name}_replay"
    first = not (rec / "replay_check.json").exists()
    out = rec if first else OUT / f"{name}_replaycheck"   # same depth as the run: paths never reach a prompt
    if out.exists():   # left by a check that was killed (SIGKILL); the next one removes it
        shutil.rmtree(out)
    keep = False
    old_term = signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))   # so `kill`/`timeout` still clean up
    try:
        keep = _replay_compare(name, a, src, rec, out, first, load_trace)
    finally:
        signal.signal(signal.SIGTERM, old_term)
        if out != rec or not keep:
            shutil.rmtree(out, ignore_errors=True)
    if not keep:
        raise SystemExit(1)


def _scores_beyond_practice(d: Path, load_trace) -> dict:
    """What a Meta-Harness run scored outside its practice candidates: the seed's score, every shadow-monitor reading
    (sealed splits, model-written code included) and every one-time final test result."""
    ev = load_trace(d)
    return {"baseline": [(e["data"].get("artifact"), e["data"]["summary"]["S"]) for e in ev if e["kind"] == "baseline"],
            "monitor": [(e["data"].get("version"), e["data"].get("artifact"), e["data"].get("decision_score"),
                         {sp: v.get("S") for sp, v in sorted((e["data"].get("sealed") or {}).items())})
                        for e in ev if e["kind"] == "monitor"],
            "test": {str(p.relative_to(d)): (json.loads(p.read_text()).get("score"),
                                             json.loads(p.read_text()).get("per_unit"))
                     for p in sorted((d / "store" / "results").glob("*/*/test.json"))}}


def _replay_compare(name: str, a, src: Path, rec: Path, out: Path, first: bool, load_trace) -> bool:
    """Replay ``name`` into ``out`` and compare; True when the check passes (and, for the first, is recorded)."""
    llm, dom = _haiku(name, offline=True), _agentqa(a.suite)
    rsi.improve(dom, method="metaharness", llm_task=llm, llm_propose=llm, config=MH_CFG, out_dir=out,
                proposer_kind="rewrite")
    # one proposer call yields k candidates; the trace stores its prompt on the first and a placeholder on the rest
    same_call = "(same proposer call as the first candidate)"
    pick = lambda d: ([e["data"].get("prompt") for e in load_trace(d)
                       if e["kind"] == "proposal" and e["data"].get("prompt") != same_call],
                      [(e["data"]["candidate"], e["data"]["summary"]["S"]) for e in load_trace(d) if e["kind"] == "eval"])
    (p0, s0), (p1, s1) = pick(src), pick(out)
    # the trace keeps at most 60,000 characters of a prompt; zero cache misses is the full-prompt proof, because the
    # cache is keyed on a hash of the whole prompt
    b0, b1 = _scores_beyond_practice(src, load_trace), _scores_beyond_practice(out, load_trace)
    rep = {"run": name, "proposer_calls": len(p0), "candidates_scored": len(s0), "prompts_identical": p0 == p1,
           "scores_identical": s0 == s1, "traces_identical": traces_identical(src, out),
           "cache_misses": llm.misses, "cache_hits": llm.hits, "usd_spent": _usd(llm),
           # the seed's score, the monitor readings and the final test: scores the practice comparison leaves out
           "beyond_practice": {"baseline": len(b0["baseline"]), "monitor_readings": sum(len(m[3]) for m in b0["monitor"]),
                               "test_results": len(b0["test"]), "identical": b0 == b1}}
    print(json.dumps(rep, indent=1))
    t, bp = rep["traces_identical"], rep["beyond_practice"]
    ok = (rep["prompts_identical"] and rep["scores_identical"] and not rep["cache_misses"]
          and t["files"] > 0 and t["identical"] == t["files"] and not t["missing_in_replay"] and not t["extra_in_replay"]
          and bp["identical"] and bp["baseline"] == 1 and bp["test_results"] > 0)
    if first:
        if ok:
            (rec / "replay_check.json").write_text(json.dumps(rep, indent=1))
            print("recorded", rec / "replay_check.json")
        else:   # a failing first check is not a record
            print("FAILED: nothing recorded")
        return ok
    old = json.loads((rec / "replay_check.json").read_text())
    # every recorded field, the trace counts included (a record predating a field still compares on the rest)
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
    errs = infra_errors(out)   # a "crash" row in results.tsv is a graded outcome (the edit broke training), not lost calls
    summ = {"run": name, "setup": {"method": "autoresearch", "task": "tinylm, 8 s CPU training budget",
                                   "llm": "claude haiku (research agent)", "max_experiments": 10},
            "stop_reason": res.stop_reason, "wall_s": round(time.time() - t0, 1), "trajectory": res.trajectory,
            "meta": res.meta, "usd_total": _usd(llm), "cache": {"loop_hits": llm.hits, "loop_misses": llm.misses},
            "infra_errors": errs, "clean": not errs}
    (out / "summary.json").write_text(json.dumps(summ, indent=1, default=str))
    print("usd", summ["usd_total"])
    if errs:
        print("INFRA_ERRORS", len(errs), *errs[:5], sep="\n  ")


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
