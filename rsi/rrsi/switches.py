"""Regularizer switches for ablations: proposal-side and selection-side mechanisms, and named arms."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Optional


@dataclass(frozen=True)
class RegularizerSwitches:
    name: str = "full"
    # proposal side
    budget_anneal: bool = True             # False -> constant_budget (None = b_max)
    constant_budget: Optional[int] = None
    history_conditioning: str = "full"     # "full" | "accepted_only" | "none"
    stall_exploration: bool = True
    # selection side
    critic: bool = True
    floor: str = "S_star"                  # "S_star" | "S_t" | "none"
    cost_rule: bool = True
    within_band: str = "shaped"            # "shaped" | "strict" | "reject" | "admit"
    prune_directives: bool = True
    domain_guards: bool = True
    selection: str = "rrsi"                # "rrsi" (Alg. 2) | "greedy" (argmax iff S' > S_t) | "argmax"
    smoke: bool = True

    @classmethod
    def full(cls) -> "RegularizerSwitches":
        return cls(name="full")

    @classmethod
    def no_proposal(cls, prune_in_proposal_group: bool = False) -> "RegularizerSwitches":
        return cls(name="no_proposal", budget_anneal=False, history_conditioning="accepted_only",
                   stall_exploration=False, prune_directives=not prune_in_proposal_group)

    @classmethod
    def no_acceptance(cls, prune_in_proposal_group: bool = False) -> "RegularizerSwitches":
        return cls(name="no_acceptance", critic=False, floor="none", cost_rule=False, within_band="strict",
                   prune_directives=prune_in_proposal_group, domain_guards=False, selection="greedy")

    @classmethod
    def none(cls) -> "RegularizerSwitches":
        """Unregularized baseline: constant b_max, accepted-only history, no critic, keep iff S' > S_t."""
        return cls(name="unregularized", budget_anneal=False, history_conditioning="accepted_only",
                   stall_exploration=False, critic=False, floor="none", cost_rule=False, within_band="strict",
                   prune_directives=False, domain_guards=False, selection="greedy")

    @classmethod
    def budget_only(cls) -> "RegularizerSwitches":
        return replace(cls.none(), name="budget_only", budget_anneal=True)

    @classmethod
    def selector_only(cls) -> "RegularizerSwitches":
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
