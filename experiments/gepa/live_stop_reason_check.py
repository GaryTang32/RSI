"""Live check for the claim-audit truncation finding (docs/claims/gepa.md, section 3 item 3).

One GEPA reflection call through ``CachedLLM(ClaudeCLI("haiku"))``, made twice with the same
seed: the first is a cache miss, the second a cache hit. It records the provider stop reason
that :class:`rsi.gepa.reflection.ReflectionProposer` sees on each (``ReflectionResult.finish``)
and the ``stop_reason`` stored in the cache entry. Before the core fix a hit carried
``raw={"cached": True, "usage": ...}`` only, so the known-truncation check never saw a
``max_tokens`` reason on a replayed reply.

    python experiments/gepa/live_stop_reason_check.py [--fresh]

``--fresh`` deletes the cache first (about $0.005-0.007 of haiku) and writes
``results/gepa/live_stop_reason_check.json``; without it the check replays from
``.rsi_cache/gepa_fix_live`` for $0 (both calls are then hits) and only prints.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import ROOT, save  # noqa: E402

from rsi.core import Artifact, CachedLLM, ClaudeCLI  # noqa: E402
from rsi.gepa.reflection import ReflectionProposer  # noqa: E402

CACHE = ROOT / ".rsi_cache" / "gepa_fix_live"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fresh", action="store_true", help="delete the cache first (a live call)")
    a = ap.parse_args()
    if a.fresh and CACHE.exists():
        shutil.rmtree(CACHE)
    llm = CachedLLM(ClaudeCLI("haiku", timeout_s=240), CACHE)
    prop = ReflectionProposer(llm)
    art = Artifact({"prompts/triage.md": "Route the ticket to one team.\n"})
    recs = {"prompts/triage.md": [{"Inputs": "Customer writes about a refund problem",
                                   "Generated Outputs": "engineering",
                                   "Feedback": "Wrong: refund tickets must go to the billing team."}]}
    calls = []
    for k in range(2):
        hits = llm.hits
        r = prop.propose(art, recs, ["prompts/triage.md"], seed_fn=lambda c: 7)
        calls.append({"call": k, "cache_hit": llm.hits > hits, "finish_reason": r.finish.get("prompts/triage.md"),
                      "parsed": bool(r.new_texts), "rejected": r.rejected,
                      "new_text_head": r.new_texts.get("prompts/triage.md", "")[:160]})
    entry = json.loads(next(CACHE.rglob("*.json")).read_text())
    usage = llm.meter.snapshot().get("_total", {})
    out = {"experiment": "live stop_reason check (reflection truncation finding)", "llm": "claude:haiku",
           "cache": str(CACHE.relative_to(ROOT)), "fresh": a.fresh, "calls": calls,
           "cache_entry_stop_reason": entry.get("stop_reason"), "spend_usd": usage.get("cost_usd", 0.0),
           "verdict": ("claude -p reports stop_reason; CachedLLM stores it and returns it on a hit; the reflection "
                       "proposer sees the same reason on miss and hit: "
                       + ", ".join(f"call {c['call']} ({'hit' if c['cache_hit'] else 'miss'}) -> {c['finish_reason']!r}"
                                   for c in calls))}
    if a.fresh:                 # only a fresh run is evidence of a cache miss; a replay just prints
        save("live_stop_reason_check", out)
    else:
        print(json.dumps(out["calls"], indent=1))
    print(out["verdict"], f"spend ${out['spend_usd']:.4f}")


if __name__ == "__main__":
    main()
