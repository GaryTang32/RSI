"""Regression tests for the SoL-Pi claims-audit fixes (docs/claims/solpi.md, "Fix log").

Every test here fails on the code before the fix (see the finding id in each docstring): the reference is
NVlabs/SoL-Pi @ 1559b5c (TypeScript + vitest vectors) and Pi 0.85.1 host semantics.
"""
import json
import os

import pytest

from rsi.core import Evaluator
from rsi.domains.agentworld import MockAgentLLM, make_domain
from rsi.solpi import (AGENTWORLD_IDEAS, Config, DualGate, EvidencePreservingReducer, GateSpec, LibraryProposer,
                       Lineage, Message, Metrics, ObservationPack, OnlineContextCompact, SmokeReviewer, ToolCall,
                       metrics_from_eval, validate_receipt)
from rsi.solpi.fusion import (EDIT_THEN_RUN_DESCRIPTION, THEN_RUN_SKIPPED, THEN_RUN_SUCCEEDED,
                              WRITE_THEN_RUN_DESCRIPTION, ActionFusion, canonical_queue_key, resolve_tool_path)
from rsi.solpi.obspack import complete_line_excerpt, create_observation, read_recall_chunk
from rsi.solpi.occ import (analyze_plan_transition, format_plan_snapshot, js_grapheme_length, parse_plan_steps,
                           validate_update_plan_args)
from rsi.solpi.reducer import (REDUCER_RECEIPT_SCHEMA, ArchiveObject, LLMReducer, receipt_text, sha256, source_lines,
                               utf16_len)
from rsi.solpi.research import IdeaPool, oracle_estimate
from rsi.solpi.runtime import ToolError, ToolResult, ToolResultEvent

from rsi.solpi import PRICES, AgentRuntime, TokenMeter, builtin_tools


# ------------------------------------------------------------------ scripted backend + env (as in _mechanisms.py)
class Script:
    """Backend that replays a list of tool-call batches (then finishes)."""

    name = "script"

    def __init__(self, steps):
        self.steps = list(steps)

    def act(self, msgs, tools, rt):
        if not self.steps:
            return Message("assistant", "done")
        calls = self.steps.pop(0)
        return Message("assistant", "step", tool_calls=tuple(ToolCall(rt.next_call_id(), n, a) for n, a in calls))


class Env:
    def __init__(self, files=None, outputs=None):
        self.files = dict(files or {})
        self.outputs = outputs or {}

    def read_file(self, p):
        return self.files.get(p)

    def tool_read(self, p, rt=None):
        return ToolResult(self.files[p]) if p in self.files else ToolResult("ENOENT", True)

    def tool_write(self, p, c, rt=None):
        self.files[p] = c
        return ToolResult(f"wrote {p}")

    def tool_edit(self, p, old, new, rt=None):
        t = self.files.get(p)
        if t is None or t.count(old) != 1:
            return ToolResult("Could not find the exact text", True)
        self.files[p] = t.replace(old, new)
        return ToolResult(f"edited {p}")

    def tool_bash(self, cmd, rt=None):
        out, err = self.outputs.get(cmd, (f"ran {cmd}", False))
        return ToolResult(out, err)


def runtime(env, steps, exts=(), **kw):
    rt = AgentRuntime(Script(steps), system_prompt="sys", meter=TokenMeter({"main": PRICES["sim-a"],
                                                                           "reducer": PRICES["reducer"],
                                                                           "compaction": PRICES["sim-a"]}),
                      env=env, **kw)
    for s in builtin_tools(env):
        rt.register_tool(s)
    for e in exts:
        rt.add_extension(e)
    return rt


# ====================================================================== OCC plan (claims §3 items 1-3)
OPEN = [{"id": "build", "goal": "build it", "status": "in_progress"}]
DONE = [{"id": "build", "goal": "build it", "status": "completed"}]


