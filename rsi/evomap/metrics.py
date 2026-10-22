"""ReuseMetrics: a hub's health dashboard computed from its records (spec §9.2.7).

``n_promoted`` counts status ``promoted``; the ``*_promoted`` rates use the admitted tier ``n_admitted`` (naive hub:
promoted; SafeHub: verified or promoted). ``vacuous_share_promoted`` re-runs lint + the discriminative check;
``trivial_command_share_promoted`` is the study's static notion (every command trivial as written).
"""
from __future__ import annotations

from typing import Callable, Optional

import numpy as np

from rsi.core.stats import spearman

from .assets import Gene
from .safehub import is_strategy_gene
from .validation import InProcessExecutor, ValidationRunner, VacuityDetector


def _nan_mean(xs) -> float:
    return float(np.mean(xs)) if xs else float("nan")


class ReuseMetrics:
    def __init__(self, truth: Optional[Callable[[Gene], float]] = None, vacuity: Optional[VacuityDetector] = None,
                 agents: Optional[list[str]] = None) -> None:
        self.truth = truth
        self.vacuity = vacuity or VacuityDetector(ValidationRunner(executor=InProcessExecutor(), mode="safe"))
        self.agents = agents

    def is_vacuous(self, rec) -> bool:
        """Vacuous unless the proof material shows a discriminating validation (strategy genes: a hub uplift)."""
        b = rec.bundle
        if is_strategy_gene(Gene.from_dict(b.gene)):
            hr = rec.hub_report or {}
            return not (hr.get("U_LCB") is not None and hr.get("delta") is not None and hr["U_LCB"] >= hr["delta"])
        if b.pre_state is None or b.post_state is None:
            return True
        return self.vacuity.verdict(list(b.gene.get("validation", [])), b.pre_state, b.post_state,
                                    mutants=False).vacuous

    def is_trivial_command(self, rec) -> bool:
        """Every validation command is trivial as written (static lint only)."""
        b = rec.bundle
        specs = list(b.gene.get("validation", []))
        if is_strategy_gene(Gene.from_dict(b.gene)):
            return False
        if not specs:
            return True
        return all(bool(f) for f in self.vacuity.lint(specs, b.post_state or {}).values())

    def compute(self, hub) -> dict:
        recs = hub.published()
        promoted_states = ("promoted",) if hub.name == "naive" else ("promoted", "verified")
        promoted = [r for r in recs if r.status in promoted_states]
        counted_only = hub.name != "naive"

        def reports(r) -> list:
            return [a for a in r.adoptions if a["consumer"] != r.author and (a.get("counted") or not counted_only)]

        def rate(rs) -> float:
            return float(np.mean([bool(r.adopters(counted_only=counted_only)) for r in rs])) if rs else float("nan")

        firsts = [min(a["epoch"] for a in reports(r)) - r.epoch for r in recs if reports(r)]
        adopts = [a for r in recs for a in reports(r)]
        out = {
            "hub": hub.name, "epoch": hub.epoch, "n_published": len(recs),
            "n_promoted": sum(1 for r in recs if r.status == "promoted"), "n_admitted": len(promoted),
            "admitted_states": list(promoted_states),
            "promotion_rate": len(promoted) / len(recs) if recs else float("nan"),
            "reuse_rate_published": rate(recs), "reuse_rate_promoted": rate(promoted),
            "never_reused_published": 1 - rate(recs) if recs else float("nan"),
            "never_reused_promoted": 1 - rate(promoted) if promoted else float("nan"),
            "time_to_first_reuse": float(np.median(firsts)) if firsts else float("nan"),
            "adoption_success_rate": _nan_mean([a["outcome"] for a in adopts]),
            "credit_gini": hub.credits.gini(self.agents),
            "credit_top10_share": hub.credits.top_share(0.10, self.agents),
            "credits_by_reason": hub.credits.by_reason(),
            "vacuous_share_promoted": _nan_mean([self.is_vacuous(r) for r in promoted]),
            "trivial_command_share_promoted": _nan_mean([self.is_trivial_command(r) for r in promoted]),
        }
        keys = [r.gene.content_key() for r in recs]
        out["duplicate_rate"] = 1 - len(set(keys)) / len(keys) if keys else float("nan")
        ranked = hub.rank()
        by = {r.asset_id: r for r in recs}
        out["rank_validity"] = float("nan")
        if self.truth is not None and len(ranked) >= 3:
            out["rank_validity"] = spearman([s for a, s in ranked], [self.truth(by[a].gene) for a, s in ranked])
            # a safe consumer's negative report means its quarantine kept the incumbent: realized uplift 0
            out["consumer_uplift_true"] = _nan_mean([self.truth(r.gene) if (not counted_only or x["outcome"] == 1)
                                                     else 0.0 for r in recs for x in reports(r)])
        out["surfacing_validity"] = out["served_true_effect"] = float("nan")
        if self.truth is not None and len(recs) >= 3:
            served = dict(ranked)  # an asset the hub does not serve ranks 0
            out["surfacing_validity"] = spearman([served.get(r.asset_id, 0.0) for r in recs],
                                                 [self.truth(r.gene) for r in recs])
            out["served_true_effect"] = _nan_mean([self.truth(r.gene) for r in recs if r.asset_id in served])
        out["consumer_uplift_measured"] = _nan_mean([a.get("uplift") for a in adopts if a.get("uplift") is not None])
        return out
