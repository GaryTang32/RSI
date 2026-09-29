"""M9 - live single-step history probe: scores-only vs LLM summaries vs raw traces vs a stated trade-off
(retry round 2; preregistered as P3/P4 in the claims audit).

Claims [paper:MH Table 3, §4.1, §5; overview]: raw traces beat scores-only and LLM summaries (L1, Q3);
summaries "do not recover the missing signal, and may even hurt" (L2); given the desired trade-off the proposer
finds harnesses across the frontier (Q14); few edits are real improvements (K3); code-space search favours
coherent algorithms over hard-coded ones (L4); reading the full history costs far more tokens (K4).

Design: 6 snapshot stores are built OFFLINE (MemoClassify seeds 200-205, MockProposer, full view, 2 iterations
x k = 2). Every program of every snapshot gets an LLM summary (the fixed ``LLMSummarizer``, haiku). Then, for each
snapshot, ONE RewriteProposer call (haiku, k = 2) per arm on a copy of the snapshot:
A scores_only, B scores_summary, C full, D full + a low-context trade-off. Candidates and the snapshot programs
are scored on 5 seeds of the search split. Everything is paired by snapshot.

    python experiments/metaharness-solpi/m9_live_history_probe.py            # live (cached; spend cap $3.80)
    python experiments/metaharness-solpi/m9_live_history_probe.py --offline  # $0 replay from the cache
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import RESULTS, RUNS, paired, save, summarize  # noqa: E402

import numpy as np  # noqa: E402

from rsi.core import CachedLLM, ClaudeCLI, Evaluator  # noqa: E402
from rsi.core.llm import estimate_tokens  # noqa: E402
from rsi.domains.memoclassify import make_domain  # noqa: E402
from rsi.metaharness import (Config, LeakageScreen, LLMSummarizer, RewriteProposer, pareto_frontier,  # noqa: E402
                             render_view, run)
from rsi.metaharness.proposer import PROTOTYPE_ON_PAPER, REWRITE_OUTPUT, SKILL_TEXT, TASK_PROMPT, format_skill  # noqa: E402
from rsi.metaharness.store import ExperienceStore, context_mean  # noqa: E402

SNAP_SEEDS = (200, 201, 202, 203, 204, 205)
ARMS = {"A_scores_only": ("scores_only", ""), "B_scores_summary": ("scores_summary", ""), "C_full": ("full", ""),
        "D_full_lowctx": ("full", "Prefer low context cost: aim for harnesses that inject under 2,000 context "
                                  "characters per query, accepting a few points of accuracy loss.")}
EVAL_SEEDS = (0, 1, 2, 3, 4)
CACHE = RESULTS / "metaharness_retry2_cache"
SRC_OUT = RESULTS / "metaharness_retry2_candidates"
WORK = RUNS / "m9"
SPEND_CAP = 3.80


class Guard:
    def __init__(self, llm):
        self.llm = llm

    def spent(self) -> float:
        return float(self.llm.meter.total().cost_usd)

    def ok(self) -> bool:
        return self.spent() < SPEND_CAP


def five_seed(dom, art) -> dict:
    ev = Evaluator(dom, dom.make_model("A"), workers=1).evaluate(art, "evolve", seeds=list(EVAL_SEEDS))
    per_seed = []
    for i in range(len(EVAL_SEEDS)):
        per_seed.append(float(np.mean([trs[i].score for trs in ev.trials.values() if len(trs) > i and trs[i]])))
    unit_ctx = {tid: float(np.mean([t.meta.get("context_chars", 0.0) for t in trs if t])) for tid, trs in
                ev.trials.items()}
    return {"score": float(ev.score), "per_seed": per_seed, "context": context_mean(unit_ctx)}


def build_snapshot(seed: int) -> Path:
    d = WORK / "snapshots" / f"s{seed}"
    if (d / "store" / "frontier_val.json").exists() and (d / "store" / "sessions" / "iter002").exists():
        return d
    shutil.rmtree(d, ignore_errors=True)
    dom = make_domain(seed=seed)
    run(dom, dom.seed_artifact("fewshot_all"), llm_task=dom.make_model("A"),
        config=Config(iterations=2, k=2, seed=seed, shadow_monitor=False, finalize=False), out_dir=d,
        baselines=dom.baselines())
    return d


def sign_flip_p(d) -> float:
    d = [x for x in d if x is not None]
    if not d:
        return float("nan")
    obs = np.mean(d)
    hits = sum(np.mean([s * x for s, x in zip(signs, d)]) >= obs - 1e-12
               for signs in itertools.product((1, -1), repeat=len(d)))
    return hits / 2 ** len(d)


def hardcoding_flags(dom, src_files: dict) -> dict:
    from rsi.core.artifact import Artifact
    art = Artifact(dict(src_files))
    screen = LeakageScreen.for_domain(dom)
    reason = screen.check(art, None)
    text = "\n".join(src_files.values())
    lits = [t for t in dom.leakage_terms("evolve") if len(t) >= 6 and t in text]
    return {"screen": reason, "literals": lits[:10], "flag_auto": bool(reason) or bool(lits)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    args = ap.parse_args()
    llm = CachedLLM(ClaudeCLI("haiku", timeout_s=600), CACHE, offline=args.offline)
    guard = Guard(llm)
    SRC_OUT.mkdir(parents=True, exist_ok=True)
    snaps = {s: build_snapshot(s) for s in SNAP_SEEDS}
    print("snapshots built", flush=True)
    # ---- summaries for every snapshot program (summary.md), before any proposer call
    summ_llm_calls = 0
    for s, d in snaps.items():
        st = ExperienceStore(d / "store")
        for n in st.names():
            p = st.cand_dir(n) / "eval" / "search" / "summary.md"
            if p.exists() and p.read_text().strip() and "unavailable" not in p.read_text():
                continue
            if not guard.ok():
                break
            st.write_summary_text(n, LLMSummarizer(llm)(st.traces(n)))
            summ_llm_calls += 1
    print(f"summaries done, spent ${guard.spent():.3f}", flush=True)
    rows, snap_rows = [], {}
    for s, d in snaps.items():
        dom = make_domain(seed=s)
        st = ExperienceStore(d / "store")
        snap_names = st.names()
        fr = st.frontier()
        inc = fr["_best"]["system"]
        progs = {n: five_seed(dom, st.artifact(n)) for n in snap_names}
        snap_rows[s] = {"programs": progs, "incumbent": inc,
                        "summaries": {n: (st.cand_dir(n) / "eval/search/summary.md").read_text()
                                      if (st.cand_dir(n) / "eval/search/summary.md").exists() else None
                                      for n in snap_names}}
        for arm, (mode, tradeoff) in ARMS.items():
            if not guard.ok():
                rows.append({"snapshot": s, "arm": arm, "skipped": "spend cap"})
                continue
            ad = WORK / "arms" / f"s{s}_{arm}"
            shutil.rmtree(ad, ignore_errors=True)
            shutil.copytree(d, ad)
            prop = RewriteProposer(llm)
            before = guard.spent()
            res = run(dom, dom.seed_artifact("fewshot_all"), llm_task=dom.make_model("A"), proposer=prop,
                      config=Config(iterations=1, k=2, history_mode=mode, summaries="never", tradeoff=tradeoff,
                                    seed=s, shadow_monitor=False, finalize=False),
                      out_dir=ad, baselines=dom.baselines())
            ast = res.loop.store
            sess = json.loads((ast.sessions_dir() / "iter003" / "meta.json").read_text())
            cands = []
            for n in ast.names():
                if n in snap_names:
                    continue
                m = ast.meta(n)
                src = {str(k): v for k, v in ast.artifact(n).files.items()}
                (SRC_OUT / f"s{s}_{arm}_{n}.py").write_text(src.get("memory.py", json.dumps(src)[:20000]))
                row = {"name": n, "status": m.get("status"), "reason": (m.get("reason") or "")[:300],
                       "base_system": m.get("base_system"), "hypothesis": (m.get("hypothesis") or "")[:400],
                       "single_seed": (ast.scores(n) or {}).get("score"),
                       "single_context": (ast.scores(n) or {}).get("context_cost"),
                       "hardcoding": hardcoding_flags(dom, src)}
                if m.get("status") == "evaluated":
                    row.update({"five_seed": five_seed(dom, ast.artifact(n))})
                cands.append(row)
            rows.append({"snapshot": s, "arm": arm, "candidates": cands, "usage": sess.get("usage"),
                         "spent_usd": guard.spent() - before, "files_read": sess.get("n_files_read"),
                         "files_read_by_kind": sess.get("files_read_by_kind"),
                         "rendered_chars": (sess.get("proposer_meta") or {}).get("rendered_chars"),
                         "error": sess.get("error")})
            print(f"s{s} {arm}: {[(c['name'], c['status'], (c.get('five_seed') or {}).get('score')) for c in cands]}"
                  f" spent ${guard.spent():.3f}", flush=True)
    out = analyse(rows, snap_rows)
    out.update({"per_arm_runs": rows, "snapshots": {str(k): v for k, v in snap_rows.items()},
                "spend_usd": guard.spent(), "summary_calls_live": summ_llm_calls,
                "cache": str(CACHE), "cache_hits": llm.hits, "cache_misses": llm.misses,
                "k4": k4_tokens(snaps, rows)})
    save("m9_live_history_probe", out)


def arm_value(r, invalid_zero=False):
    if r is None or r.get("skipped"):
        return None
    vals = [c["five_seed"]["score"] for c in r["candidates"] if c.get("five_seed")]
    if invalid_zero:
        vals += [0.0 for c in r["candidates"] if not c.get("five_seed")]
    return float(np.mean(vals)) if vals else None


def analyse(rows, snap_rows) -> dict:
    get = {(r["snapshot"], r["arm"]): r for r in rows}
    out = {"claim": "L1/L2/Q3 history ablation, Q14 trade-off steering, K3 useful-edit rate, L4 hard-coding, "
                    "K4 tokens (live, single step, paired by snapshot)",
           "preregistration": "claims-audit-metaharness.md, Retry round 2: preregistration, P3/P4",
           "config": {"snapshots": list(SNAP_SEEDS), "arms": ARMS, "eval_seeds": list(EVAL_SEEDS),
                      "proposer": "RewriteProposer(claude-haiku), k=2, 60k rendered chars",
                      "summarizer": "LLMSummarizer(claude-haiku), fixed input (retry 2)"}}
    tests = {}
    for invalid_zero in (False, True):
        key = "invalid_as_zero" if invalid_zero else "primary"
        val = {(s, a): arm_value(get.get((s, a)), invalid_zero) for s in SNAP_SEEDS for a in ARMS}
        t = {}
        for name, a, b in (("full_minus_scores_only", "A_scores_only", "C_full"),
                           ("full_minus_summary", "B_scores_summary", "C_full"),
                           ("summary_minus_scores_only", "A_scores_only", "B_scores_summary")):
            pairs = [(val[(s, a)], val[(s, b)]) for s in SNAP_SEEDS
                     if val[(s, a)] is not None and val[(s, b)] is not None]
            d = [y - x for x, y in pairs]
            t[name] = {**paired([p[0] for p in pairs], [p[1] for p in pairs]), "diffs": d,
                       "sign_flip_p_one_sided": sign_flip_p(d),
                       "n_dropped": len(SNAP_SEEDS) - len(pairs)}
        t["arm_means"] = {a: summarize([val[(s, a)] for s in SNAP_SEEDS]) for a in ARMS}
        tests[key] = t
    out["tests"] = tests
    p = tests["primary"]
    ci = lambda k: p[k].get("lo", -1) > 0      # noqa: E731
    l1 = ("REPRODUCED" if ci("full_minus_scores_only") and ci("full_minus_summary") else
          "NOT REPRODUCED" if p["full_minus_scores_only"].get("mean_diff", 0) <= 0 else "PARTIAL")
    a_ok, b_ok = ci("full_minus_summary"), p["summary_minus_scores_only"].get("mean_diff", 1) <= 0
    l2 = "REPRODUCED" if a_ok and b_ok else "PARTIAL" if a_ok or b_ok else "NOT REPRODUCED"
    # ---- Q14 steering
    logr, on_front = [], 0
    for s in SNAP_SEEDS:
        c, dd = get.get((s, "C_full")), get.get((s, "D_full_lowctx"))
        if not c or not dd or c.get("skipped") or dd.get("skipped"):
            continue
        cc = [x["five_seed"]["context"] for x in c["candidates"] if x.get("five_seed")]
        dc = [x["five_seed"]["context"] for x in dd["candidates"] if x.get("five_seed")]
        if cc and dc:
            logr.append(math.log(max(1.0, float(np.median(dc)))) - math.log(max(1.0, float(np.median(cc)))))
        pts = [(n, v["score"], v["context"]) for n, v in snap_rows[s]["programs"].items()]
        pts += [(f"C:{x['name']}", x["five_seed"]["score"], x["five_seed"]["context"]) for x in c["candidates"]
                if x.get("five_seed")]
        pts += [(f"D:{x['name']}", x["five_seed"]["score"], x["five_seed"]["context"]) for x in dd["candidates"]
                if x.get("five_seed")]
        front = {n for n, _, _ in pareto_frontier(pts)}
        on_front += any(n.startswith("D:") for n in front)
    steer = summarize(logr)
    steering = bool(logr) and steer["hi"] < 0
    q14 = ("REPRODUCED" if steering and on_front >= 4 else "PARTIAL" if steering else
           "NOT REPRODUCED" if logr and steer["mean"] >= 0 else "PARTIAL")
    out["q14"] = {"log_ratio_D_over_C_median_context": logr, "summary": steer, "snapshots_with_D_on_frontier":
                  on_front, "ratio_geo_mean": math.exp(steer["mean"]) if logr else None}
    # ---- K3 useful-edit rate
    from scipy.stats import ttest_ind
    real, n_valid, apparent, apparent_not_real = 0, 0, 0, 0
    for r in rows:
        if r.get("skipped"):
            continue
        inc = snap_rows[r["snapshot"]]["programs"][snap_rows[r["snapshot"]]["incumbent"]]
        for c in r["candidates"]:
            if not c.get("five_seed"):
                continue
            n_valid += 1
            pv = ttest_ind(c["five_seed"]["per_seed"], inc["per_seed"], equal_var=False,
                           alternative="greater").pvalue
            is_real = c["five_seed"]["score"] > inc["score"] and pv < 0.05
            real += is_real
            if c.get("single_seed") is not None and c["single_seed"] > inc["per_seed"][0]:
                apparent += 1
                apparent_not_real += not is_real
    rate = real / n_valid if n_valid else float("nan")
    out["k3"] = {"n_valid": n_valid, "n_real_improvements": real, "rate": rate, "wilson95": wilson(real, n_valid),
                 "n_single_seed_apparent_improvements": apparent, "n_apparent_not_real": apparent_not_real}
    k3 = "REPRODUCED" if n_valid and rate < 0.05 else "PARTIAL"
    # ---- L4 automatic flags (manual rubric applied in the claims audit)
    flagged = [(r["snapshot"], r["arm"], c["name"]) for r in rows if not r.get("skipped") for c in r["candidates"]
               if c["hardcoding"]["flag_auto"]]
    out["l4_auto"] = {"n_candidates": sum(len(r.get("candidates", [])) for r in rows), "flagged": flagged}
    out["verdicts"] = {"L1": l1, "L2": l2, "Q14": q14, "K3": k3}
    return out


def wilson(k, n, z=1.96):
    if not n:
        return [float("nan")] * 2
    p = k / n
    c = (p + z * z / (2 * n)) / (1 + z * z / n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [max(0.0, c - h), min(1.0, c + h)]


def k4_tokens(snaps, rows) -> dict:
    """P4: per-step tokens of an OPRO-style windowed single-completion optimiser vs the live coding agent."""
    prompt_toks = []
    for s, d in snaps.items():
        st = ExperienceStore(d / "store")
        view = st.view("window", window=4)
        rendered, _ = render_view(view, 60000)
        prompt = TASK_PROMPT.format(iteration=3, brief=make_domain(seed=s).describe(), ctx="history",
                                    output=REWRITE_OUTPUT.format(iteration=3, k=2)) + "\n\n## History (rendered)\n" + rendered
        prompt_toks.append(estimate_tokens(prompt) + estimate_tokens(format_skill(SKILL_TEXT, 2, PROTOTYPE_ON_PAPER)))
    outs = [r["usage"]["output_tokens"] for r in rows if r.get("usage") and r["usage"].get("output_tokens")]
    ins = [r["usage"]["input_tokens"] for r in rows if r.get("usage") and r["usage"].get("input_tokens")]
    agent = json.loads((RESULTS / "live_smoke_agent.json").read_text())
    agent_toks = [ss["usage"]["input_tokens"] + ss["usage"]["output_tokens"] for ss in agent["sessions"]]
    light = float(np.mean(prompt_toks)) + (float(np.mean(outs)) if outs else 0.0)
    ratios = [a / light for a in agent_toks] if light else []
    return {"window_prompt_tokens_est": prompt_toks, "rewrite_output_tokens_measured_mean": float(np.mean(outs)) if outs else None,
            "rewrite_input_tokens_measured_mean": float(np.mean(ins)) if ins else None,
            "lighter_step_tokens": light, "agent_tokens_per_iteration": agent_toks, "ratios": ratios,
            "verdict": "REPRODUCED" if ratios and min(ratios) >= 10 else "PARTIAL"}


if __name__ == "__main__":
    main()
