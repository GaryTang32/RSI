"""CostMeter: discovery-agent calls (the paper's cost unit) kept apart from replay CPU.

"Discovery cost is quantified by the total cumulative number of discovery-agent
calls" [paper:§4 p.7]; replay is "zero-execution-cost" [paper:Fig.2] but still
costs policy CPU time, and dreaming costs developer-LLM calls - the paper does not
quantify either, the meter does.
"""
from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field
from typing import Optional

from ..core.llm import LLM, Usage


@dataclass
class CostMeter:
    agent_calls: int = 0
    agent_tokens: int = 0
    agent_usd: float = 0.0
    eval_s: float = 0.0
    online_rounds: int = 0
    online_wall_s: float = 0.0
    replay_episodes: int = 0
    replay_cpu_s: float = 0.0
    replay_wall_s: float = 0.0
    developer_calls: int = 0
    developer_tokens: int = 0
    developer_usd: float = 0.0
    per_iteration: list = field(default_factory=list)

    def __post_init__(self) -> None:
        self._lock = threading.Lock()

    def add_agent(self, usage: Optional[Usage]) -> None:
        with self._lock:
            self.agent_calls += 1
            if usage is not None:
                self.agent_tokens += int(usage.total_tokens)
                self.agent_usd += float(usage.cost_usd)

    def add_eval(self, seconds: float) -> None:
        with self._lock:
            self.eval_s += float(seconds or 0.0)

    def add_replay(self, episodes: int, cpu_s: float, wall_s: float) -> None:
        with self._lock:
            self.replay_episodes += int(episodes)
            self.replay_cpu_s += float(cpu_s)
            self.replay_wall_s += float(wall_s)

    def add_developer(self, usage: Optional[Usage]) -> None:
        with self._lock:
            if usage is None:
                return
            self.developer_calls += int(usage.calls)
            self.developer_tokens += int(usage.total_tokens)
            self.developer_usd += float(usage.cost_usd)

    def close_iteration(self, t: int, **extra) -> dict:
        row = {"iteration": t, "agent_calls": self.agent_calls, "replay_episodes": self.replay_episodes,
               "replay_cpu_s": round(self.replay_cpu_s, 4), "developer_calls": self.developer_calls, **extra}
        self.per_iteration.append(row)
        return row

    def snapshot(self) -> dict:
        d = {k: v for k, v in asdict(self).items() if k != "per_iteration"}
        d["replay_ms_per_episode"] = 1000.0 * self.replay_cpu_s / max(1, self.replay_episodes)
        return d


def llm_usage(*llms: Optional[LLM]) -> dict:
    out: dict = {}
    for llm in llms:
        if llm is None:
            continue
        for role, u in llm.meter.snapshot().items():
            if role == "_total" or role.startswith("shadow:"):   # the audit monitor's spend is not the loop's
                continue
            cur = out.get(role)
            out[role] = u if cur is None else {k: cur.get(k, 0) + u.get(k, 0) for k in u}
    return out
