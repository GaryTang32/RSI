"""Adaptive exploration with inclusive recovery, dynamic slot allocation, and beta-controlled threshold.

Prefix signals: branch trajectories (successful anchors, recovery opportunity), relative improvement 
vs baseline and parent, recent failure patterns. Batch rule: cold start opens all roots in parallel; 
warm start assembles dynamic portfolio balancing exploitation (high-success branches), recovery 
(repairable failures on branches with successful anchors—controlled by beta), and exploration 
(new roots), respecting max_parallelism. Beta schedule: high beta (0.8-1.0) favors generous 
recovery and deep patience; low beta (0.0-0.2) focuses on proven branches and minimizes recovery 
slots; default beta=0.6 balances recovery and exploitation. Grid planning: widen on early gains, 
deepen on late gains, shrink on hard failures. Safeguards: (1) recovery candidates are inclusive 
(any repairable failure on branch with successful anchor) rather than trend-gated, enabling 
recovery from transient failures; (2) beta controls recovery slot allocation and soft-closure 
depth, trading patience vs efficiency; (3) dynamic slot allocation distributes recovery/exploitation 
based on actual candidate counts, preventing starvation; (4) parallel batching maintained via 
fallback fill; (5) no premature closure of repairable branches without sufficient depth evidence; 
(6) hard failures closed immediately (env/dependency issues).
"""

from policy_api import (
    GridPlan, LLMDesignedMethod, SimResult, _budget_done, _record_curve, finalize_result,
    branch_trajectories, successful_anchor, is_repairable, branch_promising, branch_failed_hard
)

NAME = "OptimalPolicy"


