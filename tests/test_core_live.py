"""Live smoke tests against the real headless ``claude`` CLI (Claude Haiku).

Deselected by default (``-m 'not live'`` in pyproject); run with::

    pytest tests/test_core_live.py -m live -v

Cost: a few cents per run (one plain completion, one cached pair, one short
coding-agent edit). Cached replies live in ``.rsi_cache/core-qa``.
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import pytest

from rsi.core import AgentEditor, Artifact, CachedLLM, ClaudeCLI

pytestmark = [pytest.mark.live,
              pytest.mark.skipif(shutil.which("claude") is None, reason="claude CLI not installed")]

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".rsi_cache" / "core-qa"
RECORD: dict = {}


@pytest.fixture(scope="module", autouse=True)
def _record_outcome():
    """Write what the live smoke did (and cost) to results/core-qa/live_smoke.json."""
    t0 = time.time()
    yield
    out = ROOT / "results" / "core-qa" / "live_smoke.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    total = sum(r.get("cost_usd", 0.0) for r in RECORD.values())
    out.write_text(json.dumps({"when": time.strftime("%Y-%m-%d %H:%M:%S"), "wall_s": round(time.time() - t0, 1),
                               "total_cost_usd": round(total, 5), "tests": RECORD}, indent=1))


def test_claude_cli_complete_live():
    cli = ClaudeCLI("haiku", timeout_s=120, retries=2)
    r = cli.complete("Reply with exactly one word: pong", system="You follow formatting instructions exactly.",
                     role="smoke")
    assert r.ok, r.error
    assert "pong" in r.text.lower()
    assert r.usage.calls == 1 and r.usage.input_tokens > 0 and r.usage.output_tokens > 0
    assert r.usage.cost_usd > 0 and r.model == cli.model
    RECORD["claude_cli_complete"] = {"ok": r.ok, "text": r.text[:100], "model": r.model, **r.usage.to_dict()}
    snap = cli.meter.snapshot()
    assert snap["smoke"]["calls"] == 1 and snap["_total"]["cost_usd"] == pytest.approx(r.usage.cost_usd)


def test_cached_claude_cli_live():
    c = CachedLLM(ClaudeCLI("haiku", timeout_s=120, retries=2), CACHE)
    prompt = "What is 17 * 3? Reply with just the number."
    first = c.complete(prompt, seed=0, role="smoke")
    second = c.complete(prompt, seed=0, role="smoke")
    RECORD["cached_claude_cli"] = {"first": first.text[:50], "second_calls": second.usage.calls,
                                   "hits": c.hits, "misses": c.misses, "cost_usd": c.meter.total().cost_usd}
    assert first.ok and second.ok, (first.error, second.error)
    assert "51" in first.text and second.text == first.text
    assert second.usage.calls == 0 and second.usage.cost_usd == 0.0 and c.hits >= 1
    assert c.meter.snapshot()["smoke:cached"]["cost_usd"] == 0.0


def test_agent_editor_live(tmp_path):
    base = Artifact({"greeting.txt": "hello\n", "locked.txt": "do not touch\n"})
    ed = AgentEditor(ClaudeCLI("haiku", timeout_s=300, retries=1), timeout_s=300)
    p = ed.edit(base,
                "Edit greeting.txt so that its entire content is the single line: hello world\n"
                "Do not modify any other file.",
                context={"history.md": "No previous attempts."}, editable=["greeting.txt"], role="proposer")
    RECORD["agent_editor"] = {"ok": p.ok, "error": p.error, "change": p.change, "blocked": p.blocked_files,
                              "greeting": p.artifact["greeting.txt"] if p.artifact else None,
                              "diff": base.diff(p.artifact) if p.artifact else "", **p.usage.to_dict()}
    assert p.ok, (p.error, p.raw[-500:])
    assert p.artifact["greeting.txt"].strip() == "hello world"
    assert p.artifact["locked.txt"] == "do not touch\n"
    assert not any(name.startswith("_context") or name == "_proposal.json" for name in p.artifact)
    assert p.usage.calls == 1 and p.usage.cost_usd > 0
