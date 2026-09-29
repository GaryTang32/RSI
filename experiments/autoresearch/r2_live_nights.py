"""R2-X5/X6 - live-agent nights for the program.md and "think harder" claims (retry round 2).

X5 (claims O6, O23): "Who improves the strategy: a person, by editing the instructions"; "you edit the
instructions the agent follows". Phase A: one design night under upstream's program (v1). A person
reads its results.tsv and edits ONLY program.md (v2 = v1 + a short notes section, file
``r2_program_v2.md``). Phase B: v1 vs v2 on fresh agent seeds 1-3, 5 experiments each, honest
fresh-seed re-eval of the baseline and the final best (``reeval_seeds=3``).

X6 (claim P28): "If you run out of ideas, think harder - ... try combining previous near-misses, try
more radical architectural changes." One night from a knob-tuned train.py (the best of the 28 Sep
scripted E1 night, seed 0), so the easy knob moves are used up; 8 experiments; every proposal is
classified K (constants only) / S (structural code change) / C (combination of earlier non-kept
settings) / R (repeat of an earlier non-kept version).

All: tinylm, 2 s wall-clock budget, hardened, default keep rule, live Claude Haiku 4.5 through
RewriteEditor (default settings, as every earlier live run), a fresh cache dir per night.
Preregistered in docs/methods/autoresearch/claims-audit.md section 5 (X5, X5b, X6).

Usage: python experiments/autoresearch/r2_live_nights.py design|eval|plateau|analyze [--arm v1|v2] [--seed N]
"""
from __future__ import annotations

from _common import ROOT, SCRATCH, ci, write  # noqa: I001

import argparse
import ast
import json
import math
import re
from pathlib import Path

import numpy as np

from rsi.autoresearch import Config, run
from rsi.core import CachedLLM, ClaudeCLI
from rsi.core.artifact import Artifact
from rsi.domains.tinylm import TinyLMTask

HERE = Path(__file__).resolve().parent
V2_PATH = HERE / "r2_program_v2.md"
LIVE = SCRATCH / "r2_live"
SPEND_CAP = 3.90
PLATEAU_START = {"CONTEXT": 4, "ACTIVATION": "relu", "BATCH_SIZE": 16, "LR": 0.024, "INIT_SCALE": 0.7, "SEED": 397}
CONST_RE = re.compile(r"^([A-Z_][A-Z0-9_]*)\s*=\s*(.+?)\s*(#.*)?$")


# ------------------------------------------------------------------ spend guard
def spent_usd(roots=None) -> float:
    """Dollars actually spent by every live call of this round: the cache entries (``cache*/<xx>/<hash>.json``)
    under the scratch root and any extra roots in ``$RSI_R2_SPEND_ROOTS`` (colon-separated)."""
    import os

    roots = roots or [SCRATCH] + [Path(p) for p in os.environ.get("RSI_R2_SPEND_ROOTS", "").split(":") if p]
    tot = float(os.environ.get("RSI_R2_EXTRA_USD", "0"))     # AgentEditor calls bypass the cache (core request 7)
    for r in roots:
        for cdir in Path(r).rglob("cache*"):
            if not cdir.is_dir():
                continue
            for p in cdir.glob("*/*.json"):
                try:
                    tot += float(json.loads(p.read_text())["usage"]["cost_usd"])
                except (ValueError, KeyError, OSError, TypeError):
                    pass
    return tot


def guard(expected: float) -> None:
    s = spent_usd()
    print(f"live spend so far ${s:.3f}; this night up to ${expected:.2f}", flush=True)
    if s + expected > SPEND_CAP:
        raise SystemExit(f"spend cap: ${s:.2f} + ${expected:.2f} > ${SPEND_CAP:.2f}; night not started")


# ------------------------------------------------------------------ nights
def setk(text: str, k: str, v) -> str:
    out, n = re.subn(rf"^{k}(\s*=\s*)[^#\n]+?(\s*(#.*)?)$", lambda m: f"{k}{m.group(1)}{v!r}{m.group(2)}", text,
                     count=1, flags=re.M)
    assert n == 1, k
    return out


