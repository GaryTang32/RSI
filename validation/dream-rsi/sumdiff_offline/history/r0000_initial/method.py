"""Parallel refine (pi_1, the paper's initial policy and the Recursive Fixed Exploration baseline).

"launches multiple independent exploration workspaces in parallel, with each workspace
maintaining its own local discovery trajectory and repeatedly refining its current
candidate" [paper:§4 p.7].

Prefix signals: none (a fixed wave schedule). Batch rule: every legal cell ordered by
(attempt, branch) - all roots first, then deepen every open branch - up to
max_parallelism. Beta: read but unused (the fixed baseline has no dial). Grid planning:
always the context's fallback grid. Stops when no legal cell remains.
"""
from policy_api import GridPlan, LLMDesignedMethod, SimResult, _budget_done, _record_curve, finalize_result

NAME = "OptimalPolicy"


class OptimalPolicy(LLMDesignedMethod):
    def __init__(self, config=None):
        super().__init__(config)
        self.beta = float(self.config.get("beta", 0.6))

    def plan_grid(self, context):
        return GridPlan(int(context.fallback_branch_count), int(context.fallback_refine_count),
                        "parallel refine: fixed fallback grid (width x depth never adapts)")

    def solve(self, question, budget=None):
        question.reset()
        res = SimResult()
        while not _budget_done(question, budget):
            legal = question.legal_actions()
            if not legal:
                break
            order = sorted(legal, key=lambda c: (question.meta(c).attempt, question.meta(c).branch))
            batch = order[: question.max_parallelism]
            question.probe_batch(batch, on_reveal=lambda _: _record_curve(res, question))
        res.stopped = "no legal cell left"
        return finalize_result(question, res)
