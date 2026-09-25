"""Dream-RSI audit trace (``rsi.trace`` format): write-only, complete and internally consistent.

* the trace and the shadow monitor never change what the loop decides: the same discovery
  ledger (statuses, scores, artifacts), the same policy ledger (statuses, replay values), the
  same trajectory, best program and final policy - with the monitor, without it, without the
  trace and without a run directory (AgentQA through DomainTask: sealed holdout + ood;
  sum-difference: no sealed split, so no monitor);
* the monitor's model spend is kept out of the loop's usage;
* every live cycle records round_start, each online round, each attempt (proposal + eval with the
  actual diff), the best-program gate/decision, the manifest, every policy version (proposal,
  critic, replay eval) and the selection gate/decision, and those records agree with the ledgers.
"""
from __future__ import annotations

import json

from rsi.core.artifact import Artifact
from rsi.domains.agentqa import AgentQADomain, SimModel, make_suite
from rsi.domains.discovery import SumDiffDomain
from rsi.domains.discovery.agentqa_agent import MECHANISMS, agentqa_mock_agent
from rsi.dream import Config, DomainTask, LLMPolicyDeveloper, mock_developer_llm, run
from rsi.trace import inspect, load_trace

KINDS = {"run_start", "noise", "baseline", "round_start", "note", "analysis", "proposal", "critic", "eval", "gate",
         "decision", "state", "monitor", "run_end"}


def decisions(res) -> tuple:
    # (workspace snapshot ids are left out: eval/score.json carries the program's wall time, which
    # differs between identical runs; the program itself is compared through res.best.id)
    disc = [(n.id, n.parent, n.status, n.score, n.change) for n in res.ledger.nodes()]
    pol = [(n.id, n.parent, n.status, n.score, n.artifact_id) for n in res.meta["policy_ledger"].nodes()]
    traj = [{k: v for k, v in r.items() if k != "wall_s"} for r in res.trajectory]
    for r in traj:
        if "dream" in r:
            r["dream"] = {k: v for k, v in r["dream"].items() if k not in ("incumbent_diag",)}
    return disc, pol, json.dumps(traj, sort_keys=True, default=str), res.best.id, res.meta["policy_id"]


def _agentqa(out_dir, **cfg):
    suite = make_suite(n_evolve=6, n_holdout=6, n_ood_per_family=1, seed=1)
    dom = AgentQADomain(suite)
    sim = SimModel(suite)
    task = DomainTask(dom, dom.seed_artifact(), sim, directions=MECHANISMS, workers=2)
    return run(task, llm_task=sim, agent=agentqa_mock_agent(),
               config=Config(rounds=3, W=2, branch_count=3, refine_count=1, M=3, sandbox="inprocess", **cfg),
               out_dir=out_dir)


def test_trace_and_monitor_are_write_only_agentqa(tmp_path):
    a = _agentqa(str(tmp_path / "a"))                             # trace + shadow monitor (holdout, ood)
    b = _agentqa(str(tmp_path / "b"), shadow_monitor=False)       # trace, no monitor
    c = _agentqa(str(tmp_path / "c"), trace=False)                # no trace
    d = _agentqa(None)                                            # no run directory
    ev = load_trace(tmp_path / "a")
    mon = [e for e in ev if e["kind"] == "monitor"]
    assert mon and set(mon[0]["data"]["sealed"]) == {"holdout", "ood"}
    assert mon[0]["data"]["version"] == "seed"
    assert not any(e["kind"] == "monitor" for e in load_trace(tmp_path / "b"))
    assert not (tmp_path / "c" / "trace.jsonl").exists()
    for other in (b, c, d):
        assert decisions(other) == decisions(a)
    # the monitor's spend never enters the loop's usage
    assert a.usage["task"] == b.usage["task"]
    assert not any(k.startswith("shadow") for k in a.usage)
    assert a.meta["shadow_usage"]["_total"]["calls"] > 0
    # one monitor event per distinct new best program (+ the seed)
    kept = [e for e in ev if e["kind"] == "decision" and e["data"].get("level", "").startswith("object")
            and e["data"]["kept"]]
    assert len(mon) == 1 + len({e["data"]["kept"] for e in kept})


def _sumdiff(out_dir, **cfg):
    return run(SumDiffDomain(), config=Config(rounds=3, W=3, branch_count=3, refine_count=2, M=3, seed=1, **cfg),
               out_dir=out_dir)


