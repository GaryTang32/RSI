"""SoL-Pi retry round 2 regression tests (claims-audit-solpi.md, "Retry round 2").

R2-F1: Pi 0.85.1's compaction summarisation call is a standalone request (serialised conversation, tool
results cut to 2,000 chars, own system prompt, ``cacheRetention: "none"``). It used to be billed as a
continuation of the conversation's cached prefix (mostly cache reads) and it reset the conversation's cache.
"""
from __future__ import annotations

import pytest

from rsi.solpi import AgentRuntime, Message, TokenMeter, ToolCall, builtin_tools
from rsi.solpi.meter import PRICES
from rsi.solpi.pi_compaction import (SUMMARIZATION_PROMPT, SUMMARIZATION_SYSTEM_PROMPT, UPDATE_SUMMARIZATION_PROMPT,
                                     serialize_conversation, summarization_prompt, summarization_request_tokens,
                                     truncate_for_summary)
from rsi.solpi.runtime import MESSAGE_OVERHEAD_TOKENS, ToolResult

# serializeConversation() output of Pi 0.85.1 (dist/core/compaction/utils.js + pi-ai contentText) for the
# conversation built in test_serialize_conversation_matches_pi_vector, produced with node 22.
PI_VECTOR = ('[User]: Fix the tests.\n\n[Assistant]: NOTE: x\n\n[Assistant tool calls]: bash(command="pytest -q", '
             'timeout=30)\n\n[Tool result]: ' + "L" * 2000 + '\n\n[... 501 more characters truncated]\n\n'
             '[Assistant tool calls]: edit(path="a.py", old="x = 1", new="x = \\"2\\" é")\n\n[Tool result]: ok')


def test_serialize_conversation_matches_pi_vector():
    msgs = [Message("user", "Fix the tests."),
            Message("assistant", "NOTE: x", tool_calls=(ToolCall("c1", "bash", {"command": "pytest -q",
                                                                                 "timeout": 30}),)),
            Message("tool", "L" * 2500 + "é", tool_call_id="c1", tool_name="bash", is_error=True),
            Message("assistant", "", tool_calls=(ToolCall("c2", "edit", {"path": "a.py", "old": "x = 1",
                                                                          "new": 'x = "2" é'}),)),
            Message("tool", "ok", tool_call_id="c2", tool_name="edit")]
    text, thinking = serialize_conversation(msgs)
    assert text == PI_VECTOR
    assert thinking == 0
    assert truncate_for_summary("ab" * 1000) == "ab" * 1000            # exactly 2,000 chars: untouched


def test_summarization_prompt_uses_previous_summary_and_custom_instructions():
    first, _ = summarization_prompt([Message("user", "task")], "")
    assert first == "<conversation>\n[User]: task\n</conversation>\n\n" + SUMMARIZATION_PROMPT
    prev = Message("user", "[compacted context summary]\nOLD", details={"compaction": True})
    upd, _ = summarization_prompt([prev, Message("user", "more")], "keep the plan")
    assert upd == ("<conversation>\n[User]: more\n</conversation>\n\n<previous-summary>\nOLD\n</previous-summary>"
                   "\n\n" + UPDATE_SUMMARIZATION_PROMPT + "\n\nAdditional focus: keep the plan")


class _Env:
    def __init__(self, big: str):
        self.big = big

    def tool_read(self, p, rt=None):
        return ToolResult("r")

    def tool_write(self, p, c, rt=None):
        return ToolResult("w")

    def tool_edit(self, p, o, n, rt=None):
        return ToolResult("e")

    def tool_bash(self, cmd, rt=None):
        return ToolResult(self.big)


class _Script:
    name = "script"

    def __init__(self, n):
        self.n, self.i = n, 0

    def act(self, msgs, tools, rt):
        self.i += 1
        if self.i > self.n:
            return Message("assistant", "done")
        return Message("assistant", f"step {self.i}", tool_calls=(ToolCall(rt.next_call_id(), "bash",
                                                                            {"command": f"c{self.i}"}),),
                       pad_tokens=50)


@pytest.mark.parametrize("prices", ["sim-a", "sim-b"])
def test_compaction_request_is_standalone_uncached_and_keeps_the_conversation_cache(prices):
    p = PRICES[prices]
    meter = TokenMeter({"main": p, "compaction": p, "reducer": PRICES["reducer"]})
    rt = AgentRuntime(_Script(6), system_prompt="sys " * 200, meter=meter, env=_Env("q" * 30000),
                      keep_recent_tokens=2_000, auto_compact=False)
    for s in builtin_tools(rt.env):
        rt.register_tool(s)
    rt.run("task")
    archived = rt.history[:rt.cut_index()]
    n_before = len(meter.requests)
    rt.compact("focus")
    comp = meter.requests[n_before]
    assert comp.role == "compaction"
    sys_tok, user_tok = summarization_request_tokens(archived, "focus", MESSAGE_OVERHEAD_TOKENS)
    assert comp.cache_read == 0 and comp.input == comp.cache_write == sys_tok + user_tok
    assert comp.output > 0 and comp.cost == pytest.approx((comp.input * p.uncached_price + comp.output * p.output) / 1e6)
    # tool results are cut to 2,000 chars: far smaller than the archived conversation it replaces
    assert comp.input < 0.25 * sum(m.tokens() for m in archived)
    assert sys_tok == -(-len(SUMMARIZATION_SYSTEM_PROMPT) // 4) + MESSAGE_OVERHEAD_TOKENS
    # the conversation's cache is untouched: the next request re-reads the (unchanged) system prompt
    rt.history.append(Message("user", "continue"))
    msgs = rt.project()
    u = meter.request([m.key() for m in msgs], [m.tokens() for m in msgs], role="main")
    assert u.cache_read == msgs[0].tokens()


def test_uncached_price_follows_the_provider_kind():
    assert PRICES["sim-a"].uncached_price == PRICES["sim-a"].write          # OpenAI-like: input price
    assert PRICES["sim-b"].uncached_price == pytest.approx(PRICES["sim-b"].write / 1.25)   # Anthropic-like
    assert PRICES["haiku"].uncached_price == pytest.approx(PRICES["haiku"].write / 1.25)


def test_logtriage_env_clamps_to_its_item_pool():
    """Retry round 2 (P-Q13b): LogTriageEnv sampled >= 16 items from a 15-item pool and raised ValueError, so every
    long-bin logtriage task scored 0 with 0 requests in every arm. It is now clamped to its 15-item maximum;
    shorter tasks are unchanged (same RNG sequence)."""
    from rsi.domains.agentworld.data_envs import LogTriageEnv
    for seed in range(6):
        env = LogTriageEnv(f"t{seed}", seed=seed, n_subtasks=20)
        assert len(env.subtasks) == LogTriageEnv.MAX_ITEMS
    assert len(LogTriageEnv("t", seed=3, n_subtasks=4).subtasks) == 4
