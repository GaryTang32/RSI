"""E15a (live): does LLM-written guidance reach the agent's prompt, and does it narrow what the agent explores?

    python experiments/dream-rsi/e15_guidance_live.py [--k 12] [--offline] [--workers 2]   # --offline: cache only, $0
"""
import argparse
import difflib
import itertools
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from rsi.core import CachedLLM, ClaudeCLI  # noqa: E402
from rsi.core.artifact import Artifact  # noqa: E402
from rsi.domains.discovery import SumDiffDomain  # noqa: E402
from rsi.domains.discovery.sumdiff import MECHANISMS  # noqa: E402
from rsi.dream import EditorAgent, LLMGuidanceSummarizer  # noqa: E402
from rsi.dream.agent import AttemptContext, history_records  # noqa: E402
from rsi.dream.tree import DiscoveryTree  # noqa: E402

RUN = ROOT / "validation" / "dream-rsi" / "sumdiff_live_c"  # history H: its two recorded live worlds
RESULTS = ROOT / "results" / "dream-rsi"
CAP_USD = 2.5


def jaccard(a, b):
    a, b = set(a), set(b)
    return 1.0 - len(a & b) / max(1, len(a | b))


def code_dist(a, b):
    return 1.0 - difflib.SequenceMatcher(None, a, b).ratio()


def mean_pair(items, f):
    ps = [f(x, y) for x, y in itertools.combinations(items, 2)]
    return float(np.mean(ps)) if ps else float("nan")


def perm_test(items, labels, f, n=10000, seed=0):
    """One-sided: statistic = meanpair(U) - meanpair(G); p = P(permuted >= observed)."""
    d = np.array([[f(x, y) for y in items] for x in items])

    def stat(lab):
        u = [i for i, l in enumerate(lab) if l == "U"]
        g = [i for i, l in enumerate(lab) if l == "G"]
        return np.mean([d[i, j] for i, j in itertools.combinations(u, 2)]) - \
            np.mean([d[i, j] for i, j in itertools.combinations(g, 2)])

    obs = stat(labels)
    rng = np.random.default_rng(seed)
    lab = np.array(labels)
    cnt = sum(stat(rng.permutation(lab)) >= obs - 1e-15 for _ in range(n))
    return float(obs), float((cnt + 1) / (n + 1))


