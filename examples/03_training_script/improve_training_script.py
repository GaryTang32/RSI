"""Autoresearch on the built-in tinylm training script, with a noise-aware keep rule.

Offline (default, ~25 s, no API key):  python examples/03_training_script/improve_training_script.py
Live (claude CLI, uses your plan):     python examples/03_training_script/improve_training_script.py --llm claude:haiku
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import sys
import tempfile
from pathlib import Path

if importlib.util.find_spec("rsi") is None:  # not pip-installed: use the clone this example lives in
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import rsi
from rsi.core import MinGain
from rsi.domains.tinylm import TinyLMTask

OFFLINE_NOTE = "offline simulated model: numbers show the loop working, not real gains"


def main(argv: list[str] | None = None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--llm", default="offline", help="offline (scripted agent) | claude:haiku | api:sonnet | ...")
    ap.add_argument("--out", default=None, help="run directory (default: a fresh temp dir)")
    ap.add_argument("--data", default=None, help="tinylm corpus dir (default: <out>/tinylm_data, built in ~2 s)")
    ap.add_argument("--experiments", type=int, default=8)
    ap.add_argument("--budget", type=float, default=30_000, help="training budget per run, in bytes seen")
    a = ap.parse_args(argv)
    out = Path(a.out or tempfile.mkdtemp(prefix="rsi-ex03-"))
    offline = a.llm == "offline"

    # 1. The problem: train.py (editable) trains a tiny numpy byte-level LM; prepare.py (locked) owns the data
    #    and the metric val_bpb (lower is better). A budget in bytes seen, not seconds, makes runs comparable
    #    on any machine; test_iid / test_shift are hidden splits the agent never sees (audit only).
    task = TinyLMTask(budget_s=a.budget, budget_kind="tokens", kill_after=60.0, data_root=a.data or out / "tinylm_data")
    task.prepare()

    # 2. The research agent. Offline: MockResearchAgent replays tinylm's scripted edit pool (knob changes,
    #    crashes, grader exploits) - the training runs are real, the "ideas" are scripted. Live: an LLM edits train.py.
    if offline:
        llm_propose = None  # autoresearch falls back to MockResearchAgent(task.mock_edit_pool())
    else:  # ---- LIVE: real model calls, cached so a rerun replays for free ----
        llm_propose = rsi.get_llm(a.llm, cache_dir=str(Path(".rsi_cache") / "example-03"))

    # 3. Noise-aware keep rule. Upstream keeps any run strictly better than the tip (one noisy run decides);
    #    here noise_runs=3 re-runs the unchanged script with fresh seeds, delta = 2 * sd(difference of two runs),
    #    and MinGain keeps an experiment only if it beats the tip by more than delta.
    cfg = {"max_experiments": a.experiments, "keep_rule": MinGain(), "noise_runs": 3, "overwrite": True, "plot": False}
    res = rsi.improve(task, method="autoresearch", llm_propose=llm_propose, config=cfg, out_dir=out)
    delta = res.meta["noise"]["delta"]
    print(f"\nnoise band: delta = {delta:.4f} bpb from {res.meta['noise']['n']} runs of the unchanged train.py")

    # 4. The experiment table (results.tsv, upstream's format) with the gain over the tip at the time.
    rows = list(csv.DictReader(Path(res.meta["results_tsv"]).read_text().splitlines(), delimiter="\t"))
    print(f"\n{'#':>2s} {'commit':7s} {'val_bpb':>8s} {'gain':>7s} {'status':8s} description")
    tip = None
    for i, r in enumerate(rows):
        bpb = float(r["val_bpb"]) if r["val_bpb"] not in ("", "0.000000") else None
        gain = "" if tip is None or bpb is None else f"{tip - bpb:+.4f}"
        note = "  <- beat the tip, but within noise" if gain and tip - bpb > 0 and r["status"] == "discard" else ""
        print(f"{i:2d} {r['commit']:7s} {r['val_bpb']:>8s} {gain:>7s} {r['status']:8s} {r['description']}{note}")
        if r["status"] == "keep" and bpb is not None:
            tip = bpb

    # 5. Hidden audit of every kept commit (computed after the loop; the agent never saw these splits).
    print(f"\n{'kept commit':12s} {'val_bpb':>8s} {'test_iid':>8s} {'test_shift':>10s}  description")
    for k in res.meta["audit"]:
        print(f"{k['commit']:12s} {k['metric']:8.4f} {k['test_iid']:8.4f} {k['test_shift']:10.4f}  {k['description']}")

    s = res.meta["analysis"]
    print(f"\nbest train.py: {out / 'best_artifact' / 'train.py'}   audit trail: python -m rsi.cli inspect {out}")
    print(
        f"\nSUMMARY autoresearch on tinylm: {s['n_experiments']} experiments, {s['n_keep'] - 1} kept, "
        f"val_bpb {s['baseline']:.4f} -> {s['best']:.4f} (delta {delta:.4f}), spend ${res.usage['_total']['cost_usd']:.2f}"
    )
    if offline:
        print(f"NOTE {OFFLINE_NOTE} (scripted agent; the tiny training runs are real)")
    return {"rows": rows, "analysis": s, "noise": res.meta["noise"], "out": str(out)}


if __name__ == "__main__":
    main()
