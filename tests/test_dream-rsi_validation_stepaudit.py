"""Stage-B (independent step audit) regressions for Dream-RSI.

* reply parsing: every reply shape seen in (or adjacent to) the live validation run yields a program
  that compiles and scores - including a LEADING ```python fence whose closing fence is followed by
  ``===`` (the residual case found by the stage-B audit);
* a repaired developer revision reports its substantive claim plus the repair;
* an independent replay implementation (experiments/dream-rsi/validate_stepaudit.py, written from
  the paper's §3 semantics) reproduces every replay value the loop recorded.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from rsi.core.artifact import Artifact
from rsi.core.llm import MockLLM
from rsi.domains.discovery import SumDiffDomain
from rsi.dream import Config, EditorAgent, LLMPolicyDeveloper, run
from rsi.dream.agent import AttemptContext, strip_reply_terminators
from rsi.dream.developer import DevContext, VersionRecord
from rsi.dream.policy import template_code

ROOT = Path(__file__).resolve().parents[1]
HDR = json.dumps({"change": "add a hill stage", "hypothesis": "h", "components": ["STAGES"]})


def _ctx(dom):
    return AttemptContext(dom.describe(), "root", dom.seed_artifact(), 0.91, 0, 0, 1, editable=["construct.py"])


def test_every_observed_reply_shape_yields_a_scoring_program():
    dom = SumDiffDomain(sandboxed=False)
    body = dom.seed_artifact()["construct.py"].replace("STAGES = []", 'STAGES = [["hill", {"iters": 10}, 7]]')
    shapes = [
        body + "===\n",                                  # live run: 7 of 12 failures
        body + "```\n",                                  # live run: 5 of 12 failures
        "```python\n" + body + "```\n===\n",             # stage B: leading fence + terminator
        "```python\n" + body,                            # stage B: leading fence, no closing fence
        "\n```py\n" + body + "```\n=== END FILE ===\n",
        body,                                            # clean
    ]
    for tail in shapes:
        reply = f"```json\n{HDR}\n```\n\n=== FILE: construct.py ===\n{tail}"
        agent = EditorAgent(MockLLM(lambda p, s, seed, i, r=reply: r), editable=["construct.py"])
        att = agent.attempt(_ctx(dom), seed=1)
        assert att.error is None, tail[:30]
        compile(att.artifact["construct.py"], "construct.py", "exec")
        ev = dom.evaluate_program(att.artifact)
        assert ev.fail_class == "ok" and ev.score > 0.9, (tail[:30], ev.error)


def test_leading_fence_is_dropped_only_at_the_file_start():
    code = "x = 1\ns = '''\n```python\n'''\n"
    a, fixed = strip_reply_terminators(Artifact({"m.py": code}), ["*.py"])
    assert fixed == [] and a["m.py"] == code          # a fence inside a string literal is untouched
    a, fixed = strip_reply_terminators(Artifact({"m.py": "```python\n" + code, "n.md": "```\n"}), ["*"])
    assert fixed == ["m.py"] and a["m.py"] == code and a["n.md"] == "```\n"


def test_repaired_revision_keeps_its_first_claim():
    good = template_code("adaptive").replace('"default_beta": 0.6', '"default_beta": 0.7')
    replies = [
        f"```json\n{json.dumps({'change': 'raise beta', 'hypothesis': 'h'})}\n```\n=== FILE: method.py ===\n"
        + good.replace("def plan_grid", "def plan_grid(:"),                       # a genuine syntax error
        f"```json\n{json.dumps({'change': 'fix the syntax error', 'hypothesis': 'h'})}\n```\n"
        f"=== FILE: method.py ===\n{good}",
    ]
    dev = LLMPolicyDeveloper(MockLLM(lambda p, s, seed, i: replies[min(i, 1)]))
    rev = dev.revise(DevContext(1, [VersionRecord(0, template_code("adaptive"))], [], [], "", "eq1", 3))
    assert rev.ok and rev.meta["repairs"] == 1
    assert rev.change == "raise beta [repaired: fix the syntax error]"


def test_independent_replay_reproduces_every_recorded_replay_value(tmp_path):
    sys.path.insert(0, str(ROOT / "experiments" / "dream-rsi"))
    from validate_stepaudit import eq1, indep_replay

    dom = SumDiffDomain(sandboxed=False)
    cfg = Config(rounds=3, W=3, branch_count=3, refine_count=3, M=3, sandbox="inprocess", seed=3)
    run(dom, agent=dom.mock_agent(), config=cfg, out_dir=str(tmp_path))
    ev = [json.loads(l) for l in (tmp_path / "trace.jsonl").read_text().splitlines()]
    trees = {int(p.parent.name[4:]): json.loads(p.read_text()) for p in tmp_path.glob("trace_pool/iter*/tree.json")}
    mans = [json.loads(p.read_text()) for p in sorted(tmp_path.glob("trace_pool/iter*/live_cycle_manifest.json"))]
    hist = {p.parent.name.split("_")[0]: p.read_text() for p in tmp_path.glob("history/r*/method.py")}
    c = ev[0]["data"]["config"]
    n = 0
    for e in ev:
        if e["kind"] != "eval" or not e["data"]["candidate"].startswith("r"):
            continue
        code = hist[e["data"]["candidate"].split("_")[0]]
        for i, w in enumerate(e["data"]["worlds"], start=1):
            r = indep_replay(code, trees[i], c["W"], mans, c)
            assert r["error"] is None
            assert [b["batch"] for b in r["batches"]] == [b["batch"] for b in w["reveal_batches"]]
            assert abs(eq1(r["best"], r["root"], r["ceiling"], r["N"], r["k"]) - w["V_i"]) < 1e-5
            n += 1
    assert n >= 6


def test_static_check_quotes_the_offending_line():
    from rsi.dream.guard import static_check

    chk = static_check(template_code("adaptive") + "```\n")
    assert not chk.ok and "offending line: '```'" in chk.errors[0]
