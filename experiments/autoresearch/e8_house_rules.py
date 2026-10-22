"""E8 - house rules: kill > 2x budget, fix trivial crashes, skip broken ideas, rewinds rare.

Usage: python experiments/autoresearch/e8_house_rules.py [--seeds N] [--quick] [--llm sim|claude:haiku]
"""
from _common import SCRATCH, is_live, parser, pool_map, propose_llm, report, slug, suffix, usage_of  # noqa: I001

import numpy as np

from rsi.autoresearch import (AutoresearchLoop, Config, LandscapeTask, LLMResearchAgent, MockResearchAgent,
                              ResearchAgent, ResultsLog, ScriptedEdit, landscape_edit_pool)
from rsi.core import RewriteEditor
from rsi.core.editors import Proposal
from rsi.domains.tinylm import TinyLMTask

SCHEDULE = ["lr_up", "typo_lr_up", "missing_import_warmup", "hang_prefetch", "oom_width", "nan_sgd", "batch_down",
            "exploit_grader"]
MODES = ("hardened", "faithful")
TYPO = "    loss = loss +\n"


def check_run(res, task) -> dict:
    rows = ResultsLog.read(res.meta["results_tsv"]).rows()
    nodes = [n for n in res.ledger.nodes() if n.kind == "candidate" and n.status != "invalid"]
    kept = [n for n in res.ledger.nodes() if n.status == "keep"]
    ws = res.meta["workspace"]
    return {
        "tsv": [(r.commit, f"{r.metric:.6f}", f"{r.memory_gb:g}", r.status, r.description) for r in rows],
        "crash_rows_format_ok": all(r.metric == 0.0 and r.memory_gb == 0.0 for r in rows if r.status == "crash"),
        "statuses_valid": all(r.status in ("keep", "discard", "crash") for r in rows),
        "head_is_last_keep": res.best.id == kept[-1].artifact_id and ws["head"] == kept[-1].meta["commit"],
        "experiments": [{"desc": n.change, "status": n.status, "crash_kind": n.meta.get("crash_kind"),
                         "crash_reason": n.meta.get("crash_reason"), "fix_attempts": n.meta.get("fix_attempts"),
                         "wall_s": n.metrics.get("wall_s")} for n in nodes],
        "kill_after_s": task.budget.hard_timeout(), "rewinds": ws["rewinds"], "resets": ws["resets"],
        "counters": res.meta["counters"],
    }


class HybridAgent(ResearchAgent):
    """Scripted proposals (the crash schedule); crash fixes by an LLM research agent."""

    name = "scripted-proposals+llm-fixes"

    def __init__(self, proposer: ResearchAgent, fixer: ResearchAgent) -> None:
        self.proposer, self.fixer = proposer, fixer

    def propose(self, ctx):
        return self.proposer.propose(ctx)

    def fix_crash(self, ctx, candidate, description, log_tail):
        return self.fixer.fix_crash(ctx, candidate, description, log_tail)


def part_a(mode: str, llm_spec: str = "sim") -> dict:
    """The scripted crash schedule on real tinylm subprocesses."""
    task = TinyLMTask(budget_s=1.5)
    agent = MockResearchAgent(task.mock_edit_pool(), schedule=SCHEDULE)
    llms = []
    if is_live(llm_spec):
        llm = propose_llm(llm_spec, pool=task.mock_edit_pool())
        agent, llms = HybridAgent(agent, LLMResearchAgent(RewriteEditor(llm))), [llm]
    cfg = Config(max_experiments=len(SCHEDULE), mode=mode, hidden_audit=False, overwrite=True, tag=f"e8-{mode}")
    res = AutoresearchLoop(task, agent, cfg, out_dir=SCRATCH / "e8" / f"{mode}_{slug(llm_spec)}", llms=llms).run()
    out = check_run(res, task)
    out["usage"] = usage_of(llms)
    exps = out["experiments"]
    typo = next(e for e in exps if "(with a typo)" in e["desc"])
    hang = next(e for e in exps if e["crash_kind"] == "timeout")
    nan = next(e for e in exps if e["crash_kind"] == "nan")
    out["checks"] = {
        "typo_fixed_and_rerun": typo["fix_attempts"] >= 1 and typo["status"] != "crash",
        "missing_import_fixed": any((e["fix_attempts"] or 0) >= 1 and e["status"] != "crash" and "Fraction" in e["desc"]
                                    for e in exps),
        "hang_killed_near_kill_after": abs(hang["wall_s"] - out["kill_after_s"]) < 2.0,
        "nan_fast_fail_before_budget": nan["wall_s"] < 1.5,
        "oom_logged_as_crash": any(e["crash_kind"] == "oom" and e["status"] == "crash" for e in exps),
        # the agent sees every crash but a timeout; the scripted agent has no OOM/NaN fix and gives up after one look
        "oom_and_nan_shown_to_agent": all((e["fix_attempts"] or 0) == 1 for e in exps
                                          if e["crash_kind"] in ("oom", "nan")),
        "timeout_not_shown_to_agent": all((e["fix_attempts"] or 0) == 0 for e in exps
                                          if e["crash_kind"] == "timeout"),
        "grader_edit": ("rejected" if mode == "hardened" else "ran")
        if any(e["desc"].startswith(("REJECTED", "tune the evaluation")) for e in exps) else "missing",
    }
    return out


