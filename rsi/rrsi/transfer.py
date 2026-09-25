"""Transfer evaluation: H_0 versus the evolved harness on splits the loop never saw
(the spec's ``TransferEvaluator.paired``; the code's ``heldout`` / ``final_eval.sh``).

Both arms are evaluated unchanged (this is the report-only step that unseals holdout /
OOD). For every split we report S, C, steps, per-family scores, the paired per-task
difference with a bootstrap CI against the reference arm (via
:func:`rsi.core.transfer_report`), and a within-task win/tie/loss count as in the
engineering domain's final evaluation. When the domain exposes an analytic
``expected(artifact, split)`` (e.g. HarnessWorld), the noise-free expected score and
cost are added as ground truth. A non-regression flag answers the paper's "no held-out
split got worse".
"""
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
    """Paired evaluation of every arm on every split (report-only; unseals sealed splits).

    Every (arm, task, seed) rollout runs once: the win/tie/loss pass replays the trials of the
    :func:`rsi.core.transfer_report` pass from a shared trial cache (a temporary directory unless
    ``cache_dir`` is given), so a live task model is not paid twice and both views agree."""
    reference = reference or next(iter(arms))
    with tempfile.TemporaryDirectory(prefix="rrsi_transfer_") as tmp:
        cache = cache_dir or tmp
        rep = transfer_report(domain, llm_task, arms, splits=splits, k=k, workers=workers, reference=reference,
                              cache_dir=cache)
        # within-task win / tie / loss against the reference (same seeds -> paired; replayed from the cache)
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
    return rep
