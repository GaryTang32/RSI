"""SoL-Pi runtime and mechanism tests (faithful-port behaviours; offline)."""
import dataclasses
import json

import pytest

from rsi.solpi import (DEFAULT_COMPACTION_ECONOMICS, FULL_SENDS, PRICES, THEN_RUN_FAILED, THEN_RUN_SKIPPED,
                       THEN_RUN_SUCCEEDED, ActionFusion, AgentRuntime, ArchiveObject, DeterministicReducer,
                       EvidencePreservingReducer, Message, MockReducer, ObservationPack, OnlineContextCompact,
                       TokenMeter, ToolCall, builtin_tools, decide_compaction, estimate_remaining_requests,
                       parse_solpi_config, validate_receipt)
from rsi.solpi.obspack import create_observation, read_recall_chunk
from rsi.solpi.occ import OnlineState, record_boundary, record_compaction, record_provider_request
from rsi.solpi.reducer import REDUCER_RECEIPT_SCHEMA, reducible_tool_result, sha256
from rsi.solpi.runtime import ToolResult, ToolResultEvent


# ------------------------------------------------------------------ scripted backend + env
class Script:
    """Backend that replays a list of tool-call batches (then finishes)."""

    name = "script"

    def __init__(self, steps):
        self.steps = list(steps)
        self.seen = []

    def act(self, msgs, tools, rt):
        self.seen.append(msgs)
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
        if cmd.startswith("cat /.solpi/") or cmd.startswith("cat /tmp/"):
            return ToolResult(rt.store[cmd[4:]])
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


# ------------------------------------------------------------------ meter
def test_prefix_cache_meter():
    m = TokenMeter({"main": PRICES["sim-a"]})
    u1 = m.request(["a", "b"], [100, 50])
    assert (u1.cache_read, u1.cache_write) == (0, 150)
    u2 = m.request(["a", "b", "c"], [100, 50, 30])
    assert (u2.cache_read, u2.cache_write) == (150, 30)
    u3 = m.request(["a", "x", "c"], [100, 60, 30])        # prefix broken at index 1
    assert (u3.cache_read, u3.cache_write) == (100, 90)
    assert PRICES["sim-a"].ratio == pytest.approx(12.5) and PRICES["sim-b"].ratio == pytest.approx(12.5)


# ------------------------------------------------------------------ Action Fusion
def test_action_fusion_success_failure_and_markers():
    env = Env({"a.py": "X = 1\n"}, {"pytest": ("1 passed", False), "pytest -x": ("1 failed", True)})
    steps = [[("edit", {"path": "a.py", "old": "X = 1", "new": "X = 2", "then_run": {"command": "pytest"}})],
             [("edit", {"path": "@a.py", "old": "X = 2", "new": "X = 3", "then_run": {"command": "pytest -x"}})],
             [("edit", {"path": "a.py", "old": "NOPE", "new": "Y", "then_run": {"command": "pytest"}})]]
    af = ActionFusion()
    rt = runtime(env, steps, [af])
    rt.run("task")
    tools = [m for m in rt.history if m.role == "tool"]
    assert THEN_RUN_SUCCEEDED in tools[0].content and not tools[0].is_error
    assert THEN_RUN_FAILED in tools[1].content and tools[1].is_error and env.files["a.py"] == "X = 3\n"  # edit kept
    assert THEN_RUN_SKIPPED in tools[2].content and "was not run" in tools[2].content
    assert af.stats["fused"] == 2 and af.stats["skipped_failed_mutation"] == 1


def test_action_fusion_hash_guard_skips_on_interleaved_write():
    env = Env({"a.py": "X = 1\n"}, {"pytest": ("1 passed", False)})
    ran = []

    def interleave(rt, path):
        env.files[path] = "X = 99\n"                           # another writer runs during the yield

    env.tool_bash = lambda cmd, rt=None: ran.append(cmd) or ToolResult("ran")
    rt = runtime(env, [[("edit", {"path": "a.py", "old": "X = 1", "new": "X = 2",
                                  "then_run": {"command": "pytest"}})]], [ActionFusion(yield_hook=interleave)])
    rt.run("task")
    res = [m for m in rt.history if m.role == "tool"][0]
    assert res.is_error and "target content changed" in res.content and ran == []


