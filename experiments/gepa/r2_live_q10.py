"""Retry round 2, R2-D: GEPA vs MIPRO-lite with a live haiku proposer on both sides (ifbench/gpt cell), vs the mock.

Run: python experiments/gepa/r2_live_q10.py [--seeds 8] [--max-usd 3.5] [--offline]
"""
from __future__ import annotations

import argparse
import json
import time
from multiprocessing import get_context

from _common import RESULTS, reflection_llm, save
import r2_paper_regime as R
from rsi.core import CachedLLM, ClaudeCLI, paired_diff_ci
from rsi.gepa import Config, FewShotConfig, run, run_fewshot

SETTING, MODEL, SEED0 = "ifbench", "gpt", 300
CACHE = RESULTS / "r2d_live_cache"


def proposer(kind: str, world, offline: bool = False):
    if kind == "mock":
        return reflection_llm("sim", world)
    return CachedLLM(ClaudeCLI("haiku", timeout_s=300), CACHE, offline=offline)


def job(spec):
    arm, kind, seed, offline = spec
    d = R.domain(SETTING, MODEL, seed)
    B = R.SETTINGS[SETTING]["B"]
    llm = proposer(kind, d.world, offline)
    t0 = time.time()
    if arm == "gepa":
        res = run(d, d.seed_artifact(), llm_propose=llm, config=Config(max_metric_calls=B, seed=seed))
    else:
        res = run_fewshot(d, d.seed_artifact(), llm_propose=llm, config=FewShotConfig(max_metric_calls=B, seed=seed))
    tot = res.usage.get("_total", {})
    return {"arm": arm, "proposer": kind, "seed": seed, "B": B, "final_test": d.expected(res.best, "test"),
            "best_val": res.meta.get("best_val"), "seed_test": d.expected(d.seed_artifact(), "test"),
            "oracle_test": d.expected(d.oracle_artifact(), "test"), "tokens": R.prompt_tokens(res.best),
            "llm_calls": int(tot.get("calls", 0)), "cost_usd": float(tot.get("cost_usd", 0.0)),
            "cache_hits": getattr(llm, "hits", None), "wall_s": round(time.time() - t0, 1),
            "best_prompts": {k: v[:4000] for k, v in res.best.files.items() if k.startswith("prompts/")}}


def diff(rows, a, b):
    """Paired (b - a) over the seeds present in both (arm, proposer) cells."""
    ka, kb = ({r["seed"]: r["final_test"] for r in rows if (r["arm"], r["proposer"]) == x} for x in (a, b))
    s = sorted(set(ka) & set(kb))
    return paired_diff_ci([ka[i] for i in s], [kb[i] for i in s]) if len(s) >= 2 else {"n": len(s)}


def analyse(rows):
    A = {"D1_gepa_live_minus_mipro_live": diff(rows, ("mipro", "live"), ("gepa", "live")),
         "mock_gepa_minus_mipro": diff(rows, ("mipro", "mock"), ("gepa", "mock")),
         "gepa_live_minus_gepa_mock": diff(rows, ("gepa", "mock"), ("gepa", "live")),
         "mipro_live_minus_mipro_mock": diff(rows, ("mipro", "mock"), ("mipro", "live"))}
    seeds = sorted({r["seed"] for r in rows if r["proposer"] == "live"})
    g = {(r["arm"], r["proposer"], r["seed"]): r["final_test"] for r in rows}
    ok = [s for s in seeds if all((a, p, s) in g for a in ("gepa", "mipro") for p in ("live", "mock"))]
    live_d = [g[("gepa", "live", s)] - g[("mipro", "live", s)] for s in ok]
    mock_d = [g[("gepa", "mock", s)] - g[("mipro", "mock", s)] for s in ok]
    A["D2_live_gap_minus_mock_gap"] = paired_diff_ci(mock_d, live_d) if len(ok) >= 2 else {"n": len(ok)}
    for arm in ("gepa", "mipro"):
        for p in ("live", "mock"):
            v = [r["final_test"] for r in rows if (r["arm"], r["proposer"]) == (arm, p)]
            A[f"mean_{arm}_{p}"] = sum(v) / len(v) if v else None
    live = [r for r in rows if r["proposer"] == "live"]
    A["live_spend_usd"] = sum(r["cost_usd"] for r in live)
    A["live_calls"] = sum(r["llm_calls"] for r in live)
    A["decision"] = ("mock negative confirmed (MIPRO-lite significantly ahead with a live proposer): Q10 stays NOT "
                     "REPRODUCED (d)" if A["D1_gepa_live_minus_mipro_live"].get("hi", 1) < 0 else
                     "mock negative not confirmed with a live proposer: Q10 root cause (b), PARTIAL; L10 PARTIAL")
    return A


def save_rows(rows: list[dict], partial: bool) -> dict:
    A = analyse(rows)
    save("r2_live_q10", {"experiment": "R2-D live-proposer check of the Q10 negative", "setting": SETTING,
                         "model": MODEL, "llm": "claude-cli:haiku (CachedLLM, results/gepa/r2d_live_cache)",
                         "partial": partial, "analysis": A, "raw": rows})
    return A


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=8)
    ap.add_argument("--max-usd", type=float, default=3.5)
    ap.add_argument("--offline", action="store_true", help="replay the live cache only ($0)")
    a = ap.parse_args()
    seeds = list(range(SEED0, SEED0 + a.seeds))
    with get_context("fork").Pool(2) as p:
        rows = p.map(job, [(arm, "mock", s, False) for s in seeds for arm in ("gepa", "mipro")], chunksize=1)
        spent = 0.0
        for s in seeds:
            if spent >= a.max_usd:
                print(f"[guard] live spend ${spent:.2f} >= ${a.max_usd}: no further seeds launched", flush=True)
                break
            out = p.map(job, [("gepa", "live", s, a.offline), ("mipro", "live", s, a.offline)], chunksize=1)
            rows += out
            spent += sum(r["cost_usd"] for r in out)
            print(f"seed {s}: " + ", ".join(f"{r['arm']} {r['final_test']:.3f} ({r['llm_calls']} calls, "
                                             f"${r['cost_usd']:.3f}, {r['wall_s']}s)" for r in out)
                  + f"; spend so far ${spent:.2f}", flush=True)
            save_rows(rows, partial=True)
    print(json.dumps(save_rows(rows, partial=False), indent=1, default=str))


if __name__ == "__main__":
    main()
