"""Validation-stage tests for the autoresearch trace instrumentation.

* the trace records every experiment with the uniform rsi.trace event kinds;
* the shadow monitor (hidden-split audit of every new incumbent) is WRITE-ONLY:
  runs with and without it make identical decisions (same ledger statuses,
  scores, results.tsv) on deterministic tasks - the landscape, a Domain task
  (AgentQA + SimModel, rsi.trace.ShadowMonitor) and tinylm under a token budget.
"""
import json

import pytest

from rsi.autoresearch import Config, MockResearchAgent, run
from rsi.autoresearch.landscape import LandscapeTask
from rsi.trace import load_trace, render_markdown


def _decisions(res):
    return [(n.round, n.kind, n.status, None if n.score is None else round(n.score, 12), n.change, n.artifact_id)
            for n in res.ledger.nodes()]


def _landscape(tmp_path, name, monitor, **mock):
    cfg = Config(max_experiments=14, plot=False, shadow_monitor=monitor, hidden_audit=True)
    return run(LandscapeTask(seed=3), config=cfg, out_dir=tmp_path / name,
               mock={"crash_rate": 0.15, "exploit_rate": 0.15, **mock})


def test_trace_has_uniform_events(tmp_path):
    res = _landscape(tmp_path, "a", True)
    ev = load_trace(res.out_dir)
    kinds = {e["kind"] for e in ev}
    for k in ("run_start", "baseline", "round_start", "analysis", "proposal", "critic", "eval", "gate", "decision",
              "state", "monitor", "run_end"):
        assert k in kinds, k
    # one decision per agent turn, one gate per candidate that ran to a keep decision
    rounds = {e["round"] for e in ev if e["kind"] == "round_start"}
    assert rounds == set(range(1, 15))
    for r in rounds:
        assert sum(1 for e in ev if e["round"] == r and e["kind"] == "decision") == 1
    # every kept version is shadow-audited exactly once (baseline included) and the decision says kept
    kept = [e for e in ev if e["kind"] == "decision" and e["data"]["kept"]]
    mons = [e for e in ev if e["kind"] == "monitor"]
    assert len(mons) == len(kept) + 1
    assert all("test_iid" in m["data"]["sealed"] for m in mons)
    # gate arithmetic is consistent with the decision
    for g in (e for e in ev if e["kind"] == "gate"):
        m = g["data"]["math"]
        d = next(e for e in ev if e["kind"] == "decision" and e["round"] == g["round"])
        if g["data"]["reason"].startswith("gain"):
            assert (m["gain(ref-cand, sign-adjusted)"] > 1e-9) == (d["data"]["status"] == "keep")
    # proposals carry the actual diff
    props = [e for e in ev if e["kind"] == "proposal" and e["data"]["stage"] == "propose" and not e["data"]["error"]]
    assert props and all(p["data"]["diff"].startswith("---") for p in props)
    md = render_markdown(ev)
    assert "Shadow monitor" in md and "Gate on" in md


def test_trace_disabled_by_flag_or_without_out_dir(tmp_path):
    res = run(LandscapeTask(seed=3), config=Config(max_experiments=3, plot=False, trace=False), out_dir=tmp_path / "x")
    assert not (tmp_path / "x" / "trace.jsonl").exists()
    res = run(LandscapeTask(seed=3), config=Config(max_experiments=3, plot=False, persist=False))
    assert res.best is not None


def test_monitor_is_write_only_landscape(tmp_path):
    a = _landscape(tmp_path, "with", True)
    b = _landscape(tmp_path, "without", False)
    assert _decisions(a) == _decisions(b)
    assert (tmp_path / "with" / "results.tsv").read_text() == (tmp_path / "without" / "results.tsv").read_text()
    assert any(e["kind"] == "monitor" for e in load_trace(a.out_dir))
    assert not any(e["kind"] == "monitor" for e in load_trace(b.out_dir))
    # the post-hoc hidden audit reuses the shadow audits: same table either way
    strip = lambda rows: [{k: v for k, v in r.items()} for r in rows]  # noqa: E731
    assert strip(a.meta["audit"]) == strip(b.meta["audit"])


