"""Retry round 2 (claims M4, M28): the released code's agentic protocols, ported in ``rsi.rrsi.agentic``.

* self-contained protocol tests (always run): the JSON action proposer's done() contract, bounces, caps
  and in-memory workspace; the agentic analyst's digest_many / report loop and the read-only digester tools;
  an end-to-end RRSI run with ``proposer_protocol="json_actions"`` and ``analyst="agentic"``;
* differential tests against the reference implementation (``google-research/rrsi@be50316``, imported from
  ``$RRSI_REF_SRC``; skipped when it is not on disk): the same scripted replies must give the same prompts,
  system prompts, interaction logs, file results, statuses, digests and reports.

Every test here fails on the pre-retry code, which had no ``rsi.rrsi.agentic`` and no such Config options.
"""
import importlib
import json
import os
import sys
from pathlib import Path

import pytest

from rsi.core import Artifact, FunctionDomain, MockLLM, Task, TaskSuite
from rsi.rrsi import Config, run
from rsi.rrsi.agentic import (ANALYST_SYSTEM_TMPL, JSON_SUFFIX, AgenticAnalyst, JsonActionProposer, MemWorkspace,
                              digest_task, ref_extract_json)
from rsi.rrsi.analyst import render_trace, task_row
from rsi.rrsi.components import Taxonomy
from rsi.rrsi.evaluate import TaskResult, aggregate

REF = os.environ.get("RRSI_REF_SRC", "/tmp/claude-0/-home-user-RSI/ebd00391-ba98-5b98-9125-83abd1dce979/scratchpad/"
                                     "src/google-research__rrsi")
BRIEF = "Toy harness domain. The harness is harness.py plus prompts."
FILES = {"harness.py": "def solve(q):\n    return q.strip()\n", "prompts/system.md": "Be exact.\n",
         "notes/readme.txt": "x\n", "data.bin": "binary-ish\n", "a/b.md": "nested\n", "a.md": "top\n"}
TRACES = {
    "t1": {"task_id": "t1", "family": "fmt", "seed": 0, "trace": "[step 1] read\n[step 2] wrote X\n[step 3] done",
           "output": "X", "score": 0.0, "tokens": 10, "steps": 3, "error": None, "feedback": "expected Y got X",
           "_role": "fail"},
    "t2": {"task_id": "t2", "family": "fmt", "seed": 0, "trace": "[step 1] read\n[step 2] wrote Y", "output": "Y",
           "score": 1.0, "tokens": 9, "steps": 2, "error": None, "feedback": "ok", "_role": "win"},
}
MEANS = {"t1": 0.0, "t2": 1.0}
DIGESTS = [{"task_id": "t1", "lens": "failure", "blocker": "wrong format"}]
EDIT = {"id": "C1", "component": "prompt", "hypothesis": "h", "targets_mode": "m", "predicted_affected": ["t1"],
        "retroactive_check": "(corrective) t1"}
SCRIPT = [
    "not json at all",
    '[{"action": "list_files"}]',
    '```json\n{"action": "read_file", "path": "harness/harness.py"}\n```',
    '{"action": "read_file", "path": "../escape.py"}',
    '{"action": "list_traces"}',
    '{"action": "read_trace", "task_id": "t1", "from_step": 2, "to_step": 2}',
    '{"action": "read_trace", "task_id": "nope"}',
    '{"action": "done", "edits": [' + json.dumps(EDIT) + ']}',                     # zero file changes -> bounced
    '{"action": "edit_file", "path": "harness.py", "old": "q.strip()", "new": "q.strip().upper()"}',
    '{"action": "edit_file", "path": "harness.py", "old": "missing", "new": "x"}',
    '{"action": "write_file", "path": "prompts/system.md", "content": "overwrite"}',
    '{"action": "write_file", "path": "tools/helper.py", "content": "def h():\\n    return 1\\n"}',
    '{"action": "write_file", "path": "tools/helper.exe", "content": "no"}',
    '{"action": "abort", "reason": "stuck"}',
    '{"action": "frobnicate"}',
    '{"action": "done", "edits": [' + ", ".join([json.dumps(EDIT)] * 3) + ']}',  # over budget -> bounced
    '{"action": "done", "edits": [' + json.dumps(dict(EDIT, component="tool")) + ']}',  # not in K -> bounced
    '{"action": "done", "summary": "upper-case", "edits": [' + json.dumps(EDIT) + ']}',
]


def _proposer(llm, K=None):
    tax = Taxonomy(K or ["prompt", "control_flow", "client_tool"], ["client_tool"])
    return JsonActionProposer(llm, tax, Config(), domain_brief=BRIEF, constitution=("SKILL", "PATTERNS"))


