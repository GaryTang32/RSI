"""Token traffic and API-cost accounting with a simulated prefix cache (spec B9.1).

Every provider request is a list of messages. The provider caches prompt prefixes:
the longest run of leading messages identical to the previous request in the same
cache *scope* is billed as ``cache_read``; everything after it is new input, billed
as ``cache_write`` (the uncached input price). Output tokens are billed separately.
``cacheWriteReadRatio`` (rho, 12.5 in SoL-Pi) is ``write / read``.

"Token traffic includes cache traffic": ``total_tokens = cache_read + cache_write + output``.
Token efficiency (SoL-Pi) is ``eta = API cost / aggregate task score``.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Iterable, Optional, Sequence


@dataclass(frozen=True)
class PriceTable:
    """USD per million tokens."""

    name: str
    read: float
    write: float
    output: float

    @property
    def ratio(self) -> float:
        return self.write / self.read if self.read > 0 else float("inf")


#: simulated backends. A ~ "GPT-5.6 Sol"-like, B ~ "Opus-5"-like (both rho = 12.5), reducer ~ "Luna"-like.
PRICES = {
    "sim-a": PriceTable("sim-a", read=0.20, write=2.50, output=15.0),
    "sim-b": PriceTable("sim-b", read=0.50, write=6.25, output=25.0),
    "reducer": PriceTable("reducer", read=0.02, write=0.25, output=2.0),
    "haiku": PriceTable("haiku", read=0.10, write=1.25, output=5.0),
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
        d = asdict(self)
        d["total_tokens"] = self.total_tokens
        return d


class TokenMeter:
    """Prefix-cache simulator + cost accounting per role (main agent, reducer, compaction ...)."""

    def __init__(self, prices: dict[str, PriceTable]) -> None:
        self.prices = dict(prices)
        self._last: dict[str, list[str]] = {}
        self.by_role: dict[str, RoleTotals] = {}
        self.requests: list[RequestUsage] = []
        self._pending: Optional[RequestUsage] = None

    def price(self, role: str) -> PriceTable:
        return self.prices.get(role) or self.prices["main"]

    def request(self, keys: Sequence[str], tokens: Sequence[int], *, role: str = "main",
                scope: Optional[str] = None) -> RequestUsage:
        """Bill one provider request whose prompt is the message sequence ``keys`` (content
        hashes) with per-message token counts ``tokens``."""
        scope = scope or role
        prev = self._last.get(scope, [])
        n = 0
        for a, b in zip(prev, keys):
            if a != b:
                break
            n += 1
        read = int(sum(tokens[:n]))
        write = int(sum(tokens[n:]))
        p = self.price(role)
        u = RequestUsage(role, read + write, read, write, 0, (read * p.read + write * p.write) / 1e6, len(keys))
        self._last[scope] = list(keys)
        self.requests.append(u)
        t = self.by_role.setdefault(role, RoleTotals())
        t.requests += 1
        t.input += u.input
        t.cache_read += read
        t.cache_write += write
        t.cost += u.cost
        return u

    def add_output(self, n: int, *, role: str = "main") -> None:
        p = self.price(role)
        c = n * p.output / 1e6
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

    def reset_cache(self, scope: str = "main") -> None:
        self._last.pop(scope, None)

    # ---- totals
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
        d = {r: t.to_dict() for r, t in self.by_role.items()}
        d["_total"] = self.total().to_dict()
        return d


@dataclass
class CostModel:
    """Token efficiency = cost / aggregate score (SoL-Pi's eta)."""

    @staticmethod
    def efficiency(cost: float, score: float) -> float:
        return cost / score if score > 0 else float("inf")

    @staticmethod
    def summarize(costs: Sequence[float], scores: Sequence[float], tokens: Sequence[float]) -> dict:
        c, s, t = float(sum(costs)), float(sum(scores)), float(sum(tokens))
        return {"cost": c, "score": s, "tokens": t, "eta": CostModel.efficiency(c, s),
                "tokens_per_score": t / s if s > 0 else float("inf")}
