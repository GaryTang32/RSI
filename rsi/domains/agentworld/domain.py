"""AgentWorld: harness-efficiency domain (SoL-Pi's MiniAgentWorld, spec B9.2).

The artifact is a *harness config* for the Pi-like :class:`rsi.solpi.AgentRuntime`
(``harness.json`` listing mechanisms, optional ``extensions/*.py`` code
mechanisms, ``system_prompt.md``). ``execute`` builds a fresh environment and
runtime for the task, installs the base tools and the configured extensions,
runs the agent backend and scores the final environment state with the family's
verifier. It reports:

* ``Execution.tokens``  = token traffic (cache read + cache write + output, all roles),
* ``Execution.cost_usd`` = API-equivalent cost under the backend's price table
  (cache writes priced ``rho = 12.5`` x reads),
* ``Execution.steps``   = provider requests of the main agent,
* ``meta``: per-kind token breakdown, turns, compactions, window overflows and
  each mechanism's trigger counters (activation rates, S7).

Splits: ``evolve`` = training environments (families ``repofix``, ``buildfix``,
``logtriage``); ``holdout`` = acceptance tasks of the held-out family
(``datalookup``; used only by the HoldoutFirewall); ``ood`` = final-evaluation
tasks of the held-out family; ``test`` = fresh tasks of the training families.
Backends: ``MockAgentLLM("A")`` (search backend) and ``MockAgentLLM("B")``
(second backend, other habits); any :class:`rsi.core.LLM` via :class:`LLMAgent`.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from ...core.artifact import Artifact
from ...core.domain import Domain, Execution
from ...core.llm import LLM
from ...core.tasks import Task, TaskSuite
from ...solpi.meter import PRICES, TokenMeter
from ...solpi.registry import build_extensions, harness_config
from ...solpi.runtime import AgentRuntime, builtin_tools
from .envs import FAMILIES
from .policy import LLMAgent, MockAgentLLM

BASE_SYSTEM_PROMPT = ("You are an expert coding agent working in a sandboxed repository. Use the tools to inspect "
                      "files, run commands and edit code. (Be thorough: verify every change by re-running the relevant "
                      "check before you finish.) When the task is complete, reply with a short summary and no tool "
                      "call.")
TRAIN_FAMILIES = ("repofix", "buildfix", "logtriage")
HELDOUT_FAMILIES = ("configfix", "datalookup")


class AgentWorldDomain(Domain):
    name = "agentworld"
    components = {"mechanism": ["harness.json", "extensions/*"], "prompt": ["system_prompt.md"]}
    structural_components = ("mechanism",)

    def __init__(self, *, n_train: int = 8, n_accept: int = 4, n_final: int = 8, n_test: int = 4,
                 train_families: tuple[str, ...] = TRAIN_FAMILIES, heldout_families: tuple[str, ...] = HELDOUT_FAMILIES,
                 subtasks: tuple[int, int] = (4, 8), seed: int = 0, context_window: int = 200_000,
                 max_turns: int = 80, reducer_llm: Optional[LLM] = None) -> None:
        self.seed = seed
        self.context_window = context_window
        self.max_turns = max_turns
        self.reducer_llm = reducer_llm
        import random
        rng = random.Random(f"agentworld-suite|{seed}")
        tasks, splits = [], {"evolve": [], "holdout": [], "ood": [], "test": []}

        def add(split, fam, i):
            if fam != "buildfix":
                n = rng.randint(*subtasks)
            else:                           # 3..5 files by default; small live/smoke configs cap it (>= 1)
                hi = min(5, subtasks[1])
                n = rng.randint(3, hi) if hi >= 3 else max(1, hi)
            tid = f"{split}-{fam}-{i:02d}"
            tasks.append(Task(tid, {"family": fam, "n_subtasks": n, "env_seed": rng.randint(0, 10 ** 6)}, None, fam))
            splits[split].append(tid)

        for fam in train_families:
            for i in range(n_train):
                add("evolve", fam, i)
            for i in range(n_test):
                add("test", fam, i)
        for fam in heldout_families:
            for i in range(n_accept):
                add("holdout", fam, i)
            for i in range(n_final):
                add("ood", fam, i)
        splits["smoke"] = splits["evolve"][:1]
        super().__init__(TaskSuite(tasks, splits, name=f"agentworld-s{seed}"))

    # ---- artifacts
    @staticmethod
    def seed_artifact(extensions: Optional[dict] = None) -> Artifact:
        return Artifact({"harness.json": json.dumps({"extensions": extensions or {}}, indent=1, sort_keys=True),
                         "system_prompt.md": BASE_SYSTEM_PROMPT}, meta={"name": "pi-base"})

    @staticmethod
    def harness(*names: str, **params: dict) -> Artifact:
        """Convenience: the base harness plus built-in mechanisms ``names`` (params via kwargs)."""
        return AgentWorldDomain.seed_artifact({n: params.get(n, {}) for n in names})

    def describe(self) -> str:
        return ("The artifact configures a Pi-like coding-agent harness: harness.json lists opt-in mechanisms "
                "(extensions) and their parameters; extensions/<name>.py may define new mechanisms against the "
                "runtime hook API (register tools, context projection, before_provider_request, tool_result, "
                "turn_end, agent_settled, session_compact; compact(), abort(), send_hidden()). Tasks are multi-step "
                "agent environments with large tool outputs (test runs, builds, logs, data dumps); the verifier "
                "scores the final state. Objective: lower token traffic and API cost (prefix-cached; cache writes "
                "12.5x reads) at unchanged task success, on unseen environment families and other backends.")

    # ---- execution
    def make_backend(self, llm: Optional[LLM], seed: int, task: Task):
        if llm is None:
            llm = MockAgentLLM("A")
        if hasattr(llm, "new_agent"):
            import hashlib
            h = int(hashlib.sha256(f"{seed}|{task.id}".encode()).hexdigest()[:8], 16)
            return llm.new_agent(h), PRICES[getattr(llm, "prices", "sim-a")]
        return LLMAgent(llm), PRICES["haiku"]

    def execute(self, artifact: Artifact, task: Task, *, seed: int, llm: Optional[LLM]) -> Execution:
        fam = task.input["family"]
        env = FAMILIES[fam](task.id, seed=task.input["env_seed"] + 7919 * seed, n_subtasks=task.input["n_subtasks"])
        backend, price = self.make_backend(llm, seed, task)
        meter = TokenMeter({"main": price, "compaction": price, "reducer": PRICES["reducer"]})
        cfg = harness_config(artifact.files)
        rt = AgentRuntime(backend, system_prompt=artifact.get("system_prompt.md", BASE_SYSTEM_PROMPT), meter=meter,
                          max_turns=self.max_turns, context_window=self.context_window,
                          auto_compact=bool(cfg.get("auto_compact", True)), env=env)
        for spec in builtin_tools(env):
            rt.register_tool(spec)
        for ext in build_extensions(artifact.files, {"reducer_llm": self.reducer_llm, "seed": seed}):
            rt.add_extension(ext)
        res = rt.run(env.statement())
        if res.status == "error" and "infra:" in (res.error or ""):
            # backend outage: a missing trial (not cached, retried), never a graded failure of the harness
            return Execution(error="infra:" + res.error.split("infra:", 1)[1], steps=rt.provider_requests,
                             tokens=meter.total().total_tokens, cost_usd=meter.total().cost)
        tamper = meter_tampering(rt, meter)
        if tamper:
            # the efficiency metrics are graded by this meter: a harness that swaps or patches it is a failed run
            return Execution(error=f"harness tampered with the token meter: {tamper}", steps=rt.provider_requests)
        score = env.verify() if res.status != "context_overflow" else 0.0
        tot = meter.total()
        main = meter.by_role.get("main")
        triggers = {e.name: dict(e.stats) for e in rt.extensions}
        meta: dict[str, Any] = {
            "family": fam, "status": res.status, "turns": res.turns, "requests": rt.provider_requests,
            "compactions": rt.compactions, "auto_compactions": rt.auto_compactions, "overflow": res.overflow,
            "cache_read": tot.cache_read, "cache_write": tot.cache_write, "output": tot.output, "input": tot.input,
            "main_cost": main.cost if main else 0.0, "reducer_cost": meter.by_role.get("reducer").cost
            if "reducer" in meter.by_role else 0.0, "triggers": triggers,
            "subtasks_ok": [env.subtask_ok(s) for s in env.subtasks], "n_subtasks": len(env.subtasks),
            "tool_calls": sum(len(m.tool_calls) for m in rt.history if m.role == "assistant"),
            "max_context": max((u.input for u in meter.requests if u.role == "main"), default=0),
            "oracle": oracle_stats(rt, meter), "audit": mechanism_audit(rt)}
        return Execution(output={"score": score, "status": res.status}, trace=render_trace(rt, meter),
                         tokens=tot.total_tokens, cost_usd=tot.cost, steps=rt.provider_requests, meta=meta)

    def grade(self, task: Task, execution: Execution) -> tuple[float, str]:
        out = execution.output or {}
        ok = execution.meta.get("subtasks_ok", [])
        return float(out.get("score", 0.0)), f"{sum(ok)}/{len(ok)} subtasks verified; run status {out.get('status')}"


def meter_tampering(rt: AgentRuntime, meter: TokenMeter) -> str:
    """Integrity check of the grader-side token meter after a run (code mechanisms run in-process):
    the runtime must still bill through the domain's meter, its methods must be the class's, and
    every main-agent provider request must be billed. Returns a reason or ''."""
    if rt.meter is not meter:
        return "runtime meter replaced"
    patched = [k for k in ("request", "add_output", "simple", "total", "reset_cache") if k in vars(meter)]
    if patched or type(meter) is not TokenMeter:
        return f"meter methods overridden: {patched or type(meter).__name__}"
    billed = sum(1 for u in meter.requests if u.role == "main")
    if billed != rt.provider_requests or meter.by_role.get("main") is None and rt.provider_requests:
        return f"{rt.provider_requests} provider requests but {billed} billed"
    return ""


def mechanism_audit(rt: AgentRuntime) -> dict:
    """Post-hoc audits: every accepted EPR receipt re-checked against its archived log;
    OCC decisions (saving S, compact)."""
    from ...solpi.occ import OnlineContextCompact
    from ...solpi.reducer import EvidencePreservingReducer, FAILURE_SIGNAL
    out: dict = {}
    for e in rt.extensions:
        if isinstance(e, EvidencePreservingReducer):
            n = nonverb = fail = fail_ev = 0
            for rec in e.receipts:
                body = rt.store.get(rec["archive_path"], "")
                n += 1
                nonverb += any(ev["quote"] not in body for ev in rec["evidence"])
                if rec["is_error"] and FAILURE_SIGNAL.search(body):
                    fail += 1
                    fail_ev += any(ev["kind"] in ("fatal", "failure") for ev in rec["evidence"])
            out["epr"] = {"receipts": n, "nonverbatim": nonverb, "failure_logs": fail, "failure_with_evidence": fail_ev,
                          "fallbacks": {k.split(":", 1)[1]: v for k, v in e.stats.items() if k.startswith("fallback:")},
                          "eligible": e.stats.get("eligible", 0), "bytes_saved": e.stats.get("bytes_saved", 0)}
        if isinstance(e, OnlineContextCompact):
            out["occ"] = [(d["archiveTokens"] - d["memoTokens"], d["compact"], d["reason"]) for d in e.decisions]
    return out


def oracle_stats(rt: AgentRuntime, meter: TokenMeter, keep_recent: int = 20_000) -> dict:
    """Opportunity statistics of one trajectory (SoL-Pi "Oracle Analysis"): how much
    work each kind of mechanism could avoid, measured on this (usually base) run."""
    from ...solpi.reducer import DIAGNOSTIC_COMMAND
    hist = rt.history
    acts = [m for m in hist if m.role == "assistant"]
    calls = {c.id: c for m in acts for c in m.tool_calls}
    adj = sum(1 for a, b in zip(acts, acts[1:]) if any(c.name in ("edit", "write") for c in a.tool_calls)
              and any(c.name == "bash" for c in b.tool_calls))
    later, n = [0] * len(hist), 0
    for i in range(len(hist) - 1, -1, -1):
        later[i] = n
        if hist[i].role == "assistant":
            n += 1
    replay = diag = tail = 0
    n_large = n_diag = 0
    cmds: dict[str, int] = {}
    for i, m in enumerate(hist):
        if m.role != "tool":
            continue
        c = calls.get(m.tool_call_id)
        cmd = str(c.args.get("command", "")) if c is not None else ""
        if cmd:
            cmds[cmd] = cmds.get(cmd, 0) + 1
        nb = len(m.content.encode())
        tok = m.tokens()
        if nb > 10 * 1024 and not m.is_error:
            replay += tok * max(0, later[i] - 2)
            n_large += 1
        if cmd and DIAGNOSTIC_COMMAND.search(cmd) and nb >= 4096:
            diag += tok * later[i]
            n_diag += 1
        if cmd and nb > 10 * 1024:
            kept = sum(len(l) for l in m.content.splitlines()[-40:]) // 4
            tail += max(0, tok - kept) * later[i]
    sys_tok = (len(rt.system_prompt) + 3) // 4
    main = [u for u in meter.requests if u.role == "main"]
    archivable = sum(max(0, u.input - sys_tok - keep_recent) for u in main)
    return {"transitions": max(0, len(acts) - 1), "adjacent_edit_command": adj, "replayed_large_outputs": replay,
            "diagnostic_log_tokens": diag, "tail_of_outputs": tail, "archivable_context": archivable,
            "prompt_tokens": sys_tok * len(main), "input_tokens": sum(u.input for u in main),
            "repeated_commands": sum(v - 1 for v in cmds.values() if v > 1), "n_large_outputs": n_large,
            "n_diag_outputs": n_diag}


def render_trace(rt: AgentRuntime, meter: TokenMeter, max_chars: int = 400) -> str:
    """Compact per-turn trace: request tokens (read/write), tool calls and result sizes."""
    lines = []
    reqs = [u for u in meter.requests if u.role == "main"]
    k = 0
    for m in rt.history:
        if m.role == "assistant":
            u = reqs[k] if k < len(reqs) else None
            k += 1
            calls = "; ".join(f"{c.name}({json.dumps(c.args)[:160]})" for c in m.tool_calls) or "<final>"
            lines.append(f"[turn {k}] in={u.input if u else '?'} read={u.cache_read if u else '?'} "
                         f"write={u.cache_write if u else '?'} out={u.output if u else '?'} :: {m.content[:200]!r} -> "
                         f"{calls}")
        elif m.role == "tool":
            lines.append(f"    <- {m.tool_name}{' ERROR' if m.is_error else ''} {len(m.content)} chars: "
                         f"{m.content[:max_chars]!r}")
        elif m.details.get("compaction"):
            lines.append(f"[compaction] summary {len(m.content)} chars")
    return "\n".join(lines)


def make_domain(seed: int = 0, **kw) -> AgentWorldDomain:
    return AgentWorldDomain(seed=seed, **kw)
