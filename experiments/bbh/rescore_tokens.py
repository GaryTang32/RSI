"""Add answer length and price per example to arms scored before the final eval recorded them, at $0.

For each arm, the final eval (holdout + ood, the arm's k, the same Evaluator seeds) is replayed from the shared LLM
cache in cache-only mode: a call that is not in the cache is never made (it becomes a missing trial and is reported).
The result goes to ``<dir>/<arm>.tokens.json`` (per-example mean output tokens per answer and summed $ of the k
trials); ``<arm>.json`` is never modified. merge.py reads the sidecar when ``<arm>.json`` has no token fields.

Run:  python experiments/bbh/rescore_tokens.py --benchmark bbeh --arm seed rrsi gepa metaharness
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for p in (str(ROOT), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import compare_live as cl  # noqa: E402
from rsi.core import Artifact, CachedLLM, get_llm  # noqa: E402
from rsi.domains.bbh.suite import make_suite  # noqa: E402


def artifact_of(arm: str, o: dict, d: Path, dom) -> Artifact:
    if arm == "seed":
        return dom.seed_artifact()
    for cand in (Path((o.get("final_harness") or {}).get("dir") or ""), d / "runs" / arm / "final_harness"):
        if str(cand) and cand.is_dir():
            return Artifact.from_dir(cand)
    raise SystemExit(f"{arm}: no final harness directory (looked at final_harness.dir and {d}/runs/{arm})")


def rescore(arm: str, d: Path, *, benchmark: str, cache_dir: Path, data_cache=None, workers: int = 8,
            llm=None) -> dict:
    o = json.loads((d / f"{arm}.json").read_text())
    P = cl.PROFILES[benchmark]
    sig = o["suite"]
    fam = o["families"]
    practice, ood = fam["practice"], fam["ood"]
    if o.get("smoke"):
        practice, ood = practice[:1], ood[:1]
    suite = make_suite(practice, ood, per_family=dict(sig["per_family"]), seed=sig["seed"], cache_dir=data_cache,
                       benchmark=P.spec)
    dom = P.domain_cls(suite)
    eval_dom = cl.evomap_eval_domain(dom) if arm == "evomap" else dom
    art = artifact_of(arm, o, d, dom)
    if llm is None:
        llm = get_llm(o["model"], cache_dir=str(cache_dir))
        if not isinstance(llm, CachedLLM):
            raise SystemExit("cache-only replay needs a cached model spec")
        llm.offline = True          # never call the model: a miss is reported, not paid for
    out = {"arm": arm, "source": f"{arm}.json", "k": o["k"], "per_task_out_tokens": {}, "per_task_usd": {},
           "score_mismatches": [], "missing": 0,
           "recorded_missing": sum(int((o.get("eval") or {}).get(sp, {}).get("missing") or 0) for sp in ("holdout", "ood"))}
    for split in ("holdout", "ood"):
        r = cl.split_eval(eval_dom, llm, art, split, o["k"], workers)
        out["per_task_out_tokens"].update(r["out_tokens"])
        out["per_task_usd"].update(r["usd"])
        out["missing"] += r["missing"]
        rec = (o.get("eval") or {}).get(split, {}).get("tasks", {})
        out["score_mismatches"] += [t for t, s in r["tasks"].items() if t in rec and abs(rec[t] - s) > 1e-9]
    out["cache_misses"] = getattr(llm, "misses", None)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--benchmark", choices=sorted(cl.PROFILES), default="bbeh")
    ap.add_argument("--arm", nargs="+", required=True)
    ap.add_argument("--dir", default=None, help="default results/<benchmark>")
    ap.add_argument("--cache-dir", default=None, help="default <dir>/live_cache")
    ap.add_argument("--data-cache", default=None)
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args(argv)
    d = Path(a.dir) if a.dir else ROOT / "results" / a.benchmark
    cache = Path(a.cache_dir) if a.cache_dir else d / "live_cache"
    bad = 0
    for arm in a.arm:
        r = rescore(arm, d, benchmark=a.benchmark, cache_dir=cache, data_cache=a.data_cache, workers=a.workers)
        # a trial missing in the original eval (an infra error is never cached) is missing in the replay too
        ok = r["missing"] <= r["recorded_missing"] and not r["score_mismatches"]
        bad += not ok
        (d / f"{arm}.tokens.json").write_text(json.dumps(r, indent=1))
        tok = [v for v in r["per_task_out_tokens"].values() if v is not None]
        print(f"{arm:12s} {'ok' if ok else 'INCOMPLETE'} examples={len(tok)} "
              f"out_tokens/answer={(sum(tok) / len(tok)) if tok else float('nan'):.0f} missing={r['missing']} "
              f"(recorded {r['recorded_missing']}) score_mismatches={len(r['score_mismatches'])} "
              f"cache_misses={r['cache_misses']}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