def test_plan_snapshot_is_json_stringify_of_steps_object():
    """§3.1: plan.ts formatPlanSnapshot = JSON.stringify({steps}) (was a bare Python list with spaces)."""
    snap = format_plan_snapshot(tuple(OPEN))
    assert snap.startswith('<sol-pi-plan task_status="active">')
    assert '{"steps":[{"id":"build","goal":"build it","status":"in_progress"}]}' in snap
    assert format_plan_snapshot(({"id": "é", "goal": "g", "status": "pending"},)).count("é") == 1  # no \\u escape


def test_plan_advice_matches_release_vectors():
    """§3.2: the release's three advice lines (changed goal; at most one; mark a pending step in_progress)."""
    _, adv = analyze_plan_transition(({"id": "a", "goal": "old", "status": "in_progress"},),
                                     ({"id": "a", "goal": "new", "status": "in_progress"},
                                      {"id": "b", "goal": "second", "status": "in_progress"}))
    assert adv == ['Plan step "a" changed goal; reuse an id only for the same goal.',
                   "Keep at most one plan step in_progress."]
    _, adv = analyze_plan_transition((), ({"id": "a", "goal": "g", "status": "pending"},))
    assert adv == ["Mark one pending plan step in_progress before starting it."]
    comp, adv = analyze_plan_transition(tuple(OPEN), tuple(DONE))
    assert [c["id"] for c in comp] == ["build"] and adv == []
    assert analyze_plan_transition(tuple(DONE), tuple(DONE))[0] == []


def test_plan_parsing_is_strict_like_plan_ts_and_the_tool_schema():
    """§3.3: non-empty ids/goals, exactly three keys; the update_plan schema rejects over-long progress arrays,
    over-long items and extra keys instead of truncating them."""
    assert parse_plan_steps([]) == ()
    assert parse_plan_steps([{"id": "", "goal": "", "status": "pending"}]) is None
    assert parse_plan_steps([{"id": "x", "goal": "g", "status": "pending", "note": 1}]) is None
    assert parse_plan_steps(OPEN) == tuple(OPEN)
    ok = {"steps": OPEN, "progress": {"files_changed": ["a.py"], "verification": [], "decisions": []}}
    assert validate_update_plan_args(ok) == []
    bad = [
        {"steps": []},                                                                          # minItems 1
        {"steps": OPEN, "extra": 1},                                                            # root extra key
        {"steps": OPEN, "progress": {"files_changed": ["f"] * 129, "verification": [], "decisions": []}},
        {"steps": OPEN, "progress": {"files_changed": [], "verification": ["x" * 1001], "decisions": []}},
        {"steps": OPEN, "progress": {"files_changed": [], "verification": []}},                 # missing key
        {"steps": OPEN, "progress": {"files_changed": [], "verification": [], "decisions": [], "x": []}},
        {"steps": [{"id": "a", "goal": "g", "status": "pending", "extra": True}]},
    ]
    for b in bad:
        assert validate_update_plan_args(b), b
    occ = OnlineContextCompact()
    rt = runtime(Env(), [], [occ])
    with pytest.raises(ToolError, match='Validation failed for tool "update_plan"'):
        occ._update_plan(bad[2], rt, "c1")
    assert js_grapheme_length("e\u0301") == 1 and js_grapheme_length("\U0001F1FA\U0001F1F8") == 1


def test_update_plan_tool_result_carries_snapshot_and_advice():
    occ = OnlineContextCompact()
    rt = runtime(Env(), [], [occ])
    r = occ._update_plan({"steps": [{"id": "a", "goal": "g", "status": "pending"}]}, rt, "c1")
    assert r.content == ('<sol-pi-plan task_status="active">{"steps":[{"id":"a","goal":"g","status":"pending"}]}'
                         '</sol-pi-plan>\nMark one pending plan step in_progress before starting it.')
    assert r.details["plan"] == [{"id": "a", "goal": "g", "status": "pending"}]


# ====================================================================== EPR (claims §3 items 4-5, M10)
def _archive(body):
    ext = EvidencePreservingReducer()
    rt = runtime(Env(), [], [ext])
    return ext._archive(rt, body)