def part_b() -> dict:
    """A trivial crash the agent cannot fix (p_fix = 0), and a fix that keeps failing (gives up after 3)."""
    task = TinyLMTask(budget_s=1.5)
    res1 = AutoresearchLoop(task, MockResearchAgent(task.mock_edit_pool(), schedule=["typo_lr_up"], p_fix=0.0),
                            Config(max_experiments=1, hidden_audit=False, overwrite=True, plot=False),
                            out_dir=SCRATCH / "e8" / "nofix").run()
    base = next(e for e in task.mock_edit_pool() if e.name == "typo_lr_up")
    stubborn = ScriptedEdit("stubborn_typo", "crash", base.apply,
                            fix=lambda f: {"train.py": f["train.py"].replace(TYPO, "    loss = loss +*\n")})
    res2 = AutoresearchLoop(task, MockResearchAgent([stubborn], schedule=["stubborn_typo"]),
                            Config(max_experiments=1, hidden_audit=False, overwrite=True, plot=False,
                                   max_fix_attempts=3), out_dir=SCRATCH / "e8" / "stubborn").run()
    n1 = next(n for n in res1.ledger.nodes() if n.kind == "candidate")
    n2 = next(n for n in res2.ledger.nodes() if n.kind == "candidate")
    return {"p_fix_0": {"status": n1.status, "fix_attempts": n1.meta["fix_attempts"]},
            "fix_keeps_failing": {"status": n2.status, "fix_attempts": n2.meta["fix_attempts"],
                                  "crash_kind": n2.meta["crash_kind"]}}


class _NoOp(ResearchAgent):
    name = "no-op"

    def propose(self, ctx):
        return Proposal(ctx.artifact, change="no-op")


class _BaselineFixer(ResearchAgent):
    """Proposes one honest edit; fixes the broken baseline it is shown."""

    name = "baseline-fixer"

    def __init__(self, pool):
        self.inner = MockResearchAgent(pool, schedule=["lr_up"])
        self.fix_calls = 0

    def propose(self, ctx):
        return self.inner.propose(ctx)

    def fix_crash(self, ctx, candidate, description, log_tail):
        self.fix_calls += 1
        t = candidate["train.py"]
        if TYPO not in t:
            return None
        return Proposal(candidate.with_files({"train.py": t.replace(TYPO, "", 1)}), change="fix typo in the baseline")


def part_d() -> dict:
    """NEVER STOP: an agent that proposes nothing runs to the budget; a crashing baseline goes to the fix path."""
    r = AutoresearchLoop(LandscapeTask(seed=0), _NoOp(),
                         Config(max_experiments=100, hidden_audit=False, plot=False, overwrite=True),
                         out_dir=SCRATCH / "e8" / "never_stop").run()
    task = TinyLMTask(budget_s=1.5)
    seed = task.seed_artifact()
    loop_line = "    loss, grads = model.loss_and_grads(x, y)\n"
    broken = seed.with_files({"train.py": seed["train.py"].replace(loop_line, loop_line + TYPO, 1)})
    agent = _BaselineFixer(task.mock_edit_pool())
    rb = AutoresearchLoop(task, agent, Config(max_experiments=1, hidden_audit=False, plot=False, overwrite=True),
                          out_dir=SCRATCH / "e8" / "baseline_fix", seed_artifact=broken).run()
    rows = ResultsLog.read(rb.meta["results_tsv"]).rows()
    return {"never_stop": {"stop_reason": r.stop_reason, "rounds": r.meta["counters"]["invalid"],
                           "n_experiments": r.meta["n_experiments"]},
            "baseline_fix": {"fix_calls": agent.fix_calls, "baseline_row": list(map(str, rows[0].__dict__.values())),
                             "stop_reason": rb.stop_reason, "fixed": rb.meta["counters"]["fixed"]}}


