"""S1 - Action Fusion removes the decision-free model turn between an edit and its check.

Usage: python experiments/metaharness-solpi/s1_action_fusion.py [--llm sim|claude:haiku] [--seeds N] [--quick]
"""
from __future__ import annotations

from functools import partial

import numpy as np

from _agentworld import agg, backend, backends, domain, rel, trial_rows
from _common import fmt, paired, parse_args, pool_map, save, summarize, table
from rsi.solpi import (THEN_RUN_FAILED, THEN_RUN_SKIPPED, THEN_RUN_SUCCEEDED, ActionFusion, AgentRuntime, Message,
                       PRICES, TokenMeter, ToolCall, builtin_tools)
from rsi.solpi.runtime import ToolResult

REPORTED = ("oracle_adjacent_share", "oracle_predicted_request_reduction", "measured_request_reduction",
            "token_reduction", "cost_reduction", "fused_calls_per_task", "share_tasks_triggered")
TABLED = ("oracle_adjacent_share", "measured_request_reduction", "token_reduction", "cost_reduction",
          "fused_calls_per_task")


def job(args, spec):
    seed, prof = spec
    n = 4 if args.quick else 8
    dom = domain(args, seed, n_train=n, n_final=n)
    llm = backend(args, prof)
    base = trial_rows(dom, llm, dom.seed_artifact())
    af = trial_rows(dom, llm, dom.harness("action_fusion"))
    b, a = agg(base), agg(af)
    adj = [r["oracle"].get("adjacent_edit_command", 0) for r in base]
    trans = [r["oracle"].get("transitions", 0) for r in base]
    fused = [r["triggers"].get("action_fusion", {}).get("fused", 0) for r in af]
    families = sorted({r["family"] for r in base})
    return {"seed": seed, "backend": prof, "base": b, "af": a,
            "paired_score": [(x["score"], y["score"]) for x, y in zip(base, af)],
            "paired_requests": [(x["requests"], y["requests"]) for x, y in zip(base, af)],
            "oracle_adjacent_share": float(np.sum(adj) / max(1, np.sum(trans))),
            "oracle_predicted_request_reduction": float(np.sum(adj) / max(1, sum(r["requests"] for r in base))),
            "measured_request_reduction": -rel(b, a, "requests"), "token_reduction": -rel(b, a, "tokens"),
            "cost_reduction": -rel(b, a, "cost"), "fused_calls_per_task": float(np.mean(fused)),
            "share_tasks_triggered": float(np.mean([f > 0 for f in fused])),
            "by_family": {f: {"base": agg([r for r in base if r["family"] == f]),
                              "af": agg([r for r in af if r["family"] == f])} for f in families}}


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


def _edit(old: str, new: str, command: str = "pytest") -> list:
    return [("edit", {"path": "a.py", "old": old, "new": new, "then_run": {"command": command}})]


SAFETY_CASES = {
    "interleaved_write_skips": (_edit("X = 1", "X = 2"), lambda rt, p: rt.env.files.__setitem__(p, "X = 7\n")),
    "failed_mutation_not_run": (_edit("NOPE", "Y"), None),
    "nonzero_exit_keeps_edit": (_edit("X = 1", "X = 3", "pytest fail"), None),
    "success": (_edit("X = 1", "X = 4"), None)}


def safety_checks() -> dict:
    out = {}
    for name, (steps, hook) in SAFETY_CASES.items():
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
    interleaved, failed, nonzero, success = out.values()
    out["all_pass"] = (interleaved["marker"] == THEN_RUN_SKIPPED and not interleaved["command_ran"]
                       and failed["marker"] == THEN_RUN_SKIPPED and not failed["command_ran"]
                       and nonzero["file"] == "X = 3" and nonzero["marker"] == THEN_RUN_FAILED
                       and success["marker"] == THEN_RUN_SUCCEEDED)
    return out


def main():
    args = parse_args(__doc__.splitlines()[0], default_seeds=5)
    profs = backends(args)
    rows = pool_map(partial(job, args), [(s, p) for s in range(args.seeds) for p in profs], args.workers)
    summ = {}
    for p in profs:
        rs = [r for r in rows if r["backend"] == p]
        sc = [x for r in rs for x in r["paired_score"]]
        summ[p] = {k: summarize([r[k] for r in rs]) for k in REPORTED}
        summ[p]["success_change"] = paired([a for a, _ in sc], [b for _, b in sc])
    safety = safety_checks()
    a = summ["A"]
    sc = a["success_change"]
    measured, predicted = a["measured_request_reduction"]["mean"], a["oracle_predicted_request_reduction"]["mean"]
    close = abs(measured - predicted) <= max(0.05, 0.5 * predicted)
    unchanged = sc.get("lo", -1) <= 0 <= sc.get("hi", 1) or sc.get("mean_diff", -1) >= -0.01
    fewer = a["measured_request_reduction"]["lo"] > 0 and a["token_reduction"]["lo"] > 0
    verdict = (("REPRODUCED" if fewer and unchanged and safety["all_pass"] else "PARTIAL")
               + f": requests {-100 * measured:+.1f}% (oracle-predicted {-100 * predicted:+.1f}%"
               + f"{' - close' if close else ''}), tokens {-100 * a['token_reduction']['mean']:+.1f}%, cost "
               + f"{-100 * a['cost_reduction']['mean']:+.1f}%, success change "
               + f"{sc.get('mean_diff', float('nan')):+.3f}; safety checks {'pass' if safety['all_pass'] else 'FAIL'}")
    if "B" in summ:
        verdict += (f"; backend B uptake {summ['B']['fused_calls_per_task']['mean']:.1f} vs A "
                    f"{a['fused_calls_per_task']['mean']:.1f} fused calls/task")
    print(table([[p] + [fmt(summ[p][k], 3) for k in TABLED] for p in profs],
                ["backend", "oracle adj share", "requests -%", "tokens -%", "cost -%", "fused/task"]))
    print("verdict:", verdict)
    save("s1_action_fusion" + ("_live" if args.live else ""), {
        "claim": "Action Fusion removes the decision-free turn between an edit and its check; success unchanged; "
                 "hash guard + skip semantics [SoL-Pi code/blog]",
        "config": {"llm": args.llm, "seeds": args.seeds, "backends": profs}, "per_seed": rows, "summary": summ,
        "safety_checks": safety, "verdict": verdict}, args.out)


if __name__ == "__main__":
    main()