def test_epr_source_lines_is_split_length():
    """§3.4: archive.ts lines = body.split("\\n").length (a log ending in \\n counts the empty last line)."""
    assert _archive("x\n" * 3000).lines == 3001 == len(("x\n" * 3000).split("\n"))
    assert _archive("a\nb").lines == 2 and source_lines("") == 0
    body = "FAILED t\n" + "." * 5000 + "\n"
    ar = _archive(body)
    ok, v = validate_receipt(json.dumps({"schema": REDUCER_RECEIPT_SCHEMA, "source_sha256": ar.hash,
                                         "status": "failure", "uncertain": False,
                                         "evidence": [{"kind": "failure", "quote": "FAILED t"}]}), ar, body, True)
    assert ok and "source_lines=3\n" in receipt_text("pytest", ar, v, "p", "m", 1)


def test_epr_lengths_are_utf16_units():
    """§3.5: quote 1..600 chars, maxChars and ArchiveObject.chars count UTF-16 units like JavaScript."""
    q = "\U0001F600" * 400                                 # 400 code points, 800 UTF-16 units
    body = "FAILED " + q + "\n" + "." * 5000
    ar = ArchiveObject(sha256(body), len(body.encode()), utf16_len(body), source_lines(body), "/x")
    ok, why = validate_receipt(json.dumps({"schema": REDUCER_RECEIPT_SCHEMA, "source_sha256": ar.hash,
                                           "status": "failure", "uncertain": False,
                                           "evidence": [{"kind": "failure", "quote": q}]}), ar, body, True)
    assert (ok, why) == (False, "unverifiable-quote")
    assert _archive(body).chars == len(body) + 400
    # maxChars: 1,107 code points but 2,207 UTF-16 units > 2,000 -> source-over-max-chars
    ext = EvidencePreservingReducer(max_chars=2000)
    rt = runtime(Env(), [], [ext])
    big = "FAILED " + "\U0001F600" * 1100
    ev = ToolResultEvent(ToolCall("c1", "bash", {"command": "pytest -q"}), ToolResult(big, True))
    assert ext._on_result(ev, rt) is None and ext.stats["fallback:source-over-max-chars"] == 1


class _Resp:
    def __init__(self, text, raw=None, ok=True):
        from rsi.core.llm import Usage
        self.text, self.raw, self.ok, self.error = text, raw, ok, None if ok else "boom"
        self.usage = Usage(1, 10, 5, 0.0, 0.0)


class _LLM:
    name = "fake"

    def __init__(self, resp, sleep=0.0, max_tokens=None):
        self.resp, self.sleep, self.calls = resp, sleep, []
        if max_tokens is not None:
            self.max_tokens = max_tokens

    def complete(self, prompt, *, system=None, max_tokens=None, seed=None, role="default"):
        import time
        self.calls.append(max_tokens)
        time.sleep(self.sleep)
        return self.resp


def test_llm_reducer_timeout_max_tokens_and_stop_reason():
    """M10: provider.ts - 90 s timeout, maxTokens = min(2048, model.maxTokens), stopReason from the provider."""
    llm = _LLM(_Resp("{}", {"stop_reason": "end_turn"}), max_tokens=1000)
    r = LLMReducer(llm).reduce("s", "u", 2048, body="", archive=None, is_error=False)
    assert llm.calls == [1000] and r.stop_reason == "stop"
    assert LLMReducer(_LLM(_Resp("{}", {"stop_reason": "refusal"}))).reduce(
        "s", "u", 2048, body="", archive=None, is_error=False).stop_reason == "error"
    assert LLMReducer(_LLM(_Resp("{}", {"stop_reason": "max_tokens"}))).reduce(
        "s", "u", 2048, body="", archive=None, is_error=False).stop_reason == "length"
    slow = LLMReducer(_LLM(_Resp("{}"), sleep=0.5), timeout_s=0.05)
    r = slow.reduce("s", "u", 2048, body="", archive=None, is_error=False)
    assert r.error == "timeout"
    # end to end: a refusal falls back with model-response-error (was silently treated as "stop")
    ext = EvidencePreservingReducer(LLMReducer(_LLM(_Resp("{}", {"stop_reason": "refusal"}))))
    rt = runtime(Env(), [], [ext])
    ev = ToolResultEvent(ToolCall("c1", "bash", {"command": "pytest -q"}), ToolResult("E fail\n" + "." * 5000, True))
    assert ext._on_result(ev, rt) is None and ext.stats["fallback:model-response-error"] == 1


