"""Shared plumbing for the RRSI experiments: CLI, search backends, HarnessWorld / AgentQA runs, stats and output."""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
import tempfile
import time
from collections import Counter
from multiprocessing import Pool
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from rsi.core import ArtifactStore, CachedLLM, ClaudeCLI, paired_diff_ci, spearman, summarize_runs  # noqa: E402
from rsi.domains.harnessworld import (WEAK, CriticProfile, HarnessWorldMockLLM, ProposerProfile,  # noqa: E402
                                      make_domain)
from rsi.rrsi import ARMS, Config, RegularizerSwitches, run  # noqa: E402

RESULTS = ROOT / "results" / "rrsi"
CACHE = ROOT / ".rsi_cache" / "rrsi"
SCRATCH = Path(os.environ.get("RRSI_SCRATCH", tempfile.gettempdir())) / "rrsi_runs"
SPLITS = ("evolve", "holdout", "ood")
METRICS = ("measured_gain", "evolve_gain", "holdout_gain", "ood_gain", "unseen_gain", "token_ratio")


def parse_args(desc: str, default_seeds: int = 50, extra: Optional[Callable] = None) -> argparse.Namespace:
    """The E0-E13 CLI; ``a.procs`` is ``--workers`` offline and 1 with a live LLM."""
    ap = argparse.ArgumentParser(description=desc)
    ap.add_argument("--llm", default="sim", help="sim (offline mocks) | claude:<model> (live, cached)")
    ap.add_argument("--seeds", type=int, default=None, help=f"number of seeds (default {default_seeds}; quick: 4)")
    ap.add_argument("--quick", action="store_true", help="few seeds / rounds for a fast check")
    ap.add_argument("--workers", type=int, default=3, help="parallel processes (runs)")
    ap.add_argument("--out", default=None, help="output JSON path")
    if extra:
        extra(ap)
    a = ap.parse_args()
    if a.seeds is None:
        a.seeds = 4 if a.quick else default_seeds
    if a.llm != "sim" and a.seeds > 3 and not a.quick:
        print("[note] live LLM: consider --seeds 1 --quick to bound cost", flush=True)
    a.procs = a.workers if a.llm == "sim" else 1
    return a


def retry_args(desc: str, seeds: int = 100, T: Optional[int] = 20) -> argparse.Namespace:
    """The retry-round CLI: ``--seeds``, ``--workers`` (2), ``--T``."""
    ap = argparse.ArgumentParser(description=desc)
    ap.add_argument("--seeds", type=int, default=seeds)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--T", type=int, default=T)
    return ap.parse_args()


def search_llm(spec: str, world=None, proposer: Optional[dict] = None, critic: Optional[dict] = None,
               agentqa: bool = False, cache_dir: Optional[Path] = None):
    """Proposer + critic backend: ``sim`` -> the domain mock, ``claude:<model>`` -> cached headless CLI."""
    if spec == "sim":
        if agentqa:
            from rsi.rrsi.mocks import AgentQAMockLLM, AgentQAMockProfile
            return AgentQAMockLLM(AgentQAMockProfile(**(proposer or {})))
        return HarnessWorldMockLLM(world, ProposerProfile(**(proposer or {})), CriticProfile(**(critic or {})))
    kind, _, model = spec.partition(":")
    if kind != "claude":
        raise SystemExit(f"unsupported --llm {spec!r}")
    cache = Path(cache_dir) if cache_dir else CACHE
    cache.mkdir(parents=True, exist_ok=True)
    return CachedLLM(ClaudeCLI(model or "haiku", timeout_s=240), cache)


def switches(arm) -> RegularizerSwitches:
    return arm if isinstance(arm, RegularizerSwitches) else ARMS[arm]()


def scratch_dir() -> str:
    SCRATCH.mkdir(parents=True, exist_ok=True)
    return str(SCRATCH)


