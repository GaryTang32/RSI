"""``proposer_kind="auto"`` sees through ``CachedLLM``: a cached claude CLI still gets the coding-agent proposer."""
import pytest

from rsi.core.llm import CachedLLM, ClaudeCLI, MockLLM
from rsi.domains.memoclassify import make_domain
from rsi.metaharness import AgentProposer, Config, MockProposer, RewriteProposer, make_proposer


def test_auto_picks_the_coding_agent_for_a_cached_claude_cli(tmp_path):
    cli = ClaudeCLI("haiku", executable="claude-not-run", retries=1)
    with pytest.warns(UserWarning, match="bypass the LLM cache"):
        prop = make_proposer(make_domain(), CachedLLM(CachedLLM(cli, tmp_path / "a"), tmp_path / "b"), Config())
    assert isinstance(prop, AgentProposer) and prop.cli is cli


def test_auto_keeps_rewrite_and_mock_proposers_for_other_backends(tmp_path):
    dom, cfg = make_domain(), Config()
    assert isinstance(make_proposer(dom, CachedLLM(MockLLM(), tmp_path), cfg), MockProposer)
    class Api(MockLLM):   # stands in for any non-CLI backend (e.g. AnthropicAPI)
        pass
    assert isinstance(make_proposer(dom, CachedLLM(Api(), tmp_path / "s"), cfg), RewriteProposer)
    with pytest.raises(ValueError, match="ClaudeCLI"):
        make_proposer(dom, CachedLLM(MockLLM(), tmp_path / "m"), cfg, kind="agent")
