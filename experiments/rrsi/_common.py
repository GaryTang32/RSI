"""Shared plumbing for the RRSI experiments (E0-E13).

* ``parse_args``: ``--llm sim|claude:haiku``, ``--seeds N``, ``--quick``, ``--workers``, ``--out``;
* ``search_llm``: the proposer/critic backend (HarnessWorld / AgentQA mocks offline, or a
  cached ``claude -p`` backend for live showcases);
* ``run_hw``: one HarnessWorld run for (seed, arm, overrides) -> ground-truth metrics;
* ``analyze_hw``: everything the experiments measure from a finished run directory;
* ``summarize`` / ``paired`` / ``save``: mean + 95% bootstrap CI (``rsi.core.stats``) and JSON output.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
import tempfile
import time
from multiprocessing import Pool
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from rsi.core import ArtifactStore, CachedLLM, ClaudeCLI, paired_diff_ci, spearman, summarize_runs  # noqa: E402
from rsi.domains.harnessworld import (CriticProfile, HarnessWorldMockLLM, ProposerProfile, WorldConfig,  # noqa: E402
                                      make_domain)
from rsi.rrsi import ARMS, Config, RegularizerSwitches, run  # noqa: E402

RESULTS = ROOT / "results" / "rrsi"
CACHE = ROOT / ".rsi_cache" / "rrsi"
SCRATCH = Path(os.environ.get("RRSI_SCRATCH", tempfile.gettempdir())) / "rrsi_runs"


def parse_args(desc: str, default_seeds: int = 50, extra: Optional[Callable] = None):
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
    return a


def search_llm(spec: str, world=None, proposer: Optional[dict] = None, critic: Optional[dict] = None,
               agentqa: bool = False):
    """Proposer + critic backend. ``sim`` -> domain mock; ``claude:<model>`` -> cached headless CLI."""
    if spec == "sim":
        if agentqa:
            from rsi.rrsi.mocks import AgentQAMockLLM, AgentQAMockProfile
            return AgentQAMockLLM(AgentQAMockProfile(**(proposer or {})))
        return HarnessWorldMockLLM(world, ProposerProfile(**(proposer or {})), CriticProfile(**(critic or {})))
    kind, _, model = spec.partition(":")
    if kind != "claude":
        raise SystemExit(f"unsupported --llm {spec!r}")
    CACHE.mkdir(parents=True, exist_ok=True)
    return CachedLLM(ClaudeCLI(model or "haiku", timeout_s=240), CACHE)


def switches(arm) -> RegularizerSwitches:
    if isinstance(arm, RegularizerSwitches):
        return arm
    if arm in ARMS:
        return ARMS[arm]()
    raise KeyError(arm)


# ----------------------------------------------------------------------------- analysis
def _true(dom, art, split, policy=None):
    return dom.expected(art, split, policy)


def analyze_hw(dom, out_dir: Path, res=None, weak=None) -> dict:
    """Ground-truth metrics of one finished HarnessWorld run."""
    store = ArtifactStore(out_dir / "artifacts")
    fr = json.loads((out_dir / "frontier.json").read_text())
    traj = fr["trajectory"]
    h0 = store.get(traj[0]["artifact_id"])
    fin = store.get(fr["incumbent"]["artifact_id"])
    w = dom.world
    m = {}
    for split in ("evolve", "holdout", "ood"):
        e0, e1 = _true(dom, h0, split), _true(dom, fin, split)
        m[f"{split}_H0"], m[f"{split}_final"], m[f"{split}_gain"] = e0["S"], e1["S"], e1["S"] - e0["S"]
    m["unseen_gain"] = 0.5 * (m["holdout_gain"] + m["ood_gain"])
    c0, c1 = _true(dom, h0, "evolve")["C"], _true(dom, fin, "evolve")["C"]
    m["tokens_H0"], m["tokens_final"], m["token_ratio"] = c0, c1, c1 / c0
    m["measured_final"] = traj[-1]["S"]
    m["measured_gain"] = traj[-1]["S"] - traj[0]["S"]
    m["winners_curse"] = m["measured_final"] - m["evolve_final"]
    if weak is not None:
        for split in ("holdout", "ood"):
            m[f"weak_{split}_gain"] = weak.expected(fin, split)["S"] - weak.expected(h0, split)["S"]
        m["weak_unseen_gain"] = 0.5 * (m["weak_holdout_gain"] + m["weak_ood_gain"])
    mechs, payload, _ = w.active(fin.files)
    kinds = [x.kind for x in mechs]
    m["n_mechanisms"] = len(mechs)
    m["kinds"] = {k: kinds.count(k) for k in sorted(set(kinds))}
    m["leaks_in_final"] = len(payload)
    ids = {x.id for x in mechs}
    m["dead_in_final"] = sum(1 for x in mechs if x.superseded_by and ids & set(x.superseded_by))
    m["structural_in_final"] = sum(1 for x in mechs if x.component in dom.structural_components)
    # curves: true scores of the incumbent after every round
    curve = {s: [] for s in ("evolve", "holdout", "ood")}
    curve["tokens"], curve["measured"] = [], []
    for x in traj:
        a = store.get(x["artifact_id"])
        for s in ("evolve", "holdout", "ood"):
            curve[s].append(_true(dom, a, s)["S"])
        curve["tokens"].append(_true(dom, a, "evolve")["C"])
        curve["measured"].append(x["S"])
    m["curve"] = curve
    # history / candidate-level statistics
    recs = [json.loads(l) for l in (out_dir / "history.jsonl").read_text().splitlines() if l.strip()]
    cands: dict = {}
    for r in recs:
        if r.get("edit_id"):
            cands.setdefault((r["t"], r["variant"]), []).append(r)
    n_eval = n_leak_eval = n_crit = n_acc = hitch = n_prune_acc = n_null_eval = n_null_gain = 0
    n_null_eval0 = n_null_gain0 = 0              # round 0 only: the reference H_0 measurement is unselected
    edit_pairs = []           # (recorded dS, true marginal) per measured edit
    by_round_inc = {x["t"]: store.get(x["artifact_id"]) for x in traj}
    for (t, v), rs in sorted(cands.items()):
        prep_p = out_dir / f"r{t}" / v / "prep.json"
        prep = json.loads(prep_p.read_text()) if prep_p.exists() else {}
        edits = prep.get("edits") or []
        outcome = rs[0]["outcome"]
        if outcome == "critic_reject":
            n_crit += 1
        if rs[0].get("delta_S") is None:
            continue
        n_eval += 1
        inc = by_round_inc.get(t)
        cand = store.get(prep["artifact_id"]) if prep.get("artifact_id") and store.has(prep["artifact_id"]) else None
        if cand is not None and inc is not None:
            _, pay_c, _ = w.active(cand.files)
            _, pay_i, _ = w.active(inc.files)
            if set(pay_c) - set(pay_i):
                n_leak_eval += 1
            base_S = _true(dom, inc, "evolve")["S"]
            harmful = False
            for e in edits:
                mid = e.get("mechanism_id")
                mm = w.catalog.get(mid) if mid else None
                if mm is None:
                    continue
                files = dict(inc.files)
                if e.get("prune"):
                    files.pop(mm.path, None)
                else:
                    files[mm.path] = mm.file_text()
                marg = w.expected(files, "evolve", dom.policy)["S"] - base_S
                edit_pairs.append((rs[0]["delta_S"], marg, mm.kind, bool(e.get("prune"))))
                if not e.get("prune") and mm.kind == "harmful":
                    harmful = True
            ek = [(w.catalog.get(x.get("mechanism_id") or ""), x.get("prune")) for x in edits]
            if ek and all(mm is not None and mm.kind == "null" and not pr for mm, pr in ek):
                n_null_eval += 1
                d_t = json.loads((out_dir / f"r{t}" / "directives.json").read_text())["delta"]
                n_null_gain += rs[0]["delta_S"] > d_t
                if t == 0:
                    n_null_eval0 += 1
                    n_null_gain0 += rs[0]["delta_S"] > d_t
            if rs[0]["accepted"]:
                n_acc += 1
                hitch += harmful
                n_prune_acc += sum(1 for e in edits if e.get("prune"))
    m.update({"n_evaluated": n_eval, "n_critic_reject": n_crit, "evals_on_leaky": n_leak_eval, "n_accepted": n_acc,
              "hitchhiker_rate": hitch / n_acc if n_acc else 0.0, "prune_accepted": n_prune_acc,
              "null_candidates": n_null_eval, "null_false_gain": n_null_gain,
              "null_candidates_r0": n_null_eval0, "null_false_gain_r0": n_null_gain0})
    if len(edit_pairs) >= 3:
        rec, tru = [p[0] for p in edit_pairs], [p[1] for p in edit_pairs]
        m["credit_corr"] = spearman(rec, tru) if np.std(rec) > 0 and np.std(tru) > 0 else float("nan")
    else:
        m["credit_corr"] = float("nan")
    comps = {r["component"] for r in recs if r.get("delta_S") is not None and r.get("component")}
    m["coverage"] = len(comps) / len(dom.components)
    acc = [r for r in recs if r.get("accepted") and r.get("edit_id")]
    m["accepted_structural"] = sum(1 for r in acc if r.get("component") in dom.structural_components)
    m["delta"] = json.loads((out_dir / "calibration.json").read_text())["delta"] if (out_dir / "calibration.json").exists() \
        else fr["config"].get("delta")
    if res is not None:
        m["usage_calls"] = {k: v.get("calls") for k, v in res.usage.items()}
        # tokens of the SEARCH roles (proposer, critic, analyst, digesters), i.e. the method's own LLM compute
        m["search_tokens"] = sum(v.get("total_tokens", 0) for k, v in res.usage.items()
                                 if k != "_total" and not k.startswith("task"))
        m["n_rollouts"] = res.meta.get("n_rollouts")
    return m


def run_hw(job: dict) -> dict:
    """One HarnessWorld run. ``job`` keys: seed, arm, cfg (Config overrides), world (WorldConfig
    overrides), proposer / critic (mock profile overrides), llm (spec), keep (bool), weak (bool)."""
    seed, arm = job["seed"], job["arm"]
    dom = make_domain(seed=seed, **job.get("world", {}))
    llm = search_llm(job.get("llm", "sim"), dom.world, job.get("proposer"), job.get("critic"))
    cfg = Config(**{"T": 20, "workers": 1, "seed": seed, **job.get("cfg", {})})
    out = Path(job.get("out_dir") or tempfile.mkdtemp(prefix=f"rrsi_{seed}_", dir=_scratch()))
    t0 = time.time()
    res = run(dom, dom.seed_artifact(), llm_propose=llm, config=cfg, out_dir=out, switches=switches(arm))
    _check_complete(res, job, out)
    weak = None
    if job.get("weak"):
        from rsi.domains.harnessworld import WEAK
        weak = dom.with_policy(WEAK)
    m = analyze_hw(dom, out, res, weak)
    m.update({"seed": seed, "arm": arm if isinstance(arm, str) else arm.name, "wall_s": time.time() - t0,
              "label": job.get("label", arm if isinstance(arm, str) else arm.name),
              "stop_reason": res.stop_reason, "rounds_settled": len(res.trajectory) - 1})
    if not job.get("keep"):
        shutil.rmtree(out, ignore_errors=True)
    else:
        m["out_dir"] = str(out)
    return m


def run_aq(job: dict) -> dict:
    """One AgentQA run with SimModel as the frozen task model. ``job``: seed, arm, cfg, llm,
    task_llm ("sim" | "claude:<model>"), weak_skill (transfer to SimModel(skill)), k_transfer."""
    from rsi.domains.agentqa import AgentQADomain, SimModel, make_suite
    from rsi.rrsi import paired_transfer
    seed, arm = job["seed"], job["arm"]
    suite = make_suite(seed=seed, **job.get("suite", {}))
    dom = AgentQADomain(suite)
    tspec = job.get("task_llm", "sim")
    task = SimModel(suite) if tspec == "sim" else CachedLLM(ClaudeCLI(tspec.partition(":")[2] or "haiku"), CACHE)
    llm = search_llm(job.get("llm", "sim"), agentqa=True, proposer=job.get("proposer"))
    cfg = Config(**{"T": 8, "workers": 4, "seed": seed, **job.get("cfg", {})})
    out = Path(job.get("out_dir") or tempfile.mkdtemp(prefix=f"rrsi_aq_{seed}_", dir=_scratch()))
    t0 = time.time()
    res = run(dom, AgentQADomain.seed_artifact(), llm_task=task, llm_propose=llm, config=cfg, out_dir=out,
              switches=switches(arm))
    _check_complete(res, job, out)
    k = job.get("k_transfer", 4)
    rep = paired_transfer(dom, task, {"H0": res.baseline, "final": res.best}, k=k, workers=4)
    m = {"seed": seed, "arm": arm, "label": job.get("label", arm), "wall_s": time.time() - t0,
         "stop_reason": res.stop_reason, "rounds_settled": len(res.trajectory) - 1,
         "measured_gain": res.trajectory[-1]["S"] - res.trajectory[0]["S"], "delta": res.meta.get("delta"),
         "files": sorted(res.best.files), "leaky_final": "memory/answers.json" in res.best,
         "usage_calls": {kk: v.get("calls") for kk, v in res.usage.items()}}
    for split in ("evolve", "holdout", "ood"):
        row = rep["splits"][split]
        m[f"{split}_H0"], m[f"{split}_final"] = row["H0"]["S"], row["final"]["S"]
        m[f"{split}_gain"] = row["final"]["S"] - row["H0"]["S"]
        m[f"{split}_tokens_ratio"] = row["final"]["C"] / row["H0"]["C"] if row["H0"]["C"] else float("nan")
    m["unseen_gain"] = 0.5 * (m["holdout_gain"] + m["ood_gain"])
    m["token_ratio"] = m["evolve_tokens_ratio"]
    if job.get("weak_skill"):
        weak = SimModel(suite, skill=job["weak_skill"], name="sim-weak")
        rw = paired_transfer(dom, weak, {"H0": res.baseline, "final": res.best}, splits=("holdout", "ood"), k=k,
                             workers=4)
        for split in ("holdout", "ood"):
            m[f"weak_{split}_gain"] = rw["splits"][split]["final"]["S"] - rw["splits"][split]["H0"]["S"]
        m["weak_unseen_gain"] = 0.5 * (m["weak_holdout_gain"] + m["weak_ood_gain"])
    if not job.get("keep"):
        shutil.rmtree(out, ignore_errors=True)
    else:
        m["out_dir"] = str(out)
    return m


def _check_complete(res, job: dict, out: Path) -> None:
    """A run that stopped early (3 consecutive round failures = the driver's infrastructure stop) must not
    be averaged in as if it were a finished search. Offline this is always a bug: fail loudly. Live runs
    keep the row (``stop_reason`` / ``rounds_settled`` are recorded) so a flaky backend is visible."""
    if res.stop_reason == "max_rounds":
        return
    errs = sorted(Path(out, "logs").glob("*.err")) if Path(out, "logs").exists() else []
    tail = errs[-1].read_text()[-2000:] if errs else ""
    msg = f"run {job.get('label', job.get('arm'))} seed {job.get('seed')} stopped early: {res.stop_reason}\n{tail}"
    if job.get("llm", "sim") == "sim":
        raise RuntimeError(msg)
    print("[warn] " + msg, flush=True)


def _scratch() -> str:
    SCRATCH.mkdir(parents=True, exist_ok=True)
    return str(SCRATCH)


def pmap(fn, jobs: list, workers: int = 3) -> list:
    if workers <= 1:
        return [fn(j) for j in jobs]
    with Pool(workers) as p:
        return p.map(fn, jobs, chunksize=1)


# ------------------------------------------------------------------------------ reporting
METRICS = ("measured_gain", "evolve_gain", "holdout_gain", "ood_gain", "unseen_gain", "token_ratio")


def summarize(rows: list, key: str = "label", metrics=METRICS) -> dict:
    out = {}
    for lab in dict.fromkeys(r[key] for r in rows):
        rs = [r for r in rows if r[key] == lab]
        out[lab] = {mname: summarize_runs([float(r[mname]) for r in rs if r.get(mname) is not None
                                           and not (isinstance(r[mname], float) and math.isnan(r[mname]))])
                    for mname in metrics}
    return out


def paired(rows: list, a: str, b: str, metric: str, key: str = "label") -> dict:
    """Paired (same seed) difference b - a with a bootstrap CI."""
    ra = {r["seed"]: r[metric] for r in rows if r[key] == a}
    rb = {r["seed"]: r[metric] for r in rows if r[key] == b}
    seeds = sorted(set(ra) & set(rb))
    return paired_diff_ci([ra[s] for s in seeds], [rb[s] for s in seeds])


def fmt(s: dict, scale: float = 100.0, nd: int = 1) -> str:
    return f"{s['mean'] * scale:+.{nd}f} [{s['lo'] * scale:+.{nd}f}, {s['hi'] * scale:+.{nd}f}]"


def strip_curves(rows: list) -> list:
    return [{k: v for k, v in r.items() if k != "curve"} for r in rows]


def mean_curves(rows: list, key: str = "label") -> dict:
    out = {}
    for lab in dict.fromkeys(r[key] for r in rows):
        rs = [r for r in rows if r[key] == lab and "curve" in r]
        if not rs:
            continue
        out[lab] = {c: np.mean([r["curve"][c] for r in rs], axis=0).tolist() for c in rs[0]["curve"]}
    return out


def save(name: str, payload: dict) -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    p = RESULTS / f"{name}.json"
    p.write_text(json.dumps(payload, indent=1, default=lambda o: float(o) if isinstance(o, np.floating) else str(o)))
    print(f"[saved] {p}", flush=True)
    return p


__all__ = ["parse_args", "search_llm", "run_hw", "analyze_hw", "pmap", "summarize", "paired", "fmt", "save",
           "strip_curves", "mean_curves", "RESULTS", "WorldConfig", "switches", "ROOT"]
