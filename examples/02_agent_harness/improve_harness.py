"""Improve an agent harness (harness.py + prompts) with RRSI on the built-in AgentQA domain.

Offline (default, ~10 s, no API key):  python examples/02_agent_harness/improve_harness.py
Live (claude CLI, uses your plan):     python examples/02_agent_harness/improve_harness.py --llm claude:haiku
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

if importlib.util.find_spec("rsi") is None:  # not pip-installed: use the clone this example lives in
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import rsi
from rsi.core import usd_of
from rsi.domains.agentqa import AgentQADomain, SimModel, make_suite
from rsi.rrsi.mocks import AgentQAMockLLM
from rsi.trace import inspect

OFFLINE_NOTE = "offline simulated model: numbers show the loop working, not real gains"


def main(argv: list[str] | None = None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--llm", default="offline", help="offline (simulated) | claude:haiku | api:sonnet | ...")
    ap.add_argument("--out", default=None, help="run directory (default: a fresh temp dir)")
    ap.add_argument("--rounds", type=int, default=6, help="RRSI rounds T")
    a = ap.parse_args(argv)
    out = Path(a.out or tempfile.mkdtemp(prefix="rsi-ex02-"))
    offline = a.llm == "offline"

    # 1. The problem: AgentQA ships tasks (evolve = practice, holdout = same kind, ood = unseen kinds; the last
    #    two are sealed), a locked exact-match grader and a seed harness (harness.py + prompts/*.md).
    suite = make_suite(seed=0)
    dom = AgentQADomain(suite)
    seed = dom.seed_artifact()
    print("splits:", {k: v["n"] for k, v in suite.summary().items()}, "| seed files:", sorted(seed))

    # 2. The models. Offline: SimModel answers like a model whose accuracy depends on what the harness does
    #    (format line, Python tool, ...), and AgentQAMockLLM is RRSI's scripted proposer + critic.
    if offline:
        llm_task, llm_propose = SimModel(suite), AgentQAMockLLM()
    else:  # ---- LIVE: real model calls, cached so a rerun replays for free ----
        llm_task = llm_propose = rsi.get_llm(a.llm, cache_dir=str(Path(".rsi_cache") / "example-02"))

    # 3. The run. RRSI measures its own noise band (delta) on the seed before round 1. Each round it admits a
    #    candidate only if it scores within delta of the best so far, passes the leakage critic and earns its extra
    #    cost, then keeps the best admitted one (or the current harness if none is admitted).
    res = rsi.improve(
        dom, seed, method="rrsi", llm_task=llm_task, llm_propose=llm_propose, config={"T": a.rounds}, out_dir=out
    )
    res.save(out)
    print(f"\nnoise band delta = {res.meta['delta']:.3f} (from k=2 trials of the seed)")

    # 4. What was kept: history.jsonl has one row per proposed edit with its outcome (ACCEPTED = part of the
    #    round's winning candidate; REJECTED / LOST / critic_reject carry the reason). A candidate bundles up to
    #    b_t edits, so a kept bundle can carry a useless edit along with a useful one: check TRACE.md.
    rows = [json.loads(line) for line in (out / "history.jsonl").read_text().splitlines() if line.strip()]
    kept = [r for r in rows if r["outcome"] == "ACCEPTED"]
    n_prop = sum(r["outcome"] not in ("BASELINE", "no_proposal") for r in rows)
    print(f"\nkept edits ({len(kept)} of {n_prop} proposed):")
    for r in kept:
        print(f"  round {r['t']} cand {r['variant']}  {r['component']:12s} {r['hypothesis']}  (evolve {r['S']:.3f})")
    if not kept:
        print("  none: no change beat the noise band; the seed harness is kept")
    print("\nfiles changed vs seed:", res.best.changed_files(res.baseline) or "none")

    # 5. Seed vs final on every split with a paired 95% CI; holdout and ood are sealed, scored only after the run.
    rep = rsi.transfer_report(dom, llm_task, {"seed": res.baseline, "rrsi": res.best}, k=2)
    print(f"\n{'split':8s} {'seed':>6s} {'rrsi':>6s}  paired diff [95% CI]   tokens/task seed -> rrsi")
    for split, row in rep["splits"].items():
        d = row["rrsi"]["vs_reference"]
        print(
            f"{split:8s} {row['seed']['S']:6.3f} {row['rrsi']['S']:6.3f}  "
            f"{d['mean_diff']:+.3f} [{d['lo']:+.3f}, {d['hi']:+.3f}]   {row['seed']['C']:.0f} -> {row['rrsi']['C']:.0f}"
        )

    # 6. The audit trail: every round's analysis, proposal, diff, critic verdict, gate arithmetic and decision.
    trace_md = inspect(out)
    print(f"\naudit trail: {trace_md}   (re-render: python -m rsi.cli inspect {out})")
    print(f"best harness: {out / 'best_artifact'}")
    usd = usd_of(llm_task) + (usd_of(llm_propose) if llm_propose is not llm_task else 0.0)  # every call, all roles
    hold = rep["splits"]["holdout"]
    print(
        f"\nSUMMARY rrsi on agentqa: {len(kept)} kept edits, holdout {hold['seed']['S']:.3f} -> "
        f"{hold['rrsi']['S']:.3f}, spend ${usd:.2f}"
    )
    if offline:
        print(f"NOTE {OFFLINE_NOTE}")
    return {"kept": kept, "report": rep, "out": str(out), "trace": trace_md}


if __name__ == "__main__":
    main()