# ====================================================================== ObservationPack (claims §3 items 6-7, M4)
def _ts_excerpt(text, budget, from_end):
    import re
    lines = re.split(r"(?<=\n)", text)
    sel, used = [], 0
    for line in (reversed(lines) if from_end else lines):
        n = len(line.encode())
        if used + n > budget:
            break
        sel.append(line)
        used += n
    return "".join(reversed(sel) if from_end else sel)


def test_obspack_excerpt_splits_after_newline_only():
    """§3.6: completeLineExcerpt splits text.split(/(?<=\\n)/) - a \\r progress bar is not a line break."""
    cr = "head\n" + "".join(f"progress {i}%\r" for i in range(100)) + "\n" + "x\n" * 6000 + "tail\n"
    for fe in (False, True):
        assert complete_line_excerpt(cr, 512, fe) == _ts_excerpt(cr, 512, fe)
    assert complete_line_excerpt(cr, 512, False) == "head\n"
    assert complete_line_excerpt("a\u2028b\nc\n", 3, False) == ""          # U+2028 is not a line end either


def test_obs_recall_rejects_negative_and_non_integer_offsets():
    """§3.7: Type.Integer({minimum: 0}) - a negative offset used to page from the end with next_offset=0."""
    op = ObservationPack()
    rt = runtime(Env(), [], [op])
    text = "line\n" * 3000
    obs = create_observation(Message("tool", text, tool_call_id="c1", tool_name="bash"))
    rt.store[obs.path] = text
    for off in (-5, 1.5, "3", True):
        with pytest.raises(ToolError, match='Validation failed for tool "obs_recall"'):
            op._recall({"id": obs.id, "offset": off}, rt, "c2")
    with pytest.raises(ToolError):
        read_recall_chunk(text.encode(), -5, 100, 10)
    r = op._recall({"id": obs.id, "offset": 0}, rt, "c3")
    assert r.details["nextOffset"] == r.details["next_offset"] > 0


def test_obspack_placeholder_ledger_entry_has_original_lines():
    """M4: the release's placeholder ledger entry carries originalLines."""
    env = Env(outputs={"dump": ("row\n" * 4000, False)})
    op = ObservationPack()
    rt = runtime(env, [[("bash", {"command": "dump"})], [("bash", {"command": "x"})], [("bash", {"command": "y"})],
                       [("bash", {"command": "z"})]], [op])
    rt.run("t")
    ph = [e for e in op.ledger if e["event"] == "placeholder"]
    assert ph and ph[0]["originalLines"] == 4000


# ====================================================================== OCC W and turn_end (§3 item 8, M13)
class Recorder(OnlineContextCompact):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.w_at_turn_end = []

    def _turn_end(self, reply, results, rt):
        if self.pending_boundary is not None:
            self.w_at_turn_end.append((self.context_tokens(rt), rt.last_context_tokens + reply.tokens()
                                       + sum(m.tokens() for m in results)))
        return super()._turn_end(reply, results, rt)


def test_occ_write_tokens_follow_pi_get_context_usage():
    """§3.8: W at turn_end = the boundary reply's usage (its request's prompt + its output) + the tool results
    after it, like Pi's getContextUsage(); it used to be the size of the last request only."""
    env = Env(outputs={"big": ("z" * 40000, False)})
    plan = lambda st: ("update_plan", {"steps": [{"id": "a", "goal": "g", "status": st}]})
    occ = Recorder(cache_write_read_ratio=12.5)
    rt = runtime(env, [[plan("in_progress")], [("bash", {"command": "big"})], [plan("completed"),
                                                                                ("bash", {"command": "big"})]], [occ])
    rt.run("t")
    assert occ.w_at_turn_end and all(w >= pi_w for w, pi_w in occ.w_at_turn_end)
    w, pi_w = occ.w_at_turn_end[-1]
    assert w == pi_w > rt.meter.requests[-2].input          # includes the reply and the 40 kB boundary result
    assert occ.decisions[-1]["writeTokens"] == w


