"""EvoMap low-severity review fix (L11): ``rsi-taskcheck --n 0`` (or negative) checks at least one task."""

import pytest
from evomap_helpers import json_writer

from rsi.domains.agentqa import AgentQADomain, SimModel
from rsi.evomap import AppendInjector, Config, read_library, run
from rsi.evomap.agent import AgentNode

GENE = {
    "id": "gene_reason_verify",
    "category": "optimize",
    "signals_match": ["task:numeric"],
    "summary": "Reason step by step and verify before answering.",
    "strategy": [
        "Think step by step before answering.",
        "Verify each intermediate result (double-check).",
        "End with a line 'ANSWER: <value>'.",
    ],
    "avoid": ["Answering without working through the steps."],
}


@pytest.mark.parametrize("n", ["0", "-3"])
def test_taskcheck_with_non_positive_n_is_clamped_to_one_task(n, monkeypatch):
    """L11: an empty scope used to reach ``with_g[0]`` and raise IndexError out of solidify/run."""
    calls = []
    real = AgentNode._taskcheck

    def spy(self, argv, files, ctx):
        out = real(self, argv, files, ctx)
        calls.append(out)
        return out

    monkeypatch.setattr(AgentNode, "_taskcheck", spy)
    dom = AgentQADomain()
    res = run(
        dom,
        dom.seed_artifact(),
        llm_task=SimModel(dom.tasks),
        llm_propose=json_writer({**GENE, "validation": [f"rsi-taskcheck --n {n}"]}),
        injector=AppendInjector("prompts/system.md"),
        config=Config(cycles=4, mode="safe", seed=0, taskcheck_k=2),
    )
    assert res.meta["audit"]["ok"] and isinstance(read_library(res.best), list)
    ran = [msg for _, msg in calls if msg.startswith("taskcheck")]
    assert ran and all(msg.startswith("taskcheck n=1 ") for msg in ran), ran  # one task, never all-but-|n|
