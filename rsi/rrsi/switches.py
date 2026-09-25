"""Regularizer switches for ablations (spec section 9.1 ``RegularizerSwitches``).

The paper's figure groups the mechanisms into

* **proposal-side** regularizers: annealed edit budget, evidence-aware history
  conditioning, structured (stall) exploration;
* **selection-side** ("acceptance") regularizers: leakage critic, noise-adjusted
  floor, complexity-aware acceptance (cost rule + within-band rule), structural
  pruning (delivered in code as the B_t proposer directive), domain guards.

The exact membership used in the paper's ablation table is not published, so the
groups follow the paper's own proposal/selection split and pruning is in the
acceptance group by default (``prune_in_proposal_group=True`` moves it).

The *unregularized* baseline (:meth:`RegularizerSwitches.none`) follows the
spec's explicit definition: constant budget b_max, accepted-only history, no stall
exploration, no critic (smoke only), and greedy selection: the argmax candidate is
accepted iff it measured strictly better than the incumbent.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Optional


@dataclass(frozen=True)
class RegularizerSwitches:
    name: str = "full"
    # ---- proposal side --------------------------------------------------------
    budget_anneal: bool = True             # b_t cosine schedule; False -> constant_budget
    constant_budget: Optional[int] = None  # used when budget_anneal=False (None -> b_max)
    history_conditioning: str = "full"     # "full" | "accepted_only" | "none"
    stall_exploration: bool = True         # sigma_t, U_t and reserved slots
    # ---- selection side -------------------------------------------------------
    critic: bool = True                    # regex/denylist precheck + LLM review + repair loop
    floor: str = "S_star"                  # "S_star" (paper) | "S_t" (relative to incumbent) | "none"
    cost_rule: bool = True                 # dC <= beta0 + beta1 * dS for gains above delta
    within_band: str = "shaped"            # "shaped" | "strict" (dS > 0) | "reject" | "admit"
    prune_directives: bool = True          # B_t handed to the proposer
    domain_guards: bool = True
    selection: str = "rrsi"                # "rrsi" (Algorithm 2) | "greedy" (argmax iff S' > S_t) | "argmax"
    smoke: bool = True

    # ---- named arms -----------------------------------------------------------------
    @classmethod
    def full(cls) -> "RegularizerSwitches":
        return cls(name="full")

    @classmethod
    def no_proposal(cls, prune_in_proposal_group: bool = False) -> "RegularizerSwitches":
        """'- proposal regularizers': constant b_max, accepted-only history, no stall exploration."""
        return cls(name="no_proposal", budget_anneal=False, history_conditioning="accepted_only",
                   stall_exploration=False, prune_directives=not prune_in_proposal_group)

    @classmethod
    def no_acceptance(cls, prune_in_proposal_group: bool = False) -> "RegularizerSwitches":
        """'- acceptance regularizers': no critic, no floor, no cost/within-band rule, no guards,
        (no prune directives unless pruning is counted in the proposal group); keep-if-better."""
        return cls(name="no_acceptance", critic=False, floor="none", cost_rule=False, within_band="strict",
                   prune_directives=prune_in_proposal_group, domain_guards=False, selection="greedy")

    @classmethod
    def none(cls) -> "RegularizerSwitches":
        """Unregularized evolution (spec's explicit baseline definition)."""
        return cls(name="unregularized", budget_anneal=False, history_conditioning="accepted_only",
                   stall_exploration=False, critic=False, floor="none", cost_rule=False, within_band="strict",
                   prune_directives=False, domain_guards=False, selection="greedy")

    @classmethod
    def budget_only(cls) -> "RegularizerSwitches":
        """E2b: only the annealed edit budget on top of the unregularized loop."""
        return replace(cls.none(), name="budget_only", budget_anneal=True)

    @classmethod
    def selector_only(cls) -> "RegularizerSwitches":
        """E2b: only the selection side (critic, floor, cost/within-band rule, prune, guards)."""
        return replace(cls.full(), name="selector_only", budget_anneal=False, history_conditioning="accepted_only",
                       stall_exploration=False)

    def but(self, **kw) -> "RegularizerSwitches":
        """Copy with some switches changed (``RegularizerSwitches.full().but(critic=False)``)."""
        kw.setdefault("name", self.name + "+" + ",".join(f"{k}={v}" for k, v in kw.items()))
        return replace(self, **kw)

    def to_json(self) -> dict:
        return asdict(self)


ARMS = {
    "full": RegularizerSwitches.full,
    "no_proposal": RegularizerSwitches.no_proposal,
    "no_acceptance": RegularizerSwitches.no_acceptance,
    "unregularized": RegularizerSwitches.none,
    "budget_only": RegularizerSwitches.budget_only,
    "selector_only": RegularizerSwitches.selector_only,
}