def part_c(seed: int) -> dict:
    """Landscape at scale with 20% crash edits."""
    task = LandscapeTask(seed=seed)
    res = AutoresearchLoop(task, MockResearchAgent(landscape_edit_pool(), seed=seed, crash_rate=0.2),
                           Config(max_experiments=60, hidden_audit=False, plot=False, overwrite=True, seed=seed),
                           out_dir=SCRATCH / "e8" / f"ls_{seed}").run()
    c = check_run(res, task)
    return {"seed": seed, "crash_kinds": res.meta["crash_kinds"], "fixed": res.meta["counters"]["fixed"],
            "format_ok": c["crash_rows_format_ok"], "head_ok": c["head_is_last_keep"], "rewinds": c["rewinds"],
            "n_crash": sum(1 for e in c["experiments"] if e["status"] == "crash")}


def tinylm_checks(tinylm: dict) -> dict:
    checks = {f"{m}:{k}": v for m in MODES for k, v in tinylm[m]["checks"].items()}
    checks.update({f"{m}:head_is_last_keep": tinylm[m]["head_is_last_keep"] for m in MODES})
    return checks


def reproduced(checks: dict) -> bool:
    return all(v in (True, "rejected", "ran") for v in checks.values())


def main_live(a) -> None:
    out = {"config": {"tinylm_budget_s": 1.5, "schedule": SCHEDULE, "llm": a.llm, "fixer": "llm"},
           "tinylm": {m: part_a(m, a.llm) for m in MODES}}
    checks = tinylm_checks(out["tinylm"])
    out["verdict"] = {"checks": checks, "usage": {m: out["tinylm"][m]["usage"] for m in MODES},
                      "claim_reproduced": reproduced(checks)}
    report("e8_house_rules" + suffix(a.llm, a.quick), out)


def main() -> None:
    a = parser(__doc__.splitlines()[0], seeds=30).parse_args()
    if is_live(a.llm):
        return main_live(a)
    out = {"config": {"tinylm_budget_s": 1.5, "schedule": SCHEDULE}, "tinylm": {m: part_a(m) for m in MODES},
           "give_up": part_b(), "never_stop_and_baseline_fix": part_d()}
    cs = pool_map(part_c, list(range(5 if a.quick else a.seeds)), a.workers)
    kinds = {}
    for c in cs:
        for k, v in c["crash_kinds"].items():
            kinds[k] = kinds.get(k, 0) + v
    out["landscape"] = {"runs": len(cs), "crash_kinds_total": kinds, "trivial_fixed_total": sum(c["fixed"] for c in cs),
                        "all_format_ok": all(c["format_ok"] for c in cs), "all_head_ok": all(c["head_ok"] for c in cs),
                        "rewinds_total": sum(c["rewinds"] for c in cs),
                        "crashes_per_run": float(np.mean([c["n_crash"] for c in cs]))}
    checks = tinylm_checks(out["tinylm"])
    checks.update({f"{m}:crash_rows_format_ok": out["tinylm"][m]["crash_rows_format_ok"] for m in MODES})
    give_up, d = out["give_up"], out["never_stop_and_baseline_fix"]
    checks["give_up_without_fix"] = give_up["p_fix_0"]["status"] == "crash"
    checks["give_up_after_max_attempts"] = (give_up["fix_keeps_failing"]["status"] == "crash" and
                                            give_up["fix_keeps_failing"]["fix_attempts"] == 3)
    checks["landscape_invariants"] = out["landscape"]["all_format_ok"] and out["landscape"]["all_head_ok"]
    checks["never_stop_runs_until_budget"] = (d["never_stop"]["stop_reason"] == "max_rounds" and
                                              d["never_stop"]["rounds"] == 100)
    checks["crashing_baseline_fixed"] = (d["baseline_fix"]["fixed"] == 1 and
                                         d["baseline_fix"]["baseline_row"][3] == "keep")
    out["verdict"] = {"checks": checks, "claim_reproduced": reproduced(checks)}
    report("e8_house_rules" + ("_quick" if a.quick else ""), out)


if __name__ == "__main__":
    main()
