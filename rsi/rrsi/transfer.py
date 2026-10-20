"""Report-only transfer evaluation: every arm (e.g. H_0 vs the evolved harness) on every split."""
from __future__ import annotations

import tempfile
from typing import Optional, Sequence

from ..core.artifact import Artifact
from ..core.domain import Domain
from ..core.evaluate import Evaluator
from ..core.llm import LLM
from ..core.run import transfer_report


def paired_transfer(domain: Domain, llm_task: Optional[LLM], arms: dict[str, Artifact], *,
                    splits: Sequence[str] = ("evolve", "holdout", "ood"), k: int = 1, workers: int = 4,
                    reference: Optional[str] = None, cache_dir: Optional[str] = None) -> dict:
    """Paired report-only evaluation of every arm on every split (unseals sealed splits)."""
    reference = reference or next(iter(arms))
    with tempfile.TemporaryDirectory(prefix="rrsi_transfer_") as tmp:
        cache = cache_dir or tmp
        rep = transfer_report(domain, llm_task, arms, splits=splits, k=k, workers=workers, reference=reference,
                              cache_dir=cache)
        ev = Evaluator(domain, llm_task, workers=workers, allow_sealed=True, cache_dir=cache)
        for split in rep["splits"]:
            res = {name: ev.evaluate(a, split, k) for name, a in arms.items()}
            ref = res[reference].task_scores()
            for name, r in res.items():
                if name == reference:
                    continue
                ts = r.task_scores()
                w = sum(1 for t in ts if ts[t] > ref[t] + 1e-12)
                l_ = sum(1 for t in ts if ts[t] < ref[t] - 1e-12)
                rep["splits"][split][name]["win_tie_loss"] = [w, len(ts) - w - l_, l_]
        rep["fresh_rollouts_in_win_tie_loss_pass"] = ev.n_rollouts
    if hasattr(domain, "expected"):
        rep["expected"] = {split: {name: domain.expected(a, split) for name, a in arms.items()}
                           for split in splits if split in domain.tasks.splits}
    unseen = [s for s in ("holdout", "ood") if s in rep["splits"]]
    rep["non_regression"] = {name: all(rep["splits"][s][name]["S"] >= rep["splits"][s][reference]["S"] - 1e-12
                                       for s in unseen) for name in arms}
    # a real regression: the paired per-task CI against the reference lies entirely below 0
    rep["significant_regression"] = {
        name: [s for s in unseen if (rep["splits"][s][name].get("vs_reference") or {}).get("hi", 0.0) < 0]
        for name in arms if name != reference}
    return rep
