"""S1 - Action Fusion removes the decision-free model turn between an edit and its check.

Claims [blog; code; sec:pengqian]: fusing an edit/write with its follow-up command saves one model round
trip ("from 3 API calls to 2"); the oracle analysis found adjacent edit->command transitions in 12.3% of
cross-turn transitions; success is unchanged; safety: an interleaved write skips the command
(``[then_run:skipped]``), a failed mutation never runs the command, a non-zero exit keeps the edit.

Per (seed, backend): base harness vs +ActionFusion on training + held-out families. Reports measured
reductions in requests / tokens / cost, the oracle-predicted request reduction (adjacent edit->command
transitions per task / requests), success change (paired over tasks), fusion uptake, and scripted safety
checks.

    python experiments/metaharness-solpi/s1_action_fusion.py [--llm sim|claude:haiku] [--seeds N] [--quick]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _agentworld import agg, backend, domain, rel, row, trials_of  # noqa: E402
from _common import fmt, paired, parse_args, pool_map, save, summarize, table  # noqa: E402

import numpy as np  # noqa: E402

from rsi.solpi import (THEN_RUN_FAILED, THEN_RUN_SKIPPED, THEN_RUN_SUCCEEDED, ActionFusion, AgentRuntime,  # noqa
                       Message, PRICES, TokenMeter, ToolCall, builtin_tools)
from rsi.solpi.runtime import ToolResult  # noqa: E402

ARGS = None


def job(spec):
    seed, prof = spec
    dom = domain(ARGS, seed, n_train=4 if ARGS.quick else 8, n_final=4 if ARGS.quick else 8)
    llm = backend(ARGS, prof)
    base = [row(t) for t in trials_of(dom, llm, dom.seed_artifact())]
    af = [row(t) for t in trials_of(dom, llm, dom.harness("action_fusion"))]
    adj = [r["oracle"].get("adjacent_edit_command", 0) for r in base]
    trans = [r["oracle"].get("transitions", 0) for r in base]
    fused = [r["triggers"].get("action_fusion", {}).get("fused", 0) for r in af]
    return {"seed": seed, "backend": prof, "base": agg(base), "af": agg(af),
            "paired_score": [(b["score"], a["score"]) for b, a in zip(base, af)],
            "paired_requests": [(b["requests"], a["requests"]) for b, a in zip(base, af)],
            "oracle_adjacent_share": float(np.sum(adj) / max(1, np.sum(trans))),
            "oracle_predicted_request_reduction": float(np.sum(adj) / max(1, sum(r["requests"] for r in base))),
            "measured_request_reduction": -rel(agg(base), agg(af), "requests"),
            "token_reduction": -rel(agg(base), agg(af), "tokens"), "cost_reduction": -rel(agg(base), agg(af), "cost"),
            "fused_calls_per_task": float(np.mean(fused)),
            "share_tasks_triggered": float(np.mean([f > 0 for f in fused])),
            "by_family": {f: {"base": agg([r for r in base if r["family"] == f]),
                              "af": agg([r for r in af if r["family"] == f])} for f in sorted({r["family"] for r in base})}}


class _Env:
    def __init__(self):
        self.files = {"a.py": "X = 1\n"}
        self.ran = []

    def read_file(self, p):
        return self.files.get(p)

    def tool_read(self, p, rt=None):
        return ToolResult(self.files.get(p, ""))

    def tool_write(self, p, c, rt=None):
        self.files[p] = c
        return ToolResult("ok")

    def tool_edit(self, p, o, n, rt=None):
        if self.files.get(p, "").count(o) != 1:
            return ToolResult("Could not find the exact text", True)
        self.files[p] = self.files[p].replace(o, n)
        return ToolResult("edited")

    def tool_bash(self, c, rt=None):
        self.ran.append(c)
        return ToolResult("exit 1" if "fail" in c else "ok", "fail" in c)


class _Script:
    name = "script"

    def __init__(self, steps):
        self.steps = list(steps)

    def act(self, msgs, tools, rt):
        if not self.steps:
            return Message("assistant", "done")
        n, a = self.steps.pop(0)
        return Message("assistant", "", tool_calls=(ToolCall(rt.next_call_id(), n, a),))


def safety_checks() -> dict:
    out = {}
    for name, steps, hook in [
        ("interleaved_write_skips", [("edit", {"path": "a.py", "old": "X = 1", "new": "X = 2",
                                               "then_run": {"command": "pytest"}})],
         lambda rt, p: rt.env.files.__setitem__(p, "X = 7\n")),
        ("failed_mutation_not_run", [("edit", {"path": "a.py", "old": "NOPE", "new": "Y",
                                               "then_run": {"command": "pytest"}})], None),
        ("nonzero_exit_keeps_edit", [("edit", {"path": "a.py", "old": "X = 1", "new": "X = 3",
                                               "then_run": {"command": "pytest fail"}})], None),
        ("success", [("edit", {"path": "a.py", "old": "X = 1", "new": "X = 4", "then_run": {"command": "pytest"}})],
         None)]:
        env = _Env()
        rt = AgentRuntime(_Script(steps), system_prompt="s", meter=TokenMeter({"main": PRICES["sim-a"]}), env=env)
        for s in builtin_tools(env):
            rt.register_tool(s)
        rt.add_extension(ActionFusion(yield_hook=hook))
        rt.run("t")
        res = [m for m in rt.history if m.role == "tool"][0]
        out[name] = {"marker": next((mk for mk in (THEN_RUN_SUCCEEDED, THEN_RUN_FAILED, THEN_RUN_SKIPPED)
                                     if mk in res.content), None), "command_ran": bool(env.ran),
                     "file": env.files["a.py"].strip(), "is_error": res.is_error}
    ok = (out["interleaved_write_skips"]["marker"] == THEN_RUN_SKIPPED and not out["interleaved_write_skips"][
        "command_ran"] and out["failed_mutation_not_run"]["marker"] == THEN_RUN_SKIPPED and not out[
        "failed_mutation_not_run"]["command_ran"] and out["nonzero_exit_keeps_edit"]["file"] == "X = 3" and out[
        "nonzero_exit_keeps_edit"]["marker"] == THEN_RUN_FAILED and out["success"]["marker"] == THEN_RUN_SUCCEEDED)
    out["all_pass"] = ok
    return out


def main():
    global ARGS
    ARGS = parse_args(__doc__.splitlines()[0], default_seeds=5)
    profs = ["A"] if ARGS.live else ["A", "B"]
    rows = pool_map(job, [(s, p) for s in range(ARGS.seeds) for p in profs], ARGS.workers)
    summ = {}
    for p in profs:
        rs = [r for r in rows if r["backend"] == p]
        sc = [x for r in rs for x in r["paired_score"]]
        summ[p] = {k: summarize([r[k] for r in rs]) for k in
                   ("oracle_adjacent_share", "oracle_predicted_request_reduction", "measured_request_reduction",
                    "token_reduction", "cost_reduction", "fused_calls_per_task", "share_tasks_triggered")}
        summ[p]["success_change"] = paired([a for a, _ in sc], [b for _, b in sc])
    safety = safety_checks()
    a = summ["A"]
    close = abs(a["measured_request_reduction"]["mean"] - a["oracle_predicted_request_reduction"]["mean"]) <= \
        max(0.05, 0.5 * a["oracle_predicted_request_reduction"]["mean"])
    unchanged = summ["A"]["success_change"].get("lo", -1) <= 0 <= summ["A"]["success_change"].get("hi", 1) or \
        summ["A"]["success_change"].get("mean_diff", -1) >= -0.01
    fewer = a["measured_request_reduction"]["lo"] > 0 and a["token_reduction"]["lo"] > 0
    verdict = ("REPRODUCED" if fewer and unchanged and safety["all_pass"] else "PARTIAL") + \
        f": requests -{100 * a['measured_request_reduction']['mean']:.1f}% (oracle-predicted " \
        f"-{100 * a['oracle_predicted_request_reduction']['mean']:.1f}%{' - close' if close else ''}), tokens " \
        f"-{100 * a['token_reduction']['mean']:.1f}%, cost -{100 * a['cost_reduction']['mean']:.1f}%, success change " \
        f"{summ['A']['success_change'].get('mean_diff', float('nan')):+.3f}; safety checks " \
        f"{'pass' if safety['all_pass'] else 'FAIL'}" + \
        (f"; backend B uptake {summ['B']['fused_calls_per_task']['mean']:.1f} vs A " \
         f"{a['fused_calls_per_task']['mean']:.1f} fused calls/task" if "B" in summ else "")
    print(table([[p] + [fmt(summ[p][k], 3) for k in ("oracle_adjacent_share", "measured_request_reduction",
                                                     "token_reduction", "cost_reduction", "fused_calls_per_task")]
                 for p in profs], ["backend", "oracle adj share", "requests -%", "tokens -%", "cost -%",
                                   "fused/task"]))
    print("verdict:", verdict)
    save("s1_action_fusion" + ("_live" if ARGS.live else ""), {
        "claim": "Action Fusion removes the decision-free turn between an edit and its check; success unchanged; "
                 "hash guard + skip semantics [SoL-Pi code/blog]",
        "config": {"llm": ARGS.llm, "seeds": ARGS.seeds, "backends": profs}, "per_seed": rows, "summary": summ,
        "safety_checks": safety, "verdict": verdict}, ARGS.out)


if __name__ == "__main__":
    main()