def _scripted(script):
    it = iter(script)
    return MockLLM(lambda prompt, system, seed, i: next(it))


def _run_ours(script, **kw):
    llm = _scripted(script)
    prop = _proposer(llm)
    base = Artifact(dict(FILES))
    out = prop.propose(base, budget=2, explore={"text": "explore text", "untried": ["client_tool"]},
                       reserved=False, prune_set=[], report={"failure_modes": []}, history_rows=[{"t": 0}],
                       scoreboard=[], digests=DIGESTS, variant_brief="You are variant A.", traces=TRACES,
                       task_means=MEANS, capture=True, **kw)
    return out, llm


def test_json_action_protocol_contract_and_workspace():
    out, llm = _run_ours(SCRIPT)
    assert out["status"] == "done" and out["n_edits"] == 2 and out["summary"] == "upper-case"
    art = out["artifact"]
    assert art["harness.py"] == "def solve(q):\n    return q.strip().upper()\n"
    assert art["tools/helper.py"].startswith("def h()") and art["prompts/system.md"] == "Be exact.\n"
    assert "tools/helper.exe" not in art
    log = llm.calls[-1]["prompt"].split("=== INTERACTION LOG ===", 1)[1]
    for expected in ("ERROR: not valid JSON", "ERROR: path escapes harness dir", "made ZERO file changes",
                     "ERROR: old string not found", "exists; use edit_file", "extension .exe not allowed",
                     "there is no abort action", "ERROR: unknown action frobnicate",
                     "3 edits exceed the budget b_t = 2", "not in ['prompt', 'control_flow', 'client_tool']",
                     "ERROR: no trace for nope", "[step 2] wrote X", "=== GRADING / VERIFIER ==="):
        assert expected in log, expected
    assert "[step 1] read" not in log.split('"from_step": 2')[1].split("[you]")[0]   # the step filter
    assert llm.calls[0]["system"].endswith(JSON_SUFFIX)
    assert llm.calls[0]["prompt"].startswith("=== CONSTITUTION (SKILL.md) ===\n\nSKILL")


def test_json_action_abort_limit_and_max_turns():
    out, _ = _run_ours(['{"action": "abort"}'] * 4)
    assert out["status"] == "abort" and out["artifact"] is None
    out, llm = _run_ours(['{"action": "list_files"}'] * 40)
    assert out["status"] == "max_turns" and len(llm.calls) == 40


def test_mem_workspace_sorts_like_rglob_and_dumps_source_files_only():
    ws = MemWorkspace(dict(FILES))
    assert ws.list_files().splitlines()[0].startswith("a/b.md")        # Path-part order: a/b.md before a.md
    assert "data.bin" not in ws.dump() and "===== FILE: harness.py =====" in ws.dump()
    assert ref_extract_json('Sure:\n```json\n{"a": 1}\n```') == '{"a": 1}'


def _analyst_responder(prompt, system, seed, i):
    """Replies as a function of (system, prompt) only, so parallel digests give the same answers in any order."""
    if system.startswith("You are a trajectory digester"):
        n = prompt.count("[you]")
        tid = prompt.split("Assigned trace: ", 1)[1].split(".txt", 1)[0]
        steps = [{"action": "grep", "pattern": "step", "path": f"{tid}.txt", "max_hits": 2},
                 {"action": "bash", "cmd": f"grep -n GRADING {tid}.txt | head -3"},
                 {"action": "bash", "cmd": "rm -rf ."},
                 {"action": "read_file", "path": f"{tid}.txt", "offset": 1, "limit": 3},
                 {"action": "glob", "pattern": "*.txt"},
                 {"action": "return", "digest": {"blocker": "x" * 7000}},
                 {"action": "return", "digest": {"blocker": f"format of {tid}", "evidence": []}}]
        return json.dumps(steps[min(n, len(steps) - 1)])
    n = prompt.count("\n[you] ")
    if n == 0:
        return json.dumps({"action": "digest_many", "requests": [{"task_id": "t1", "lens": "failure"},
                                                                 {"task_id": "t2", "lens": "success"},
                                                                 {"task_id": "zz", "lens": "failure"}]})
    if n == 1:
        return "garbage"
    return json.dumps({"action": "report", "failure_modes": [{"mode": "a", "n_tasks": 1}, {"mode": "b", "n_tasks": 3}],
                       "capability_gaps": [], "success_habits": [{"habit": "h", "n_tasks": 1}]})