def test_occ_turn_end_skips_aborted_or_error_replies():
    """M13: turn_end returns early when stopReason is error/aborted or the signal is aborted."""
    occ = OnlineContextCompact(cache_write_read_ratio=0.0)
    rt = runtime(Env(), [], [occ])
    occ.pending_boundary = "c1"
    res = [Message("tool", "ok", tool_call_id="c1", tool_name="update_plan")]
    occ._turn_end(Message("assistant", "x", details={"stop_reason": "aborted"}), res, rt)
    rt.abort()
    occ.pending_boundary = "c1"
    occ._turn_end(Message("assistant", "x"), res, rt)
    assert occ.decisions == []


# ====================================================================== runtime (§3 item 11)
class CountingContext:
    name = "counter"

    def __init__(self):
        from collections import Counter
        self.stats, self.calls = Counter(), 0

    def register(self, rt):
        rt.on("context", self._ctx)

    def _ctx(self, msgs, rt):
        self.calls += 1


def test_runtime_projects_once_per_request_even_when_auto_compacting():
    """§3.11: an auto-compaction turn used to call project() twice (ObservationPack counted two sends)."""
    env = Env(outputs={f"c{i}": ("q" * 30000, False) for i in range(30)})
    cnt = CountingContext()
    rt = runtime(env, [[("bash", {"command": f"c{i}"})] for i in range(30)], [cnt], context_window=60_000,
                 keep_recent_tokens=8_000, auto_compact_reserve=16_384, max_turns=40)
    rt.run("t")
    assert rt.auto_compactions >= 1
    assert cnt.calls == rt.provider_requests


def test_runtime_auto_compaction_threshold_is_strict_and_uses_context_usage():
    """§3.11: Pi's shouldCompact is contextTokens > window - reserve (was >=), measured with getContextUsage()
    (the last reply's usage + the messages after it) on the stored messages, not on a projection."""
    rt = runtime(Env(outputs={"a": ("w" * 4000, False)}), [[("bash", {"command": "a"})] for _ in range(8)],
                 keep_recent_tokens=500)
    rt.run("t")
    last = rt.history[-1]
    assert last.details["usage_tokens"] == rt.last_context_tokens + last.tokens() == rt.context_usage()
    rt.history.append(Message("tool", "x" * 400, tool_call_id="c", tool_name="bash"))
    u = rt.context_usage()
    assert u == last.details["usage_tokens"] + rt.history[-1].tokens()
    assert rt.compactable()
    rt.auto_compact_reserve = 1000
    rt.context_window = u + 1000                   # exactly at the threshold: no compaction (Pi: strict ">")
    assert not rt.should_auto_compact()
    rt.context_window = u + 999                    # one token over
    assert rt.should_auto_compact()
    rt.compact("")                                 # after a compaction the old usage is stale -> estimate
    assert rt.context_usage() is None


# ====================================================================== Action Fusion (§3 items 9-10, M2)
def _af(env=None, hook=None, steps=()):
    env = env or Env()
    rt = runtime(env, list(steps), [ActionFusion(yield_hook=hook)])
    return env, rt


def test_af_text_details_match_then_run_ts():
    """§3.9: empty output -> '[then_run:succeeded]' (no trailing newline); write has its own description;
    an empty command is run (not silently skipped); empty failure parts are dropped."""
    env, rt = _af(Env(outputs={"true": ("", False)}))
    r = rt.tools["write"].execute({"path": "t.py", "content": "x", "then_run": {"command": "true"}}, rt, "c1")
    assert r.content == "wrote t.py\n" + THEN_RUN_SUCCEEDED
    ran = []
    env.tool_bash = lambda cmd, rt=None: (ran.append(cmd), ToolResult(""))[1]
    r = rt.tools["write"].execute({"path": "t.py", "content": "y", "then_run": {"command": ""}}, rt, "c2")
    assert ran == [""] and r.content.endswith(THEN_RUN_SUCCEEDED)
    env.tool_bash = lambda cmd, rt=None: ToolResult("", True)
    r = rt.tools["write"].execute({"path": "t.py", "content": "z", "then_run": {"command": "boom"}}, rt, "c3")
    assert r.is_error and r.content == "wrote t.py\n\n[then_run:failed]"
    assert rt.tools["write"].description.endswith(WRITE_THEN_RUN_DESCRIPTION)
    assert rt.tools["edit"].description.endswith(EDIT_THEN_RUN_DESCRIPTION)
    with pytest.raises(ToolError, match="Validation failed"):
        rt.tools["write"].execute({"path": "t.py", "content": "z", "then_run": "pytest"}, rt, "c4")