def night(name: str, *, program: str, seed: int, n: int, start: dict | None = None, reeval: int = 0,
          max_usd: float = 1.0) -> dict:
    task = TinyLMTask(budget_s=2.0)
    seed_art = None
    if start:
        files = dict(task.seed_artifact().files)
        t = files["train.py"]
        for k, v in start.items():
            t = setk(t, k, v)
        seed_art = Artifact({**files, "train.py": t})
    llm = CachedLLM(ClaudeCLI("haiku", timeout_s=300), LIVE / f"cache_{name}")
    cfg = Config(max_experiments=n, seed=seed, program=program, reeval_seeds=reeval, max_usd=max_usd, plot=False,
                 overwrite=True, tag=f"r2-{name}")
    res = run(task, seed_art, llm_propose=llm, config=cfg, out_dir=LIVE / name)
    return {"name": name, "results_tsv": (LIVE / name / "results.tsv").read_text(), "reeval": res.meta.get("reeval"),
            "usage": (res.usage or {}).get("_total"), "stop_reason": res.meta.get("stop_reason")}


# ------------------------------------------------------------------ classification
def constants(src: str) -> dict:
    out = {}
    for line in src.splitlines():
        m = CONST_RE.match(line)
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


def structure(src: str) -> str:
    """ast dump of train.py without its module docstring and without top-level ALL_CAPS assignments."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return "SYNTAX_ERROR:" + src
    body = []
    for i, node in enumerate(tree.body):
        if i == 0 and isinstance(node, ast.Expr) and isinstance(getattr(node, "value", None), ast.Constant):
            continue
        if isinstance(node, ast.Assign) and all(isinstance(t, ast.Name) and t.id.isupper() for t in node.targets):
            continue
        body.append(node)
    return ast.dump(ast.Module(body=body, type_ignores=[]))


def classify(d: Path) -> dict:
    """K/S/C/R per proposal from the trace's proposal events (the exact parent and candidate files)."""
    events = [json.loads(l) for l in (d / "trace.jsonl").read_text().splitlines() if l.strip()]
    ledger = [json.loads(l) for l in (d / "ledger.jsonl").read_text().splitlines() if l.strip()]
    from rsi.core.ledger import ArtifactStore

    store = ArtifactStore(d / "artifacts")
    by_round = {}
    for nd in ledger:
        if nd.get("kind") == "candidate":
            by_round.setdefault(nd["round"], []).append(nd)
    decisions = [{"round": e["round"], **e["data"]} for e in events if e.get("kind") == "decision"]
    props = [e["data"] for e in events if e.get("kind") == "proposal"
             and str(e["data"].get("stage", "")).startswith("propose")]
    base_train = None
    for nd in ledger:
        if nd.get("kind") == "baseline":
            base_train = store.get(nd["artifact_id"]).files["train.py"]
    tried_nonkept: list[dict] = []
    tried_versions: set = set()
    seq = []
    streak, stuck_at = 0, None
    parent_train = base_train
    for dec in decisions:
        r = dec.get("round")
        status = dec.get("status")
        nds = [x for x in by_round.get(r, []) if x.get("status") == status] or by_round.get(r, [])
        nd = nds[-1] if nds else None
        row = {"round": r, "status": status, "description": dec.get("description")}
        if nd is not None and nd.get("artifact_id"):
            try:
                cand = store.get(nd["artifact_id"]).files["train.py"]
            except Exception:  # noqa: BLE001
                cand = None
        else:
            cand = None
        if cand is not None and parent_train is not None:
            c0, c1 = constants(parent_train), constants(cand)
            changed = {k: c1.get(k) for k in set(c0) | set(c1) if c0.get(k) != c1.get(k)}
            s_changed = structure(parent_train) != structure(cand)
            prior_vals = [t["changed"] for t in tried_nonkept]
            reused = [k for k, v in changed.items() if any(pv.get(k) == v for pv in prior_vals)]
            desc = (row["description"] or "").lower()
            combo = (len(changed) >= 2 and len(reused) >= 2) or any(w in desc for w in ("combin", "near-miss", "near miss"))
            row.update({"changed_constants": changed, "structural": s_changed, "combination": combo,
                        "reused_nonkept_settings": reused, "repeat": cand in tried_versions,
                        "kind": "S" if s_changed else "K"})
            if status in ("discard", "crash", "rejected"):
                tried_nonkept.append({"changed": changed})
                tried_versions.add(cand)
        else:
            row.update({"kind": "none", "structural": False, "combination": False, "repeat": status == "duplicate"})
        if status == "keep" and cand is not None:
            parent_train = cand
        if status in ("discard", "crash", "rejected", "invalid", "duplicate"):
            streak += 1
        elif status == "keep":
            streak = 0
        if stuck_at is None and streak >= 4:
            stuck_at = r
        seq.append(row)
    replies = [p.get("reply", "") for p in props]
    ask = [i for i, t in enumerate(replies) if re.search(r"should I (continue|keep going|stop)|would you like|"
                                                         r"let me know|shall I|do you want", t or "", re.I)]
    after = [s for s in seq if stuck_at is not None and s["round"] > stuck_at]
    before = [s for s in seq if stuck_at is None or s["round"] <= stuck_at]
    return {"proposals": seq, "stuck_at": stuck_at, "n_invalid": sum(s["status"] == "invalid" for s in seq),
            "asking_turns": ask, "after_stuck": {"n": len(after), "C": sum(s["combination"] for s in after),
                                                 "S": sum(s["structural"] for s in after),
                                                 "R": sum(bool(s["repeat"]) for s in after)},
            "before_stuck": {"n": len(before), "C": sum(s["combination"] for s in before),
                             "S": sum(s["structural"] for s in before), "R": sum(bool(s["repeat"]) for s in before)}}


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("phase", choices=("design", "eval", "plateau", "analyze"))
    ap.add_argument("--arm", choices=("v1", "v2"))
    ap.add_argument("--seed", type=int)
    ap.add_argument("--experiments", type=int)
    a = ap.parse_args()
    if a.phase == "design":
        guard(0.9)
        out = night("design_v1_s100", program="upstream", seed=100, n=a.experiments or 10, max_usd=0.9)
        print(out["results_tsv"])
    elif a.phase == "eval":
        guard(0.45)
        prog = "upstream" if a.arm == "v1" else str(V2_PATH)
        out = night(f"eval_{a.arm}_s{a.seed}", program=prog, seed=a.seed, n=a.experiments or 5, reeval=3, max_usd=0.45)
        print(out["results_tsv"], json.dumps(out["reeval"], default=str)[:800])
    elif a.phase == "plateau":
        n = a.experiments or 8
        guard(0.09 * n)
        out = night("plateau_v1_s200", program="upstream", seed=200, n=n, start=PLATEAU_START, max_usd=0.09 * n)
        print(out["results_tsv"])
    else:
        analyze()