def test_agentic_analyst_digests_with_read_only_tools_and_reports(tmp_path):
    llm = MockLLM(_analyst_responder)
    an = AgenticAnalyst(llm, domain_brief=BRIEF, workers=2)
    meas = aggregate("inc", 1, {t: TaskResult([MEANS[t]]) for t in TRACES})
    report, digests = an.analyze(TRACES, meas, prior={"failure_modes": [{"mode": "old", "description": "d"}]},
                                 workdir=tmp_path)
    assert [m["mode"] for m in report["failure_modes"]] == ["b", "a"] and report["n_digests"] == 2
    assert sorted(d["task_id"] for d in digests) == ["t1", "t2"]
    assert (tmp_path / "analysis" / "rendered" / "t1.txt").exists()
    dig = [c for c in llm.calls if c["system"].startswith("You are a trajectory digester")]
    logs = " ".join(c["prompt"] for c in dig)
    assert "ERROR: command rejected (read-only shell" in logs and "cap is 6000" in logs
    assert "1: === TASK t1" in logs and "t1.txt\nt2.txt" in logs
    assert (tmp_path / "analysis" / "rendered").exists() and not (tmp_path / "rm").exists()
    last = [c for c in llm.calls if c["system"].startswith("You are the batch analyst")][-1]["prompt"]
    assert "[1 requests skipped: unknown task_id]" in last and "ERROR: invalid JSON" in last
    assert '"mode": "old"' in last


# ---------------------------------------------------------------------------------- end to end in the loop
def _toy_domain():
    tasks = [Task(f"t{i}", {"x": i}, str(i * 2), "dbl") for i in range(12)]
    suite = TaskSuite(tasks, {"evolve": [f"t{i}" for i in range(8)], "holdout": [f"t{i}" for i in range(8, 12)]})

    def execute(art, task, seed, llm):
        return str(task.input["x"] * int(art["factor.txt"].strip()))

    dom = FunctionDomain(suite, execute, lambda task, out: float(out == task.target), name="toy-double")
    dom.components = {"prompt": ["*.txt"], "control_flow": ["*.py"]}
    return dom


def _loop_responder(prompt, system, seed, i):
    s = system or ""
    if s.startswith("You are a harness engineer agent"):
        n = prompt.count("\n[you] ")
        if "REPAIR ROUND" in prompt or n >= 1:
            return json.dumps({"action": "done", "summary": "double", "edits": [
                {"id": "C1", "component": "prompt", "hypothesis": "multiply by two", "targets_mode": "wrong value",
                 "predicted_affected": ["t1"], "retroactive_check": "(corrective) t1 moves"}]})
        return json.dumps({"action": "edit_file", "path": "factor.txt", "old": "1", "new": "2"})
    if "strict reviewer of harness" in s:
        return json.dumps({"verdict": "accept", "reasons": [], "risk_notes": []})
    return _analyst_responder(prompt, system, seed, i).replace('"t1"', '"t0"').replace('"t2"', '"t1"')


def test_loop_runs_with_json_actions_proposer_and_agentic_analyst(tmp_path):
    dom = _toy_domain()
    llm = MockLLM(_loop_responder)
    res = run(dom, Artifact({"factor.txt": "1\n"}), llm_propose=llm,
              config=Config(T=1, k=2, delta=0.05, workers=1, proposer_protocol="json_actions", analyst="agentic"),
              out_dir=tmp_path / "run")
    assert res.best["factor.txt"].strip() == "2" and res.trajectory[-1]["S"] == 1.0
    assert res.meta.get("analyst_mode") == "agentic"
    assert (tmp_path / "run" / "r0" / "analysis" / "digests").exists()
    with pytest.raises(ValueError):
        run(dom, Artifact({"factor.txt": "1\n"}), llm_propose=llm, config=Config(T=1, proposer_protocol="x"),
            out_dir=tmp_path / "bad")


# ---------------------------------------------------------------------------------- differential vs reference
def _ref():
    if not Path(REF, "rrsi", "propose.py").exists():
        pytest.skip(f"reference code not found at {REF} (set RRSI_REF_SRC)")
    if REF not in sys.path:
        sys.path.insert(0, REF)
    return (importlib.import_module("rrsi.propose"), importlib.import_module("rrsi.analyst"),
            importlib.import_module("rrsi.digester"), importlib.import_module("rrsi.llm"),
            importlib.import_module("rrsi.evaluate"))