def test_af_path_resolution_matches_file_queue_ts(tmp_path):
    """§3.10: ~ resolves against the home directory (was cwd-relative), file:// URLs and unicode spaces
    like resolveToolPath; U+200B is not a unicode space."""
    assert resolve_tool_path("~/proj/a.py", "/work", "/home/u") == "/home/u/proj/a.py"
    assert resolve_tool_path("~", "/work", "/home/u") == "/home/u"
    assert resolve_tool_path("~/proj/a.py") == os.path.join(os.path.expanduser("~"), "proj/a.py")
    assert resolve_tool_path("~\\notes.txt", "/work", "/h") == "/work/~\\notes.txt"
    assert resolve_tool_path("@target\u00a0file.txt", "/work") == "/work/target file.txt"
    assert resolve_tool_path("@file:///work/a%20b.txt", "/x") == "/work/a b.txt"
    assert resolve_tool_path("a\u200bb", "/w") == "/w/a\u200bb"
    assert resolve_tool_path("./a.py") == "a.py"
    # AgentWorld resolves like Pi's built-ins: ./src/a.py, /repo/src/a.py and src/a.py are one file
    from rsi.domains.agentworld.base import Env as AWEnv
    assert AWEnv.resolve_path(AWEnv.__new__(AWEnv), "/repo/src/a.py") == "src/a.py"
    assert AWEnv.resolve_path(AWEnv.__new__(AWEnv), "~/x.py") == "/home/agent/x.py"


def test_af_queue_key_is_realpath_so_symlinks_share_a_queue(tmp_path):
    """§3.10: canonicalQueueKey realpath - a symlink and its target serialise on one queue."""
    target = tmp_path / "real.txt"
    target.write_text("x")
    link = tmp_path / "link.txt"
    os.symlink(target, link)
    assert canonical_queue_key(str(link)) == canonical_queue_key(str(target))
    assert canonical_queue_key(str(tmp_path / "missing" / "f.txt")) == os.path.join(os.path.realpath(tmp_path),
                                                                                     "missing", "f.txt")

    class FsEnv(Env):
        cwd = str(tmp_path)

        def read_file(self, p):
            return open(p).read() if os.path.exists(p) else None

    af = ActionFusion()
    env, rt = FsEnv(), None
    rt = runtime(env, [], [af])
    assert af.queue_key(rt, "link.txt") == af.queue_key(rt, str(target))
    assert af._queue(af.queue_key(rt, "link.txt")) is af._queue(af.queue_key(rt, "real.txt"))


def test_af_hash_guard_yields_by_default_and_reports_enoent(monkeypatch):
    """M2: production code yields between the two hashes (it did not - only the test hook did); a vanished
    target gives the release's ENOENT text, not "content changed"."""
    import rsi.solpi.fusion as fusion
    env, rt = _af()
    calls = []

    def interfering_writer():
        calls.append(1)
        env.files["t.py"] = "changed by another writer"

    monkeypatch.setattr(fusion, "_yield_for_interference", interfering_writer)
    with pytest.raises(ToolError, match=r"\[then_run:skipped\] target content changed"):
        rt.tools["write"].execute({"path": "t.py", "content": "x", "then_run": {"command": "pytest"}}, rt, "c1")
    assert calls == [1]
    env2, rt2 = _af(hook=lambda r, p: env2.files.pop(p, None))
    with pytest.raises(ToolError) as e:
        rt2.tools["write"].execute({"path": "t.py", "content": "x", "then_run": {"command": "pytest"}}, rt2, "c2")
    assert str(e.value) == f"{THEN_RUN_SKIPPED} ENOENT: no such file or directory, open 't.py'; the command was not run."


