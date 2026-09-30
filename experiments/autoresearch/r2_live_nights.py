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

Usage: python experiments/autoresearch/r2_live_nights.py design|eval|plateau|stuck|analyze [--arm v1|v2] [--seed N]
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


# ------------------------------------------------------------------ X6b: a live agent at a replayed stuck point
def tail_nonkeeps(results_tsv: str) -> int:
    """Number of consecutive non-keep rows (discard/crash) at the end of a results.tsv."""
    n = 0
    for line in reversed([l for l in results_tsv.strip().splitlines()[1:] if l.strip()]):
        cols = line.split("\t")
        if len(cols) >= 4 and cols[3] in ("discard", "crash"):
            n += 1
        else:
            break
    return n


class StuckSwitchAgent:
    """X6b (preregistered review follow-up): the scripted greedy agent proposes until results.tsv ends in
    >= ``stuck_after`` consecutive non-keeps (the X6 stuck point); from then on every proposal and crash fix
    comes from the live agent, and after ``n_live`` live proposals a STOP file ends the night."""

    name = "stuck-switch"

    def __init__(self, scripted, live, *, n_live: int, stop_dir: Path, stuck_after: int = 4) -> None:
        # only ``proposer`` is exposed: agent_llms() meters ``proposer`` and ``fixer`` separately, so exposing the
        # same live agent as both double-counted its spend in the X6b night (usage 0.413 vs 0.207 in the cache),
        # and max_usd stopped the night after 3 of the 4 preregistered live proposals
        self.scripted, self.proposer = scripted, live
        self.n_live, self.stop_dir, self.stuck_after = n_live, Path(stop_dir), stuck_after
        self.switched_at = None
        self.live_proposals = 0

    def propose(self, ctx):
        if self.switched_at is None and tail_nonkeeps(ctx.results_tsv) >= self.stuck_after:
            self.switched_at = ctx.experiment
        if self.switched_at is None:
            return self.scripted.propose(ctx)
        self.live_proposals += 1
        if self.live_proposals >= self.n_live:
            self.stop_dir.mkdir(parents=True, exist_ok=True)
            (self.stop_dir / "STOP").touch()
        return self.proposer.propose(ctx)

    def fix_crash(self, ctx, candidate, description, log_tail):
        who = self.scripted if self.switched_at is None else self.proposer
        return who.fix_crash(ctx, candidate, description, log_tail)

    def usage(self) -> dict:
        return self.proposer.usage()


