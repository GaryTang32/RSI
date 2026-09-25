"""Adaptive exploration with recovery-first portfolio and conservative closure.

Prefix signals: branch trajectories (successful anchors, recovery signals), relative improvement 
vs baseline and parent, recent failure patterns on branches with strong history. Batch rule: cold 
start opens all roots in parallel; warm start assembles portfolio with recovery-first priority 
(retry promising branches after repairable failures), then exploitation (deepen promising branches), 
then exploration (open new roots), respecting max_parallelism. Beta schedule: high beta (0.8-1.0) 
favors wide recovery, deep patience, generous recovery slots; low beta (0.0-0.2) favors exploitation, 
minimal recovery slots; default beta=0.6 balances recovery and exploitation. Grid planning: widen 
when roots improve early, deepen when refinements improve late, shrink when hard failures dominate. 

Safeguards: (1) soft-closure only closes branches that never produced success + many repairable 
failures (conservative); branches with successful anchors stay open for recovery despite recent 
failures; (2) recovery prioritized above new-root exploration in batch building, ensuring branches 
with good history get retry attempts early; (3) fallback fill ensures parallel batching and 
prevents serial probes; (4) hard-failure branches closed immediately (env/dependency issues); 
(5) no permanent starvation after repairable failures—recovery candidates are queued and executed.
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
        
        High beta (0.8-1.0): wide/patient (more recovery, deeper soft-close)
        Low beta (0.0-0.2): narrow/eager (less recovery, earlier soft-close)
        Default beta=0.6: moderate balance toward recovery
        """
        return {
            "recovery_frac": 0.15 + 0.25 * beta,  # 0.15-0.4: recovery slots per batch
            "hard_prune_depth": max(1, int(1 + 2 * beta)),  # 1-3 attempts before hard-close
            "soft_prune_depth": max(2, int(2 + 3 * beta)),  # 2-5 attempts before soft-close
        }

    def plan_grid(self, context):
        """Adapt grid size (branches and depth) from history of gain timing and failures.
        
        Widen when roots show early gains → opportunity for more parallelism.
        Deepen when refinements show late gains → refinement is productive.
        Shrink when hard failures dominate → avoid wasting probes on broken paths.
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
        
        # Widen if roots improve early (suggests more branches would help)
        if early_gain_avg > 0.5:
            branch_count = min(int(context.hard_max_branch_count), int(branch_count * 1.2))
            reason.append("widen: early gains")
        
        # Deepen if refinements improve late (suggests depth is productive)
        if late_gain_avg > 0.3:
            refine_count = min(int(context.hard_max_refine_count), int(refine_count * 1.2))
            reason.append("deepen: late gains")
        
        # Shrink if hard failures dominate (environment issues, not agent)
        if hard_fail_avg > 0.4:
            branch_count = max(1, int(branch_count * 0.8))
            reason.append("shrink: hard failures")
        
        return GridPlan(
            min(int(context.hard_max_branch_count), max(1, branch_count)),
            min(int(context.hard_max_refine_count), max(0, refine_count)),
            "; ".join(reason) if reason else "stable"
        )

    def solve(self, question, budget=None):
        """Adaptive exploration loop with recovery-first portfolio batching."""
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
                # Cold start: open all roots in parallel
                roots = question.legal_roots()
                batch = roots[:W]
            else:
                # Warm start: recovery-first portfolio batching
                trajs = branch_trajectories(prefix)
                baseline = question.baseline_score
                
                # Identify hard-failed branches (environment/dependency issues only)
                closed_branches = set()
                for bid, traj in trajs.items():
                    # Hard failure: environment/dependency errors with consistent pattern
                    if branch_failed_hard(traj, window=int(sch["hard_prune_depth"])):
                        closed_branches.add(bid)
                    # Soft closure: only branches that never succeeded AND have many repairable failures
                    elif len(traj) >= int(sch["soft_prune_depth"]):
                        has_success = any(o.success for o in traj)
                        if not has_success:
                            # Never produced a successful result
                            tail = traj[-max(1, int(sch["hard_prune_depth"])):]
                            if tail and all(is_repairable(o) for o in tail):
                                # Many repairable failures but never succeeded—likely unrecoverable
                                closed_branches.add(bid)
                
                # Categorize legal actions by potential and recovery needs
                roots_available = [c for c in legal if question.meta(c).attempt == 0]
                exploit_candidates = []  # (cell, anchor_negated, depth)
                repair_candidates = []   # (cell, anchor_negated, depth)
                
                for cell in legal:
                    m = question.meta(cell)
                    # Skip roots and hard-failed branches
                    if m.attempt == 0 or m.branch in closed_branches:
                        continue
                    
                    traj = trajs.get(m.branch)
                    if not traj:
                        continue
                    
                    anchor = successful_anchor(traj)
                    last_obs = traj[-1] if traj else None
                    
                    # Exploitation: branch is promising (good anchor, not regressing)
                    if branch_promising(traj, baseline):
                        exploit_candidates.append((
                            cell,
                            -(anchor if anchor is not None else 0.0),  # Negate for descending sort
                            len(traj)  # Depth for secondary sort
                        ))
                    # Recovery: branch has success but latest attempt is repairable failure
                    elif last_obs and is_repairable(last_obs) and anchor is not None:
                        repair_candidates.append((
                            cell,
                            -(anchor if anchor is not None else 0.0),
                            len(traj)
                        ))
                
                # Sort by anchor (high first), then by depth
                exploit_candidates.sort(key=lambda x: (x[1], x[2]))
                repair_candidates.sort(key=lambda x: (x[1], x[2]))
                
                # Build batch with recovery prioritized over exploration
                batch = []
                recovery_slots = max(0, round(W * sch["recovery_frac"]))
                exploitation_slots = W - recovery_slots
                
                # Priority 1: Recovery (retry branches with good anchors after repairable failure)
                for cell, _, _ in repair_candidates[:recovery_slots]:
                    batch.append(cell)
                
                # Priority 2: Exploitation (deepen promising branches)
                exploit_taken = len(batch)
                for cell, _, _ in exploit_candidates[:exploitation_slots - exploit_taken]:
                    batch.append(cell)
                
                # Priority 3: Exploration (open new roots to fill exploitation slots)
                roots_taken = len(batch) - recovery_slots
                for cell in roots_available[:exploitation_slots - roots_taken]:
                    batch.append(cell)
                
                # Priority 4: Fallback (fill remaining slots to maintain parallelism)
                if len(batch) < W:
                    remaining = [c for c in legal if c not in batch]
                    batch.extend(remaining[:W - len(batch)])
            
            if not batch:
                break
            
            question.probe_batch(batch, on_reveal=lambda _: _record_curve(res, question))
        
        res.stopped = "no legal cell" if not question.legal_actions() else "done"
        return finalize_result(question, res)
