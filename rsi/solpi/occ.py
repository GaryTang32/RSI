"""Online Context Compact (OCC): compact at subtask boundaries only when it pays.

Port of ``src/sol-pi/extensions/online-context-compact/{economics,state,plan,tools,extension}.ts``
(spec B3.4 / B4.4). The agent maintains a plan with ``update_plan``; a newly
completed step is a *boundary*. At ``turn_end`` after a clean boundary call,
:func:`decide_compaction` prices the compaction:

* ``W`` = context tokens, ``F`` = system tokens, ``K`` = keep-recent tokens (20,000),
  ``A = max(0, W - F - K)`` archived, ``m`` = 1,000 summary tokens, ``S = A - m``;
* horizon: ``mu`` = mean requests per completed boundary, ``R_unb = 1 + floor(L * B_rem * scale)``,
  ``R_win = max(0, floor((W_win - C) / mean_delta))``, ``R = min(R_unb, R_win)``;
* break-even ``b = W * max(0, rho - 1) / S``, with carried debt ``b_c = (D + W*rho')/S``;
* first compaction: economic iff ``R_eff = min(2R, R_win) > 0 and b <= R_eff``; later:
  ``R > 0 and b <= R and 1.5 b <= R and b_c <= R``; window protection iff
  ``C >= W_win - 16,384``; compact iff ``S > 0 and (window_protection or economic)``.

If it compacts, the run is aborted, native compaction runs with
:data:`BOUNDARY_COMPACTION_INSTRUCTIONS` at ``agent_settled``, and the hidden
:data:`POST_COMPACTION_PLAN_REMINDER` restarts the agent. The debt
``D = W * rho'`` is repaid ``A - m`` tokens per later request.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Optional

from .runtime import AgentRuntime, Extension, Message, ToolError, ToolResult, ToolSpec

DEFAULT_KEEP_RECENT_TOKENS = 20_000
DEFAULT_NATIVE_SUMMARY_TOKEN_ESTIMATE = 1_000
MINIMUM_VARIANCE_SAMPLES = 3
SMALL_SAMPLE_SCALE = 0.5
BOUNDARY_COMPACTION_INSTRUCTIONS = ("Preserve completed work, verification results, important decisions, and "
                                    "remaining work.")
POST_COMPACTION_PLAN_REMINDER = ("Online context compaction finished. The parent task is still active. "
                                 "Before continuing work, call update_plan with a fresh plan for the remaining work.")
ONLINE_STATE_ENTRY = "sol-pi-online-context-state-v1"
MAX_PLAN_STEPS = 128
MAX_STRING_BYTES = 16_384


@dataclass(frozen=True)
class CompactionEconomics:
    remaining_request_scale: float = 1
    remaining_request_stddev_k: float = 0
    window_reserve_tokens: int = 16_384
    first_compaction_request_scale: float = 2
    subsequent_compaction_margin: float = 1.5


DEFAULT_COMPACTION_ECONOMICS = CompactionEconomics()


def estimate_remaining_requests(*, completed_boundary_request_counts, remaining_boundaries, scale, standard_deviation_k,
                                context_tokens, context_window_tokens, average_context_token_increment) -> dict:
    c = list(completed_boundary_request_counts)
    mean = sum(c) / max(1, len(c))
    lower = mean
    if standard_deviation_k != 0:
        if len(c) < MINIMUM_VARIANCE_SAMPLES:
            lower *= SMALL_SAMPLE_SCALE
        else:
            var = sum((x - mean) ** 2 for x in c)
            dev = math.sqrt(var / (len(c) - 1))
            lower = max(0.0, mean - standard_deviation_k * dev)
    unbounded = 1 + math.floor(lower * max(0, remaining_boundaries) * scale)
    win = None
    if context_window_tokens is not None and average_context_token_increment is not None and \
            average_context_token_increment > 0:
        win = max(0, math.floor((context_window_tokens - context_tokens) / average_context_token_increment))
    return {"completedBoundaryRequestCounts": c, "requestsPerBoundaryMean": mean,
            "requestsPerBoundaryLowerBound": lower, "unboundedExpectedRemainingRequests": unbounded,
            "averageContextTokenIncrement": average_context_token_increment, "windowRequestUpperBound": win,
            "expectedRemainingRequests": unbounded if win is None else min(unbounded, win)}


def decide_compaction(*, write_tokens, archive_tokens, memo_tokens, context_tokens, completed_boundary_request_counts,
                      remaining_boundaries, average_context_token_increment, context_window_tokens,
                      prior_compaction_count, carried_debt_tokens, cache_debt_repayment_tokens, cache_write_read_ratio,
                      economics: CompactionEconomics = DEFAULT_COMPACTION_ECONOMICS) -> dict:
    """Exact port of ``economics.ts:decideCompaction`` (returns every intermediate)."""
    horizon = None
    if completed_boundary_request_counts is not None:
        horizon = estimate_remaining_requests(
            completed_boundary_request_counts=completed_boundary_request_counts,
            remaining_boundaries=remaining_boundaries, scale=economics.remaining_request_scale,
            standard_deviation_k=economics.remaining_request_stddev_k, context_tokens=context_tokens,
            context_window_tokens=context_window_tokens,
            average_context_token_increment=average_context_token_increment)
    saving = archive_tokens - memo_tokens
    incr = None if cache_write_read_ratio is None else max(0.0, cache_write_read_ratio - 1)
    breakeven = (write_tokens * incr) / saving if saving > 0 and incr is not None else None
    combined = (carried_debt_tokens + write_tokens * incr) / saving if saving > 0 and incr is not None else None
    first = prior_compaction_count == 0
    if horizon is None:
        eff = None
    elif first:
        wub = horizon["windowRequestUpperBound"]
        eff = min(horizon["expectedRemainingRequests"] * economics.first_compaction_request_scale,
                  wub if wub is not None else math.inf)
    else:
        eff = horizon["expectedRemainingRequests"]
    window_protection = context_window_tokens is not None and \
        context_tokens >= context_window_tokens - economics.window_reserve_tokens
    R = horizon["expectedRemainingRequests"] if horizon else None
    base_economic = horizon is not None and R > 0 and breakeven is not None and breakeven <= R
    first_economic = first and eff is not None and eff > 0 and breakeven is not None and breakeven <= eff
    margin_open = (not first) and horizon is not None and breakeven is not None and \
        breakeven * economics.subsequent_compaction_margin <= R
    debt_open = (not first) and horizon is not None and combined is not None and combined <= R
    economic = first_economic if first else (base_economic and margin_open and debt_open)
    compressible = saving > 0
    compact = compressible and (window_protection or economic)
    if not compressible:
        reason = "non_positive_saving"
    elif window_protection:
        reason = "window_protection"
    elif economic:
        reason = "economic"
    elif horizon is None:
        reason = "horizon_unavailable"
    elif breakeven is None:
        reason = "cache_ratio_unavailable"
    elif not first and base_economic and not margin_open:
        reason = "deferred_subsequent_margin"
    elif not first and base_economic and not debt_open:
        reason = "deferred_carried_debt"
    else:
        reason = "deferred_economic"
    hz = horizon or {"completedBoundaryRequestCounts": None, "requestsPerBoundaryMean": None,
                     "requestsPerBoundaryLowerBound": None, "unboundedExpectedRemainingRequests": None,
                     "averageContextTokenIncrement": average_context_token_increment,
                     "windowRequestUpperBound": None, "expectedRemainingRequests": None}
    return {"writeTokens": write_tokens, "archiveTokens": archive_tokens, "memoTokens": memo_tokens,
            "contextTokens": context_tokens, **hz, "breakevenRequests": breakeven,
            "combinedBreakevenRequests": combined, "effectiveHorizonRequests": eff,
            "cacheWriteReadRatio": cache_write_read_ratio, "incrementalCacheCostRatio": incr,
            "priorCompactionCount": prior_compaction_count, "carriedDebtTokens": carried_debt_tokens,
            "cacheDebtRepaymentTokens": cache_debt_repayment_tokens, "compact": compact, "reason": reason}


# ------------------------------------------------------------------ state (state.ts)
@dataclass(frozen=True)
class OnlineState:
    version: int = 1
    epoch: int = 0
    plan: tuple = ()
    pending_progress: tuple = ()
    request_count: int = 0
    last_boundary_request_count: int = 0
    completed_boundary_request_counts: tuple = ()
    last_context_tokens: Optional[int] = None
    positive_context_delta_total: float = 0.0
    positive_context_delta_count: int = 0
    native_compaction_count: int = 0
    cache_debt_tokens: float = 0.0
    cache_debt_repayment_tokens: float = 0.0


def record_provider_request(s: OnlineState, context_tokens: int) -> OnlineState:
    delta = 0 if s.last_context_tokens is None else context_tokens - s.last_context_tokens
    debt = max(0.0, s.cache_debt_tokens - s.cache_debt_repayment_tokens)
    return replace(s, request_count=s.request_count + 1, last_context_tokens=context_tokens,
                   positive_context_delta_total=s.positive_context_delta_total + max(0, delta),
                   positive_context_delta_count=s.positive_context_delta_count + (1 if delta > 0 else 0),
                   cache_debt_tokens=debt, cache_debt_repayment_tokens=0 if debt == 0 else s.cache_debt_repayment_tokens)


def record_boundary(s: OnlineState, plan, progress: Optional[dict]) -> OnlineState:
    interval = max(0, s.request_count - s.last_boundary_request_count)
    return replace(s, plan=tuple(plan), pending_progress=s.pending_progress + ((progress,) if progress else ()),
                   last_boundary_request_count=s.request_count,
                   completed_boundary_request_counts=s.completed_boundary_request_counts + (interval,))


def record_compaction(s: OnlineState, debt_tokens: float, repayment_tokens: float) -> OnlineState:
    return replace(s, epoch=s.epoch + 1, plan=(), pending_progress=(), last_context_tokens=None,
                   positive_context_delta_total=0.0, positive_context_delta_count=0,
                   native_compaction_count=s.native_compaction_count + 1, cache_debt_tokens=max(0.0, debt_tokens),
                   cache_debt_repayment_tokens=max(0.0, repayment_tokens))


def record_correction(s: OnlineState) -> OnlineState:
    return replace(s, epoch=s.epoch + 1, plan=(), pending_progress=(), last_boundary_request_count=s.request_count,
                   completed_boundary_request_counts=(), last_context_tokens=None, positive_context_delta_total=0.0,
                   positive_context_delta_count=0, cache_debt_tokens=0.0, cache_debt_repayment_tokens=0.0)


# ------------------------------------------------------------------ plan (plan.ts / tools.ts)
def parse_plan_steps(value: Any) -> Optional[tuple]:
    if not isinstance(value, list) or len(value) > MAX_PLAN_STEPS:
        return None
    out, ids = [], set()
    for s in value:
        if not isinstance(s, dict):
            return None
        sid, goal, status = s.get("id"), s.get("goal"), s.get("status")
        if not isinstance(sid, str) or not isinstance(goal, str) or status not in ("pending", "in_progress",
                                                                                      "completed"):
            return None
        if len(sid.encode()) > MAX_STRING_BYTES or len(goal.encode()) > MAX_STRING_BYTES or sid in ids:
            return None
        ids.add(sid)
        out.append({"id": sid, "goal": goal, "status": status})
    return tuple(out)


def analyze_plan_transition(prev, steps) -> tuple[list[dict], list[str]]:
    before = {s["id"]: s["status"] for s in prev}
    completed = [s for s in steps if s["status"] == "completed" and before.get(s["id"]) != "completed"]
    advice = []
    if sum(s["status"] == "in_progress" for s in steps) > 1:
        advice.append("Keep at most one step in_progress.")
    return completed, advice


def format_plan_snapshot(steps) -> str:
    return f'<sol-pi-plan task_status="active">{json.dumps(list(steps))}</sol-pi-plan>'


def _progress(args: dict, step_id: str) -> Optional[dict]:
    p = args.get("progress")
    step = next((s for s in args.get("steps", []) if isinstance(s, dict) and s.get("id") == step_id), None)
    if not isinstance(p, dict) or step is None:
        return None
    return {"stepId": step_id, "goal": step.get("goal", ""), "filesChanged": list(p.get("files_changed", []))[:128],
            "verification": list(p.get("verification", []))[:64], "decisions": list(p.get("decisions", []))[:64],
            "nextWork": [s.get("goal", "") for s in args.get("steps", []) if s.get("status") != "completed"]}


class OnlineContextCompact(Extension):
    name = "online_context_compact"

    def __init__(self, cache_write_read_ratio: Optional[float] = 12.5, keep_recent_tokens: int =
                 DEFAULT_KEEP_RECENT_TOKENS, economics: CompactionEconomics = DEFAULT_COMPACTION_ECONOMICS,
                 memo_tokens: int = DEFAULT_NATIVE_SUMMARY_TOKEN_ESTIMATE, policy: str = "economic") -> None:
        """``policy="economic"`` is SoL-Pi; ``"always"`` compacts at every clean boundary
        where native compaction has something to summarise (the S4 comparison arm)."""
        super().__init__()
        self.policy = policy
        if cache_write_read_ratio is not None and (not math.isfinite(cache_write_read_ratio) or
                                                   cache_write_read_ratio < 0):
            raise ValueError("cacheWriteReadRatio must be finite and non-negative")
        if not isinstance(keep_recent_tokens, int) or keep_recent_tokens < 1:
            raise ValueError("keepRecentTokens must be a positive integer")
        self.rho = cache_write_read_ratio
        self.keep_recent = keep_recent_tokens
        self.economics = economics
        self.memo = memo_tokens
        self.state = OnlineState()
        self.observed: list[Message] = []
        self.pending_boundary: Optional[str] = None
        self.selected: Optional[dict] = None
        self.active_debt: Optional[tuple[float, float]] = None
        self.decisions: list[dict] = []

    def register(self, rt: AgentRuntime) -> None:
        rt.keep_recent_tokens = self.keep_recent
        rt.register_tool(ToolSpec(
            "update_plan",
            "Replace the complete working plan. A newly completed step becomes a safe point where SoL-Pi may compact "
            "context if doing so is economical. Send the complete plan on every update_plan call; keep at most one "
            "step in_progress and mark finished steps completed; when completing a step, include concise progress "
            "evidence when available.",
            {"steps": "[{id, goal, status: pending|in_progress|completed}]",
             "progress": "{files_changed, verification, decisions} (optional)"}, self._update_plan))
        rt.on("session_start", lambda r: self._restore())
        rt.on("context", self._context)
        rt.on("before_provider_request", self._before_request)
        rt.on("input", self._input)
        rt.on("turn_end", self._turn_end)
        rt.on("agent_settled", self._settled)
        rt.on("session_compact", self._session_compact)

    def _save(self, rt: AgentRuntime) -> None:
        rt.append_entry(ONLINE_STATE_ENTRY, asdict(self.state))

    def _restore(self) -> None:
        self.pending_boundary = self.selected = self.active_debt = None

    def _context(self, msgs: list[Message], rt: AgentRuntime) -> None:
        self.observed = list(msgs)
        return None

    def context_tokens(self, rt: AgentRuntime) -> int:
        sys_tokens = math.ceil(len(rt.system_prompt.encode()) / 4)
        visible = sum(m.tokens() for m in self.observed if m.role != "system")
        return int(visible + sys_tokens)

    def _before_request(self, msgs: list[Message], rt: AgentRuntime) -> None:
        self.state = record_provider_request(self.state, self.context_tokens(rt))
        self._save(rt)

    def _input(self, event: dict, rt: AgentRuntime) -> dict:
        if event.get("streaming_behavior") != "steer" and not str(event.get("text", "")).startswith("CORRECTION:"):
            return {"action": "continue"}
        self.pending_boundary = self.selected = self.active_debt = None
        self.state = record_correction(self.state)
        self._save(rt)
        return {"action": "continue"}

    def _update_plan(self, args: dict, rt: AgentRuntime, cid: str) -> ToolResult:
        steps = parse_plan_steps(args.get("steps"))
        if not steps:
            raise ToolError("Plan must contain at least one valid step")
        completed, advice = analyze_plan_transition(self.state.plan, steps)
        ids = [s["id"] for s in completed]
        if ids:
            self.state = record_boundary(self.state, steps, _progress(args, ids[0]))
            if self.pending_boundary is None:
                self.pending_boundary = cid
            self.stats["boundaries"] += 1
        elif list(self.state.plan) != list(steps):
            self.state = replace(self.state, plan=steps)
        self._save(rt)
        return ToolResult("\n".join([format_plan_snapshot(steps)] + advice),
                          details={"boundary": bool(ids), "completed_step_ids": ids, "task_status": "active"})

    def _turn_end(self, reply: Message, results: list[Message], rt: AgentRuntime) -> None:
        boundary, self.pending_boundary = self.pending_boundary, None
        if boundary is None or self.selected is not None:
            return
        tr = next((m for m in results if m.tool_call_id == boundary), None)
        if reply.role != "assistant" or tr is None or tr.is_error:
            return
        W = self.context_tokens(rt)
        F = math.ceil(len(rt.system_prompt.encode()) / 4)
        A = max(0, W - F - self.keep_recent)
        s = self.state
        mean_delta = None if s.positive_context_delta_count == 0 else \
            s.positive_context_delta_total / s.positive_context_delta_count
        d = decide_compaction(write_tokens=W, archive_tokens=A, memo_tokens=self.memo, context_tokens=W,
                              completed_boundary_request_counts=list(s.completed_boundary_request_counts),
                              remaining_boundaries=sum(1 for st in s.plan if st["status"] != "completed"),
                              average_context_token_increment=mean_delta, context_window_tokens=rt.context_window,
                              prior_compaction_count=s.native_compaction_count, carried_debt_tokens=s.cache_debt_tokens,
                              cache_debt_repayment_tokens=s.cache_debt_repayment_tokens,
                              cache_write_read_ratio=self.rho, economics=self.economics)
        if self.policy == "always":
            d = {**d, "compact": A - self.memo > 0, "reason": "always" if A - self.memo > 0 else
                 "non_positive_saving"}
        if d["compact"] and not rt.compactable():
            d = {**d, "compact": False, "reason": "native_not_compactable"}
        self.decisions.append({**d, "turn": rt.turns})
        self.stats[f"decision:{d['reason']}"] += 1
        if d["compact"]:
            self.selected = d
            rt.abort()

    def _settled(self, rt: AgentRuntime) -> None:
        pending, self.selected = self.selected, None
        if pending is None:
            return
        self.active_debt = (pending["writeTokens"] * (pending["incrementalCacheCostRatio"] or 0),
                            max(0, pending["archiveTokens"] - pending["memoTokens"]))
        summary = rt.compact(BOUNDARY_COMPACTION_INSTRUCTIONS)
        if summary is not None:
            self.stats["compactions"] += 1
            rt.send_hidden(POST_COMPACTION_PLAN_REMINDER, trigger_turn=True)
        self.active_debt = None if summary is None else self.active_debt

    def _session_compact(self, event: dict, rt: AgentRuntime) -> None:
        debt = (0.0, 0.0) if event.get("from_extension") or self.active_debt is None else self.active_debt
        self.state = record_compaction(self.state, *debt)
        self.active_debt = None
        self._save(rt)