def analyze_hw(dom, out_dir: Path, res=None, weak=None) -> dict:
    """Ground-truth metrics of one finished HarnessWorld run."""
    store = ArtifactStore(out_dir / "artifacts")
    fr = json.loads((out_dir / "frontier.json").read_text())
    traj = fr["trajectory"]
    arts = [store.get(x["artifact_id"]) for x in traj]
    h0, fin = arts[0], store.get(fr["incumbent"]["artifact_id"])
    w = dom.world
    m = {}
    for split in SPLITS:
        e0, e1 = dom.expected(h0, split)["S"], dom.expected(fin, split)["S"]
        m[f"{split}_H0"], m[f"{split}_final"], m[f"{split}_gain"] = e0, e1, e1 - e0
    m["unseen_gain"] = 0.5 * (m["holdout_gain"] + m["ood_gain"])
    c0, c1 = dom.expected(h0, "evolve")["C"], dom.expected(fin, "evolve")["C"]
    m["tokens_H0"], m["tokens_final"], m["token_ratio"] = c0, c1, c1 / c0
    m["measured_final"] = traj[-1]["S"]
    m["measured_gain"] = traj[-1]["S"] - traj[0]["S"]
    m["winners_curse"] = m["measured_final"] - m["evolve_final"]
    if weak is not None:
        for split in ("holdout", "ood"):
            m[f"weak_{split}_gain"] = weak.expected(fin, split)["S"] - weak.expected(h0, split)["S"]
        m["weak_unseen_gain"] = 0.5 * (m["weak_holdout_gain"] + m["weak_ood_gain"])
    mechs, payload, _ = w.active(fin.files)
    ids = {x.id for x in mechs}
    m["n_mechanisms"] = len(mechs)
    m["kinds"] = dict(sorted(Counter(x.kind for x in mechs).items()))
    m["leaks_in_final"] = len(payload)
    m["dead_in_final"] = sum(1 for x in mechs if x.superseded_by and ids & set(x.superseded_by))
    m["structural_in_final"] = sum(1 for x in mechs if x.component in dom.structural_components)
    m["curve"] = {s: [dom.expected(a, s)["S"] for a in arts] for s in SPLITS}
    m["curve"]["tokens"] = [dom.expected(a, "evolve")["C"] for a in arts]
    m["curve"]["measured"] = [x["S"] for x in traj]
    recs = [json.loads(line) for line in (out_dir / "history.jsonl").read_text().splitlines() if line.strip()]
    first: dict = {}                       # all records of one candidate share its outcome and dS
    for r in recs:
        if r.get("edit_id"):
            first.setdefault((r["t"], r["variant"]), r)
    inc_at = {x["t"]: a for x, a in zip(traj, arts)}
    n = Counter()
    credit = []                            # (recorded dS, true marginal effect) per measured edit
    for (t, v), r in sorted(first.items()):
        prep_p = out_dir / f"r{t}" / v / "prep.json"
        prep = json.loads(prep_p.read_text()) if prep_p.exists() else {}
        edits = prep.get("edits") or []
        n["crit"] += r["outcome"] == "critic_reject"
        if r.get("delta_S") is None:
            continue
        n["eval"] += 1
        inc, aid = inc_at.get(t), prep.get("artifact_id")
        cand = store.get(aid) if aid and store.has(aid) else None
        if cand is None or inc is None:
            continue
        n["leak_eval"] += bool(set(w.active(cand.files)[1]) - set(w.active(inc.files)[1]))
        base_S = dom.expected(inc, "evolve")["S"]
        ek = [(w.catalog.get(e.get("mechanism_id") or ""), e.get("prune")) for e in edits]
        for mm, prune in ek:
            if mm is None:
                continue
            files = dict(inc.files)
            if prune:
                files.pop(mm.path, None)
            else:
                files[mm.path] = mm.file_text()
            credit.append((r["delta_S"], w.expected(files, "evolve", dom.policy)["S"] - base_S))
        if ek and all(mm is not None and mm.kind == "null" and not prune for mm, prune in ek):
            d_t = json.loads((out_dir / f"r{t}" / "directives.json").read_text())["delta"]
            n["null_eval"] += 1
            n["null_gain"] += r["delta_S"] > d_t
            if t == 0:                     # round 0 compares against H_0's unselected measurement
                n["null_eval0"] += 1
                n["null_gain0"] += r["delta_S"] > d_t
        if r["accepted"]:
            n["acc"] += 1
            n["hitch"] += any(mm is not None and not prune and mm.kind == "harmful" for mm, prune in ek)
            n["prune_acc"] += sum(1 for e in edits if e.get("prune"))
    m.update({"n_evaluated": n["eval"], "n_critic_reject": n["crit"], "evals_on_leaky": n["leak_eval"],
              "n_accepted": n["acc"], "hitchhiker_rate": n["hitch"] / n["acc"] if n["acc"] else 0.0,
              "prune_accepted": n["prune_acc"], "null_candidates": n["null_eval"], "null_false_gain": n["null_gain"],
              "null_candidates_r0": n["null_eval0"], "null_false_gain_r0": n["null_gain0"]})
    rec, tru = [p[0] for p in credit], [p[1] for p in credit]
    ok = len(credit) >= 3 and np.std(rec) > 0 and np.std(tru) > 0
    m["credit_corr"] = spearman(rec, tru) if ok else float("nan")
    comps = {r["component"] for r in recs if r.get("delta_S") is not None and r.get("component")}
    m["coverage"] = len(comps) / len(dom.components)
    m["accepted_structural"] = sum(1 for r in recs if r.get("accepted") and r.get("edit_id")
                                   and r.get("component") in dom.structural_components)
    cal = out_dir / "calibration.json"
    m["delta"] = json.loads(cal.read_text())["delta"] if cal.exists() else fr["config"].get("delta")
    if res is not None:
        m["usage_calls"] = {k: v.get("calls") for k, v in res.usage.items()}
        # tokens of the search roles only (proposer, critic, analyst, digesters)
        m["search_tokens"] = sum(v.get("total_tokens", 0) for k, v in res.usage.items()
                                 if k != "_total" and not k.startswith("task"))
        m["n_rollouts"] = res.meta.get("n_rollouts")
    return m


