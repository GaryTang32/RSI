"""Regression tests for the SoL-Pi claims-audit fixes (reference: NVlabs/SoL-Pi @ 1559b5c, Pi 0.85.1)."""
import json
import os
import re
import time
from types import SimpleNamespace

import pytest

import rsi.domains.agentworld as aw
import rsi.solpi.fusion as fusion
from rsi.core import Evaluator
from rsi.core.llm import Usage
from rsi.domains.agentworld import MockAgentLLM, make_domain
from rsi.domains.agentworld import domain as aw_domain
from rsi.domains.agentworld.base import Env as AWEnv
from rsi.domains.agentworld.envs import FAMILIES
from rsi.solpi import (AGENTWORLD_IDEAS, Config, DualGate, EvidencePreservingReducer, GateSpec, LibraryProposer,
                       Lineage, Message, Metrics, ObservationPack, OnlineContextCompact, SmokeReviewer, ToolCall,
                       metrics_from_eval, run, validate_receipt)
from rsi.solpi.fusion import (EDIT_THEN_RUN_DESCRIPTION, THEN_RUN_SKIPPED, THEN_RUN_SUCCEEDED,
                              WRITE_THEN_RUN_DESCRIPTION, ActionFusion, canonical_queue_key, resolve_tool_path)
from rsi.solpi.obspack import complete_line_excerpt, create_observation, read_recall_chunk
from rsi.solpi.occ import (analyze_plan_transition, format_plan_snapshot, js_grapheme_length, parse_plan_steps,
                           validate_update_plan_args)
from rsi.solpi.reducer import ArchiveObject, LLMReducer, receipt_text, sha256, source_lines, utf16_len
from rsi.solpi.research import IdeaPool, oracle_estimate
from rsi.solpi.runtime import Extension, ToolError, ToolResult, ToolResultEvent
from solpi_testkit import Env, bash_steps, failure_receipt, runtime

OPEN = [{"id": "build", "goal": "build it", "status": "in_progress"}]
DONE = [{"id": "build", "goal": "build it", "status": "completed"}]


def _idea(idea_id):
    return next(i for i in AGENTWORLD_IDEAS if i.id == idea_id)


def _pytest_event(body):
    return ToolResultEvent(ToolCall("c1", "bash", {"command": "pytest -q"}), ToolResult(body, True))


def test_plan_snapshot_is_json_stringify_of_steps_object():
    snap = format_plan_snapshot(tuple(OPEN))
    assert snap.startswith('<sol-pi-plan task_status="active">')
    assert '{"steps":[{"id":"build","goal":"build it","status":"in_progress"}]}' in snap
    assert format_plan_snapshot(({"id": "é", "goal": "g", "status": "pending"},)).count("é") == 1  # no \\u escape


def test_plan_advice_matches_release_vectors():
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
    """Schema violations are rejected, never truncated."""
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
        {"steps": OPEN, "progress": None},             # Type.Optional(object): absent is fine, null is not
    ]
    for b in bad:
        assert validate_update_plan_args(b), b
    occ = OnlineContextCompact()
    rt = runtime(exts=[occ])
    with pytest.raises(ToolError, match='Validation failed for tool "update_plan"'):
        occ._update_plan(bad[2], rt, "c1")
    assert js_grapheme_length("e\u0301") == 1 and js_grapheme_length("\U0001F1FA\U0001F1F8") == 1


def test_update_plan_tool_result_carries_snapshot_and_advice():
    occ = OnlineContextCompact()
    r = occ._update_plan({"steps": [{"id": "a", "goal": "g", "status": "pending"}]}, runtime(exts=[occ]), "c1")
    assert r.content == ('<sol-pi-plan task_status="active">{"steps":[{"id":"a","goal":"g","status":"pending"}]}'
                         '</sol-pi-plan>\nMark one pending plan step in_progress before starting it.')
    assert r.details["plan"] == [{"id": "a", "goal": "g", "status": "pending"}]


def _archive(body):
    ext = EvidencePreservingReducer()
    return ext._archive(runtime(exts=[ext]), body)


