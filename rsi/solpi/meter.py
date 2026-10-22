"""Token traffic and API cost with a simulated prefix cache: ``total_tokens = cache_read + cache_write + output``."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Iterable, Optional, Sequence


@dataclass(frozen=True)
class PriceTable:
    """USD per million tokens."""

    name: str
    read: float
    write: float
    output: float
    #: input sent with ``cacheRetention: "none"``; ``None`` = the ``write`` price
    uncached: Optional[float] = None

    @property
    def ratio(self) -> float:
        return self.write / self.read if self.read > 0 else float("inf")

    @property
    def uncached_price(self) -> float:
        return self.write if self.uncached is None else self.uncached


#: simulated backends A ("Sol"-like) and B ("Opus"-like), both rho = 12.5, plus a cheap reducer
PRICES = {
    "sim-a": PriceTable("sim-a", read=0.20, write=2.50, output=15.0),
    "sim-b": PriceTable("sim-b", read=0.50, write=6.25, output=25.0, uncached=5.0),
    "reducer": PriceTable("reducer", read=0.02, write=0.25, output=2.0),
    "haiku": PriceTable("haiku", read=0.10, write=1.25, output=5.0, uncached=1.0),
}


def estimate_tokens(text: str) -> int:
    return int(math.ceil(len(text or "") / 4))


@dataclass
class RequestUsage:
    role: str
    input: int = 0
    cache_read: int = 0
    cache_write: int = 0
    output: int = 0
    cost: float = 0.0
    n_messages: int = 0

    @property
    def total(self) -> int:
        return self.input + self.output


@dataclass
class RoleTotals:
    requests: int = 0
    input: int = 0
    cache_read: int = 0
    cache_write: int = 0
    output: int = 0
    cost: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.input + self.output

    def to_dict(self) -> dict:
        return {**asdict(self), "total_tokens": self.total_tokens}


class TokenMeter:
    """Prefix-cache simulator and cost accounting per role (main agent, reducer, compaction ...)."""

    def __init__(self, prices: dict[str, PriceTable]) -> None:
        self.prices = dict(prices)
        self._last: dict[str, list[str]] = {}
        self.by_role: dict[str, RoleTotals] = {}
        self.requests: list[RequestUsage] = []

    def price(self, role: str) -> PriceTable:
        return self.prices.get(role) or self.prices["main"]

    def _bill(self, u: RequestUsage) -> RequestUsage:
        self.requests.append(u)
        t = self.by_role.setdefault(u.role, RoleTotals())
        t.requests += 1
        t.input += u.input
        t.cache_read += u.cache_read
        t.cache_write += u.cache_write
        t.cost += u.cost
        return u

    def request(self, keys: Sequence[str], tokens: Sequence[int], *, role: str = "main",
                scope: Optional[str] = None) -> RequestUsage:
        """Bill one request: the prefix of ``keys`` shared with the scope's last request is a cache read."""
        scope = scope or role
        n = 0
        for a, b in zip(self._last.get(scope, []), keys):
            if a != b:
                break
            n += 1
        read, write = int(sum(tokens[:n])), int(sum(tokens[n:]))
        p = self.price(role)
        self._last[scope] = list(keys)
        return self._bill(RequestUsage(role, read + write, read, write, 0, (read * p.read + write * p.write) / 1e6,
                                       len(keys)))

    def add_output(self, n: int, *, role: str = "main") -> None:
        c = n * self.price(role).output / 1e6
        t = self.by_role.setdefault(role, RoleTotals())
        t.output += n
        t.cost += c
        if self.requests and self.requests[-1].role == role:
            self.requests[-1].output += n
            self.requests[-1].cost += c

    def simple(self, input_tokens: int, output_tokens: int, *, role: str) -> RequestUsage:
        """A standalone request with no reusable prefix (e.g. a reducer call)."""
        u = self.request([f"{role}-standalone-{len(self.requests)}"], [input_tokens], role=role,
                         scope=f"{role}-standalone")
        self.add_output(output_tokens, role=role)
        return u

    def uncached(self, tokens: Sequence[int], *, role: str) -> RequestUsage:
        """A ``cacheRetention: "none"`` request: all input billed at the uncached price; no scope is touched."""
        n = int(sum(tokens))
        return self._bill(RequestUsage(role, n, 0, n, 0, n * self.price(role).uncached_price / 1e6, len(tokens)))

    def reset_cache(self, scope: str = "main") -> None:
        self._last.pop(scope, None)

    def total(self, roles: Optional[Iterable[str]] = None) -> RoleTotals:
        out = RoleTotals()
        for r, t in self.by_role.items():
            if roles is None or r in roles:
                out.requests += t.requests
                out.input += t.input
                out.cache_read += t.cache_read
                out.cache_write += t.cache_write
                out.output += t.output
                out.cost += t.cost
        return out

    def snapshot(self) -> dict:
        return {**{r: t.to_dict() for r, t in self.by_role.items()}, "_total": self.total().to_dict()}


@dataclass
class CostModel:
    """Token efficiency eta = cost / aggregate score (SoL-Pi)."""

    @staticmethod
    def efficiency(cost: float, score: float) -> float:
        return cost / score if score > 0 else float("inf")

    @staticmethod
    def summarize(costs: Sequence[float], scores: Sequence[float], tokens: Sequence[float]) -> dict:
        c, s, t = float(sum(costs)), float(sum(scores)), float(sum(tokens))
        return {"cost": c, "score": s, "tokens": t, "eta": CostModel.efficiency(c, s),
                "tokens_per_score": t / s if s > 0 else float("inf")}
