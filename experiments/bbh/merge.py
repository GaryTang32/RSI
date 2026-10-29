"""Merge results/bbh/<arm>.json into one table with bootstrap CIs and paired-by-example differences.

- Eval arms (seed, cot3, rrsi, gepa, metaharness): accuracy over holdout + ood (per-example mean of the k trials),
  percentile-bootstrap 95% CI, paired differences vs seed and vs cot3 on the same examples (10k resamples).
- Harness² and best-of-n stream the 36-example subsample: their accuracy is reported against seed and cot3 restricted
  to the same examples, plus Harness² vs best-of-n.
- Per-family accuracy table, eval $ per example, search $, fresh and cached calls, status (failed leak checks and
  errors are listed but never compared).

Run:  python experiments/bbh/merge.py [--benchmark bbh|bbeh] [--dir results/<bench>] [--reps 10000]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rsi.core.stats import bootstrap_ci, paired_diff_ci  # noqa: E402

EVAL_ARMS = ("seed", "cot3", "rrsi", "gepa", "metaharness")
STREAM_ARMS = ("harness2", "bestofn")
REFS = ("seed", "cot3")


def load(d: Path) -> dict:
    arms = {}
    for name in (*EVAL_ARMS, *STREAM_ARMS, "pilot"):
        p = d / f"{name}.json"
        if p.exists():
            arms[name] = json.loads(p.read_text())
    return arms


def _ci(x, reps):
    return [round(v, 4) for v in bootstrap_ci(x, reps=reps)]


def _diff(ref: dict, arm: dict, ids, reps) -> dict:
    d = paired_diff_ci([ref[i] for i in ids], [arm[i] for i in ids], reps=reps)
    return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in d.items()}


def _usable(o: dict) -> bool:
    return o.get("status") == "ok" and bool(o.get("per_task"))


def merge(d: Path, reps: int = 10000) -> dict:
    arms = load(d)
    sigs = {n: json.dumps(o.get("suite", {}).get("summary"), sort_keys=True) for n, o in arms.items() if n != "pilot"}
    ref_sig = sigs.get("seed") or next(iter(sigs.values()), None)
    mismatched = sorted(n for n, s in sigs.items() if s != ref_sig)
    rows, fam_rows = {}, {}
    for name, o in arms.items():
        if name == "pilot":
            continue
        sp = o.get("spend", {})
        # compute cost: every call the arm used at its original price (fresh + replayed from the shared cache), so a
        # resumed run or one that reuses another arm's calls is not shown as free
        full = {k: round((sp.get(k, {}).get("usd") or 0) + (sp.get(k, {}).get("usd_saved_by_cache") or 0), 4)
                for k in ("search", "eval")}
        ph = {x.get("phase"): x for x in o.get("phases") or []}
        if "prewarm" in ph and "stream" in ph:   # best-of-n: the stream replays the prewarmed rollouts; count once
            full["search"] = round(full["search"] - (ph["stream"].get("usd_saved_by_cache") or 0), 4)
        own = ((o.get("search") or {}).get("usage") or {}).get("cost_usd")
        if own:     # a run resumed from saved method state never replays its earlier calls: take the method's counter
            full["search"] = round(max(full["search"], own), 4)
        n_ex = len(o.get("per_task") or {})
        row = {"status": o.get("status"), "n": n_ex,
               "search_usd": full["search"], "eval_usd": full["eval"],
               "fresh_search_usd": sp.get("search", {}).get("usd"), "fresh_eval_usd": sp.get("eval", {}).get("usd"),
               "eval_usd_per_example": round(full["eval"] / n_ex, 4) if n_ex else None, "fresh_usd": o.get("usd"),
               "fresh_calls": sp.get("total", {}).get("fresh_calls"),
               "cached_calls": sp.get("total", {}).get("cached_calls"),
               "leak_check": None if "leak_check" not in o else ("pass" if o["leak_check"]["passed"] else "FAIL")}
        if name in mismatched:
            row["status"] = f"{row['status']} (different suite: not compared)"
        rows[name] = row
        if not _usable(o) or name in mismatched:
            continue
        pt = o["per_task"]
        row["acc"] = _ci(list(pt.values()), reps)
        if name in EVAL_ARMS:
            for split in ("holdout", "ood"):
                ts = o["eval"][split]["tasks"]
                row[f"acc_{split}"] = round(sum(ts.values()) / max(1, len(ts)), 4)
        else:
            row["subset"] = len(o.get("subset") or [])
            row["calls_per_task"] = o.get("calls_per_task")
            if name == "bestofn":
                row["n_rollouts"] = o.get("n")
        for ref in REFS:
            r = arms.get(ref)
            if ref == name or r is None or not _usable(r) or ref in mismatched:
                continue
            ids = sorted(set(pt) & set(r["per_task"]))
            if ids:
                row[f"vs_{ref}"] = _diff(r["per_task"], pt, ids, reps)
        fam = o.get("task_family") or {}
        by: dict[str, list] = {}
        for t, s in pt.items():
            by.setdefault(fam.get(t, "?"), []).append(s)
        fam_rows[name] = {f: round(sum(v) / len(v), 4) for f, v in sorted(by.items())}
    h2, bon = arms.get("harness2"), arms.get("bestofn")
    extra = {}
    if h2 and bon and _usable(h2) and _usable(bon):
        ids = sorted(set(h2["per_task"]) & set(bon["per_task"]))
        extra["harness2_minus_bestofn"] = _diff(bon["per_task"], h2["per_task"], ids, reps)
    if h2 and _usable(h2):
        ids = sorted(h2.get("base_per_task", {}))
        extra["harness2_submitted_minus_H0_base1"] = _diff(h2["base_per_task"], h2["per_task"], ids, reps)
    pilot = arms.get("pilot")
    return {"dir": str(d), "reps": reps, "suite": ref_sig and json.loads(ref_sig), "mismatched_suites": mismatched,
            "arms": rows, "families": fam_rows, "paired_extra": extra,
            "pilot": None if not pilot else {"family_accuracy": pilot.get("family_accuracy"),
                                             "suggestion": pilot.get("suggestion"), "usd": pilot.get("usd")},
            "fresh_usd_this_run": round(sum((o.get("usd") or 0) for o in arms.values()), 4)}


def _fmt_ci(v):
    return "-" if not v else f"{v[0]:.3f} [{v[1]:.3f}, {v[2]:.3f}]"


def _fmt_diff(v):
    return "-" if not v else f"{v['mean_diff']:+.3f} [{v['lo']:+.3f}, {v['hi']:+.3f}] (n={v['n']})"


def _money(v):
    return "-" if v is None else f"{v:.3f}"


def markdown(m: dict) -> str:
    out = ["| arm | status | n | accuracy [95% CI] | holdout | ood | vs seed | vs cot3 | eval $/ex | search $ | "
           "calls (fresh+cached) | leak |", "|" + "---|" * 12]
    for name, r in m["arms"].items():
        out.append(f"| {name} | {r['status']} | {r['n']} | {_fmt_ci(r.get('acc'))} | {r.get('acc_holdout', '-')} | "
                   f"{r.get('acc_ood', '-')} | {_fmt_diff(r.get('vs_seed'))} | {_fmt_diff(r.get('vs_cot3'))} | "
                   f"{_money(r.get('eval_usd_per_example'))} | {_money(r.get('search_usd'))} | "
                   f"{r.get('fresh_calls')}+{r.get('cached_calls')} | {r.get('leak_check') or '-'} |")
    for k, v in m["paired_extra"].items():
        out.append(f"\n{k}: {_fmt_diff(v)}")
    fams = sorted({f for v in m["families"].values() for f in v})
    if fams:
        out.append("\n| family | " + " | ".join(m["families"]) + " |")
        out.append("|" + "---|" * (len(m["families"]) + 1))
        for f in fams:
            out.append(f"| {f} | " + " | ".join(str(v.get(f, "-")) for v in m["families"].values()) + " |")
    out.append(f"\nHarness² and best-of-n rows use the {next((r.get('subset') for r in m['arms'].values() if r.get('subset')), '?')}"
               "-example subsample; their vs-seed/vs-cot3 columns compare on those examples only.")
    out.append("eval $/ex and search $ price every call an arm used, fresh or replayed from the shared cache (its compute "
               "cost); the money actually spent is the sum over the cache, as the shared cache bills each call once.")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--benchmark", choices=("bbh", "bbeh"), default="bbh")
    ap.add_argument("--dir", default=None, help="default results/<benchmark>")
    ap.add_argument("--reps", type=int, default=10000)
    a = ap.parse_args(argv)
    d = Path(a.dir) if a.dir else ROOT / "results" / a.benchmark
    m = merge(d, a.reps)
    (d / "merged.json").write_text(json.dumps(m, indent=1, default=str))
    md = markdown(m)
    (d / "merged.md").write_text(md + "\n")
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