def test_trace_is_write_only_sumdiff_and_complete(tmp_path):
    a = _sumdiff(str(tmp_path / "a"))
    c = _sumdiff(str(tmp_path / "c"), trace=False)
    d = _sumdiff(None)
    assert decisions(a) == decisions(c) == decisions(d)
    ev = load_trace(tmp_path / "a")
    assert {e["kind"] for e in ev} <= KINDS
    assert not any(e["kind"] == "monitor" for e in ev)             # sum-difference has no sealed split
    assert [e["kind"] for e in ev[:3]] == ["run_start", "noise", "baseline"]
    assert ev[-1]["kind"] == "run_end"
    # every attempt of every live search: one proposal (with the actual diff) and one eval
    nodes = [n for n in a.ledger.nodes() if n.parent is not None]
    props = {e["data"]["candidate"]: e["data"] for e in ev if e["kind"] == "proposal" and e["data"]["candidate"][0] == "t"}
    evals = {e["data"]["candidate"]: e["data"] for e in ev if e["kind"] == "eval" and e["data"]["candidate"][0] == "t"}
    assert set(props) == set(evals) == {n.id for n in nodes}
    for n in nodes:
        assert evals[n.id]["summary"]["S"] == n.score
        assert props[n.id]["diff"].startswith("--- a/construct.py")
    # the online rounds in the trace are exactly the recorded batches of each world
    for t, w in enumerate(a.meta["worlds"], 1):
        rounds = [e["data"] for e in ev if e["kind"] == "note" and e["round"] == t and e["data"]["what"] == "online_round"]
        by_round = {}
        for n in w.non_root():
            by_round.setdefault(n.round, []).append(n.id)
        assert [sorted(r["batch"]) for r in rounds] == [sorted(by_round[k]) for k in sorted(by_round)]
    # dreaming: one replay eval per evaluated version, one gate per version, one policy decision per phase
    pol = a.meta["policy_ledger"].nodes()
    for t, row in enumerate(a.trajectory, 1):
        if "dream" not in row:
            continue
        gates = [e["data"] for e in ev if e["kind"] == "gate" and e["round"] == t
                 and e["data"].get("level", "").startswith("policy")]
        assert [g["math"]["V_m"] for g in gates] == [round(v, 6) for v in row["dream"]["values"]]
        assert sum(g["accept"] for g in gates) == 1 and gates[row["dream"]["selected"]]["accept"]
        dec = [e["data"] for e in ev if e["kind"] == "decision" and e["round"] == t
               and e["data"]["level"].startswith("policy")]
        assert len(dec) == 1
        # proposal diffs are the actual code change from the version the developer started from
        for e in ev:
            if e["kind"] == "proposal" and e["round"] == t and e["data"]["candidate"].startswith("r"):
                rid = e["data"]["candidate"].split("_")[0]
                node = next(n for n in pol if n.id == rid)
                assert node.artifact_id is not None
                assert e["data"]["diff"].startswith("--- a/method.py")
    # replayed Eq.-1 arithmetic of every world in every replay eval
    for e in ev:
        if e["kind"] == "eval" and "worlds" in e["data"]:
            for w in e["data"]["worlds"]:
                if w.get("terms"):
                    tm = w["terms"]
                    v = tm["quality"] + tm["cost"] + tm["parallel_bonus"]
                    assert abs(v - w["V_i"]) < 1e-5
                    assert abs(tm["cost"] + 0.01 * w["N"]) < 1e-9
    md = inspect(tmp_path / "a")
    text = open(md).read()
    assert "## Round 1" in text and "Decision:" in text and "Actual diff" in text


def test_llm_developer_prompts_and_screen_are_traced(tmp_path):
    res = run(SumDiffDomain(), config=Config(rounds=2, W=3, branch_count=2, refine_count=1, M=2, seed=0),
              developer=LLMPolicyDeveloper(mock_developer_llm(0)), out_dir=str(tmp_path))
    ev = load_trace(tmp_path)
    props = [e["data"] for e in ev if e["kind"] == "proposal" and e["data"]["candidate"].startswith("r")]
    assert props and all("Edit only `method.py`" in p["prompt"] or "prefix-only exploration policy" in p["prompt"]
                         for p in props)
    assert all("=== FILE: method.py ===" in p["reply"] for p in props)
    crit = [e["data"] for e in ev if e["kind"] == "critic"]
    assert crit and any("static check + leakage screen" in c["stage"] for c in crit)
    assert list((tmp_path / "dream_prompts").glob("*_prompt.txt"))
    assert res.meta["best_score"] >= res.meta["seed_score"]


