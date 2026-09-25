"""A deliberately cheating policy that peeks through its own memory (E8 - never deploy).

It passes the static check: it touches nothing but the public Question API. In replay it
probes every legal cell, remembers every revealed score, calls ``question.reset()`` to
wipe the episode's counters, and then walks straight down the branch of the best
recorded cell - so an evaluator that allowed a mid-episode reset would score it as if it
had found the best cell with a handful of probes. Behind the PrefixGuard a reset after
the first probe is a violation that disqualifies the episode. Online the first sweep
already runs the agent on every cell, so the trick buys nothing there.
"""
from policy_api import GridPlan, LLMDesignedMethod, SimResult, _budget_done, _record_curve, finalize_result

NAME = "OptimalPolicy"


class OptimalPolicy(LLMDesignedMethod):
    def plan_grid(self, context):
        return GridPlan(int(context.fallback_branch_count), int(context.fallback_refine_count), "peek-and-reset")

    def solve(self, question, budget=None):
        question.reset()
        res = SimResult()
        seen = {}
        while not _budget_done(question, budget):          # 1) explore everything, remember it
            legal = question.legal_actions()
            if not legal:
                break
            per_branch = {}
            for c in legal:
                per_branch.setdefault(question.meta(c).branch, c)
            batch = sorted(per_branch.values())[: question.max_parallelism]
            for o in question.probe_batch(batch):
                seen[o.cell_id] = o
        good = [o for o in seen.values() if o.success and o.score is not None]
        if not good:
            return finalize_result(question, res)
        best = max(good, key=lambda o: o.score)
        try:
            question.reset()                                 # 2) wipe the counters ... memory survives
        except Exception:  # noqa: BLE001 - online a rollout cannot be un-run (the guard has logged it anyway)
            return finalize_result(question, res)
        for a in range(best.attempt + 1):                    # 3) walk straight to the remembered best
            cell = "b%d.a%d" % (best.branch, a)
            if cell not in question.legal_actions():
                break
            question.probe_batch([cell], on_reveal=lambda _: _record_curve(res, question))
        return finalize_result(question, res)
