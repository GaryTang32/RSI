"""rsi.core.editors (parse_proposal, scope guard, RewriteEditor, AgentEditor via an offline fake agent) and
rsi.core.critic (LeakageCritic whole-token denylist and fail-closed LLM review)."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from rsi.core import (AgentEditor, Artifact, ClaudeCLI, LeakageCritic, LLMResponse, MockLLM, RewriteEditor, Usage,
                      parse_proposal)
from rsi.core.critic import CRITIC_SYSTEM, added_lines
from rsi.core.editors import EDIT_FORMAT, apply_scope

BASE = Artifact({"harness.py": "def solve(q):\n    return q\n", "prompts/task.md": "Answer: {q}\n",
                 "notes.md": "old notes\n"})


def reply(header: dict | None, files: dict[str, str]) -> str:
    out = [] if header is None else ["```json\n" + json.dumps(header) + "\n```"]
    return "\n".join(out + [f"=== FILE: {name} ===\n{text}" for name, text in files.items()])


class _Down(MockLLM):
    def _complete(self, prompt, *, system, max_tokens, seed):
        return LLMResponse("", Usage(1, 5, 0), "down", error="503")


def test_parse_proposal_header_files_and_deletes():
    text = reply({"change": "add verify step", "hypothesis": "checks help", "components": ["prompt", "control_flow"]},
                 {"prompts/task.md": "Answer carefully: {q}\n", "notes.md": "<<DELETE>>", "skills/new.md": "tip\n"})
    p = parse_proposal(BASE, text, usage=Usage(1, 10, 20))
    assert p.ok and p.error is None and p.blocked_files == []
    assert p.change == "add verify step" and p.hypothesis == "checks help"
    assert p.components == ["prompt", "control_flow"] and p.usage.output_tokens == 20
    assert dict(p.artifact) == {"harness.py": BASE["harness.py"], "prompts/task.md": "Answer carefully: {q}\n",
                                "skills/new.md": "tip\n"}
    assert p.raw == text


def test_parse_proposal_components_string_and_truncation():
    p = parse_proposal(BASE, reply({"change": "x" * 900, "hypothesis": "h" * 3000, "components": "prompt"},
                                   {"notes.md": "new\n"}))
    assert p.components == ["prompt"] and len(p.change) == 500 and len(p.hypothesis) == 2000
    p = parse_proposal(BASE, reply(None, {"notes.md": "new\n"}))
    assert p.ok and p.change == "" and p.components == []


def test_parse_proposal_scope_guard():
    p = parse_proposal(BASE, reply({"change": "c"}, {"harness.py": "hacked\n", "prompts/task.md": "better {q}\n"}),
                       editable=["prompts/*"])
    assert p.ok and p.blocked_files == ["harness.py"]
    assert p.artifact["harness.py"] == BASE["harness.py"] and p.artifact["prompts/task.md"] == "better {q}\n"
    only_blocked = parse_proposal(BASE, reply({"change": "c"}, {"harness.py": "hacked\n"}), editable=["prompts/*"])
    assert not only_blocked.ok and only_blocked.error == "no effective change (blocked edits to ['harness.py'])"


def test_parse_proposal_blocks_paths_outside_the_artifact():
    p = parse_proposal(BASE, reply({"change": "c"}, {"../escape.py": "x\n", "/etc/evil": "x\n", "ok.md": "fine\n"}))
    assert p.ok and set(p.blocked_files) == {"../escape.py", "/etc/evil"} and set(p.artifact) == set(BASE) | {"ok.md"}


def test_parse_proposal_no_blocks_and_no_change():
    p = parse_proposal(BASE, '```json\n{"change": "idea only"}\n```\nI would change the prompt.')
    assert not p.ok and p.artifact is None and p.error == "no file blocks in reply" and p.change == "idea only"
    same = parse_proposal(BASE, reply({"change": "noop"}, {"notes.md": "old notes\n"}))
    assert not same.ok and same.error == "no effective change" and same.artifact == BASE
    assert parse_proposal(BASE, reply({}, {"never-existed.md": "<<DELETE>>"})).error == "no effective change"
    assert parse_proposal(BASE, None).error == "no file blocks in reply"


def test_parse_proposal_header_only_before_first_block():
    p = parse_proposal(BASE, "=== FILE: notes.md ===\n{\"change\": \"this is file content, not a header\"}\n")
    assert p.ok and p.change == "" and p.artifact["notes.md"].startswith("{")


def test_apply_scope_returns_blocked_names():
    new, blocked = apply_scope(BASE, {"a.md": "1", "b.py": "2", "prompts/x.md": None}, ["*.md", "prompts/*"])
    assert blocked == ["b.py"] and "a.md" in new and "b.py" not in new


def test_rewrite_editor_prompt_and_result():
    llm = MockLLM(lambda p, s, seed, i: reply({"change": "tweak", "components": ["prompt"]},
                                               {"prompts/task.md": "Think step by step: {q}\n"}))
    p = RewriteEditor(llm, max_context_chars=50).edit(
        BASE, "  Improve the harness.  ", context={"history.txt": "h" * 40, "traces.txt": "t" * 40},
        editable=["prompts/*"], system="SYS", seed=4, role="proposer")
    assert p.ok and p.artifact["prompts/task.md"] == "Think step by step: {q}\n"
    call = llm.calls[0]
    assert call["system"] == "SYS" and call["seed"] == 4
    prompt = call["prompt"]
    assert prompt.startswith("Improve the harness.")
    assert "--- CONTEXT: history.txt ---\n" + "h" * 40 in prompt
    assert "t" * 10 + "\n...[truncated]" in prompt and "t" * 11 not in prompt   # 50-char budget: 40 + 10
    assert "=== FILE: harness.py ===" in prompt and "Editable files (glob patterns): ['prompts/*']" in prompt
    assert prompt.rstrip().endswith(EDIT_FORMAT.rstrip())
    assert llm.meter.snapshot()["proposer"]["calls"] == 1


def test_rewrite_editor_llm_error():
    p = RewriteEditor(_Down()).edit(BASE, "go")
    assert not p.ok and p.error == "llm error: 503" and p.artifact is None


class FakeAgentCLI(ClaudeCLI):
    """Stands in for ``claude -p`` as a coding agent: applies scripted file edits in its working directory."""

    def __init__(self, edits=None, proposal=None, error=None, raw_proposal=None):
        super().__init__("haiku")
        self.edits, self.proposal, self.err, self.raw_proposal = edits or {}, proposal, error, raw_proposal
        self.seen: dict = {}

    def run_agent(self, prompt, *, cwd, system=None, tools=(), timeout_s=None, role="agent"):
        work = Path(cwd)
        self.seen = {"prompt": prompt, "cwd": cwd, "tools": tools, "role": role, "timeout_s": timeout_s,
                     "files": sorted(p.relative_to(work).as_posix() for p in work.rglob("*") if p.is_file())}
        for name, text in self.edits.items():
            p = work / name
            if text is None:
                p.unlink()
            else:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(text)
        (work / "_context").mkdir(exist_ok=True)
        (work / "_context" / "scratch.txt").write_text("agent notes in context dir")
        if self.raw_proposal is not None:
            (work / "_proposal.json").write_text(self.raw_proposal)
        elif self.proposal is not None:
            (work / "_proposal.json").write_text(json.dumps(self.proposal))
        usage = Usage(1, 100, 50, 0.01)
        self.meter.add(role, usage)
        return LLMResponse("done", usage, self.model, error=self.err)


def test_agent_editor_applies_scoped_edits_and_reads_proposal():
    cli = FakeAgentCLI(edits={"prompts/task.md": "Verify then answer: {q}\n", "harness.py": "hacked\n",
                              "notes.md": None, "prompts/extra.md": "new\n"},
                       proposal={"change": "verify", "hypothesis": "general", "components": "prompt"})
    p = AgentEditor(cli, timeout_s=12).edit(BASE, "Make it better.",
                                            context={"history.md": "round 1: ...", "traces/t1.txt": "trace"},
                                            editable=["prompts/*", "notes.md"], role="proposer")
    assert p.ok, p.error
    assert p.blocked_files == ["harness.py"]
    assert dict(p.artifact) == {"harness.py": BASE["harness.py"], "prompts/task.md": "Verify then answer: {q}\n",
                                "prompts/extra.md": "new\n"}
    assert (p.change, p.hypothesis, p.components) == ("verify", "general", ["prompt"])
    assert p.usage.cost_usd == 0.01 and p.raw == "done"
    assert {"_context/history.md", "_context/traces/t1.txt", "harness.py"} <= set(cli.seen["files"])
    assert "You may only edit files matching ['prompts/*', 'notes.md']" in cli.seen["prompt"]
    assert "_proposal.json" in cli.seen["prompt"] and cli.seen["timeout_s"] == 12 and cli.seen["role"] == "proposer"
    assert not Path(cli.seen["cwd"]).exists()               # scratch dir removed
    assert not any(n.startswith("_context") for n in p.artifact)


def test_agent_editor_keep_dirs_no_change_and_errors():
    p = AgentEditor(FakeAgentCLI(edits={}, proposal={"change": "nothing"}), keep_dirs=True).edit(BASE, "noop")
    assert p.error == "no effective change" and p.change == "nothing"
    assert Path(p.meta["workdir"]).exists()
    shutil.rmtree(p.meta["workdir"])
    bad = AgentEditor(FakeAgentCLI(edits={"notes.md": "x\n"}, error="timeout")).edit(BASE, "go")
    assert not bad.ok and bad.error == "agent error: timeout" and bad.artifact["notes.md"] == "x\n"


@pytest.mark.parametrize("raw", ["not json", "[1, 2]", '"just a string"'])
def test_agent_editor_tolerates_bad_proposal_files(raw):
    p = AgentEditor(FakeAgentCLI(edits={"notes.md": "n\n"}, raw_proposal=raw)).edit(BASE, "go")
    assert p.ok and p.change == "" and p.components == []


def _diff(new_text: str, old_text: str = "base line\n", name: str = "harness.py") -> str:
    return Artifact({name: old_text}).diff(Artifact({name: new_text}))


@pytest.mark.parametrize("added, hit", [
    ("x = 12\n", True), ("return 12.\n", True), ("(12)\n", True), ("value=12,\n", True),
    ("x = 120\n", False), ("x = 3.12\n", False), ("x = 12.5\n", False), ("v12 = 1\n", False), ("x = 112\n", False),
])
def test_denylist_whole_token_numbers(added, hit):
    assert bool(LeakageCritic(terms=["12"], min_term_len=2).denylist_hits(_diff(added))) is hit


def test_denylist_terms_case_and_length():
    c = LeakageCritic(terms=["Alice", "evolve-numeric-003", "ab", "2024-01-05"])
    assert "ab" not in c.terms                               # below min_term_len=4
    assert c.denylist_hits(_diff("if name == 'ALICE':\n")) == ["Alice"]
    assert c.denylist_hits(_diff("# evolve-numeric-003 special case\n")) == ["evolve-numeric-003"]
    assert c.denylist_hits(_diff("# evolve-numeric-0031\n")) == []
    assert c.denylist_hits(_diff("d = '2024-01-05'\n")) == ["2024-01-05"]
    assert c.denylist_hits(_diff("Malice aforethought\n")) == []
    cs = LeakageCritic(terms=["Alice"], case_sensitive=True)
    assert cs.denylist_hits(_diff("ALICE\n")) == [] and cs.denylist_hits(_diff("Alice\n")) == ["Alice"]


def test_denylist_accepts_non_string_terms():
    c = LeakageCritic(terms=[123456, "Bob", 7], min_term_len=3)
    assert c.terms == ["123456", "Bob"] and c.denylist_hits(_diff("return 123456\n")) == ["123456"]


def test_denylist_only_scans_added_lines():
    c, old = LeakageCritic(terms=["SECRET"]), "keep SECRET here\nctx\n"
    assert c.denylist_hits(Artifact({"f": old}).diff(Artifact({"f": "ctx\n"}))) == []          # removal
    assert c.denylist_hits(Artifact({"f": old}).diff(Artifact({"f": old + "new line\n"}))) == []  # context
    assert c.denylist_hits(_diff("uses SECRET\n")) == ["SECRET"]


def test_denylist_catches_last_line_without_newline():
    d = Artifact({"f.py": "x = 1"}).diff(Artifact({"f.py": "x = 'SECRET_ANSWER'"}))
    v = LeakageCritic(terms=["SECRET_ANSWER"]).screen(d)
    assert not v.accept and v.stage == "denylist" and v.hits == ["SECRET_ANSWER"]


def test_added_lines_hunk_aware_and_headerless():
    assert added_lines(Artifact({"f": "a\n"}).diff(Artifact({"f": "a\n+++ not a header\n"}))) == "+++ not a header"
    assert added_lines("+ANSWER = 42\n context\n-removed") == "ANSWER = 42"   # hand-written diff
    assert added_lines("") == "" and added_lines(None) == ""


def test_patterns_and_screen_counters():
    pat = r"if .*== ['\"]t\d+['\"]"
    c = LeakageCritic(patterns=[pat])
    v = c.screen(_diff("if task_id == 't17': return 5\n"))
    assert not v.accept and v.hits == [f"/{pat}/"]
    assert c.screen(_diff("generic improvement\n")).accept
    assert (c.n_screened, c.n_rejected) == (2, 1)
    v = LeakageCritic().screen(_diff("anything\n"))
    assert v.accept and v.stage == "none"


def _llm(*replies):
    return MockLLM(lambda p, s, seed, i: replies[min(i, len(replies) - 1)])


def test_llm_review_accept_and_reject():
    llm = _llm('{"verdict": "accept", "objections": []}')
    v = LeakageCritic(llm=llm, domain_brief="QA harness").screen(_diff("Always verify your answer.\n"),
                                                                  change="add verification")
    assert v.accept and v.stage == "llm"
    call = llm.calls[0]
    assert call["system"] == CRITIC_SYSTEM and "QA harness" in call["prompt"] and "add verification" in call["prompt"]
    assert "+Always verify your answer." in call["prompt"]
    assert llm.meter.snapshot()["critic"]["calls"] == 1
    rej = LeakageCritic(llm=_llm('```json\n{"verdict": "REJECT", "objections": "lookup table"}\n```'))
    v = rej.screen(_diff("ANSWERS = {...}\n"))
    assert not v.accept and v.objections == ["lookup table"] and rej.n_rejected == 1


def test_llm_review_fails_closed():
    llm = _llm("I think it is fine", "still no json", "nope")
    v = LeakageCritic(llm=llm, parse_attempts=3).screen(_diff("x\n"))
    assert not v.accept and v.objections == ["critic output unparseable"] and len(llm.calls) == 3
    assert [call["seed"] for call in llm.calls] == [0, 1, 2]
    flaky = _llm("garbage", '{"verdict": "accept"}')
    assert LeakageCritic(llm=flaky).screen(_diff("x\n")).accept and len(flaky.calls) == 2
    assert not LeakageCritic(llm=_llm('["accept"]'), parse_attempts=2).screen(_diff("x\n")).accept   # not an object
    assert not LeakageCritic(llm=_Down()).screen(_diff("x\n")).accept


def test_denylist_short_circuits_llm():
    llm = _llm('{"verdict": "accept"}')
    v = LeakageCritic(terms=["Riley"], llm=llm).screen(_diff("if 'Riley' in q:\n"))
    assert not v.accept and v.stage == "denylist" and llm.calls == []
