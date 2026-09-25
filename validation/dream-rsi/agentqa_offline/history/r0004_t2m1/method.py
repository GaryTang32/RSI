"""OptimalPolicy: adaptive portfolio exploration policy (prefix-only).

Prefix signals: for every opened branch the ordered trajectory from question.observed():
successful anchor (best score of a successful evaluation), running-anchor gains, the
stagnation run (attempts since the anchor last rose by more than gain_tol * spread,
where spread = best anchor - baseline of the current prefix), the current failure
episode (consecutive trailing failures, hard vs repairable by fail_class) and the
number of successful evaluations (evidence).

Batch rule: one dynamic portfolio per round, up to max_parallelism distinct legal cells:
exploration (new roots, preferring direction tags not opened yet) while the opened width
is below the beta-scheduled target or the portfolio stalled; the top exploitation
frontier; at most one recovery (a repairable failure with its repair allowance left);
remaining slots by exploitation priority = w_anchor*anchor + w_trend*last gain -
w_depth*depth (all relative to the prefix spread). Deterministic ties.

Beta schedule: _schedule(beta) routes width (open_frac, max_width), patience, gain
tolerance, relative pruning margin, evidence needed before closing, and repair
allowance. High beta = wider, more patient, weaker pruning; low beta = fewer probes,
earlier stagnation stops, stronger pruning. Beta is fixed within an episode.

Default beta rationale: PARAMS["default_beta"] is chosen between cycles by the policy
developer from live manifests + beta sweeps (0.6 when evidence is insufficient).

Grid planning: plan_grid reads only earlier live manifests: widen when the live best
plateaued or roots improved early while depth stalled; deepen when gains came late;
shrink on repeated hard failures; conservative bootstrap without history.

Safeguards: a repairable failure never closes a branch by itself and a later success
reopens it (closure is recomputed from the full trajectory each round); no closure
before min_evidence successful attempts (shallow weak scores can recover); stop only
when no root, frontier or recovery candidate is eligible; batches are filled with every
eligible candidate up to max_parallelism (no serial probing when several are eligible).
"""
import math

from policy_api import (GridPlan, LLMDesignedMethod, SimResult, HARD_FAIL_CLASSES, _budget_done, _record_curve,
                        branch_trajectories, clamp, finalize_result, lerp)

NAME = "OptimalPolicy"

PARAMS = {
    'default_beta': 0.6,
    'open_lo': 0.5,
    'open_hi': 1.0,
    'max_width_lo': 0.8235,
    'max_width_hi': 1.0,
    'patience_lo': 0.5,
    'patience_hi': 1.5,
    'gain_lo': 0.17,
    'gain_hi': 0.01,
    'prune_lo': 0.05,
    'prune_hi': 0.2704,
    'evidence_lo': 1.0,
    'evidence_hi': 2.0,
    'repairs_lo': 1.0,
    'repairs_hi': 2.3691,
    'explore_on_stall': 0.8285,
    'w_anchor': 0.48,
    'w_trend': 0.8054,
    'w_depth': 0.05,
    'plan_width_step': 1,
    'plan_depth_step': 2,
}


def _schedule(beta):
    P = PARAMS
    return {
        "open_frac": lerp(P["open_lo"], P["open_hi"], beta),
        "max_width": lerp(P["max_width_lo"], P["max_width_hi"], beta),
        "patience": lerp(P["patience_lo"], P["patience_hi"], beta),
        "gain_tol": lerp(P["gain_lo"], P["gain_hi"], beta),
        "prune": lerp(P["prune_lo"], P["prune_hi"], beta),
        "evidence": lerp(P["evidence_lo"], P["evidence_hi"], beta),
        "repairs": lerp(P["repairs_lo"], P["repairs_hi"], beta),
    }


