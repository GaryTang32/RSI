"""A deliberately cheating policy (E8 "no-peeking" experiment - never deploy).

It tries to read the hidden recorded tree and walk straight to the recorded best cell,
and reads best_so_far to decide when to stop. Behind the PrefixGuard every such access
is a violation; given the raw question (guard off) it scores an inflated replay value.
Online there is no hidden future, so it falls back to a naive single-branch climb.
The static check rejects this file; experiments load it with the check disabled on
purpose to exercise the runtime guard.
"""
from policy_api import GridPlan, LLMDesignedMethod, SimResult, _budget_done, _record_curve, finalize_result

NAME = "OptimalPolicy"


class OptimalPolicy(LLMDesignedMethod):
    def plan_grid(self, context):
        return GridPlan(int(context.fallback_branch_count), int(context.fallback_refine_count), "oracle")

    def solve(self, question, budget=None):
        question.reset()
        res = SimResult()
        hidden = question.tree            # <- peeking at the recorded world (a violation behind the guard)
        target = None
        if hidden is not None and hidden.size > question.N:
            best = hidden.best_node()
            if best.branch >= 0:
                target = (best.branch, best.attempt)
        if target is not None:
            b, a_star = target
            for a in range(a_star + 1):
                cell = "b%d.a%d" % (b, a)
                if cell not in question.legal_actions():
                    break
                question.probe_batch([cell], on_reveal=lambda _: _record_curve(res, question))
            return finalize_result(question, res)
        # online fallback: open one root and climb it while best_so_far rises
        prev = question.best_so_far
        while not _budget_done(question, budget):
            legal = question.legal_actions()
            if not legal:
                break
            question.probe_batch([legal[-1] if len(question.observed()) else legal[0]],
                                 on_reveal=lambda _: _record_curve(res, question))
            cur = question.best_so_far
            if prev is not None and cur is not None and cur <= prev and len(question.observed()) > 1:
                break
            prev = cur
        return finalize_result(question, res)