# ------------------------------------------------------------------ ObservationPack
BIG = "\n".join(f"line {i:05d} " + "x" * 60 for i in range(400)) + "\n"


def test_obspack_placeholder_after_full_sends_and_recall_fidelity():
    env = Env(outputs={"cat big": (BIG, False)})
    op = ObservationPack()
    steps = [[("bash", {"command": "cat big"})], [("read", {"path": "nope"})], [("read", {"path": "nope"})],
             [("read", {"path": "nope"})]]
    rt = runtime(env, steps, [op])
    rt.run("task")
    sent = rt.backend.seen
    carried = [any(m.content == BIG for m in msgs) for msgs in sent]
    assert carried[1:1 + FULL_SENDS] == [True] * FULL_SENDS and carried[1 + FULL_SENDS] is False
    ph = [m for m in sent[-1] if m.content.startswith("[large tool result replaced")][0]
    assert "id: obs_" in ph.content and rt.history[2].content == BIG            # history itself is untouched
    oid = ph.content.split("id: ")[1].split("\n")[0]
    pages, off = [], 0
    while True:
        r = op._recall({"id": oid, "offset": off}, rt, "c")
        body = r.content.split("\n", 2)[2]
        pages.append(body)
        off = r.details["next_offset"]
        if r.details["eof"]:
            break
        assert len(r.content.encode()) <= 16 * 1024
    assert "".join(pages) == BIG and len(pages) >= 2
    assert {e["event"] for e in op.ledger} >= {"full", "placeholder", "recall"}


