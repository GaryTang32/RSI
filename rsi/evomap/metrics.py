"""ReuseMetrics: the health dashboard of a hub (spec §9.2.7).

Everything is computed from the hub's records (and, in simulations, an optional
ground-truth function):

* ``reuse_rate_published`` / ``reuse_rate_promoted`` - share of assets with >= 1
  non-author adoption (counted adoptions only on SafeHub); ``never_reused``;
* ``time_to_first_reuse`` (median epochs), ``adoption_success_rate``;
* ``consumer_uplift`` - mean measured (or true) uplift of adopted assets;
* ``credit_gini`` / ``credit_top10_share`` over agents' *earned* credits;
* ``vacuous_share_promoted`` - the discriminative check (+ lint) re-run on the
  promoted assets' proof material (assets without proof material count as
  vacuous: nothing demonstrates that their validation discriminates);
* ``rank_validity`` - Spearman(rank score, ground-truth effect) over ranked assets
  (else vs later adoption success); ``duplicate_rate``.
"""
from __future__ import annotations

from typing import Callable, Optional

import numpy as np

from rsi.core.stats import spearman

from .assets import Gene
from .validation import InProcessExecutor, ValidationRunner, VacuityDetector


class ReuseMetrics:
    def __init__(self, truth: Optional[Callable[[Gene], float]] = None, vacuity: Optional[VacuityDetector] = None,
                 agents: Optional[list[str]] = None) -> None:
        self.truth = truth
        self.vacuity = vacuity or VacuityDetector(ValidationRunner(executor=InProcessExecutor(), mode="safe"))
        self.agents = agents

    def is_vacuous(self, rec) -> bool:
        b = rec.bundle
        specs = list(b.gene.get("validation", []))
        if b.pre_state is None or b.post_state is None:
            return True
        return self.vacuity.verdict(specs, b.pre_state, b.post_state, mutants=False).vacuous

    def compute(self, hub) -> dict:
        recs = hub.published()
        promoted_states = ("promoted",) if hub.name == "naive" else ("promoted", "verified")
        promoted = [r for r in recs if r.status in promoted_states]
        strictly_promoted = [r for r in recs if r.status == "promoted"]
        counted_only = hub.name != "naive"

        def reused(r) -> bool:
            return bool(r.adopters(counted_only=counted_only))

        def rate(rs) -> float:
            return float(np.mean([reused(r) for r in rs])) if rs else float("nan")

        firsts = []
        for r in recs:
            eps = [a["epoch"] for a in r.adoptions if a["consumer"] != r.author and (a.get("counted") or not counted_only)]
            if eps:
                firsts.append(min(eps) - r.epoch)
        adopts = [a for r in recs for a in r.adoptions if a["consumer"] != r.author and
                  (a.get("counted") or not counted_only)]
        out = {
            "hub": hub.name, "epoch": hub.epoch, "n_published": len(recs), "n_promoted": len(promoted),
            "n_promoted_strict": len(strictly_promoted),
            "promotion_rate": len(promoted) / len(recs) if recs else float("nan"),
            "reuse_rate_published": rate(recs), "reuse_rate_promoted": rate(promoted),
            "never_reused_published": 1 - rate(recs) if recs else float("nan"),
            "never_reused_promoted": 1 - rate(promoted) if promoted else float("nan"),
            "time_to_first_reuse": float(np.median(firsts)) if firsts else float("nan"),
            "adoption_success_rate": float(np.mean([a["outcome"] for a in adopts])) if adopts else float("nan"),
            "credit_gini": hub.credits.gini(self.agents),
            "credit_top10_share": hub.credits.top_share(0.10, self.agents),
            "credits_by_reason": hub.credits.by_reason(),
        }
        if promoted:
            out["vacuous_share_promoted"] = float(np.mean([self.is_vacuous(r) for r in promoted]))
        else:
            out["vacuous_share_promoted"] = float("nan")
        keys = [r.gene.content_key() for r in recs]
        out["duplicate_rate"] = 1 - len(set(keys)) / len(keys) if keys else float("nan")
        ranked = hub.rank()
        by = {r.asset_id: r for r in recs}
        if self.truth is not None and len(ranked) >= 3:
            xs = [s for a, s in ranked]
            ys = [self.truth(by[a].gene) for a, s in ranked]
            out["rank_validity"] = spearman(xs, ys)
            upl = [self.truth(by[a["asset"]].gene) for a in
                   [{"asset": r.asset_id} for r in recs for x in r.adoptions if x["consumer"] != r.author and
                    (x.get("counted") or not counted_only)]]
            out["consumer_uplift_true"] = float(np.mean(upl)) if upl else float("nan")
        else:
            out["rank_validity"] = float("nan")
        if self.truth is not None and len(recs) >= 3:
            # surfacing validity: over ALL submissions, rank 0 when the hub does not serve the asset
            served = dict(ranked)
            xs = [served.get(r.asset_id, 0.0) for r in recs]
            ys = [self.truth(r.gene) for r in recs]
            out["surfacing_validity"] = spearman(xs, ys)
            top = [r for r in recs if r.asset_id in served]
            out["served_true_effect"] = float(np.mean([self.truth(r.gene) for r in top])) if top else float("nan")
        else:
            out["surfacing_validity"] = float("nan")
            out["served_true_effect"] = float("nan")
        meas = [a.get("uplift") for a in adopts if a.get("uplift") is not None]
        out["consumer_uplift_measured"] = float(np.mean(meas)) if meas else float("nan")
        return out