def _check_complete(res, job: dict, out: Path) -> None:
    """An early stop (3 failed rounds) is a bug offline: raise; live, keep the row and warn."""
    if res.stop_reason == "max_rounds":
        return
    errs = sorted((out / "logs").glob("*.err"))
    tail = errs[-1].read_text()[-2000:] if errs else ""
    msg = f"run {job.get('label', job.get('arm'))} seed {job.get('seed')} stopped early: {res.stop_reason}\n{tail}"
    if job.get("llm", "sim") == "sim":
        raise RuntimeError(msg)
    print("[warn] " + msg, flush=True)


def _finish(m: dict, job: dict, out: Path) -> dict:
    if job.get("keep"):
        m["out_dir"] = str(out)
    else:
        shutil.rmtree(out, ignore_errors=True)
    return m


def run_hw(job: dict) -> dict:
    """One HarnessWorld run. ``job``: seed, arm, label, llm, cfg / world / proposer / critic overrides, keep, weak."""
    seed, arm = job["seed"], job["arm"]
    dom = make_domain(seed=seed, **job.get("world", {}))
    llm = search_llm(job.get("llm", "sim"), dom.world, job.get("proposer"), job.get("critic"))
    cfg = Config(**{"T": 20, "workers": 1, "seed": seed, **job.get("cfg", {})})
    out = Path(tempfile.mkdtemp(prefix=f"rrsi_{seed}_", dir=scratch_dir()))
    t0 = time.time()
    res = run(dom, dom.seed_artifact(), llm_propose=llm, config=cfg, out_dir=out, switches=switches(arm))
    _check_complete(res, job, out)
    m = analyze_hw(dom, out, res, dom.with_policy(WEAK) if job.get("weak") else None)
    name = arm if isinstance(arm, str) else arm.name
    m.update({"seed": seed, "arm": name, "wall_s": time.time() - t0, "label": job.get("label", name),
              "stop_reason": res.stop_reason, "rounds_settled": len(res.trajectory) - 1,
              # "heuristic" (deterministic clusterer, offline default) or "llm" (digesters + aggregator)
              "analyst_mode": res.meta.get("analyst_mode")})
    return _finish(m, job, out)


def run_aq(job: dict) -> dict:
    """One AgentQA run, SimModel as the frozen task model. ``job``: seed, arm, label, llm, cfg, weak_skill."""
    from rsi.domains.agentqa import AgentQADomain, SimModel, make_suite
    from rsi.rrsi import paired_transfer
    seed, arm = job["seed"], job["arm"]
    suite = make_suite(seed=seed)
    dom, task = AgentQADomain(suite), SimModel(suite)
    llm = search_llm(job.get("llm", "sim"), agentqa=True)
    cfg = Config(**{"T": 8, "workers": 4, "seed": seed, **job.get("cfg", {})})
    out = Path(tempfile.mkdtemp(prefix=f"rrsi_aq_{seed}_", dir=scratch_dir()))
    t0 = time.time()
    res = run(dom, AgentQADomain.seed_artifact(), llm_task=task, llm_propose=llm, config=cfg, out_dir=out,
              switches=switches(arm))
    _check_complete(res, job, out)
    harnesses = {"H0": res.baseline, "final": res.best}
    rep = paired_transfer(dom, task, harnesses, k=4, workers=4)["splits"]
    m = {"seed": seed, "arm": arm, "label": job.get("label", arm), "wall_s": time.time() - t0,
         "stop_reason": res.stop_reason, "rounds_settled": len(res.trajectory) - 1,
         "analyst_mode": res.meta.get("analyst_mode"),
         "measured_gain": res.trajectory[-1]["S"] - res.trajectory[0]["S"], "delta": res.meta.get("delta"),
         "files": sorted(res.best.files), "leaky_final": "memory/answers.json" in res.best,
         "usage_calls": {k: v.get("calls") for k, v in res.usage.items()}}
    for split in SPLITS:
        h0, fin = rep[split]["H0"], rep[split]["final"]
        m[f"{split}_H0"], m[f"{split}_final"] = h0["S"], fin["S"]
        m[f"{split}_gain"] = fin["S"] - h0["S"]
        m[f"{split}_tokens_ratio"] = fin["C"] / h0["C"] if h0["C"] else float("nan")
    m["unseen_gain"] = 0.5 * (m["holdout_gain"] + m["ood_gain"])
    m["token_ratio"] = m["evolve_tokens_ratio"]
    if job.get("weak_skill"):
        weak = SimModel(suite, skill=job["weak_skill"], name="sim-weak")
        rw = paired_transfer(dom, weak, harnesses, splits=("holdout", "ood"), k=4, workers=4)["splits"]
        for split in ("holdout", "ood"):
            m[f"weak_{split}_gain"] = rw[split]["final"]["S"] - rw[split]["H0"]["S"]
        m["weak_unseen_gain"] = 0.5 * (m["weak_holdout_gain"] + m["weak_ood_gain"])
    return _finish(m, job, out)