def test_epr_source_lines_is_split_length():
    """archive.ts: lines = body.split("\\n").length."""
    assert _archive("x\n" * 3000).lines == 3001 == len(("x\n" * 3000).split("\n"))
    assert _archive("a\nb").lines == 2 and source_lines("") == 0
    body = "FAILED t\n" + "." * 5000 + "\n"
    ar = _archive(body)
    ok, v = validate_receipt(json.dumps(failure_receipt(ar.hash, "FAILED t")), ar, body, True)
    assert ok and "source_lines=3\n" in receipt_text("pytest", ar, v, "p", "m", 1)


def test_epr_lengths_are_utf16_units():
    """Quote length, maxChars and ArchiveObject.chars count UTF-16 units like JavaScript."""
    q = "\U0001F600" * 400                                 # 400 code points, 800 UTF-16 units
    body = "FAILED " + q + "\n" + "." * 5000
    ar = ArchiveObject(sha256(body), len(body.encode()), utf16_len(body), source_lines(body), "/x")
    assert validate_receipt(json.dumps(failure_receipt(ar.hash, q)), ar, body, True) == (False, "unverifiable-quote")
    assert _archive(body).chars == len(body) + 400
    ext = EvidencePreservingReducer(max_chars=2000)
    rt = runtime(exts=[ext])
    big = "FAILED " + "\U0001F600" * 1100                  # 1,107 code points but 2,207 UTF-16 units
    assert ext._on_result(_pytest_event(big), rt) is None and ext.stats["fallback:source-over-max-chars"] == 1


class _LLM:
    name = "fake"

    def __init__(self, raw=None, sleep=0.0, max_tokens=None):
        self.resp = SimpleNamespace(text="{}", raw=raw, ok=True, error=None, usage=Usage(1, 10, 5, 0.0, 0.0))
        self.sleep, self.max_tokens, self.calls = sleep, max_tokens, []

    def complete(self, prompt, *, system=None, max_tokens=None, seed=None, role="default"):
        self.calls.append(max_tokens)
        time.sleep(self.sleep)
        return self.resp


def _reduce(llm, **kw):
    return LLMReducer(llm, **kw).reduce("s", "u", 2048, body="", archive=None, is_error=False)


def test_llm_reducer_timeout_max_tokens_and_stop_reason():
    """provider.ts: 90 s timeout, maxTokens = min(2048, model.maxTokens), stopReason from the provider."""
    llm = _LLM({"stop_reason": "end_turn"}, max_tokens=1000)
    assert _reduce(llm).stop_reason == "stop" and llm.calls == [1000]
    assert _reduce(_LLM({"stop_reason": "refusal"})).stop_reason == "error"
    assert _reduce(_LLM({"stop_reason": "max_tokens"})).stop_reason == "length"
    assert _reduce(_LLM(sleep=0.5), timeout_s=0.05).error == "timeout"
    ext = EvidencePreservingReducer(LLMReducer(_LLM({"stop_reason": "refusal"})))
    rt = runtime(exts=[ext])
    assert ext._on_result(_pytest_event("E fail\n" + "." * 5000), rt) is None
    assert ext.stats["fallback:model-response-error"] == 1


def _ts_excerpt(text, budget, from_end):
    """Reference port of completeLineExcerpt."""
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
    cr = "head\n" + "".join(f"progress {i}%\r" for i in range(100)) + "\n" + "x\n" * 6000 + "tail\n"
    for fe in (False, True):
        assert complete_line_excerpt(cr, 512, fe) == _ts_excerpt(cr, 512, fe)
    assert complete_line_excerpt(cr, 512, False) == "head\n"
    assert complete_line_excerpt("a\u2028b\nc\n", 3, False) == ""     # U+2028 is not a line end either


def test_obs_recall_rejects_negative_and_non_integer_offsets():
    op = ObservationPack()
    rt = runtime(exts=[op])
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
    op = ObservationPack()
    runtime(Env(outputs={"dump": ("row\n" * 4000, False)}), bash_steps("dump", "x", "y", "z"), [op]).run("t")
    ph = [e for e in op.ledger if e["event"] == "placeholder"]
    assert ph and ph[0]["originalLines"] == 4000


