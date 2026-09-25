"""rsi.core.llm: usage metering, MockLLM, CachedLLM, get_llm, ClaudeCLI (against a
fake ``claude`` executable, fully offline) and the text-parsing helpers."""
from __future__ import annotations

import json
import os
import stat
import sys
import threading
from pathlib import Path

import pytest

import rsi.core.llm as llm_mod
from rsi.core import (LLM, CachedLLM, ClaudeCLI, LLMResponse, MockLLM, Usage, UsageMeter, extract_code,
                      extract_code_blocks, extract_json, extract_tag, get_llm)
from rsi.core.llm import estimate_tokens, resolve_model


# ------------------------------------------------------------------ Usage / meter
def test_usage_add_and_total_tokens():
    a = Usage(1, 10, 5, 0.25, 1.0)
    b = Usage(2, 1, 2, 0.5, 0.5)
    c = a + b
    assert (c.calls, c.input_tokens, c.output_tokens) == (3, 11, 7)
    assert c.cost_usd == pytest.approx(0.75) and c.latency_s == pytest.approx(1.5)
    assert c.total_tokens == 18
    assert c.to_dict()["total_tokens"] == 18


def test_usage_meter_roles_snapshot_reset():
    m = UsageMeter()
    m.add("task", Usage(1, 10, 1, 0.1))
    m.add("task", Usage(1, 10, 1, 0.1))
    m.add("proposer", Usage(1, 100, 50, 1.0))
    snap = m.snapshot()
    assert snap["task"]["calls"] == 2 and snap["task"]["input_tokens"] == 20
    assert snap["_total"]["calls"] == 3
    assert snap["_total"]["cost_usd"] == pytest.approx(1.2)
    assert m.total().total_tokens == 20 + 2 + 150
    m.reset()
    assert m.total().calls == 0 and m.snapshot() == {"_total": Usage().to_dict()}