class OptimalPolicy(LLMDesignedMethod):
    def __init__(self, config=None):
        super().__init__(config)
        self.beta = float(self.config.get("beta", 0.6))

    def _schedule(self, beta):
        """Adapt exploration and pruning thresholds by beta [0, 1].
        
        High beta (0.8-1.0): wider exploration, deeper patience, generous recovery.
        Low beta (0.0-0.2): focused exploitation, earlier pruning, minimal recovery.
        Default beta=0.6: balanced.
        """
        return {
            "recovery_frac": 0.1 + 0.25 * beta,          # 0.1-0.35: recovery slots per batch
            "hard_prune_depth": max(1, int(1 + 2 * beta)),  # 1-3: hard-failure threshold
            "soft_prune_depth": max(2, int(2 + 3 * beta)),  # 2-5: soft-closure depth
            "min_success_rate": 0.5 - 0.1 * beta,        # 0.4-0.5: exploit threshold
        }

    def _branch_success_rate(self, traj):
        """Fraction of successful probes in branch trajectory."""
        if not traj:
            return 0.0
        n_success = sum(1 for o in traj if o.success)
        return n_success / len(traj)

    def _branch_recent_failure_rate(self, traj, window=3):
        """Recent failure rate in sliding window."""
        if not traj:
            return 0.0
        recent = traj[-min(window, len(traj)):]
        return sum(1 for o in recent if not o.success) / len(recent)

    def plan_grid(self, context):
        """Adapt grid size (branches and depth) from history of gain timing and failures.
        
        Widen when roots show early, broad success → more parallelism opportunities.
        Deepen when refinements yield late gains → depth is productive.
        Shrink when hard failures dominate → environment issues, not agent.
        """
        if not context.history:
            return GridPlan(int(context.fallback_branch_count),
                          int(context.fallback_refine_count), "bootstrap")
        
        recent = context.history[-min(5, len(context.history)):]
        early_gain_avg = sum(m.get("gain_early", 0.0) for m in recent) / len(recent)
        late_gain_avg = sum(m.get("gain_late", 0.0) for m in recent) / len(recent)
        hard_fail_avg = sum(m.get("hard_fail_frac", 0.0) for m in recent) / len(recent)
        
        branch_count = int(context.fallback_branch_count)
        refine_count = int(context.fallback_refine_count)
        reason = []
        
        if early_gain_avg > 0.7:
            branch_count = min(int(context.hard_max_branch_count), int(branch_count * 1.15))
            reason.append("widen: strong early gains")
        
        if late_gain_avg > 0.15:
            refine_count = min(int(context.hard_max_refine_count), int(refine_count * 1.15))
            reason.append("deepen: late gains present")
        
        if hard_fail_avg > 0.3:
            branch_count = max(1, int(branch_count * 0.85))
            reason.append("shrink: hard failures")
        
        return GridPlan(
            min(int(context.hard_max_branch_count), max(1, branch_count)),
            min(int(context.hard_max_refine_count), max(0, refine_count)),
            "; ".join(reason) if reason else "stable"
        )

    def solve(self, question, budget=None):
        """Portfolio-based exploration with inclusive recovery and dynamic slot allocation."""
        question.reset()
        res = SimResult()
        sch = self._schedule(self.beta)
        W = question.max_parallelism
        
        while not _budget_done(question, budget):
            legal = question.legal_actions()
            if not legal:
                break
            
            prefix = question.observed()
            
            if not prefix:
                # Cold start: open all available roots in parallel
                roots = question.legal_roots()
                batch = roots[:W]
            else:
                # Warm start: dynamic portfolio construction with inclusive recovery
                trajs = branch_trajectories(prefix)
                baseline = question.baseline_score
                
                # Identify branches to close
                closed = set()
                
                # Hard failures (environment/dependency issues)
                for bid, traj in trajs.items():
                    if branch_failed_hard(traj, window=int(sch["hard_prune_depth"])):
                        closed.add(bid)
                
                # Soft closure: never succeeded + many repairable failures at depth
                for bid, traj in trajs.items():
                    if bid not in closed and len(traj) >= int(sch["soft_prune_depth"]):
                        has_success = any(o.success for o in traj)
                        if not has_success:
                            tail = traj[-max(1, int(sch["hard_prune_depth"])):]
                            if tail and all(is_repairable(o) for o in tail):
                                closed.add(bid)
                
                # Categorize legal actions by potential
                roots_available = [c for c in legal if question.meta(c).attempt == 0]
                exploit_candidates = []     # (cell, -sr, recent_fail_rate, -anchor)
                recovery_candidates = []    # (cell, -anchor)
                
                for cell in legal:
                    m = question.meta(cell)
                    # Skip roots and closed branches
                    if m.attempt == 0 or m.branch in closed:
                        continue
                    
                    traj = trajs.get(m.branch)
                    if not traj:
                        continue
                    
                    sr = self._branch_success_rate(traj)
                    recent_fail = self._branch_recent_failure_rate(traj)
                    anchor = successful_anchor(traj)
                    last_obs = traj[-1]
                    
                    # Exploitation: high success rate or branch is promising
                    if sr >= sch["min_success_rate"] or branch_promising(traj, baseline):
                        exploit_candidates.append((
                            cell,
                            -sr,  # Descending: prefer high success rate
                            recent_fail,  # Ascending: prefer low recent failure
                            -(anchor if anchor is not None else 0.0)
                        ))
                    # Recovery: repairable failure on branch with successful anchor
                    # (inclusive: not gated by improvement trend; beta controls slot allocation)
                    elif is_repairable(last_obs) and anchor is not None:
                        recovery_candidates.append((cell, -(anchor or 0.0)))
                
                # Sort candidates (by success rate desc, then recent fail rate asc, then anchor desc)
                exploit_candidates.sort(key=lambda x: (x[1], x[2], x[3]))
                recovery_candidates.sort(key=lambda x: (x[1],))
                
                # Build batch with dynamic slot allocation
                batch = []
                
                # Calculate recovery budget (rounded to avoid starvation)
                base_recovery_slots = round(W * sch["recovery_frac"])
                actual_recovery_slots = min(base_recovery_slots, len(recovery_candidates))
                exploit_slots_target = W - actual_recovery_slots
                
                # Priority 1: Exploit high-success branches with stable recent performance
                for cell, _, _, _ in exploit_candidates[:exploit_slots_target]:
                    batch.append(cell)
                
                # Priority 2: Explore new roots to fill exploitation target
                roots_needed = exploit_slots_target - len(batch)
                for cell in roots_available[:roots_needed]:
                    batch.append(cell)
                
                # Priority 3: Attempt recovery on branches with successful history
                # (repairable failures with good anchors, regardless of trend)
                recovery_slots = min(W - len(batch), len(recovery_candidates))
                for cell, _ in recovery_candidates[:recovery_slots]:
                    batch.append(cell)
                
                # Priority 4: Fallback fill (maintain parallelism)
                if len(batch) < W:
                    remaining = [c for c in legal if c not in batch]
                    for cell in remaining:
                        if len(batch) < W:
                            batch.append(cell)
            
            if not batch:
                break
            
            question.probe_batch(batch, on_reveal=lambda _: _record_curve(res, question))
        
        res.stopped = "no legal action" if not question.legal_actions() else "done"
        return finalize_result(question, res)