class Recorder(OnlineContextCompact):
    """Records (W used, Pi's getContextUsage W) at each boundary turn_end."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.w_at_turn_end = []

    def _turn_end(self, reply, results, rt):
        if self.pending_boundary is not None:
            self.w_at_turn_end.append((self.context_tokens(rt), rt.last_context_tokens + reply.tokens()
                                       + sum(m.tokens() for m in results)))
        return super()._turn_end(reply, results, rt)


def test_occ_write_tokens_follow_pi_get_context_usage():
    """W = the boundary reply's prompt + output + the tool results after it."""
    def plan(status):
        return "update_plan", {"steps": [{"id": "a", "goal": "g", "status": status}]}

    occ = Recorder(cache_write_read_ratio=12.5)
    steps = [[plan("in_progress")], [("bash", {"command": "big"})], [plan("completed"), ("bash", {"command": "big"})]]
    rt = runtime(Env(outputs={"big": ("z" * 40000, False)}), steps, [occ])
    rt.run("t")
    assert occ.w_at_turn_end and all(w >= pi_w for w, pi_w in occ.w_at_turn_end)
    w, pi_w = occ.w_at_turn_end[-1]
    assert w == pi_w > rt.meter.requests[-2].input          # includes the reply and the 40 kB boundary result
    assert occ.decisions[-1]["writeTokens"] == w


def test_occ_turn_end_skips_aborted_or_error_replies():
    occ = OnlineContextCompact(cache_write_read_ratio=0.0)
    rt = runtime(exts=[occ])
    res = [Message("tool", "ok", tool_call_id="c1", tool_name="update_plan")]
    occ.pending_boundary = "c1"
    occ._turn_end(Message("assistant", "x", details={"stop_reason": "aborted"}), res, rt)
    rt.abort()
    occ.pending_boundary = "c1"
    occ._turn_end(Message("assistant", "x"), res, rt)
    assert occ.decisions == []


class CountingContext(Extension):
    name = "counter"
    calls = 0

    def register(self, rt):
        rt.on("context", self._ctx)

    def _ctx(self, msgs, rt):
        self.calls += 1


def test_runtime_projects_once_per_request_even_when_auto_compacting():
    cnt = CountingContext()
    rt = runtime(Env(outputs={f"c{i}": ("q" * 30000, False) for i in range(30)}),
                 bash_steps(*(f"c{i}" for i in range(30))), [cnt], context_window=60_000, keep_recent_tokens=8_000,
                 auto_compact_reserve=16_384, max_turns=40)
    rt.run("t")
    assert rt.auto_compactions >= 1 and cnt.calls == rt.provider_requests


def test_runtime_auto_compaction_threshold_is_strict_and_uses_context_usage():
    """Pi's shouldCompact: contextTokens > window - reserve, from getContextUsage() on the stored messages."""
    rt = runtime(Env(outputs={"a": ("w" * 4000, False)}), bash_steps(*["a"] * 8), keep_recent_tokens=500)
    rt.run("t")
    last = rt.history[-1]
    assert last.details["usage_tokens"] == rt.last_context_tokens + last.tokens() == rt.context_usage()
    rt.history.append(Message("tool", "x" * 400, tool_call_id="c", tool_name="bash"))
    u = rt.context_usage()
    assert u == last.details["usage_tokens"] + rt.history[-1].tokens() and rt.compactable()
    rt.auto_compact_reserve = 1000
    rt.context_window = u + 1000                   # exactly at the threshold: no compaction
    assert not rt.should_auto_compact()
    rt.context_window = u + 999
    assert rt.should_auto_compact()
    rt.compact("")                                 # the old usage is stale after a compaction
    assert rt.context_usage() is None


def test_af_text_details_match_then_run_ts():
    rt = runtime(Env(outputs={"true": ("", False)}), exts=[ActionFusion()])
    write = rt.tools["write"]
    r = write.execute({"path": "t.py", "content": "x", "then_run": {"command": "true"}}, rt, "c1")
    assert r.content == "wrote t.py\n" + THEN_RUN_SUCCEEDED                 # empty output: marker only
    ran = []
    rt.env.tool_bash = lambda cmd, rt=None: (ran.append(cmd), ToolResult(""))[1]
    r = write.execute({"path": "t.py", "content": "y", "then_run": {"command": ""}}, rt, "c2")
    assert ran == [""] and r.content.endswith(THEN_RUN_SUCCEEDED)           # an empty command is run
    rt.env.tool_bash = lambda cmd, rt=None: ToolResult("", True)
    r = write.execute({"path": "t.py", "content": "z", "then_run": {"command": "boom"}}, rt, "c3")
    assert r.is_error and r.content == "wrote t.py\n\n[then_run:failed]"   # empty parts dropped
    assert write.description.endswith(WRITE_THEN_RUN_DESCRIPTION)
    assert rt.tools["edit"].description.endswith(EDIT_THEN_RUN_DESCRIPTION)
    with pytest.raises(ToolError, match="Validation failed"):
        write.execute({"path": "t.py", "content": "z", "then_run": "pytest"}, rt, "c4")


