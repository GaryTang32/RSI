"""Recording domain and improver shared by the Harness² tests."""
from __future__ import annotations

from rsi.core.llm import LLMResponse, Usage
from rsi.domains.agentqa import AgentQADomain
from rsi.harness2 import MockImprover


class RecordingDomain(AgentQADomain):
    """AgentQA that logs every rollout (the task object it was given) and every grade, in one event sequence."""

    def __init__(self, suite) -> None:
        super().__init__(suite)
        self.events: list[tuple] = []

    def rollout(self, artifact, task, *, seed=0, llm=None):
        self.events.append(("rollout", task.id, task.target, dict(task.meta), seed))
        return super().rollout(artifact, task, seed=seed, llm=llm)

    def grade(self, task, execution):
        self.events.append(("grade", task.id))
        return super().grade(task, execution)


class RecordingImprover(MockImprover):
    """The offline improver, plus a log of (role, prompt) and an optional fixed cost per call."""

    def __init__(self, cost: float = 0.0, **kw) -> None:
        super().__init__(**kw)
        self.cost = cost
        self.prompts: list[tuple[str, str]] = []

    def _complete(self, prompt, *, system, max_tokens, seed) -> LLMResponse:
        resp = super()._complete(prompt, system=system, max_tokens=max_tokens, seed=seed)
        self.prompts.append((self.roles[-1], prompt))
        resp.usage = Usage(1, resp.usage.input_tokens, resp.usage.output_tokens, self.cost, 0.0)
        return resp
