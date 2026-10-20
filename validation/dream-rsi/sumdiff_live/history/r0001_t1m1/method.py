"""Adaptive exploration with trajectory-guided batching and beta-controlled thresholds.

Prefix signals: branch trajectories (successful anchors, failure patterns, recoverability),
relative improvement vs baseline and parent. Batch rule: open roots initially, then rank
frontiers by promise (anchor strength, recent success trend) and recovery potential (repairable
failures with upside); assemble portfolio balancing exploitation (strong frontiers), exploration
(new roots), recovery (fixable failures); respect max_parallelism. Beta schedule: high beta
favors wide exploration, deep patience, recovery attempts; low beta favors exploitation,
aggressive pruning, few recovery slots. Grid planning: widen when roots improve early (many
branches show early success), deepen when refinements improve late (better scores at depth >1),
shrink when hard failures dominate. Safeguards: never close repairable branches without depth
evidence, resume branches after successful re-attempt, maintain parallel batching (rare serial
probes), stop only after confirming legal portfolio exhaustion.
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
        Default beta=0.6: moderate balance
        """
        return {
            "recovery_frac": 0.1 + 0.2 * beta,  # 0.1-0.3: recovery slots per batch
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
        """Adaptive exploration loop with trajectory-guided selection."""
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
                # Cold start: open roots in parallel (exploit initial parallelism)
                roots = question.legal_roots()
                batch = roots[:W]
            else:
                # Warm start: trajectory-guided adaptive selection
                trajs = branch_trajectories(prefix)
                baseline = question.baseline_score
                
                # Identify closed branches (hard evidence of failure)
                closed_branches = set()
                for bid, traj in trajs.items():
                    # Hard failure (environment/dependency) with shallow evidence
                    if branch_failed_hard(traj, window=int(sch["hard_prune_depth"])):
                        closed_branches.add(bid)
                    # Soft closure: not promising and consistent repairable failures at depth
                    elif len(traj) >= int(sch["soft_prune_depth"]):
                        if not branch_promising(traj, baseline):
                            tail = traj[-max(1, int(sch["hard_prune_depth"])):]
                            if tail and all(is_repairable(o) for o in tail):
                                closed_branches.add(bid)
                
                # Categorize legal actions by potential
                roots_available = [c for c in legal if question.meta(c).attempt == 0]
                exploit_candidates = []  # promising frontiers (successful anchor)
                repair_candidates = []   # repairable failures (might recover)
                
                for cell in legal:
                    m = question.meta(cell)
                    # Skip roots and closed branches
                    if m.attempt == 0 or m.branch in closed_branches:
                        continue
                    
                    traj = trajs.get(m.branch)
                    if not traj:
                        continue
                    
                    anchor = successful_anchor(traj)
                    
                    # Exploitation: branch has a successful anchor and recent improvement
                    if branch_promising(traj, baseline):
                        exploit_candidates.append((cell, -(anchor if anchor is not None else 0.0)))
                    # Recovery: repairable failure, might fix on next attempt
                    elif is_repairable(traj[-1]):
                        repair_candidates.append((cell, -(anchor if anchor is not None else 0.0)))
                
                # Sort by anchor (best first)
                exploit_candidates.sort(key=lambda x: x[1])
                repair_candidates.sort(key=lambda x: x[1])
                
                # Build batch respecting recovery slot budget
                batch = []
                recovery_slots = max(0, int(W * sch["recovery_frac"]))
                exploitation_slots = W - recovery_slots
                
                # Priority 1: Exploit (continue promising branches)
                for cell, _ in exploit_candidates:
                    if len(batch) < exploitation_slots:
                        batch.append(cell)
                
                # Priority 2: Explore (open new roots)
                for cell in roots_available:
                    if len(batch) < exploitation_slots:
                        batch.append(cell)
                
                # Priority 3: Repair (attempt recovery)
                for cell, _ in repair_candidates:
                    if len(batch) < W:
                        batch.append(cell)
                
                # Fallback: fill remaining with any legal cell (rare)
                if len(batch) < W:
                    for cell in legal:
                        if cell not in batch:
                            batch.append(cell)
                        if len(batch) >= W:
                            break
            
            if not batch:
                break
            
            question.probe_batch(batch, on_reveal=lambda _: _record_curve(res, question))
        
        res.stopped = "no legal cell" if not question.legal_actions() else "done"
        return finalize_result(question, res)