def test_monitor_is_write_only_domain_shadowmonitor(tmp_path):
    from rsi.autoresearch.pools import harness_edit_pool
    from rsi.domains.agentqa import AgentQADomain, SimModel

    def go(name, monitor):
        dom = AgentQADomain()
        return run(dom, dom.seed_artifact(), llm_task=SimModel(dom.tasks),
                   agent=MockResearchAgent(harness_edit_pool(), seed=1), task_kwargs={"k": 1},
                   config=Config(max_experiments=5, plot=False, shadow_monitor=monitor, hidden_audit=False),
                   out_dir=tmp_path / name)

    a, b = go("with", True), go("without", False)
    assert _decisions(a) == _decisions(b)
    mons = [e for e in load_trace(a.out_dir) if e["kind"] == "monitor"]
    assert mons and set(mons[0]["data"]["sealed"]) == {"holdout", "ood"}
    ev = load_trace(a.out_dir)
    assert json.loads(json.dumps(ev[0]))["data"]["config"]["monitor"] == "ShadowMonitor"
    # per-task scores of each Domain experiment are in the eval events
    assert any(e["kind"] == "eval" and e["data"]["per_task"] for e in ev)


@pytest.fixture(scope="module")
def tinylm_tokens(tmp_path_factory):
    from rsi.domains.tinylm import TinyLMTask

    root = tmp_path_factory.mktemp("tinylm_data")
    task = TinyLMTask(budget_s=6000, budget_kind="tokens", kill_after=30.0, data_root=root)
    task.prepare()
    return task


def test_monitor_is_write_only_tinylm(tmp_path, tinylm_tokens):
    def go(name, monitor):
        return run(tinylm_tokens, config=Config(max_experiments=4, plot=False, shadow_monitor=monitor,
                                                hidden_audit=False, seed=2),
                   out_dir=tmp_path / name)

    a, b = go("with", True), go("without", False)
    assert _decisions(a) == _decisions(b)
    mons = [e for e in load_trace(a.out_dir) if e["kind"] == "monitor"]
    assert mons and {"test_iid", "test_shift"} <= set(mons[0]["data"]["sealed"])


def test_llm_agent_strips_reply_debris_seen_live():
    """Live run (validation/autoresearch/tinylm_live, round 1): claude -p appended commit
    trailers after the file block, and a fix reply left a lone closing fence; both made
    train.py a SyntaxError. The agent now strips such lines from the end of changed files."""
    from rsi.autoresearch.agent import AgentContext, LLMResearchAgent
    from rsi.core import Artifact, MockLLM, RewriteEditor

    base = Artifact({"train.py": "LR = 0.1\nprint('val_bpb: 1.0')\n", "notes.md": "x\n"})
    reply = ('```json\n{"change": "LR 0.1 -> 0.2"}\n```\n=== FILE: train.py ===\nLR = 0.2\nprint(\'val_bpb: 1.0\')\n'
             '\nCo-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>\nClaude-Session: https://example/s\n'
             '=== FILE: notes.md ===\nexample:\n```\ncode\n```\n')
    ag = LLMResearchAgent(RewriteEditor(MockLLM(lambda *a: reply)))
    ctx = AgentContext(program="p", artifact=base, editable=("train.py",), locked=(), results_tsv="", git_log="")
    prop = ag.propose(ctx)
    assert prop.artifact["train.py"] == "LR = 0.2\nprint('val_bpb: 1.0')\n"
    assert prop.artifact["notes.md"].endswith("```\n")                  # a closing fence is legal in markdown
    assert "Co-Authored-By" in prop.meta["sanitized"]["train.py"][0]
    fence = Artifact({"train.py": "LR = 0.2\nx = 1 +\n"})                   # crashed candidate (typo)
    ag2 = LLMResearchAgent(RewriteEditor(MockLLM(lambda *a: '{"change": "fix"}\n=== FILE: train.py ===\nLR = 0.2\nx = 2\n```\n')))
    fixed = ag2.fix_crash(ctx, fence, "d", "SyntaxError")
    assert fixed.artifact["train.py"] == "LR = 0.2\nx = 2\n"
    raw = LLMResearchAgent(RewriteEditor(MockLLM(lambda *a: reply)), sanitize=False).propose(ctx)
    assert "Co-Authored-By" in raw.artifact["train.py"]