def test_editor_agent_prompt_and_reply_are_traced(tmp_path):
    """The Listing-1 agent path records the exact prompt, the raw reply and the actual diff."""
    from rsi.core.llm import MockLLM
    from rsi.dream import EditorAgent

    def respond(prompt, system, seed, i):
        code = prompt.split("=== FILE: construct.py ===\n", 1)[1].split("\n=== FILE:", 1)[0] \
            if "=== FILE: construct.py ===" in prompt else None
        if code is None:
            return "no"
        code = code.replace("STAGES = []", 'STAGES = [["hill", {"iters": 10}, 7]]', 1)
        hdr = json.dumps({"change": "add a hill stage", "hypothesis": "local search helps", "components": ["program"]})
        return f"```json\n{hdr}\n```\n=== FILE: construct.py ===\n{code}"

    agent = EditorAgent(MockLLM(respond), editable=["construct.py"])
    run(SumDiffDomain(), agent=agent, config=Config(rounds=1, W=2, branch_count=2, refine_count=0, M=2),
        out_dir=str(tmp_path))
    ev = load_trace(tmp_path)
    props = [e["data"] for e in ev if e["kind"] == "proposal" and e["data"]["candidate"].startswith("t1/")]
    assert len(props) == 2
    for p in props:
        assert "You must read every historical proposal" in p["prompt"]
        assert p["reply"].startswith("```json")
        assert p["change"] == "add a hill stage"
        assert '+STAGES = [["hill", {"iters": 10}, 7]]' in p["diff"]


def test_strip_reply_terminators_repairs_fenced_file_ends():
    """The live validation run lost 6/12 round-1 attempts to a trailing ``===`` / closing fence that
    the reply parser left in construct.py; the EditorAgent now drops such lines (and only those)."""
    from rsi.dream.agent import strip_reply_terminators

    body = "def construct():\n    return [0, 1, 3]\n"
    for tail in ("===\n", "```\n", "```python\n\n", "=== END FILE ===\n", "=== END ===\n"):
        a, fixed = strip_reply_terminators(Artifact({"construct.py": body + tail, "notes.md": "x\n===\n"}),
                                           ["construct.py"])
        assert fixed == ["construct.py"] and a["construct.py"] == body and a["notes.md"] == "x\n===\n"
    a, fixed = strip_reply_terminators(Artifact({"construct.py": body + "x = '==='\n"}), ["construct.py"])
    assert fixed == [] and a["construct.py"].endswith("x = '==='\n")
    compile(strip_reply_terminators(Artifact({"construct.py": body + "===\n"}), ["*.py"])[0]["construct.py"],
            "c", "exec")


def test_attempt_record_shows_the_cause_of_a_failure():
    from rsi.dream.agent import AttemptRecord

    tb = ('  File "/tmp/x/_runner.py", line 12, in <module>\n    _main()\n' * 6 +
          '  File "/tmp/x/candidate.py", line 121\n    ===\n    ^^\nSyntaxError: invalid syntax')
    text = AttemptRecord("b0.a1", 0, 1, 2, "p", 0.0, "compile_other", tb).render()
    assert "SyntaxError: invalid syntax" in text and "===" in text


def test_llm_developer_strips_a_trailing_fence_instead_of_a_repair_round():
    """In the live run all 3 developer 'syntax errors' were a trailing ``` left by the reply parser;
    the repair round then rewrote the policy on a wrong diagnosis. Now the fence is dropped."""
    from rsi.core.llm import MockLLM
    from rsi.dream.developer import DevContext, VersionRecord
    from rsi.dream.policy import template_code

    code = template_code("adaptive").replace('"default_beta": 0.6', '"default_beta": 0.7')
    hdr = json.dumps({"change": "raise the default beta", "hypothesis": "h", "components": ["policy"]})
    dev = LLMPolicyDeveloper(MockLLM(lambda p, s, seed, i: f"```json\n{hdr}\n```\n=== FILE: method.py ===\n{code}```\n"))
    rev = dev.revise(DevContext(1, [VersionRecord(0, template_code("adaptive"))], [], [], "", "eq1", 3))
    assert rev.ok and rev.meta["repairs"] == 0 and rev.change == "raise the default beta"
    assert rev.meta["calls"][0]["sanitized"] in (["method.py"], []) and not rev.code.rstrip().endswith("```")