def test_af_on_agentworld_passes_the_agent_path_to_the_builtin():
    """§3.10: the release passes editInput (the agent's path) to the built-in; the env resolves it."""
    dom = make_domain(seed=0, n_train=1, n_accept=0, n_final=0, n_test=0)
    from rsi.domains.agentworld.envs import FAMILIES
    task = dom.tasks.get(dom.tasks.splits["evolve"][0])
    env = FAMILIES[task.input["family"]](task.id, seed=task.input["env_seed"], n_subtasks=task.input["n_subtasks"])
    rt = runtime(env, [], [ActionFusion()])
    r = rt.tools["write"].execute({"path": "./notes/x.txt", "content": "hi", "then_run": {"command": "ls notes"}},
                                  rt, "c1")
    assert env.files["notes/x.txt"] == "hi" and "to ./notes/x.txt" in r.content and THEN_RUN_SUCCEEDED in r.content


# ====================================================================== domain docstring (§3 item 12)
def test_agentworld_docstrings_name_both_heldout_families():
    import rsi.domains.agentworld as aw
    from rsi.domains.agentworld import domain as d
    assert d.HELDOUT_FAMILIES == ("configfix", "datalookup")
    for doc in (d.__doc__, aw.__doc__):
        assert "configfix" in doc and "datalookup" in doc
    assert "acceptance tasks of the held-out family\n(``datalookup``" not in d.__doc__


# ====================================================================== idea pool + oracle (§3 item 13, R1)
def test_idea_pool_covers_family_m_and_the_oracle_filters():
    pool = IdeaPool(AGENTWORLD_IDEAS)
    assert set(pool.by_family()) == {"C", "P", "T", "D", "R", "M"}
    assert len(AGENTWORLD_IDEAS) > Config().n_lineages               # the default breadth budget filters
    p20 = next(i for i in AGENTWORLD_IDEAS if i.id == "P20")
    assert p20.oracle == "late_turn_tokens"                          # was prompt_tokens (a turn cap's wrong target)


def test_protocol_runs_only_the_oracle_top_ideas(tmp_path):
    from rsi.solpi import run
    d = make_domain(seed=0, n_train=2, n_accept=1, n_final=1, n_test=0)
    res = run(d, d.seed_artifact(), llm_task=MockAgentLLM("A"), config=Config(n_lineages=4, firewall=False),
              out_dir=tmp_path)
    r = res.meta["rounds"][0]
    est = r["oracle"]
    ranked = sorted(est, key=lambda i: (-est[i], i))
    assert r["chosen"] == ranked[:4] and [l["idea"] for l in r["lineages"]] == ranked[:4]
    assert all(est[i] >= est[j] for i in ranked[:4] for j in ranked[4:])
    trials = [t for trs in Evaluator(d, MockAgentLLM("A")).evaluate(d.seed_artifact(), "evolve").trials.values()
              for t in trs]
    p20 = next(i for i in AGENTWORLD_IDEAS if i.id == "P20")
    assert 0 < oracle_estimate(p20, trials) < 1


# ====================================================================== capability floor (L4 / S6)
def test_default_gate_rejects_the_lenient_turn_cap():
    """L4: a 24-turn cap scores 0.984 (inside a score-only 2% floor) but finishes only 22/24 tasks; the
    predeclared floor now has two capability metrics (score AND solved rate)."""
    base = Metrics({"score": 1.0, "solved": 1.0, "tokens": 100.0, "cost": 1.0, "steps": 15.0, "eta": 1.0}, {}, 24)
    cap24 = Metrics({"score": 0.984, "solved": 0.917, "tokens": 92.0, "cost": 0.93, "steps": 14.0, "eta": 0.945},
                    {}, 24)
    real = Metrics({"score": 1.0, "solved": 1.0, "tokens": 70.0, "cost": 0.8, "steps": 11.0, "eta": 0.8}, {}, 24)
    assert DualGate(GateSpec(capability=(("score", 0.02),))).accept(base, cap24).accept      # the old floor
    g = DualGate(GateSpec()).accept(base, cap24)
    assert not g.accept and g.capability["solved"]["pass"] is False and g.capability["score"]["pass"] is True
    assert DualGate(GateSpec()).accept(base, real).accept
    missing = Metrics({"score": 1.0, "tokens": 50.0, "cost": 0.5, "steps": 1.0, "eta": 0.5}, {}, 1)
    assert not DualGate(GateSpec()).accept(base, missing).accept                               # fails closed


