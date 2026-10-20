"""GEPA strategies (``gepa/strategies/*``), all drawing from the one shared seeded RNG."""
from __future__ import annotations

import random
from collections import Counter
from typing import Sequence

from .frontier import select_from_pareto_front


def idxmax(values: Sequence[float]) -> int:
    return list(values).index(max(values))


class _Stateless:
    def get_state(self) -> dict:
        return {}

    def set_state(self, d: dict) -> None:
        pass


class ParetoSelector(_Stateless):
    """Algorithm 2: set-cover pruning, then frequency-weighted sampling."""

    name = "pareto"

    def __init__(self, rng: random.Random) -> None:
        self.rng = rng

    def select(self, state) -> int:
        return select_from_pareto_front(state.frontier.mapping(), state.agg_scores(), self.rng)


class CurrentBestSelector(_Stateless):
    name = "current_best"

    def __init__(self, rng: random.Random | None = None) -> None:
        self.rng = rng

    def select(self, state) -> int:
        return idxmax(state.agg_scores())


class EpsilonGreedySelector(_Stateless):
    name = "epsilon_greedy"

    def __init__(self, rng: random.Random, epsilon: float = 0.1) -> None:
        assert 0.0 <= epsilon <= 1.0
        self.rng, self.epsilon = rng, epsilon

    def select(self, state) -> int:
        if self.rng.random() < self.epsilon:
            return self.rng.randint(0, len(state.candidates) - 1)
        return idxmax(state.agg_scores())


class TopKParetoSelector(_Stateless):
    """The Pareto rule restricted to the top k candidates by aggregate score."""

    name = "top_k_pareto"

    def __init__(self, rng: random.Random, k: int = 5) -> None:
        assert k > 0
        self.rng, self.k = rng, k

    def select(self, state) -> int:
        scores = state.agg_scores()
        top = set(sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[: self.k])
        filtered = {key: progs & top for key, progs in state.frontier.mapping().items() if progs & top}
        return select_from_pareto_front(filtered, scores, self.rng) if filtered else idxmax(scores)


class BeamSearchSelector:
    """BeamSearch(N) of the paper's Table 3: expand the current top-N candidates in turn."""

    name = "beam_search"

    def __init__(self, rng: random.Random | None = None, n: int = 4) -> None:
        self.n = n
        self.t = 0

    def select(self, state) -> int:
        scores = state.agg_scores()
        beam = sorted(range(len(scores)), key=lambda i: (-scores[i], i))[: self.n]
        idx = beam[self.t % len(beam)]
        self.t += 1
        return idx

    def get_state(self) -> dict:
        return {"t": self.t}

    def set_state(self, d: dict) -> None:
        self.t = int(d.get("t", 0))


SELECTORS = {"pareto": ParetoSelector, "current_best": CurrentBestSelector, "epsilon_greedy": EpsilonGreedySelector,
             "top_k_pareto": TopKParetoSelector, "beam_search": BeamSearchSelector}


def make_selector(name: str, rng: random.Random, *, epsilon: float = 0.1, top_k: int = 5, beam_n: int = 4):
    if name not in SELECTORS:
        raise ValueError(f"unknown candidate selector {name!r}; choose from {sorted(SELECTORS)}")
    args = {"epsilon_greedy": (epsilon,), "top_k_pareto": (top_k,), "beam_search": (beam_n,)}.get(name, ())
    return SELECTORS[name](rng, *args)


class RoundRobinComponents:
    """One component per iteration; children inherit the parent's already-advanced pointer."""

    name = "round_robin"

    def __call__(self, state, idx: int) -> list[str]:
        pid = state.rr[idx]
        state.rr[idx] = (pid + 1) % len(state.components)
        return [state.components[pid]]


class AllComponents:
    name = "all"

    def __call__(self, state, idx: int) -> list[str]:
        return list(state.components)


def make_component_selector(name: str):
    if name == "round_robin":
        return RoundRobinComponents()
    if name == "all":
        return AllComponents()
    raise ValueError(f"unknown module selector {name!r}")


class EpochShuffledBatchSampler:
    """Port of ``gepa/strategies/batch_sampler.py``: shuffle each epoch, pad with the least-frequent ids."""

    def __init__(self, minibatch_size: int, rng: random.Random) -> None:
        self.minibatch_size = minibatch_size
        self.rng = rng
        self.shuffled_ids: list[str] = []
        self.epoch = -1
        self.id_freqs: Counter = Counter()
        self.last_trainset_size = 0

    def _update_shuffled(self, ids: Sequence[str]) -> None:
        self.last_trainset_size = len(ids)
        self.shuffled_ids = list(ids)
        self.rng.shuffle(self.shuffled_ids)
        self.id_freqs = Counter(self.shuffled_ids)
        for _ in range(-len(ids) % self.minibatch_size):
            selected = self.id_freqs.most_common()[::-1][0][0]
            self.shuffled_ids.append(selected)
            self.id_freqs[selected] += 1

    def next_ids(self, ids: Sequence[str], iteration: int) -> list[str]:
        if not ids:
            raise ValueError("Cannot sample a minibatch from an empty train set.")
        base = iteration * self.minibatch_size
        curr_epoch = 0 if self.epoch == -1 else base // max(len(self.shuffled_ids), 1)
        if not self.shuffled_ids or len(ids) != self.last_trainset_size or curr_epoch > self.epoch:
            self.epoch = curr_epoch
            self._update_shuffled(ids)
        start = base % len(self.shuffled_ids)
        return self.shuffled_ids[start:start + self.minibatch_size]

    def get_state(self) -> dict:
        return {"shuffled_ids": list(self.shuffled_ids), "epoch": self.epoch,
                "id_freqs": [[k, v] for k, v in self.id_freqs.items()], "last_trainset_size": self.last_trainset_size}

    def set_state(self, d: dict) -> None:
        self.shuffled_ids = list(d["shuffled_ids"])
        self.epoch = int(d["epoch"])
        self.id_freqs = Counter({k: v for k, v in d["id_freqs"]})
        self.last_trainset_size = int(d["last_trainset_size"])


class StrictImprovement:
    name = "strict_improvement"

    def accept(self, before: Sequence[float], after: Sequence[float]) -> bool:
        return sum(after) > sum(before)


class ImprovementOrEqual:
    name = "improvement_or_equal"

    def accept(self, before: Sequence[float], after: Sequence[float]) -> bool:
        return sum(after) >= sum(before)


class NoiseMargin:
    """Accept iff mean(after) > mean(before) + delta (an RRSI-style noise floor; extension)."""

    name = "noise_margin"

    def __init__(self, delta: float) -> None:
        self.delta = delta

    def accept(self, before: Sequence[float], after: Sequence[float]) -> bool:
        return sum(after) / max(len(after), 1) > sum(before) / max(len(before), 1) + self.delta


def make_acceptance(name: str, delta: float = 0.0):
    if name == "strict_improvement":
        return StrictImprovement()
    if name == "improvement_or_equal":
        return ImprovementOrEqual()
    if name == "noise_margin":
        return NoiseMargin(delta)
    raise ValueError(f"unknown acceptance {name!r}")