def agentqa_rows(a, arms: list, **extra) -> tuple[int, list]:
    """The AgentQA arms of E1 / E12: (number of suite seeds, rows)."""
    n = min(5, a.seeds) if a.llm == "sim" else 1
    jobs = [{"seed": s, "arm": arm, "label": arm, "cfg": {"T": 4 if a.quick else 8}, "llm": a.llm, **extra}
            for s in range(n) for arm in arms]
    return n, pmap(run_aq, jobs, min(2, a.procs))


def pmap(fn, jobs: list, workers: int = 3) -> list:
    if workers <= 1:
        return [fn(j) for j in jobs]
    with Pool(workers) as p:
        return p.map(fn, jobs, chunksize=1)


def _finite(v) -> bool:
    return v is not None and not (isinstance(v, float) and math.isnan(v))


def summarize(rows: list, key: str = "label", metrics=METRICS) -> dict:
    """Per label and metric: mean + 95% bootstrap CI over the rows where the metric is present and not NaN."""
    return {lab: {m: summarize_runs([float(r[m]) for r in rows if r[key] == lab and _finite(r.get(m))])
                  for m in metrics} for lab in dict.fromkeys(r[key] for r in rows)}


def paired(rows: list, a: str, b: str, metric: str, key: str = "label") -> dict:
    """Paired (same seed) difference b - a with a bootstrap CI."""
    ra = {r["seed"]: r[metric] for r in rows if r[key] == a}
    rb = {r["seed"]: r[metric] for r in rows if r[key] == b}
    seeds = sorted(set(ra) & set(rb))
    return paired_diff_ci([ra[s] for s in seeds], [rb[s] for s in seeds])


def pairs(rows: list, a: str, b: str, metrics, key: str = "label") -> dict:
    return {m: paired(rows, a, b, m, key) for m in metrics}


def fmt(s: dict, scale: float = 100.0, nd: int = 1) -> str:
    return f"{s['mean'] * scale:+.{nd}f} [{s['lo'] * scale:+.{nd}f}, {s['hi'] * scale:+.{nd}f}]"


def fmt_ratio(s: dict, unit: str = "") -> str:
    return f"{s['mean']:.2f}{unit} [{s['lo']:.2f}, {s['hi']:.2f}]"


def ci_pts(x: dict, scale: float = 100, nd: int = 2) -> tuple:
    return round(x["mean_diff"] * scale, nd), round(x["lo"] * scale, nd), round(x["hi"] * scale, nd)


def verdict(checks: dict, fail: str = "PARTIAL - not met: ") -> str:
    return "REPRODUCED" if all(checks.values()) else fail + "; ".join(k for k, v in checks.items() if not v)


def print_means(summ: dict, metrics=None, nd: int = 3) -> None:
    for lab, s in summ.items():
        print(lab, {m: round(s[m]["mean"], nd) for m in metrics or s})


def strip_curves(rows: list) -> list:
    return [{k: v for k, v in r.items() if k != "curve"} for r in rows]


def mean_curves(rows: list, key: str = "label") -> dict:
    out = {}
    for lab in dict.fromkeys(r[key] for r in rows):
        rs = [r for r in rows if r[key] == lab and "curve" in r]
        if rs:
            out[lab] = {c: np.mean([r["curve"][c] for r in rs], axis=0).tolist() for c in rs[0]["curve"]}
    return out


def save(name: str, payload: dict) -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    p = RESULTS / f"{name}.json"
    p.write_text(json.dumps(payload, indent=1, default=lambda o: float(o) if isinstance(o, np.floating) else str(o)))
    print(f"[saved] {p}", flush=True)
    return p


def figure(name: str, draw: Callable, ncols: int = 1, **subplots) -> None:
    """Save ``draw(ax)`` as results/rrsi/<name>.png; a plotting error only skips the figure."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(1, ncols, **subplots)
        draw(ax)
        fig.tight_layout()
        fig.savefig(RESULTS / f"{name}.png", dpi=110)
    except Exception as e:  # noqa: BLE001
        print("figure skipped:", e)
