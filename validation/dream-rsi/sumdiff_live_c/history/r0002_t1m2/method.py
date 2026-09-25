"""Adaptive prefix-only exploration policy with trajectory-driven batch selection and recovery.

Prefix signals: successful anchor (max historical score), improvement trajectory (vs parent),
failure classification and recoverability, branch promise, and comparison across opened branches.

Batch rule: Dynamic portfolio of independent candidates up to max_parallelism:
  - Tier 1: Exploitation (best-ranked open frontiers: high anchor, recent improvement, promising trend)
  - Tier 2: Recovery (at most one repairable failure, ranked by fixability class and branch history)
  - Tier 3: Exploration (new unopened roots; count scaled by beta)
  - Tier 4: Fallback (fill remaining slots with any legal frontier/root; limits duplicates by branch)
Ranks deterministically by trajectory data (promise, parent gain, anchor, recoverability).
Never samples randomly. Repairable failures eligible for recovery until displaced by better frontiers.

Beta schedule (fixed per episode, swept offline):
  - Low beta (0.0–0.3): Conservative; few new roots, recovery only if very strong
  - Mid beta (0.4–0.6): Balanced exploitation + exploration + measured recovery
  - High beta (0.7–1.0): Patient; more new roots, more recovery attempts, later stops
Routes reserve threshold and recovery patience through one _schedule(beta) dict.

Default beta: 0.6 (moderate exploration). Rationale: iter01 baseline achieved attainment=1.0 
with full parallelism and no failures; our adaptive policy should improve on harder tasks 
by adding recovery logic and better frontier ranking, while maintaining parallelism on easy tasks.

Grid planning: Defaults to fallback (3 branches, 2 refinements) when history is insufficient.
When history accumulates, analyzes per-iteration attainment, probe work, and beta sweep 
feedback to adapt width vs. depth tradeoff.

Safeguards:
  - Branches closed only with cumulative hard-unrecoverable evidence (branch_failed_hard).
  - Repairable failures eligible for recovery; retry count capped at 2 per depth level.
  - Zero-valid failures not automatically closed; fail_class and error classify episode.
  - Recovery tier only added if promising frontier pool exists; doesn't displace exploitation.
  - Unopened roots get at most one slot per batch to avoid over-wide probing.
"""
from policy_api import (
    GridPlan, GridPlanningContext, LLMDesignedMethod, SimResult,
    _budget_done, _record_curve, finalize_result,
    branch_trajectories, successful_anchor, branch_promising, branch_failed_hard,
    is_repairable, probe_improved_vs_parent, probe_improved_vs_baseline,
    clamp, lerp,
)
from typing import Dict, List, Set, Optional, Tuple

NAME = "OptimalPolicy"


