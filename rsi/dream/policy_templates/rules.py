"""Rule-based policy family (hand-written strategies; the overview demo and the E2 policy zoo).

Prefix signals: branch anchors (best successful score) and each branch's latest gain
over its parent. Batch rule: open roots first (all, or a fixed number), then refine the
frontiers of branches that are not stopped, best anchor first, optionally only the top-k,
capped at `cap` cells per round. A branch stops once its latest gain drops below
`gain_stop` (raw points; the demo's "stop once its gains shrink to a couple of points")
or below `rel_gain_stop` x spread. `max_rounds` stops the whole search early.
Beta: scales gain thresholds (high beta = more patient). Grid planning: optional fixed
override (`plan_w` x `plan_r`), else the fallback grid.
"""
from policy_api import GridPlan, LLMDesignedMethod, SimResult, _budget_done, _record_curve, finalize_result

NAME = "OptimalPolicy"

PARAMS = {
    "open": -1,
    "max_rounds": 0,
    "gain_stop": 0.0,
    "rel_gain_stop": 0.0,
    "top_k": 0,
    "cap": 0,
    "plan_w": 0,
    "plan_r": -1,
    "default_beta": 0.5,
}


class OptimalPolicy(LLMDesignedMethod):
    def __init__(self, config=None):
        super().__init__(config)
        self.beta = float(self.config.get("beta", PARAMS["default_beta"]))

    def plan_grid(self, context):
        w = int(PARAMS["plan_w"]) or int(context.fallback_branch_count)
        r = int(PARAMS["plan_r"]) if int(PARAMS["plan_r"]) >= 0 else int(context.fallback_refine_count)
        w = max(1, min(w, int(context.hard_max_branch_count)))
        r = max(0, min(r, int(context.hard_max_refine_count)))
        return GridPlan(w, r, "rule policy: fixed grid")

    def _batch(self, question, rounds):
        W = question.max_parallelism
        cap = int(PARAMS["cap"]) or W
        cap = min(cap, W)
        prefix = question.observed()
        roots = question.legal_roots()
        opened = len({o.branch for o in prefix.values()})
        n_open = len(roots) if int(PARAMS["open"]) < 0 else max(0, int(PARAMS["open"]) - opened)
        batch = sorted(roots, key=lambda c: question.meta(c).branch)[:n_open]
        succ = [o.score for o in prefix.values() if o.success and o.score is not None]
        base = question.baseline_score if question.baseline_score is not None else (min(succ) if succ else 0.0)
        spread = (max(succ) - base) if succ else 0.0
        scale = 1.0 + (0.5 - self.beta)          # beta 0.5 = the thresholds as written
        cands = []
        for c in question.legal_actions():
            m = question.meta(c)
            if m.attempt == 0:
                continue
            traj = sorted([o for o in prefix.values() if o.branch == m.branch], key=lambda o: o.attempt)
            last = traj[-1]
            anchor = max([o.score for o in traj if o.success and o.score is not None] or [base])
            if last.attempt >= 1 and last.success and last.delta_vs_parent is not None:
                g = last.delta_vs_parent
                if PARAMS["gain_stop"] > 0 and g < PARAMS["gain_stop"] * scale:
                    continue
                if PARAMS["rel_gain_stop"] > 0 and spread > 0 and g < PARAMS["rel_gain_stop"] * scale * spread:
                    continue
            cands.append((-anchor, m.branch, c))
        cands.sort()
        if int(PARAMS["top_k"]) > 0:
            cands = cands[: int(PARAMS["top_k"])]
        batch.extend(c for _, _, c in cands)
        return batch[:cap]

    def solve(self, question, budget=None):
        question.reset()
        res = SimResult()
        rounds = 0
        while not _budget_done(question, budget):
            if int(PARAMS["max_rounds"]) > 0 and rounds >= int(PARAMS["max_rounds"]):
                res.stopped = "max_rounds"
                break
            batch = self._batch(question, rounds)
            if not batch:
                res.stopped = "no candidate"
                break
            question.probe_batch(batch, on_reveal=lambda _: _record_curve(res, question))
            rounds += 1
        return finalize_result(question, res)