def test_usage_meter_is_thread_safe():
    m = UsageMeter()

    def work():
        for _ in range(2000):
            m.add("r", Usage(1, 1, 1, 0.0))

    ts = [threading.Thread(target=work) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert m.total().calls == 16000


def test_estimate_tokens_and_model_aliases():
    assert estimate_tokens("") == 0 and estimate_tokens(None) == 0
    assert estimate_tokens("abcd") == 1 and estimate_tokens("abcde") == 2
    assert resolve_model("haiku").startswith("claude-haiku")
    assert resolve_model("my-custom-model") == "my-custom-model"


# ------------------------------------------------------------------------ MockLLM
def test_mock_llm_default_and_responder_arguments():
    assert MockLLM().complete("hi").text == "OK"
    seen = []

    def responder(prompt, system, seed, i):
        seen.append((prompt, system, seed, i))
        return f"{prompt}|{system}|{seed}|{i}"

    m = MockLLM(responder, name="m1")
    r0 = m.complete("p", system="s", seed=7, role="task")
    r1 = m.complete("q", role="proposer")
    assert r0.text == "p|s|7|0" and r1.text == "q|None|None|1"
    assert r0.ok and r0.model == "m1" and r0.error is None
    assert seen[1] == ("q", None, None, 1)
    assert [c["reply"] for c in m.calls] == [r0.text, r1.text]
    snap = m.meter.snapshot()
    assert snap["task"]["calls"] == 1 and snap["proposer"]["calls"] == 1
    # usage is estimated from text length, so cost rules see realistic token counts
    assert r0.usage.input_tokens == estimate_tokens("s" + "p")
    assert r0.usage.output_tokens == estimate_tokens(r0.text)


def test_mock_llm_threaded_call_indices_unique():
    m = MockLLM(lambda p, s, seed, i: str(i))
    outs = []
    lock = threading.Lock()

    def work():
        for _ in range(50):
            r = m.complete("x")
            with lock:
                outs.append(int(r.text))

    ts = [threading.Thread(target=work) for _ in range(6)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sorted(outs) == list(range(300))


class _Priced(MockLLM):
    """Mock that reports a dollar cost per call and can be switched to fail."""

    def __init__(self, cost=0.5, fail=False):
        super().__init__(lambda p, s, seed, i: f"reply to {p} #{seed}", name="priced")
        self.cost, self.fail = cost, fail

    def _complete(self, prompt, *, system, max_tokens, seed):
        r = super()._complete(prompt, system=system, max_tokens=max_tokens, seed=seed)
        r.usage.cost_usd = self.cost
        if self.fail:
            r.error = "backend down"
        return r


# ---------------------------------------------------------------------- CachedLLM
def test_cached_llm_miss_then_hit(tmp_path):
    inner = _Priced()
    c = CachedLLM(inner, tmp_path)
    r1 = c.complete("hello", seed=1, role="task")
    r2 = c.complete("hello", seed=1, role="task")
    assert r1.text == r2.text
    assert len(inner.calls) == 1, "second call must be served from disk"
    assert (c.hits, c.misses) == (1, 1)
    # the hit costs nothing and counts no call, but keeps its token counts (artifact cost C)
    assert r2.usage.calls == 0 and r2.usage.cost_usd == 0.0
    assert r2.usage.input_tokens == r1.usage.input_tokens and r2.usage.output_tokens == r1.usage.output_tokens
    snap = c.meter.snapshot()
    assert snap["task"]["calls"] == 1 and snap["task"]["cost_usd"] == pytest.approx(0.5)
    assert snap["task:cached"]["calls"] == 0 and snap["task:cached"]["cost_usd"] == 0.0
    assert snap["task:cached"]["output_tokens"] == r1.usage.output_tokens
    # totals report real spend only (bug fix: cached hits used to re-count their original dollars)
    assert c.meter.total().cost_usd == pytest.approx(0.5) and c.meter.total().calls == 1
    assert c.saved.total().cost_usd == pytest.approx(0.5)


def test_cached_llm_replay_in_new_instance_is_free(tmp_path):
    CachedLLM(_Priced(), tmp_path).complete("p", seed=0)
    inner = _Priced()
    c2 = CachedLLM(inner, tmp_path)
    assert c2.complete("p", seed=0).text == "reply to p #0"
    assert inner.calls == [] and c2.meter.total().cost_usd == 0.0


def test_cached_llm_key_components(tmp_path):
    inner = _Priced()
    c = CachedLLM(inner, tmp_path)
    c.complete("p", seed=0)
    c.complete("p", seed=1)                  # seed distinguishes repeated samples
    c.complete("p", seed=0, system="sys")    # system prompt is part of the key
    c.complete("p", seed=0, max_tokens=5)    # so is max_tokens
    assert len(inner.calls) == 4
    other = _Priced()
    other.name = "other-model"               # and the inner backend's name
    CachedLLM(other, tmp_path).complete("p", seed=0)
    assert len(other.calls) == 1


def test_cached_llm_does_not_cache_errors(tmp_path):
    inner = _Priced(fail=True)
    c = CachedLLM(inner, tmp_path)
    assert not c.complete("p").ok
    inner.fail = False
    assert c.complete("p").ok
    assert len(inner.calls) == 2
    assert c.complete("p").ok and len(inner.calls) == 2


def test_cached_llm_corrupt_entry_is_a_miss(tmp_path):
    inner = _Priced()
    c = CachedLLM(inner, tmp_path)
    c.complete("p", seed=0)
    path = c._key("p", None, None, 0)
    path.write_text('{"text": "trunc')        # e.g. a crash mid-write by an older version
    r = c.complete("p", seed=0)
    assert r.ok and r.text == "reply to p #0" and len(inner.calls) == 2
    assert json.loads(path.read_text())["text"] == "reply to p #0"


def test_cached_llm_concurrent_writers_same_key(tmp_path):
    """Bug fix: concurrent misses on one key used to race on a shared temp file
    (FileNotFoundError inside the harness -> a spurious failed trial)."""
    c = CachedLLM(MockLLM(lambda p, s, seed, i: "R" * 2000), tmp_path)
    errors = []

    def work():
        try:
            for k in range(60):
                c.complete(f"prompt {k}", seed=0)
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))

    ts = [threading.Thread(target=work) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert errors == []
    assert not list(tmp_path.rglob("*.tmp"))
    assert c.hits + c.misses == 480


# ------------------------------------------------------------------------ get_llm
def test_get_llm_specs(tmp_path, monkeypatch):
    assert isinstance(get_llm("mock"), MockLLM)
    cli = get_llm("claude:haiku", timeout_s=5)
    assert isinstance(cli, ClaudeCLI) and cli.model == resolve_model("haiku") and cli.timeout_s == 5
    assert get_llm("claude").model == resolve_model("haiku")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert isinstance(get_llm("auto:haiku"), ClaudeCLI)
    cached = get_llm("mock", cache_dir=str(tmp_path))
    assert isinstance(cached, CachedLLM) and isinstance(cached.inner, MockLLM)
    with pytest.raises(ValueError):
        get_llm("bogus:x")


def test_base_llm_is_abstract():
    with pytest.raises(NotImplementedError):
        LLM().complete("x")


# ---------------------------------------------------- ClaudeCLI vs a fake executable
FAKE_CLAUDE = r'''
import json, os, sys, time
from pathlib import Path
prompt = sys.stdin.read()
log = os.environ.get("FAKE_CLAUDE_LOG")
if log:
    with open(log, "a") as f:
        f.write(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd(), "prompt": prompt}) + "\n")
mode = "ok"
for line in prompt.splitlines():
    if line.startswith("@@mode "):
        mode = line.split(" ", 1)[1].strip()
    elif line.startswith("@@write "):
        _, path, text = line.split(" ", 2)
        p = Path(path); p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text.replace("\\n", "\n"))
    elif line.startswith("@@delete "):
        Path(line.split(" ", 1)[1].strip()).unlink()
result = {"type": "result", "subtype": "success", "is_error": False, "result": "fake reply",
          "usage": {"input_tokens": 10, "cache_read_input_tokens": 5, "cache_creation_input_tokens": 1,
                    "output_tokens": 3}, "total_cost_usd": 0.0012}
if mode == "flaky":
    counter = Path(os.environ["FAKE_CLAUDE_COUNTER"])
    n = int(counter.read_text()) if counter.exists() else 0
    counter.write_text(str(n + 1))
    mode = "error" if n == 0 else "ok"
if mode == "ok":
    print(json.dumps(result))
elif mode == "list":
    print(json.dumps([{"type": "system", "subtype": "init"}, {"type": "assistant"}, dict(result, result="from list")]))
elif mode == "error":
    print(json.dumps({"type": "result", "is_error": True, "result": "rate limited"}))
elif mode == "garbage":
    print("this is not json"); print("boom", file=sys.stderr); sys.exit(1)
elif mode == "sleep":
    time.sleep(30)
'''


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    exe = tmp_path / "fake_claude"
    exe.write_text(f"#!{sys.executable}\n" + FAKE_CLAUDE)
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    monkeypatch.setenv("FAKE_CLAUDE_COUNTER", str(tmp_path / "counter"))
    monkeypatch.setattr(llm_mod.time, "sleep", lambda s: None)  # no real backoff in tests

    def calls():
        return [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []

    return str(exe), calls


def test_claude_cli_parses_usage_and_cost(fake_claude):
    exe, calls = fake_claude
    cli = ClaudeCLI("haiku", executable=exe, retries=1)
    r = cli.complete("hello", system="be brief", role="task")
    assert r.ok and r.text == "fake reply" and r.model == resolve_model("haiku")
    # cache read/creation tokens count as input tokens
    assert r.usage.input_tokens == 16 and r.usage.output_tokens == 3
    assert r.usage.cost_usd == pytest.approx(0.0012) and r.usage.calls == 1
    assert cli.meter.snapshot()["task"]["cost_usd"] == pytest.approx(0.0012)
    argv = calls()[0]["argv"]
    assert argv[0] == "-p" and argv[argv.index("--model") + 1] == resolve_model("haiku")
    assert argv[argv.index("--system-prompt") + 1] == "be brief"
    assert argv[argv.index("--tools") + 1] == ""          # plain completions get no tools
    assert "--no-session-persistence" in argv and "--strict-mcp-config" in argv
    assert calls()[0]["prompt"] == "hello"


def test_claude_cli_list_output_and_errors(fake_claude):
    exe, calls = fake_claude
    cli = ClaudeCLI("haiku", executable=exe, retries=2)
    assert cli.complete("@@mode list").text == "from list"
    r = cli.complete("@@mode error")
    assert not r.ok and "rate limited" in r.error and r.text == ""
    assert r.usage.calls == 1 and r.usage.cost_usd == 0.0
    r = cli.complete("@@mode garbage")
    assert not r.ok and "boom" in r.error
    n_before = len(calls())
    assert cli.complete("@@mode flaky").ok            # retried after one failure
    assert len(calls()) - n_before == 2


def test_claude_cli_timeout(fake_claude):
    exe, _ = fake_claude
    cli = ClaudeCLI("haiku", executable=exe, retries=1, timeout_s=0.5)
    r = cli.complete("@@mode sleep")
    assert not r.ok and r.error == "timeout"


def test_claude_cli_run_agent_command(fake_claude, tmp_path):
    exe, calls = fake_claude
    cli = ClaudeCLI("haiku", executable=exe, retries=1, effort="low")
    work = tmp_path / "work"
    work.mkdir()
    r = cli.run_agent("@@write x.txt hi", cwd=str(work), tools=("Read", "Edit"), role="agent")
    assert r.ok and (work / "x.txt").read_text() == "hi"
    c = calls()[-1]
    assert c["cwd"] == str(work)
    argv = c["argv"]
    assert argv[argv.index("--tools") + 1] == "Read,Edit"
    assert argv[argv.index("--permission-mode") + 1] == "acceptEdits"
    assert argv[argv.index("--add-dir") + 1] == str(work)
    assert argv[argv.index("--effort") + 1] == "low"
    assert cli.meter.snapshot()["agent"]["calls"] == 1


# ---------------------------------------------------------------- text parsing
def test_extract_code_blocks_and_code():
    text = "intro\n```python\nx = 1\n```\nmid\n```js\nlet y = 2;\n```\n```\nplain\n```\n```python title\nlonger = 2\n```"
    assert extract_code_blocks(text) == ["x = 1\n", "let y = 2;\n", "plain\n", "longer = 2\n"]
    assert extract_code_blocks(text, "PYTHON") == ["x = 1\n", "longer = 2\n"]
    assert extract_code(text) == "longer = 2\n"                   # longest python block
    assert extract_code("```\nonly\n```") == "only\n"             # falls back to any fence
    assert extract_code("no fences") is None
    assert extract_code_blocks(None) == []


def test_extract_json_variants():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('text ```\n[1, 2]\n``` more') == [1, 2]
    assert extract_json('prefix {"a": {"b": "}"}, "c": [1]} suffix') == {"a": {"b": "}"}, "c": [1]}
    assert extract_json('escaped {"q": "say \\"hi\\" {"} end') == {"q": 'say "hi" {'}
    assert extract_json("list only: [1, 2, 3]") == [1, 2, 3]
    # objects are preferred over arrays (callers expect a header object)
    assert extract_json('steps [1] then {"verdict": "accept"}') == {"verdict": "accept"}
    # a broken candidate is skipped in favour of a later valid one
    assert extract_json('{not json} and {"ok": true}') == {"ok": True}
    # a fenced scalar does not shadow a later object; alone it is still returned
    assert extract_json('```\n42\n```\n{"a": 1}') == {"a": 1}
    assert extract_json("```\n42\n```") == 42
    with pytest.raises(ValueError):
        extract_json("nothing here")
    with pytest.raises(ValueError):
        extract_json(None)


def test_extract_tag():
    assert extract_tag("<a> one </a> x <a>two</a>", "a") == "two"
    assert extract_tag("<a>\nmulti\nline\n</a>", "a") == "multi\nline"
    assert extract_tag("none", "a") is None


def test_llm_response_ok():
    assert LLMResponse("x", Usage(), "m").ok
    assert not LLMResponse("", Usage(), "m", error="e").ok