def stuck_night(name: str, *, seed: int, n_live: int, max_usd: float, cap: int = 40) -> dict:
    from rsi.autoresearch import MockResearchAgent
    from rsi.autoresearch.loop import make_agent

    task = TinyLMTask(budget_s=2.0)
    files = dict(task.seed_artifact().files)
    t = files["train.py"]
    for k, v in PLATEAU_START.items():
        t = setk(t, k, v)
    seed_art = Artifact({**files, "train.py": t})
    llm = CachedLLM(ClaudeCLI("haiku", timeout_s=300), LIVE / f"cache_{name}")
    agent = StuckSwitchAgent(MockResearchAgent(task.mock_edit_pool(), seed=seed), make_agent(task, llm),
                             n_live=n_live, stop_dir=LIVE / name)
    cfg = Config(max_experiments=cap, seed=seed, program="upstream", max_usd=max_usd, plot=False, overwrite=True,
                 tag=f"r2-{name}")
    res = run(task, seed_art, llm_propose=llm, agent=agent, config=cfg, out_dir=LIVE / name)
    meta = {"switched_at_experiment": agent.switched_at, "live_proposals": agent.live_proposals,
            "stop_reason": res.meta.get("stop_reason")}
    (LIVE / name / "x6b_switch.json").write_text(json.dumps(meta))
    return {"name": name, "results_tsv": (LIVE / name / "results.tsv").read_text(), **meta,
            "usage": (res.usage or {}).get("_total")}


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
            row.update({"changed_constants": changed, "parent_constants": {k: c0.get(k) for k in changed},
                        "structural": s_changed, "combination": combo,
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
    ap.add_argument("phase", choices=("design", "eval", "plateau", "stuck", "analyze"))
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
    elif a.phase == "stuck":
        n = a.experiments or 4
        guard(0.35)
        out = stuck_night("stuck_v1_s200", seed=200, n_live=n, max_usd=0.35)
        print(out["results_tsv"], json.dumps({k: v for k, v in out.items() if k != "results_tsv"}, default=str))
    else:
        analyze()


GROWTH_KNOBS = ("CONTEXT", "HIDDEN", "EMBED_DIM", "DEPTH", "TRAIN_SEQ_LEN", "BATCH_SIZE")


def _num(v):
    try:
        return float(ast.literal_eval(str(v)))
    except (ValueError, SyntaxError, TypeError):
        return None


def discouraged(row: dict) -> bool | None:
    """X5b behaviour measure: the proposal raises a growth knob or sets WARMDOWN_RATIO to 0 (None: no candidate)."""
    ch, par = row.get("changed_constants"), row.get("parent_constants") or {}
    if ch is None:
        return None
    for k in GROWTH_KNOBS:
        if k in ch:
            a, b = _num(par.get(k)), _num(ch.get(k))
            if a is not None and b is not None and b > a:
                return True
    return "WARMDOWN_RATIO" in ch and _num(ch["WARMDOWN_RATIO"]) == 0.0


def honest(d: Path) -> dict | None:
    s = json.loads((d / "summary.json").read_text())
    rv = s.get("reeval") or (s.get("meta") or {}).get("reeval")
    if not rv:
        return None
    b, f = rv["baseline"]["values"], rv["final"]["values"]
    return {"baseline": b, "final": f, "improvement": float(np.mean(b) - np.mean(f))}


def x6_pool(nights: dict, pick) -> dict:
    """X6 secondary: stop/ask turns, invalid turns, longest non-keep run and K/S/C/R counts over some nights."""
    names = sorted(k for k in nights if pick(k))
    rows, longest, asks, invalid = [], 0, 0, 0
    for k in names:
        c = nights[k]["classification"]
        rows += c["proposals"]
        asks += len(c["asking_turns"])
        invalid += c["n_invalid"]
        run_ = 0
        for r in c["proposals"]:
            run_ = 0 if r["status"] == "keep" else run_ + 1
            longest = max(longest, run_)
    cand = [r for r in rows if r.get("kind") in ("K", "S")]
    return {"nights": names, "n_proposals": len(rows), "asking_turns": asks, "invalid_turns": invalid,
            "longest_nonkeep_run": longest, "stuck_point_reached": longest >= 4,
            "K": sum(r["kind"] == "K" for r in cand), "S": sum(bool(r["structural"]) for r in cand),
            "C": sum(bool(r["combination"]) for r in cand), "R": sum(bool(r["repeat"]) for r in cand)}


def x6b(d: Path, c: dict) -> dict:
    """X6b rule on the live proposals of the replayed-stuck night (rounds from the switch on)."""
    meta = json.loads((d / "x6b_switch.json").read_text()) if (d / "x6b_switch.json").exists() else {}
    sw = meta.get("switched_at_experiment")
    live = [r for r in c["proposals"] if sw is not None and r["round"] >= sw]   # sw = round of the first live proposal
    C = sum(bool(r["combination"]) for r in live)
    S = sum(bool(r["structural"]) for r in live)
    stops = len(c["asking_turns"])
    rule = ("NOT RUN" if sw is None else "REPRODUCED-rule" if (stops == 0 and C >= 1 and S >= 1)
            else "PARTIAL-rule" if (C >= 1 or S >= 1) else "NOT REPRODUCED-rule")
    return {**meta, "classifier_stuck_at": c["stuck_at"], "live_rows": live, "n_live": len(live), "C": C, "S": S,
            "R": sum(bool(r["repeat"]) for r in live), "asking_turns": stops,
            "invalid": sum(r["status"] == "invalid" for r in live), "rule_outcome": rule}


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
    cnt = {}
    for arm in ("v1", "v2"):
        rows = [r for k, v in out["nights"].items() if k.startswith(f"eval_{arm}_")
                for r in v["classification"]["proposals"] if discouraged(r) is not None]
        cnt[arm] = {"discouraged": sum(discouraged(r) for r in rows), "n": len(rows),
                    "which": [(r["round"], r["description"]) for r in rows if discouraged(r)]}
    if cnt["v1"]["n"] and cnt["v2"]["n"]:
        tab = [[cnt["v2"]["discouraged"], cnt["v2"]["n"] - cnt["v2"]["discouraged"]],
               [cnt["v1"]["discouraged"], cnt["v1"]["n"] - cnt["v1"]["discouraged"]]]
        p = float(stats.fisher_exact(tab, alternative="less").pvalue)
        out["x5b_behaviour"] = {**cnt, "fisher_p_one_sided_v2_lower": p, "pass": bool(p < 0.05)}
    out["x6_secondary"] = {
        "preregistered_v1_nights": x6_pool(out["nights"], lambda k: k == "design_v1_s100" or k.startswith("eval_v1_")),
        "extra_all_upstream_and_v2_nights": x6_pool(out["nights"], lambda k: k.startswith(("design_", "eval_")))}
    if "stuck_v1_s200" in out["nights"]:
        out["x6b_stuck"] = x6b(LIVE / "stuck_v1_s200", out["nights"]["stuck_v1_s200"]["classification"])
    write("r2_live_nights", out)
    print(json.dumps({k: v for k, v in out.items() if k != "nights"}, indent=1, default=str))
    for k, v in out["nights"].items():
        c = v["classification"]
        print(k, "stuck_at", c["stuck_at"], "after", c["after_stuck"], "before", c["before_stuck"],
              "invalid", c["n_invalid"], "ask", c["asking_turns"], "honest", v["honest"] and v["honest"]["improvement"])


if __name__ == "__main__":
    main()