def test_turn_cap_lineage_is_abandoned_under_the_default_floor():
    dom = make_domain(seed=0, n_train=8, n_accept=0, n_final=0, n_test=0)
    llm = MockAgentLLM("A")
    ev = Evaluator(dom, llm)
    base = dom.seed_artifact()
    bm = metrics_from_eval(ev.evaluate(base, "evolve"))
    assert "solved" in bm.agg
    p20 = next(i for i in AGENTWORLD_IDEAS if i.id == "P20")
    out = {}
    for name, spec in (("score_only", GateSpec(capability=(("score", 0.02),))), ("default", GateSpec())):
        lin = Lineage(p20, evaluator=ev, gate=DualGate(spec), proposer=LibraryProposer(),
                      reviewer=SmokeReviewer(dom, llm), base=base, base_metrics=bm)
        out[name] = lin.run()
    assert out["score_only"].frozen is not None                   # the finding: max_turns 24 passed at S 0.984
    assert out["default"].frozen is None


# ====================================================================== nondominated sweep (R4)
def test_lineage_sweeps_and_freezes_the_nondominated_best_eta_variant():
    """R4: by default a lineage keeps the nondominated passing variant with the best eta (it froze the first,
    most aggressive ObservationPack variant, which raised cost)."""
    assert Config().sweep is True
    dom = make_domain(seed=0, n_train=4, n_accept=0, n_final=0, n_test=0)
    llm = MockAgentLLM("A")
    ev = Evaluator(dom, llm)
    base = dom.seed_artifact()
    bm = metrics_from_eval(ev.evaluate(base, "evolve"))
    c23 = next(i for i in AGENTWORLD_IDEAS if i.id == "C23")
    gate = DualGate(GateSpec())
    lin = Lineage(c23, evaluator=ev, gate=gate, proposer=LibraryProposer(), reviewer=SmokeReviewer(dom, llm),
                  base=base, base_metrics=bm)
    res = lin.run()
    passing = [it for it in res.iterations if it.get("outcome") == "frozen"]
    assert res.frozen is not None and len(passing) >= 1
    etas = [it["metrics"]["agg"]["eta"] for it in passing]
    assert res.frozen.metrics.agg["eta"] == pytest.approx(min(etas))
    first = Lineage(c23, evaluator=ev, gate=gate, proposer=LibraryProposer(), reviewer=SmokeReviewer(dom, llm),
                    base=base, base_metrics=bm, sweep=False).run()
    assert first.frozen.metrics.agg["eta"] >= res.frozen.metrics.agg["eta"]


# ====================================================================== EPR archive failure (M7 documented deviation)
def test_epr_archive_integrity_failure_throws_like_archive_ts():
    """M7: archive.ts throws on an integrity failure and Pi's runner keeps the original result and reports an
    extension error; we used to journal a misleading "model-call-exception" fallback instead."""
    body = "E boom\n" + "." * 5000
    env = Env(outputs={"pytest -q": (body, True)})
    ext = EvidencePreservingReducer()
    rt = runtime(env, [[("bash", {"command": "pytest -q"})]], [ext])
    h = sha256(body)
    rt.store[f"/.solpi/evidence-preserving-reducer/objects/{h[:2]}/{h}.txt"] = "tampered"
    rt.run("t")
    tool = next(m for m in rt.history if m.role == "tool")
    assert tool.content == body and tool.is_error                            # original result kept
    assert any(e["type"] == "tool_result_handler_error" and "integrity" in e["data"]["error"] for e in rt.entries)
    assert ext.stats["fallbacks"] == 0