def test_af_path_resolution_matches_file_queue_ts():
    assert resolve_tool_path("~/proj/a.py", "/work", "/home/u") == "/home/u/proj/a.py"
    assert resolve_tool_path("~", "/work", "/home/u") == "/home/u"
    assert resolve_tool_path("~/proj/a.py") == os.path.join(os.path.expanduser("~"), "proj/a.py")
    assert resolve_tool_path("~\\notes.txt", "/work", "/h") == "/work/~\\notes.txt"
    assert resolve_tool_path("@target\u00a0file.txt", "/work") == "/work/target file.txt"
    assert resolve_tool_path("@file:///work/a%20b.txt", "/x") == "/work/a b.txt"
    assert resolve_tool_path("a\u200bb", "/w") == "/w/a\u200bb"      # U+200B is not a unicode space
    assert resolve_tool_path("./a.py") == "a.py"
    aw_env = AWEnv.__new__(AWEnv)                  # AgentWorld resolves like Pi's built-ins
    assert aw_env.resolve_path("/repo/src/a.py") == "src/a.py"
    assert aw_env.resolve_path("~/x.py") == "/home/agent/x.py"


def test_af_queue_key_is_realpath_so_symlinks_share_a_queue(tmp_path):
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
    rt = runtime(FsEnv(), exts=[af])
    assert af.queue_key(rt, "link.txt") == af.queue_key(rt, str(target))
    assert af._queue(af.queue_key(rt, "link.txt")) is af._queue(af.queue_key(rt, "real.txt"))


def test_af_hash_guard_yields_by_default_and_reports_enoent(monkeypatch):
    def write(rt, cid):
        return rt.tools["write"].execute({"path": "t.py", "content": "x", "then_run": {"command": "pytest"}}, rt, cid)

    env, calls = Env(), []
    rt = runtime(env, exts=[ActionFusion()])

    def interfering_writer():
        calls.append(1)
        env.files["t.py"] = "changed by another writer"

    monkeypatch.setattr(fusion, "_yield_for_interference", interfering_writer)
    with pytest.raises(ToolError, match=r"\[then_run:skipped\] target content changed"):
        write(rt, "c1")
    assert calls == [1]
    env2 = Env()
    rt2 = runtime(env2, exts=[ActionFusion(yield_hook=lambda r, p: env2.files.pop(p, None))])
    with pytest.raises(ToolError) as e:
        write(rt2, "c2")
    assert str(e.value) == (f"{THEN_RUN_SKIPPED} ENOENT: no such file or directory, open 't.py'; "
                            "the command was not run.")


def test_af_on_agentworld_passes_the_agent_path_to_the_builtin():
    dom = make_domain(seed=0, n_train=1, n_accept=0, n_final=0, n_test=0)
    task = dom.tasks.get(dom.tasks.splits["evolve"][0])
    env = FAMILIES[task.input["family"]](task.id, seed=task.input["env_seed"], n_subtasks=task.input["n_subtasks"])
    rt = runtime(env, exts=[ActionFusion()])
    r = rt.tools["write"].execute({"path": "./notes/x.txt", "content": "hi", "then_run": {"command": "ls notes"}},
                                  rt, "c1")
    assert env.files["notes/x.txt"] == "hi" and "to ./notes/x.txt" in r.content and THEN_RUN_SUCCEEDED in r.content


def test_agentworld_docstrings_name_both_heldout_families():
    assert aw_domain.HELDOUT_FAMILIES == ("configfix", "datalookup")
    for doc in (aw_domain.__doc__, aw.__doc__):
        assert "configfix" in doc and "datalookup" in doc
    assert "acceptance tasks of the held-out family\n(``datalookup``" not in aw_domain.__doc__


