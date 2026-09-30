"""E15 (retry round 2, preregistered P5 in docs/methods/rrsi/claims-audit.md section 6): a prior method on
HarnessWorld, for claims L6, L8, L9, L31 (and the directional part of Q10/Q27).

Table 1 compares RRSI with prior harness-evolution methods; until now only our own ablation arms
existed. ``rsi.metaharness`` (the Meta-Harness package of this framework, used unchanged through its
public ``run``) is run on HarnessWorld with its faithful defaults: full-history view, no leakage screen,
every candidate scored once on the search split, output = the highest-score point of the (score,
tokens) Pareto frontier. Its proposer is :class:`HWMetaHarnessProposer` below, a HarnessWorld mock with
the SAME proposal distribution as the RRSI mock (same catalog, kind shares, fill rule, family bias from
the parent's lowest-scoring families, avoidance of mechanisms whose visible application regressed), and
Meta-Harness's parent rule (best by search score; the second slot may take a cheaper Pareto member).
Edit cap 4 per candidate (Meta-Harness itself has none; 4 = the default b_max).

Budget matched to RRSI: 20 iterations x 2 candidates, 2 trials per task -> 41 x 200 = 8,200 rollouts
(RRSI: 1 + 20 x 2 evaluations of 200). Worlds 0-99. The RRSI side is E2c's full arm (workspace preset,
same worlds), read from ``results/rrsi/e2c_ablations_workspace.json``.
``validate_in_subprocess=False``: HarnessWorld harnesses cannot hang, so the fork only costs time.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import RESULTS, paired, pmap, save, summarize  # noqa: E402

from rsi.core import Artifact  # noqa: E402
from rsi.domains.harnessworld import HarnessWorldMockLLM, ProposerProfile, make_domain  # noqa: E402
from rsi.metaharness import Config as MHConfig  # noqa: E402
from rsi.metaharness import run as mh_run  # noqa: E402
from rsi.metaharness.proposer import CandidateSpec, ProposalBatch, Proposer  # noqa: E402

EDIT_CAP = 4


class HWMetaHarnessProposer(Proposer):
    """Meta-Harness proposer mock for HarnessWorld (reads only the store view)."""

    def __init__(self, world, seed: int = 0, explore_frac: float = 0.5, history_compliance: float = 0.9) -> None:
        self.world = world
        self.seed = seed
        self.explore_frac = explore_frac
        self.history_compliance = history_compliance
        self.drawer = HarnessWorldMockLLM(world, ProposerProfile())      # the RRSI mock's _draw (shares, family bias)
        self.pp = self.drawer.pp

    def _read(self, view: dict) -> list[dict]:
        cands: dict[str, dict] = {}
        for path, text in view.items():
            if not path.startswith("candidates/"):
                continue
            _, name, rest = path.split("/", 2)
            c = cands.setdefault(name, {"name": name, "files": {}, "score": None, "cost": None, "families": {},
                                        "meta": {}})
            if rest.startswith("src/"):
                c["files"][rest[4:]] = text
            elif rest == "eval/search/scores.json":
                s = json.loads(text)
                c["score"], c["cost"], c["families"] = s.get("score"), s.get("context_cost"), s.get("families") or {}
            elif rest == "meta.json":
                c["meta"] = json.loads(text)
        return list(cands.values())

    def propose(self, *, iteration, view, k, brief, artifacts, seed=0) -> ProposalBatch:
        rng = random.Random(f"hw-mh|{self.seed}|{seed}|{iteration}")
        seen = self._read(view)
        batch = ProposalBatch()
        scored = sorted([c for c in seen if c["score"] is not None], key=lambda c: (-c["score"], c["cost"] or 0, c["name"]))
        if not scored:
            batch.error = "no scored candidate"
            return batch
        by = {c["name"]: c for c in seen}
        front = []
        if "frontier_val.json" in view:
            try:
                front = [p["system"] for p in json.loads(view["frontier_val.json"]).get("_pareto", [])
                         if p.get("system") in by]
            except (ValueError, TypeError):
                front = []
        regressed = set()                                    # mechanisms whose visible application lowered the score
        for c in seen:
            base = by.get(c["meta"].get("base_system", ""))
            if base and c["score"] is not None and base["score"] is not None and c["score"] < base["score"]:
                regressed.update(c["meta"].get("components") or [])
        avoid = {m for m in sorted(regressed) if rng.random() < self.history_compliance}
        out = []
        for slot in range(k):
            parent = scored[0]
            if slot % 2 == 1:
                fr = [by[n] for n in front if n in by and by[n]["score"] is not None]
                if len(fr) > 1 and rng.random() < self.explore_frac:
                    parent = fr[rng.randrange(1, len(fr))]
                elif len(scored) > 1 and rng.random() < 0.5:
                    parent = scored[1]
            present = {m.id: m.path for m in self.world.active(parent["files"])[0]}
            fams = sorted(parent["families"].items(), key=lambda kv: kv[1])
            top_fams = [f for f, _ in fams[:2]]
            n = EDIT_CAP if rng.random() < self.pp.fill_budget_p else rng.randint(1, EDIT_CAP)
            chosen = []
            while len(chosen) < n:
                m = self.drawer._draw(rng, present, [(x, False) for x in chosen], avoid, top_fams)
                if m is None:
                    break
                chosen.append(m)
            if not chosen:
                continue
            files = dict(parent["files"])
            for m in chosen:
                files[m.path] = m.file_text()
            ids = [m.id for m in chosen]
            out.append(CandidateSpec(f"i{iteration:02d}_{slot}_{'_'.join(ids)}", Artifact(files),
                                     hypothesis="; ".join(f"[{m.id}] {m.title}" for m in chosen),
                                     axis="exploitation" if slot == 0 else "exploration", components=ids,
                                     base_system=parent["name"], parents_read=[parent["name"]]))
        batch.candidates = out
        return batch


def one(job: dict) -> dict:
    seed = job["seed"]
    dom = make_domain(seed=seed)
    w = dom.world
    out = Path(tempfile.mkdtemp(prefix=f"e15_mh_{seed}_", dir=job["scratch"]))
    t0 = time.time()
    seed_art = Artifact(dom.seed_artifact().files, {"name": "seed"})
    cfg = MHConfig(iterations=job["T"], k=2, trials=2, objectives=("score", "context_cost"), cost_metric="tokens",
                   search_split="evolve", finalize=False, trace=False, shadow_monitor=False,
                   validate_in_subprocess=False, seed=seed)
    res = mh_run(dom, seed_art, config=cfg, out_dir=out, proposer=HWMetaHarnessProposer(w, seed=seed))
    h0, fin = res.baseline, res.best
    m = {"seed": seed, "label": "Meta-Harness", "wall_s": time.time() - t0}
    for split in ("evolve", "holdout", "ood"):
        e0, e1 = dom.expected(h0, split), dom.expected(fin, split)
        m[f"{split}_gain"] = e1["S"] - e0["S"]
    m["unseen_gain"] = 0.5 * (m["holdout_gain"] + m["ood_gain"])
    c0, c1 = dom.expected(h0, "evolve")["C"], dom.expected(fin, "evolve")["C"]
    m["token_ratio"] = c1 / c0
    store = res.loop.store
    s0 = store.scores("seed")["score"]
    s1 = store.scores(res.meta["best_system"])["score"]
    m["measured_gain"] = s1 - s0
    mechs, payload, _ = w.active(fin.files)
    m["n_mechanisms"] = len(mechs)
    m["steps_proxy"] = 1 + len(mechs)
    m["leaks_in_final"] = len(payload)
    m["kinds"] = {kk: [x.kind for x in mechs].count(kk) for kk in sorted({x.kind for x in mechs})}
    n_eval = n_leak_eval = 0
    for name in store.names():
        if name == "seed" or store.scores(name) is None:
            continue
        n_eval += 1
        base = (store.meta(name) if hasattr(store, "meta") else {}) or {}
        art = store.artifact(name)
        pa = set(w.active(art.files)[1])
        bname = base.get("base_system") if isinstance(base, dict) else None
        pb = set(w.active(store.artifact(bname).files)[1]) if bname and store.has(bname) else set()
        n_leak_eval += bool(pa - pb)
    m.update(n_evaluated=n_eval, evals_on_leaky=n_leak_eval, n_rollouts=(n_eval + 1) * 200)
    shutil.rmtree(out, ignore_errors=True)
    return m


def main():
    ap = argparse.ArgumentParser(description="E15: Meta-Harness on HarnessWorld vs RRSI")
    ap.add_argument("--seeds", type=int, default=100)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--T", type=int, default=20)
    ap.add_argument("--rrsi", default=str(RESULTS / "e2c_ablations_workspace.json"))
    a = ap.parse_args()
    scratch = Path(os.environ.get("RRSI_SCRATCH", tempfile.gettempdir())) / "rrsi_runs"
    scratch.mkdir(parents=True, exist_ok=True)
    rows = pmap(one, [{"seed": s, "T": a.T, "scratch": str(scratch)} for s in range(a.seeds)], a.workers)
    e2c = json.loads(Path(a.rrsi).read_text())
    keep = {"full": "RRSI (full)", "unregularized": "unregularized", "-proposal": "-proposal",
            "-acceptance": "-acceptance"}
    rr = [dict(r, label=keep[r["label"]], steps_proxy=1 + r["n_mechanisms"]) for r in e2c["rows"]
          if r["label"] in keep and r["seed"] < a.seeds]
    allrows = rows + rr
    metrics = ("measured_gain", "evolve_gain", "holdout_gain", "ood_gain", "token_ratio", "steps_proxy",
               "n_mechanisms", "leaks_in_final", "evals_on_leaky")
    summ = summarize(allrows, metrics=metrics)
    pd = {m: paired(allrows, "Meta-Harness", "RRSI (full)", m) for m in metrics}          # RRSI - MH
    methods = list(summ)
    ev = {x: summ[x]["measured_gain"]["mean"] for x in methods}
    ood = {x: summ[x]["ood_gain"]["mean"] for x in methods}
    checks = {
        "L6: RRSI - MH measured evolve CI < 0 (RRSI practises less)": pd["measured_gain"]["hi"] < 0,
        "L6: RRSI - MH OOD CI > 0 (RRSI transfers better)": pd["ood_gain"]["lo"] > 0,
        "L6 (all methods run): best practiser = worst transferer": max(ev, key=ev.get) == min(ood, key=ood.get),
        "L6 (all methods run): RRSI highest OOD": max(ood, key=ood.get) == "RRSI (full)",
        "L6 (all methods run): RRSI lowest measured evolve": min(ev, key=ev.get) == "RRSI (full)",
        "L8: RRSI steps proxy < MH (CI)": pd["steps_proxy"]["hi"] < 0,
        "L8: RRSI steps proxy > H_0 (= 3)": summ["RRSI (full)"]["steps_proxy"]["lo"] > 3,
        "L9: RRSI tokens < MH (CI)": pd["token_ratio"]["hi"] < 0,
        "L9: RRSI tokens > H_0": summ["RRSI (full)"]["token_ratio"]["lo"] > 1.0,
        "L31: MH keeps more leaky mechanisms than RRSI (RRSI - MH CI < 0)": pd["leaks_in_final"]["hi"] < 0,
        "L31: MH spends more evaluations on leaky candidates (RRSI - MH CI < 0)": pd["evals_on_leaky"]["hi"] < 0,
    }
    out = {"experiment": "E15 Meta-Harness rival on HarnessWorld (retry round 2: P5)",
           "config": {"seeds": a.seeds, "iterations": a.T, "k": 2, "trials": 2, "edit_cap": EDIT_CAP,
                      "rrsi_rows": a.rrsi, "mh_defaults": "full history, no leakage screen, best = top search score"},
           "summary": summ, "paired_rrsi_minus_mh": pd, "checks": checks, "rows": rows}
    save("e15_metaharness_rival", out)
    for lab, s in summ.items():
        print(lab, {m: round(s[m]["mean"] * (100 if m.endswith("gain") else 1), 2) for m in metrics})
    print({m: (round(v["mean_diff"], 4), round(v["lo"], 4), round(v["hi"], 4)) for m, v in pd.items()})
    for c, v in checks.items():
        print(("PASS " if v else "FAIL ") + c)


if __name__ == "__main__":
    main()
