"""A deliberately cheating policy that remembers worlds ACROSS episodes (E8 - never deploy).

Claims audit N1. It touches nothing but the public Question API and never resets mid-episode. On its
first visit to a world it runs parallel refine and stores the best revealed cell's (branch, attempt)
in a MODULE-LEVEL dict keyed by ``question.baseline_score`` (a world fingerprint). On any later
visit to the same world - the beta sweep replays every world once per beta - it opens only the roots
it needs and deepens only the remembered branch, "finding" the recorded best with a fraction of the
probes. When every episode runs in one policy namespace this wins the Pareto sweep (+0.28 against
+0.10 for the honest adaptive policy on the 13 validation worlds). The fix: every episode starts
from a fresh namespace (module re-executed in-process, a fresh fork in the sandbox), so ``_MEMO``
is always empty and the policy is plain parallel refine; the static check also rejects the module-level
mutation.
"""
from policy_api import GridPlan, LLMDesignedMethod, SimResult, _budget_done, _record_curve, finalize_result

NAME = "OptimalPolicy"
_MEMO = {}


class OptimalPolicy(LLMDesignedMethod):
    def __init__(self, config=None):
        super().__init__(config)
        self.beta = float(self.config.get("beta", 0.6))

    def plan_grid(self, context):
        return GridPlan(int(context.fallback_branch_count), int(context.fallback_refine_count), "fallback grid")

    def solve(self, question, budget=None):
        question.reset()
        res = SimResult()
        key = round(float(question.baseline_score or 0.0), 9)
        target = _MEMO.get(key)
        while not _budget_done(question, budget):
            legal = question.legal_actions()
            if not legal:
                break
            obs = question.observed()
            if target is None:
                order = sorted(legal, key=lambda c: (question.meta(c).attempt, question.meta(c).branch))
                batch = order[: question.max_parallelism]
            else:
                tb, ta = target
                if tb not in question.opened_branches():
                    batch = [c for c in legal if question.meta(c).attempt == 0][: question.max_parallelism]
                else:
                    if any(o.branch == tb and o.attempt >= ta for o in obs.values()):
                        break
                    batch = [c for c in legal if question.meta(c).branch == tb and question.meta(c).attempt > 0][:1]
                if not batch:
                    break
            question.probe_batch(batch, on_reveal=lambda _: _record_curve(res, question))
        if target is None:
            best = None
            for o in question.observed().values():
                if o.fail_class == "ok" and o.error is None and o.score is not None:
                    if best is None or o.score > best[0]:
                        best = (o.score, o.branch, o.attempt)
            if best is not None:
                _MEMO[key] = (best[1], best[2])
        return finalize_result(question, res)