class _RefDomain:
    briefs = {"proposer": BRIEF, "analyst": BRIEF, "digester": BRIEF}
    source_exts = {".py", ".txt", ".md", ".json"}

    @staticmethod
    def render_trace(rec, detail=False):
        return render_trace(rec, None, cap=60_000 if detail else 6000)

    @staticmethod
    def task_row(tid, rec, tr):
        return task_row(tid, rec, tr.mean if tr else None)


def _recording_generate(script, calls, llm_mod):
    it = iter(script) if isinstance(script, list) else None

    def gen(prompt, system=None, max_retries=6, json_only=False, model=None, max_tokens=20000, cache_prefix=None):
        text = next(it) if it is not None else script(prompt, system + (JSON_SUFFIX if json_only else ""), 0, 0)
        calls.append({"prompt": prompt, "system": system, "json_only": json_only, "cache_prefix": cache_prefix})
        return llm_mod.extract_json(text) if json_only else text
    return gen


def test_json_action_proposer_matches_the_reference_turn_by_turn(tmp_path, monkeypatch):
    RP, _, _, RL, RE = _ref()
    ref_calls: list = []
    monkeypatch.setattr(RP, "generate", _recording_generate(SCRIPT, ref_calls, RL))
    hdir = tmp_path / "harness"
    for p, t in FILES.items():
        (hdir / p).parent.mkdir(parents=True, exist_ok=True)
        (hdir / p).write_text(t)
    per_task = {t: RE.TaskResult(rewards=[m]) for t, m in MEANS.items()}
    orig_K = list(RP.K)
    RP.K[:] = ["prompt", "control_flow", "client_tool"]                   # the same K for both (restored below)
    try:
        ref = RP.propose(_RefDomain, hdir, {"failure_modes": []}, [{"t": 0}], "SKILL", "PATTERNS", 2,
                         {"text": "explore text", "untried": ["client_tool"]}, False, [], traces=TRACES,
                         per_task=per_task, findings=DIGESTS, scoreboard=[], variant_brief="You are variant A.")
    finally:
        RP.K[:] = orig_K
    ours, llm = _run_ours(SCRIPT)
    assert len(llm.calls) == len(ref_calls) == len(SCRIPT)
    for o, r in zip(llm.calls, ref_calls):
        assert r["json_only"] and o["system"] == r["system"] + JSON_SUFFIX
        assert o["prompt"] == r["cache_prefix"] + "\n\n" + r["prompt"]
    assert ours["status"] == ref["status"] and ours["n_edits"] == ref["n_edits"] and ours["edits"] == ref["edits"]
    assert ours["log"] == ref["log"]
    for p in set(FILES) | {"tools/helper.py"}:
        assert ours["artifact"][p] == (hdir / p).read_text(), p


def test_agentic_analyst_matches_the_reference(tmp_path, monkeypatch):
    _, RA, RD, RL, RE = _ref()
    ref_calls: list = []
    gen = _recording_generate(_analyst_responder, ref_calls, RL)
    monkeypatch.setattr(RA, "generate", gen)
    monkeypatch.setattr(RD, "generate", gen)
    per_task = {t: RE.TaskResult(rewards=[m]) for t, m in MEANS.items()}
    prior_modes = [{"mode": "old", "description": "d"}]
    ref_rep = RA.analyze(_RefDomain, TRACES, per_task, tmp_path / "ref", prior_modes=prior_modes, prior_habits=[])
    llm = MockLLM(_analyst_responder)
    meas = aggregate("inc", 1, {t: TaskResult([MEANS[t]]) for t in TRACES})
    ours, digests = AgenticAnalyst(llm, domain_brief=BRIEF).analyze(
        TRACES, meas, prior={"failure_modes": prior_modes, "success_habits": []}, workdir=tmp_path / "ours")
    assert {k: v for k, v in ours.items() if k != "analyst"} == ref_rep
    assert digests == RA.load_digests(tmp_path / "ref")
    key = lambda c: (c["system"][:40], c["prompt"])                        # noqa: E731 (parallel digests: any order)
    ours_c = sorted(({"system": c["system"], "prompt": c["prompt"]} for c in llm.calls), key=key)
    ref_c = sorted(({"system": c["system"] + JSON_SUFFIX, "prompt": c["prompt"]} for c in ref_calls), key=key)
    assert ours_c == ref_c
    for p in (tmp_path / "ref" / "analysis" / "rendered").iterdir():
        assert (tmp_path / "ours" / "analysis" / "rendered" / p.name).read_text() == p.read_text()
    assert ANALYST_SYSTEM_TMPL.format(domain_brief=BRIEF) == RA.SYSTEM_TMPL.format(domain_brief=BRIEF)
    assert digest_task.__doc__