class OptimalPolicy(LLMDesignedMethod):
    def __init__(self, config=None):
        super().__init__(config)
        self.beta = float(self.config.get("beta", PARAMS["default_beta"]))
        self.sch = _schedule(self.beta)

    # ------------------------------------------------------------------ grid plan
    def plan_grid(self, context):
        fb_w, fb_r = int(context.fallback_branch_count), int(context.fallback_refine_count)
        hw, hr = int(context.hard_max_branch_count), int(context.hard_max_refine_count)
        hist = [h for h in (context.history or []) if h.get("planned_grid")]
        if not hist:
            return GridPlan(fb_w, fb_r, "insufficient live history: conservative bootstrap from the fallback grid")
        last = hist[-1]
        w = int(last["planned_grid"]["branch_count"])
        r = int(last["planned_grid"]["refine_count"])
        sw, sr = int(PARAMS["plan_width_step"]), int(PARAMS["plan_depth_step"])
        improving = len(hist) < 2 or last.get("final_best", 0) > hist[-2].get("final_best", 0)
        early, late = float(last.get("gain_early", 0.0)), float(last.get("gain_late", 0.0))
        used_depth = int(last.get("max_depth", r))
        if float(last.get("hard_fail_frac", 0.0)) > 0.5:
            w, r, why = w - sw, r - sr, "repeated hard failures: reduce width and depth"
        elif not improving:
            w, why = w + sw, "live best plateaued: widen to cover new directions"
        elif late > early and used_depth >= r:
            r, why = r + sr, "gains arrived late on few directions: deepen"
        elif early > late and used_depth < r:
            w, r, why = w + sw, max(used_depth, r - sr), "roots improved early while depth stalled: widen, trim depth"
        else:
            why = "live best still improving with balanced gains: hold the grid"
        w = int(clamp(w, 1, hw))
        r = int(clamp(r, 0, hr))
        return GridPlan(w, r, why)

    # ------------------------------------------------------------------ analysis
    def _analyse(self, prefix, base):
        trajs = branch_trajectories(prefix)
        anchors = [o.score for t in trajs.values() for o in t if o.success and o.score is not None]
        best = max(anchors + [base]) if anchors else base
        spread = best - base
        thr = self.sch["gain_tol"] * spread if spread > 0 else 0.0
        info = {}
        for b, t in trajs.items():
            run_anchor, anchor, n_succ, stall, last_gain = base, None, 0, 0, 0.0
            for o in t:
                if o.success and o.score is not None:
                    n_succ += 1
                    gain = o.score - run_anchor
                    if gain > thr:
                        stall, last_gain = 0, gain
                    else:
                        stall += 1
                    if o.score > run_anchor:
                        run_anchor = o.score
                    anchor = o.score if anchor is None else max(anchor, o.score)
                else:
                    stall += 1
            tail = []
            for o in reversed(t):
                if o.success:
                    break
                tail.append(o)
            hard = any(o.fail_class in HARD_FAIL_CLASSES for o in tail)
            info[b] = {"anchor": anchor, "n_succ": n_succ, "stall": stall, "last_gain": last_gain,
                       "fail_tail": len(tail), "hard": hard, "depth": t[-1].attempt,
                       "latest_ok": t[-1].success}
        return info, best, spread

    def _closed(self, st, best, spread):
        s = self.sch
        if st["fail_tail"]:
            if st["hard"] and (st["fail_tail"] >= 2 or st["n_succ"] == 0):
                return True                                   # hard-unrecoverable episode
            return st["fail_tail"] > s["repairs"]             # repair allowance exhausted
        if st["n_succ"] < s["evidence"]:
            return False                                      # underexplored: shallow scores can recover
        if st["stall"] >= s["patience"]:
            return True                                       # repeatedly unpromising after evidence
        if spread > 0 and st["anchor"] is not None and st["anchor"] < best - s["prune"] * spread:
            return True                                       # clearly dominated by other branches
        return False

    # -------------------------------------------------------------------- batch
    def _select_batch(self, question):
        prefix = question.observed()
        W = question.max_parallelism
        succ = [o.score for o in prefix.values() if o.success and o.score is not None]
        base = question.baseline_score
        if base is None:
            base = min(succ) if succ else 0.0
        info, best, spread = self._analyse(prefix, base)
        legal = question.legal_actions()
        roots = question.legal_roots()
        frontier = {}
        for c in legal:
            m = question.meta(c)
            if m.attempt > 0:
                frontier[m.branch] = (c, m)
        opened = len(info)
        slots_total = opened + len(roots)
        target_open = int(math.ceil(self.sch["open_frac"] * slots_total - 1e-9))
        max_open = max(1, int(math.ceil(self.sch["max_width"] * slots_total - 1e-9)))
        exploit, recover = [], []
        active = 0
        for b in sorted(info):
            st = info[b]
            if b not in frontier or self._closed(st, best, spread):
                continue
            active += 1
            cell, m = frontier[b]
            scale = spread if spread > 0 else 1.0
            anc = ((st["anchor"] - base) / scale) if st["anchor"] is not None else -0.5
            pr = (PARAMS["w_anchor"] * anc + PARAMS["w_trend"] * st["last_gain"] / scale
                  - PARAMS["w_depth"] * m.attempt)
            if st["fail_tail"]:
                recover.append((-pr, b, cell))
            else:
                exploit.append((-pr, b, cell))
        exploit.sort()
        recover.sort()
        stalled = opened > 0 and all(info[b]["stall"] >= 1 for b in info)
        n_explore = 0
        if roots and opened < max_open:
            if opened < target_open:
                n_explore = target_open - opened
            elif PARAMS["explore_on_stall"] >= 0.5 and (stalled or active == 0):
                n_explore = max(1, W - len(exploit) - min(1, len(recover)))
            n_explore = min(n_explore, max_open - opened, len(roots))
        seen_dirs = set()
        for o in prefix.values():
            if o.attempt == 0:
                d = question.meta(o.cell_id).tags.get("direction")
                if d is not None:
                    seen_dirs.add(d)
        ranked_roots = sorted(roots, key=lambda c: (question.meta(c).tags.get("direction") in seen_dirs,
                                                    question.meta(c).branch))
        batch = []
        if exploit:
            n_explore = min(n_explore, W - 1)
        batch.extend(ranked_roots[:max(0, n_explore)])
        if exploit and len(batch) < W:
            batch.append(exploit[0][2])
        if recover and len(batch) < W:
            batch.append(recover[0][2])
        for _, _, cell in exploit[1:]:
            if len(batch) >= W:
                break
            batch.append(cell)
        return batch[:W]

    # -------------------------------------------------------------------- solve
    def solve(self, question, budget=None):
        question.reset()
        res = SimResult()
        while not _budget_done(question, budget):
            batch = self._select_batch(question)
            if not batch:
                res.stopped = "portfolio stop: no eligible root, frontier or recovery candidate"
                break
            question.probe_batch(batch, on_reveal=lambda _: _record_curve(res, question))
        res.info["beta"] = self.beta
        return finalize_result(question, res)