def test_idea_pool_covers_family_m_and_the_oracle_filters():
    assert set(IdeaPool(AGENTWORLD_IDEAS).by_family()) == {"C", "P", "T", "D", "R", "M"}
    assert len(AGENTWORLD_IDEAS) > Config().n_lineages               # the default breadth budget filters
    assert _idea("P20").oracle == "late_turn_tokens"


def test_protocol_runs_only_the_oracle_top_ideas(tmp_path):
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
    assert 0 < oracle_estimate(_idea("P20"), trials) < 1


def test_default_gate_rejects_the_lenient_turn_cap():
    """A 24-turn cap passes a score-only 2% floor (0.984) but solves only 22/24 tasks."""
    base = Metrics({"score": 1.0, "solved": 1.0, "tokens": 100.0, "cost": 1.0, "steps": 15.0, "eta": 1.0}, {}, 24)
    cap24 = Metrics({"score": 0.984, "solved": 0.917, "tokens": 92.0, "cost": 0.93, "steps": 14.0, "eta": 0.945},
                    {}, 24)
    real = Metrics({"score": 1.0, "solved": 1.0, "tokens": 70.0, "cost": 0.8, "steps": 11.0, "eta": 0.8}, {}, 24)
    assert DualGate(GateSpec(capability=(("score", 0.02),))).accept(base, cap24).accept
    g = DualGate(GateSpec()).accept(base, cap24)
    assert not g.accept and g.capability["solved"]["pass"] is False and g.capability["score"]["pass"] is True
    assert DualGate(GateSpec()).accept(base, real).accept
    missing = Metrics({"score": 1.0, "tokens": 50.0, "cost": 0.5, "steps": 1.0, "eta": 0.5}, {}, 1)
    assert not DualGate(GateSpec()).accept(base, missing).accept                               # fails closed


def _lineage_factory(n_train):
    """Base metrics on a fresh AgentWorld evolve split and a Lineage builder over it."""
    dom = make_domain(seed=0, n_train=n_train, n_accept=0, n_final=0, n_test=0)
    llm = MockAgentLLM("A")
    ev = Evaluator(dom, llm)
    base = dom.seed_artifact()
    bm = metrics_from_eval(ev.evaluate(base, "evolve"))

    def lineage(idea_id, spec=GateSpec(), **kw):
        return Lineage(_idea(idea_id), evaluator=ev, gate=DualGate(spec), proposer=LibraryProposer(),
                       reviewer=SmokeReviewer(dom, llm), base=base, base_metrics=bm, **kw)

    return bm, lineage


def test_turn_cap_lineage_is_abandoned_under_the_default_floor():
    bm, lineage = _lineage_factory(8)
    assert "solved" in bm.agg
    assert lineage("P20", GateSpec(capability=(("score", 0.02),))).run().frozen is not None
    assert lineage("P20").run().frozen is None


def test_lineage_sweeps_and_freezes_the_nondominated_best_eta_variant():
    assert Config().sweep is True
    _, lineage = _lineage_factory(4)
    res = lineage("C23").run()
    passing = [it for it in res.iterations if it.get("outcome") == "frozen"]
    assert res.frozen is not None and len(passing) >= 1
    assert res.frozen.metrics.agg["eta"] == pytest.approx(min(it["metrics"]["agg"]["eta"] for it in passing))
    first = lineage("C23", sweep=False).run()
    assert first.frozen.metrics.agg["eta"] >= res.frozen.metrics.agg["eta"]


def test_epr_archive_integrity_failure_throws_like_archive_ts():
    """An integrity failure keeps the original result and reports an extension error, not a fallback."""
    body = "E boom\n" + "." * 5000
    ext = EvidencePreservingReducer()
    rt = runtime(Env(outputs={"pytest -q": (body, True)}), bash_steps("pytest -q"), [ext])
    h = sha256(body)
    rt.store[f"/.solpi/evidence-preserving-reducer/objects/{h[:2]}/{h}.txt"] = "tampered"
    rt.run("t")
    tool = next(m for m in rt.history if m.role == "tool")
    assert tool.content == body and tool.is_error
    assert any(e["type"] == "tool_result_handler_error" and "integrity" in e["data"]["error"] for e in rt.entries)
    assert ext.stats["fallbacks"] == 0