def analyse(recs, guidance, root_score, k):
    """M27: the advice is in every guided prompt and no unguided one; L6: guided attempts are less diverse."""
    ok = [r for r in recs if r.get("set")]
    items, labels, codes = [r["set"] for r in ok], [r["arm"] for r in ok], [r["code"] for r in ok]
    jac_obs, jac_p = perm_test(items, labels, jaccard) if len(set(labels)) == 2 else (None, None)
    code_diff = (mean_pair([c for c, l in zip(codes, labels) if l == "U"], code_dist)
                 - mean_pair([c for c, l in zip(codes, labels) if l == "G"], code_dist)) if ok else None
    m27 = {"advice_nonempty": bool(guidance.text.strip()),
           "advice_in_all_G_prompts": all(r["prompt_has_advice"] for r in recs
                                          if r["arm"] == "G" and "prompt_has_advice" in r),
           "advice_in_no_U_prompt": not any(r["prompt_has_advice"] for r in recs
                                            if r["arm"] == "U" and "prompt_has_advice" in r),
           "n_G_prompts": sum(1 for r in recs if r["arm"] == "G" and "prompt_has_advice" in r)}
    m27["passes"] = bool(m27["advice_nonempty"] and m27["advice_in_all_G_prompts"] and m27["advice_in_no_U_prompt"]
                         and m27["n_G_prompts"] == k)
    arm_stats = {}
    for arm in ("U", "G"):
        rs = [r for r in recs if r["arm"] == arm]
        sc = [r["score"] for r in rs if r.get("fail_class") == "ok"]
        arm_stats[arm] = {"attempts": len(rs), "ok": len(sc), "mean_gamma_ok": float(np.mean(sc)) if sc else None,
                          "best_gamma": max(sc) if sc else None,
                          "improved_over_root": sum(s > root_score + 1e-12 for s in sc),
                          "jaccard_diversity": mean_pair([r["set"] for r in rs if r.get("set")], jaccard),
                          "code_diversity": mean_pair([r["code"] for r in rs if r.get("set")], code_dist),
                          "mentions_focus_direction": sum(any(d in (r.get("proposal") or "").lower()
                                                              for d in guidance.focus) for r in rs)}
    l6 = {"jaccard_U_minus_G": jac_obs, "permutation_p": jac_p, "code_U_minus_G": code_diff,
          "passes": bool(jac_obs is not None and jac_obs > 0 and jac_p < 0.05 and code_diff is not None
                         and code_diff > 0)}
    return {"M27": m27, "L6": l6}, arm_stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=12)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--offline", action="store_true", help="replay the cache only ($0)")
    ap.add_argument("--cache", default=str(RESULTS / "e15_live_cache"))
    ap.add_argument("--out", default=str(RESULTS / "e15_guidance_live.json"))
    a = ap.parse_args()
    llm = CachedLLM(ClaudeCLI("haiku", timeout_s=600), a.cache, offline=a.offline)
    dom = SumDiffDomain(sandboxed=True)
    worlds = [DiscoveryTree.load(RUN / "trace_pool" / f"iter{t:02d}" / "tree.json") for t in (1, 2)]
    root = Artifact({"construct.py": (RUN / "best_artifact" / "construct.py").read_text()})
    root_ev = dom.evaluate_program(root)
    seed_ev = dom.evaluate_program(dom.seed_artifact())
    hist = history_records(worlds)
    guidance = LLMGuidanceSummarizer(llm).summarize(worlds, list(MECHANISMS))
    prob = dom.describe()
    agent = EditorAgent(llm, editable=["construct.py"])

    def work(job):
        """One new root attempt: arm U (unguided) or G (the summarizer's advice as $direction_guidance)."""
        arm, b = job
        if llm.meter.total().cost_usd > CAP_USD:
            return job, {"skipped": "spend cap reached"}
        c = AttemptContext(prob, "root", root, root_ev.score, b, 0, 3, [], [], hist, seed_ev.score, {},
                           guidance.text if arm == "G" else "", ["construct.py"], None, seed_ev.fail_class,
                           seed_ev.error, "")
        prompt = agent.build_instructions(c)
        att = agent.attempt(c, seed=b)
        rec = {"arm": arm, "branch": b, "prompt_has_advice": bool(guidance.text) and guidance.text in prompt,
               "prompt_chars": len(prompt), "agent_error": att.error, "proposal": att.proposal[:600]}
        if att.artifact is not None:
            rec["code"] = att.artifact.get("construct.py") or ""
            out, err, fc, _ = dom.run_program(att.artifact)
            if err is None:
                ev = dom.check(out)
                rec.update(fail_class=ev.fail_class, score=ev.score, set=sorted(int(x) for x in out))
            else:
                rec.update(fail_class=fc, score=0.0, error=err[:300])
        return job, rec

    jobs = [(arm, b) for b in range(a.k) for arm in ("U", "G")]  # interleaved: a cap stops both arms evenly
    results = {}
    t0 = time.time()
    with ThreadPoolExecutor(a.workers) as ex:
        for job, rec in ex.map(work, jobs):
            results[f"{job[0]}{job[1]}"] = rec
            print(f"[{job[0]}{job[1]}] {rec.get('fail_class', rec.get('skipped') or rec.get('agent_error'))} "
                  f"score {rec.get('score')} spend ${llm.meter.total().cost_usd:.3f}", flush=True)
    tests, arm_stats = analyse(list(results.values()), guidance, root_ev.score, a.k)
    spend = llm.meter.total()
    out = {"experiment": "e15_guidance_live", "created": time.strftime("%Y-%m-%d %H:%M:%S"),
           "config": {"history": str(RUN.relative_to(ROOT)), "root_gamma": root_ev.score, "k_per_arm": a.k,
                      "model": "claude haiku via ClaudeCLI + CachedLLM", "cache": a.cache, "cap_usd": CAP_USD,
                      "direction_assignment": "none in both arms", "offline": a.offline},
           "guidance": {"text": guidance.text, "focus": guidance.focus, "avoid": guidance.avoid},
           "tests": tests, "arms": arm_stats, "attempts": results,
           "spend_usd": spend.cost_usd, "llm_calls": spend.calls, "cache_hits": getattr(llm, "hits", None),
           "wall_s": round(time.time() - t0, 1)}
    Path(a.out).write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps({"tests": out["tests"], "arms": arm_stats, "spend_usd": spend.cost_usd}, indent=1, default=str))
    print(f"[saved] {a.out}")


if __name__ == "__main__":
    main()