def honest(d: Path) -> dict | None:
    s = json.loads((d / "summary.json").read_text())
    rv = s.get("reeval") or (s.get("meta") or {}).get("reeval")
    if not rv:
        return None
    b, f = rv["baseline"]["values"], rv["final"]["values"]
    return {"baseline": b, "final": f, "improvement": float(np.mean(b) - np.mean(f))}


def analyze() -> None:
    from scipy import stats

    out = {"spend_usd": spent_usd(), "nights": {}}
    for d in sorted(LIVE.iterdir()):
        if not d.is_dir() or d.name.startswith("cache_") or not (d / "ledger.jsonl").exists():
            continue
        entry = {"classification": classify(d), "results_tsv": (d / "results.tsv").read_text()}
        try:
            entry["honest"] = honest(d)
        except (OSError, ValueError, KeyError):
            entry["honest"] = None
        out["nights"][d.name] = entry
    ev = {arm: [out["nights"][k]["honest"]["improvement"] for k in out["nights"]
                if k.startswith(f"eval_{arm}_") and out["nights"][k]["honest"]] for arm in ("v1", "v2")}
    if all(len(v) >= 2 for v in ev.values()):
        t = stats.ttest_ind(ev["v2"], ev["v1"], equal_var=False, alternative="greater")
        out["x5_primary"] = {"v1": ev["v1"], "v2": ev["v2"], "mean_v1": float(np.mean(ev["v1"])),
                             "mean_v2": float(np.mean(ev["v2"])), "diff": float(np.mean(ev["v2"]) - np.mean(ev["v1"])),
                             "welch_t": float(t.statistic), "p_one_sided": float(t.pvalue),
                             "pass": bool(np.mean(ev["v2"]) > np.mean(ev["v1"]) and t.pvalue < 0.05)}
    write("r2_live_nights", out)
    print(json.dumps({k: v for k, v in out.items() if k != "nights"}, indent=1, default=str))
    for k, v in out["nights"].items():
        c = v["classification"]
        print(k, "stuck_at", c["stuck_at"], "after", c["after_stuck"], "before", c["before_stuck"],
              "invalid", c["n_invalid"], "ask", c["asking_turns"], "honest", v["honest"] and v["honest"]["improvement"])


if __name__ == "__main__":
    main()
