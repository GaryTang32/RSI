"""Pi 0.85.1 compaction requests: standalone, uncached, and leaving the conversation cache intact."""
import pytest

from rsi.domains.agentworld.data_envs import LogTriageEnv
from rsi.solpi import Message, ToolCall
from rsi.solpi.meter import PRICES
from rsi.solpi.pi_compaction import (SUMMARIZATION_PROMPT, SUMMARIZATION_SYSTEM_PROMPT, UPDATE_SUMMARIZATION_PROMPT,
                                     serialize_conversation, summarization_prompt, summarization_request_tokens,
                                     truncate_for_summary)
from rsi.solpi.runtime import MESSAGE_OVERHEAD_TOKENS
from solpi_testkit import Env, bash_steps, runtime

# Pi 0.85.1 serializeConversation() (node 22) of the conversation in test_serialize_conversation_matches_pi_vector.
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
    assert serialize_conversation(msgs) == (PI_VECTOR, 0)
    assert truncate_for_summary("ab" * 1000) == "ab" * 1000            # exactly 2,000 chars: untouched


def test_summarization_prompt_uses_previous_summary_and_custom_instructions():
    first, _ = summarization_prompt([Message("user", "task")], "")
    assert first == "<conversation>\n[User]: task\n</conversation>\n\n" + SUMMARIZATION_PROMPT
    prev = Message("user", "[compacted context summary]\nOLD", details={"compaction": True})
    upd, _ = summarization_prompt([prev, Message("user", "more")], "keep the plan")
    assert upd == ("<conversation>\n[User]: more\n</conversation>\n\n<previous-summary>\nOLD\n</previous-summary>"
                   "\n\n" + UPDATE_SUMMARIZATION_PROMPT + "\n\nAdditional focus: keep the plan")


@pytest.mark.parametrize("prices", ["sim-a", "sim-b"])
def test_compaction_request_is_standalone_uncached_and_keeps_the_conversation_cache(prices):
    p = PRICES[prices]
    commands = [f"c{i}" for i in range(1, 7)]
    rt = runtime(Env(outputs={c: ("q" * 30000, False) for c in commands}), bash_steps(*commands), prices=prices,
                 system_prompt="sys " * 200, pad_tokens=50, keep_recent_tokens=2_000, auto_compact=False)
    meter = rt.meter
    rt.run("task")
    archived = rt.history[:rt.cut_index()]
    n_before = len(meter.requests)
    rt.compact("focus")
    comp = meter.requests[n_before]
    assert comp.role == "compaction"
    sys_tok, user_tok = summarization_request_tokens(archived, "focus", MESSAGE_OVERHEAD_TOKENS)
    assert comp.cache_read == 0 and comp.input == comp.cache_write == sys_tok + user_tok
    assert comp.output > 0
    assert comp.cost == pytest.approx((comp.input * p.uncached_price + comp.output * p.output) / 1e6)
    assert comp.input < 0.25 * sum(m.tokens() for m in archived)        # tool results are cut to 2,000 chars
    assert sys_tok == -(-len(SUMMARIZATION_SYSTEM_PROMPT) // 4) + MESSAGE_OVERHEAD_TOKENS
    rt.history.append(Message("user", "continue"))                      # the next request re-reads the cache
    msgs = rt.project()
    u = meter.request([m.key() for m in msgs], [m.tokens() for m in msgs], role="main")
    assert u.cache_read == msgs[0].tokens()


def test_uncached_price_follows_the_provider_kind():
    assert PRICES["sim-a"].uncached_price == PRICES["sim-a"].write          # OpenAI-like: input price
    assert PRICES["sim-b"].uncached_price == pytest.approx(PRICES["sim-b"].write / 1.25)   # Anthropic-like
    assert PRICES["haiku"].uncached_price == pytest.approx(PRICES["haiku"].write / 1.25)


def test_logtriage_env_clamps_to_its_item_pool():
    """Long tasks clamp to the 15-item pool; shorter tasks keep their RNG sequence."""
    for seed in range(6):
        assert len(LogTriageEnv(f"t{seed}", seed=seed, n_subtasks=20).subtasks) == LogTriageEnv.MAX_ITEMS
    assert len(LogTriageEnv("t", seed=3, n_subtasks=4).subtasks) == 4