def test_obspack_rules_threshold_errors_receipts_utf8_and_fail_open():
    small = Message("tool", "x" * 100, tool_call_id="c", tool_name="bash")
    assert create_observation(small) is None
    rec = Message("tool", "sol_pi_evidence_receipt_v1\n" + BIG, tool_call_id="c", tool_name="bash")
    assert create_observation(rec) is None
    data = ("é" * 9000).encode()
    ch = read_recall_chunk(data, 0, 1001, 400)
    assert ch["bytes"] % 2 == 0 and ch["text"] == "é" * (ch["bytes"] // 2)
    env = Env(outputs={"cat big": (BIG, False), "bad": (BIG, True)})
    op = ObservationPack()
    op.fail_store = True
    rt = runtime(env, [[("bash", {"command": "cat big"})], [("bash", {"command": "bad"})]] +
                 [[("read", {"path": "n"})]] * 3, [op])
    rt.run("t")
    assert op.stats["fail_open"] > 0 and any(m.content == BIG for m in rt.backend.seen[-1])


# ------------------------------------------------------------------ EPR
LOG = "\n".join(f"test_{i} PASSED" for i in range(300)) + "\nFAILED tests/test_a.py::t - AssertionError: 1 != 2\n" \
      + "\n".join(f"debug {i}" for i in range(200)) + "\n1 failed, 300 passed\n"


def _arch(body):
    return ArchiveObject(sha256(body), len(body.encode()), len(body), body.count("\n"), "/.solpi/x")


def test_validate_receipt_all_rejection_reasons():
    a = _arch(LOG)
    good = {"schema": REDUCER_RECEIPT_SCHEMA, "source_sha256": a.hash, "status": "failure", "uncertain": False,
            "evidence": [{"kind": "failure", "quote": "FAILED tests/test_a.py::t - AssertionError: 1 != 2"}]}
    ok, v = validate_receipt(json.dumps(good), a, LOG, True)
    assert ok and v["evidence"][0]["line"] == 301
    cases = {"invalid-json": "nope",
             "schema-mismatch": json.dumps({**good, "status": "success"}),
             "unverifiable-quote": json.dumps({**good, "evidence": [{"kind": "failure", "quote": "FAILED: made up"}]}),
             "missing-failure-evidence": json.dumps({**good, "evidence": [{"kind": "summary",
                                                                          "quote": "1 failed, 300 passed"}]})}
    for reason, raw in cases.items():
        assert validate_receipt(raw, a, LOG, True) == (False, reason)
    assert validate_receipt(json.dumps({**good, "source_sha256": "0" * 64}), a, LOG, True)[1] == "schema-mismatch"
    too_many = {**good, "evidence": good["evidence"] * 13}
    assert validate_receipt(json.dumps(too_many), a, LOG, True)[1] == "schema-mismatch"
    dup = {**good, "evidence": good["evidence"] * 3}
    assert len(validate_receipt(json.dumps(dup), a, LOG, True)[1]["evidence"]) == 1


def test_epr_replaces_long_diagnostic_output_and_falls_back():
    env = Env(outputs={"pytest -q": (LOG, True), "cat notes": (LOG, True), "pytest short": ("1 failed", True),
                       "make": ("api_key=abc123\n" + LOG, True)})
    epr = EvidencePreservingReducer(DeterministicReducer())
    rt = runtime(env, [[("bash", {"command": "pytest -q"})], [("bash", {"command": "cat notes"})],
                       [("bash", {"command": "pytest short"})], [("bash", {"command": "make"})]], [epr])
    rt.run("t")
    tools = [m for m in rt.history if m.role == "tool"]
    assert tools[0].content.startswith("sol_pi_evidence_receipt_v1") and len(tools[0].content) < len(LOG)
    assert "FAILED tests/test_a.py::t" in tools[0].content and "source_artifact=/.solpi/" in tools[0].content
    assert tools[1].content == LOG                    # not a diagnostic command
    assert tools[2].content == "1 failed"             # below 4096 bytes
    assert tools[3].content.startswith("api_key")     # likely secret -> fallback
    assert epr.stats["fallback:likely-secret"] == 1 and epr.stats["applied"] == 1
    path = [l for l in tools[0].content.splitlines() if l.startswith("source_artifact=")][0].split("=", 1)[1]
    assert rt.store[path] == LOG                      # exact readback available


def test_epr_accepted_receipts_are_always_verbatim_under_hallucination():
    for h in (0.0, 0.5, 1.0):
        mr = MockReducer(h=h, seed=1)
        epr = EvidencePreservingReducer(mr)
        env = Env(outputs={f"pytest {i}": (LOG.replace("1 != 2", f"{i} != 2"), True) for i in range(30)})
        rt = runtime(env, [[("bash", {"command": f"pytest {i}"})] for i in range(30)], [epr], max_turns=40)
        rt.run("t")
        for rec in epr.receipts:
            body = rt.store[rec["archive_path"]]
            assert all(e["quote"] in body for e in rec["evidence"])
            assert any(e["kind"] in ("failure", "fatal") for e in rec["evidence"])
        if h == 1.0:
            assert epr.stats["fallbacks"] >= 20
        if h == 0.0:
            assert epr.stats["applied"] == 30


def test_epr_handles_fused_then_run_results():
    content = "edited a.py\n\n" + THEN_RUN_FAILED + "\n\n" + LOG
    ev = ToolResultEvent(ToolCall("c1", "edit", {"path": "a.py", "then_run": {"command": "pytest"}}),
                         ToolResult(content, True))
    r = reducible_tool_result(ev)
    assert r.command == "pytest" and r.body == LOG and r.project("RECEIPT").endswith("\n\nRECEIPT")


# ------------------------------------------------------------------ OCC
def test_occ_economics_test_vectors():
    h = estimate_remaining_requests(completed_boundary_request_counts=[4, 6, 5], remaining_boundaries=3, scale=1,
                                    standard_deviation_k=0, context_tokens=100_000, context_window_tokens=200_000,
                                    average_context_token_increment=5_000)
    assert (h["requestsPerBoundaryMean"], h["expectedRemainingRequests"], h["windowRequestUpperBound"]) == (5, 16, 20)

    def dec(**o):
        base = dict(write_tokens=80_000, archive_tokens=60_000, memo_tokens=1_000, context_tokens=80_000,
                    completed_boundary_request_counts=[4, 6, 5], remaining_boundaries=4,
                    average_context_token_increment=2_000, context_window_tokens=200_000, prior_compaction_count=0,
                    carried_debt_tokens=0, cache_debt_repayment_tokens=0, cache_write_read_ratio=1,
                    economics=DEFAULT_COMPACTION_ECONOMICS)
        return decide_compaction(**{**base, **o})

    assert (dec()["compact"], dec()["reason"]) == (True, "economic")
    assert dec(archive_tokens=500, memo_tokens=1000)["reason"] == "non_positive_saving"
    assert dec(context_tokens=195_000, cache_write_read_ratio=100,
               economics=dataclasses.replace(DEFAULT_COMPACTION_ECONOMICS, window_reserve_tokens=10_000))["reason"] \
        == "window_protection"
    assert dec(cache_write_read_ratio=None)["reason"] == "cache_ratio_unavailable"
    r = dec(prior_compaction_count=1, cache_write_read_ratio=2, carried_debt_tokens=2_000_000)
    assert (r["compact"], r["reason"]) == (False, "deferred_carried_debt")
    assert r["combinedBreakevenRequests"] > r["breakevenRequests"]


def test_occ_state_transitions():
    s = record_provider_request(OnlineState(), 1000)
    s = record_provider_request(s, 1500)
    assert (s.request_count, s.positive_context_delta_total, s.positive_context_delta_count) == (2, 500, 1)
    s = record_boundary(s, ({"id": "a", "goal": "g", "status": "completed"},), None)
    assert s.completed_boundary_request_counts == (2,)
    s = record_compaction(s, 1000, 400)
    assert (s.native_compaction_count, s.epoch, s.plan) == (1, 1, ())
    s = record_provider_request(s, 800)
    assert s.cache_debt_tokens == 600


def test_occ_compacts_at_boundary_and_reminds(tmp_path):
    env = Env(outputs={f"run {i}": ("y" * 60000, False) for i in range(20)})
    plan = lambda done: [("update_plan", {"steps": [{"id": f"s{j}", "goal": "g",
                                                     "status": "completed" if j < done else "pending"}
                                                    for j in range(8)]})]
    steps = []
    for i in range(8):
        steps.append([("bash", {"command": f"run {i}"})])
        steps.append(plan(i + 1) + [("bash", {"command": f"run {i + 10}"})])
    occ = OnlineContextCompact(cache_write_read_ratio=1.0)
    rt = runtime(env, [plan(0)] + steps, [occ], context_window=400_000, max_turns=60)
    rt.run("t")
    assert occ.stats["boundaries"] >= 3 and occ.stats["compactions"] >= 1
    assert any(m.hidden and "Online context compaction finished" in m.content for m in rt.history)
    assert rt.history[0].details.get("compaction") or any(m.details.get("compaction") for m in rt.history)
    assert occ.state.native_compaction_count == rt.compactions >= 1
    assert all(d["reason"] in {"economic", "window_protection", "deferred_economic", "deferred_subsequent_margin",
                               "deferred_carried_debt", "horizon_unavailable", "cache_ratio_unavailable",
                               "native_not_compactable", "non_positive_saving"} for d in occ.decisions)


def test_solpi_config_validation():
    assert parse_solpi_config({"actionFusion": True})["extensions"] == {"action_fusion": {}}
    for bad in ({"foo": 1}, {"version": 2}, {"actionFusion": "yes"}, {"cacheWriteReadRatio": -1},
                {"evidencePreservingReducerModel": " "}):
        with pytest.raises(ValueError):
            parse_solpi_config(bad)