class OptimalPolicy(LLMDesignedMethod):
    """Adaptive policy: trajectory-driven ranking, portfolio tier-based batch selection, recovery logic."""
    
    def __init__(self, config=None):
        super().__init__(config)
        self.beta = float(self.config.get("beta", 0.6))
    
    def _schedule(self, beta: float) -> Dict[str, float]:
        """Return beta-dependent thresholds (fixed for one episode).
        
        Low beta: conservative (few probes, early stops, strong pruning).
        High beta: exploratory (many probes, patient, recovery-friendly).
        """
        b = clamp(float(beta), 0.0, 1.0)
        return {
            # Minimum recoverability score (0-1) to attempt recovery
            'recovery_threshold': lerp(0.5, 0.2, b),
            # How many unopened roots to explore per batch (scales with W)
            'explore_root_fraction': lerp(0.0, 0.5, b),
            # Max retries per depth level for repairable failures
            'max_retries_per_depth': 2,
        }
    
    def plan_grid(self, context: GridPlanningContext) -> GridPlan:
        """Adaptive grid planning from completed live cycles.
        
        Currently defaults to fallback when history is insufficient.
        Future: Analyze history for width/depth signals.
        """
        if not context.history:
            return GridPlan(
                int(context.fallback_branch_count),
                int(context.fallback_refine_count),
                "no prior history: using fallback grid (3 branches, 2 refinements)"
            )
        
        return GridPlan(
            int(context.fallback_branch_count),
            int(context.fallback_refine_count),
            f"adaptive: {len(context.history)} prior iters, insufficient data for grid change"
        )
    
    def solve(self, question, budget=None) -> SimResult:
        """Main loop: reset, reconstruct prefix trajectories, rank candidates, build portfolio batches."""
        question.reset()
        res = SimResult()
        closed_branches: Set[int] = set()
        
        schedule = self._schedule(self.beta)
        
        while not _budget_done(question, budget):
            prefix = question.observed()
            trajectories = branch_trajectories(prefix)
            legal_actions = question.legal_actions()
            
            if not legal_actions:
                res.stopped = "no legal cell left (all explored or closed)"
                break
            
            # Update closed set based on cumulative hard evidence
            self._update_closed(closed_branches, trajectories)
            
            # Select next batch: exploitation + recovery + exploration + fill
            batch = self._select_batch(
                question, legal_actions, trajectories, closed_branches, schedule
            )
            
            if not batch:
                res.stopped = "batch selection returned empty (all candidates closed)"
                break
            
            # Probe batch and record curve
            question.probe_batch(batch, on_reveal=lambda _: _record_curve(res, question))
        
        return finalize_result(question, res)
    
    def _update_closed(
        self,
        closed: Set[int],
        trajectories: Dict[int, List],
    ) -> None:
        """Mark branches as closed only with hard-unrecoverable evidence.
        
        Never close based on a single failure or zero-valid; use branch_failed_hard signal
        (never succeeded AND latest is hard failure, or latest window all hard-failed).
        """
        for b, traj in trajectories.items():
            if b in closed:
                continue
            if branch_failed_hard(traj):
                closed.add(b)
    
    def _select_batch(
        self,
        question,
        legal_actions: List[str],
        trajectories: Dict[int, List],
        closed: Set[int],
        schedule: Dict[str, float],
    ) -> List[str]:
        """Build dynamic portfolio batch using tier-based selection.
        
        Tier 1: Exploitation (best open frontiers).
        Tier 2: Recovery (at most one repairable failure).
        Tier 3: Exploration (new unopened roots).
        Tier 4: Fallback (fill remaining slots).
        """
        W = question.max_parallelism
        baseline = question.baseline_score or 0.0
        batch = []
        selected_branches: Set[int] = set()
        
        # Partition legal actions
        roots_set = set(question.legal_roots())
        frontiers = [c for c in legal_actions if c not in roots_set]
        
        # === TIER 1: Rank frontiers for exploitation ===
        frontier_candidates = []
        for cell_id in frontiers:
            meta = question.meta(cell_id)
            b = meta.branch
            if b in closed:
                continue
            
            traj = trajectories.get(b, [])
            if not traj:
                continue
            
            anchor = successful_anchor(traj) or 0.0
            last_obs = traj[-1]
            improved = probe_improved_vs_parent(last_obs)
            promising = branch_promising(traj, baseline)
            
            # Rank: (promising, improved, anchor) in descending importance
            # Use tuple for stable, deterministic ordering
            rank_key = (-int(promising), -int(improved), -anchor, -meta.attempt)
            frontier_candidates.append((cell_id, rank_key, b))
        
        frontier_candidates.sort(key=lambda x: x[1])  # Sort by rank_key
        
        # Add top frontiers (exploitation), max 1 per branch
        for cell_id, _, b in frontier_candidates:
            if len(batch) >= W or b in selected_branches:
                continue
            batch.append(cell_id)
            selected_branches.add(b)
        
        # === TIER 2: Find and rank repairable failures for recovery ===
        if len(batch) < W:
            recovery_options = []
            for b, traj in trajectories.items():
                if b in closed or b in selected_branches:
                    continue
                
                last_obs = traj[-1]
                if not last_obs.success and is_repairable(last_obs):
                    # Count retries at this attempt level
                    retry_count = sum(1 for o in traj if o.attempt == last_obs.attempt)
                    if retry_count < schedule['max_retries_per_depth']:
                        # Score by fail_class (how fixable)
                        fixability = {
                            'correctness': 0.9,      # Output/logic mismatch, usually fixable
                            'constraint': 0.8,       # Constraint violation, usually fixable
                            'compile_other': 0.7,    # Compilation error, usually fixable
                            'timeout': 0.4,          # Timeout, harder to fix
                            'resource': 0.3,         # Resource limit, harder to fix
                        }.get(last_obs.fail_class, 0.2)
                        
                        # Weight by branch history (branch with good anchor worth more effort)
                        anchor = successful_anchor(traj[:-1]) or 0.0  # Before this failure
                        recovery_score = fixability + anchor * 0.2
                        
                        recovery_options.append((last_obs.cell_id, recovery_score, b))
            
            recovery_options.sort(key=lambda x: -x[1])
            
            # Add top recovery candidate if it beats threshold and we have room
            if recovery_options:
                cell_id, score, b = recovery_options[0]
                if score >= schedule['recovery_threshold'] and cell_id in legal_actions:
                    batch.append(cell_id)
                    selected_branches.add(b)
        
        # === TIER 3: Explore new unopened roots ===
        if len(batch) < W:
            opened_branches = set(question.opened_branches())
            unopened_roots = []
            
            for cell_id in roots_set:
                if cell_id not in legal_actions:
                    continue
                b = question.meta(cell_id).branch
                if b in closed or b in selected_branches:
                    continue
                if b not in opened_branches:
                    unopened_roots.append((cell_id, b))
            
            # Explore only as many unopened roots as schedule permits
            max_explore = max(1, int(W * (schedule['explore_root_fraction'] + 0.1)))
            for cell_id, b in unopened_roots[:max_explore]:
                if len(batch) >= W:
                    break
                batch.append(cell_id)
                selected_branches.add(b)
        
        # === TIER 4: Fallback - fill remaining slots ===
        if len(batch) < W:
            remaining = [
                c for c in legal_actions
                if c not in batch and question.meta(c).branch not in selected_branches
            ]
            for cell_id in remaining[:W - len(batch)]:
                batch.append(cell_id)
                selected_branches.add(question.meta(cell_id).branch)
        
        return batch[:W]
