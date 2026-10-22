"""Trace events, and the write-only shadow monitor: runs with and without it make identical decisions."""
import json

from autoresearch_helpers import context, quiet
from rsi.autoresearch import Config, LandscapeTask, LLMResearchAgent, MockResearchAgent, run
from rsi.autoresearch.pools import harness_edit_pool
from rsi.core import Artifact, MockLLM, RewriteEditor
from rsi.domains.agentqa import AgentQADomain, SimModel
from rsi.trace import load_trace, render_markdown


def _decisions(res):
    return [(n.round, n.kind, n.status, None if n.score is None else round(n.score, 12), n.change, n.artifact_id)
            for n in res.ledger.nodes()]


def _landscape(tmp_path, name, monitor):
    cfg = Config(max_experiments=14, plot=False, shadow_monitor=monitor, hidden_audit=True)
    return run(LandscapeTask(seed=3), config=cfg, out_dir=tmp_path / name,
               mock={"crash_rate": 0.15, "exploit_rate": 0.15})


def _monitors(res) -> list:
    return [e for e in load_trace(res.out_dir) if e["kind"] == "monitor"]


def test_trace_has_uniform_events(tmp_path):
    ev = load_trace(_landscape(tmp_path, "a", True).out_dir)
    assert {"run_start", "baseline", "round_start", "analysis", "proposal", "critic", "eval", "gate", "decision",
            "state", "monitor", "run_end"} <= {e["kind"] for e in ev}
    rounds = {e["round"] for e in ev if e["kind"] == "round_start"}
    assert rounds == set(range(1, 15))
    assert all(sum(e["round"] == r and e["kind"] == "decision" for e in ev) == 1 for r in rounds)
    # every kept version, the baseline included, is shadow-audited exactly once
    kept = [e for e in ev if e["kind"] == "decision" and e["data"]["kept"]]
    monitors = [e for e in ev if e["kind"] == "monitor"]
    assert len(monitors) == len(kept) + 1 and all("test_iid" in m["data"]["sealed"] for m in monitors)
    for g in (e for e in ev if e["kind"] == "gate"):
        d = next(e for e in ev if e["kind"] == "decision" and e["round"] == g["round"])
        if g["data"]["reason"].startswith("gain"):
            assert (g["data"]["math"]["gain(ref-cand, sign-adjusted)"] > 1e-9) == (d["data"]["status"] == "keep")
    props = [e for e in ev if e["kind"] == "proposal" and e["data"]["stage"] == "propose" and not e["data"]["error"]]
    assert props and all(p["data"]["diff"].startswith("---") for p in props)
    md = render_markdown(ev)
    assert "Shadow monitor" in md and "Gate on" in md


def test_trace_disabled_by_flag_or_without_out_dir(tmp_path):
    run(LandscapeTask(seed=3), config=Config(max_experiments=3, plot=False, trace=False), out_dir=tmp_path / "x")
    assert not (tmp_path / "x" / "trace.jsonl").exists()
    assert run(LandscapeTask(seed=3), config=Config(max_experiments=3, plot=False, persist=False)).best is not None


def test_monitor_is_write_only_landscape(tmp_path):
    a, b = _landscape(tmp_path, "with", True), _landscape(tmp_path, "without", False)
    assert _decisions(a) == _decisions(b)
    assert (tmp_path / "with" / "results.tsv").read_text() == (tmp_path / "without" / "results.tsv").read_text()
    assert _monitors(a) and not _monitors(b)
    assert a.meta["audit"] == b.meta["audit"]  # the post-hoc audit reuses the shadow audits


def test_monitor_is_write_only_domain_shadowmonitor(tmp_path):
    def go(name, monitor):
        dom = AgentQADomain()
        return run(dom, dom.seed_artifact(), llm_task=SimModel(dom.tasks),
                   agent=MockResearchAgent(harness_edit_pool(), seed=1), task_kwargs={"k": 1},
                   config=quiet(max_experiments=5, shadow_monitor=monitor), out_dir=tmp_path / name)

    a, b = go("with", True), go("without", False)
    assert _decisions(a) == _decisions(b)
    monitors = _monitors(a)
    assert monitors and set(monitors[0]["data"]["sealed"]) == {"holdout", "ood"}
    ev = load_trace(a.out_dir)
    assert json.loads(json.dumps(ev[0]))["data"]["config"]["monitor"] == "ShadowMonitor"
    assert any(e["kind"] == "eval" and e["data"]["per_task"] for e in ev)  # per-task scores of each experiment


def test_monitor_is_write_only_tinylm(tmp_path, tinylm_tokens_6k):
    def go(name, monitor):
        return run(tinylm_tokens_6k, config=quiet(max_experiments=4, shadow_monitor=monitor, seed=2),
                   out_dir=tmp_path / name)

    a, b = go("with", True), go("without", False)
    assert _decisions(a) == _decisions(b)
    monitors = _monitors(a)
    assert monitors and {"test_iid", "test_shift"} <= set(monitors[0]["data"]["sealed"])


def test_llm_agent_strips_reply_debris_seen_live():
    """Commit trailers and a stray closing fence after a file block are stripped from changed files."""
    base = Artifact({"train.py": "LR = 0.1\nprint('val_bpb: 1.0')\n", "notes.md": "x\n"})
    reply = ('```json\n{"change": "LR 0.1 -> 0.2"}\n```\n=== FILE: train.py ===\nLR = 0.2\nprint(\'val_bpb: 1.0\')\n'
             '\nCo-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>\nClaude-Session: https://example/s\n'
             '=== FILE: notes.md ===\nexample:\n```\ncode\n```\n')
    ctx = context(base, program="p")
    prop = LLMResearchAgent(RewriteEditor(MockLLM(lambda *a: reply))).propose(ctx)
    assert prop.artifact["train.py"] == "LR = 0.2\nprint('val_bpb: 1.0')\n"
    assert prop.artifact["notes.md"].endswith("```\n")  # a closing fence is legal in markdown
    assert "Co-Authored-By" in prop.meta["sanitized"]["train.py"][0]
    fix_reply = '{"change": "fix"}\n=== FILE: train.py ===\nLR = 0.2\nx = 2\n```\n'
    fixed = LLMResearchAgent(RewriteEditor(MockLLM(lambda *a: fix_reply))).fix_crash(
        ctx, Artifact({"train.py": "LR = 0.2\nx = 1 +\n"}), "d", "SyntaxError")
    assert fixed.artifact["train.py"] == "LR = 0.2\nx = 2\n"
    raw = LLMResearchAgent(RewriteEditor(MockLLM(lambda *a: reply)), sanitize=False).propose(ctx)
    assert "Co-Authored-By" in raw.artifact["train.py"]
