"""E8 - house rules: kill > 2x budget, fix trivial crashes, skip broken ideas, rewinds rare.

Claim [doc]: "if a run takes over 10 minutes, kill it and count it as a failure. If it
crashes on something trivial, like a typo or missing import, fix it and rerun. If the
idea is fundamentally broken, move on. Rewinding to older versions is allowed but meant
to be very rare." Plus the results.tsv crash convention (0.000000 / 0.0) and the NaN
fast-fail ("FAIL" instead of burning the budget).

A. tinylm (real subprocesses, 1.5 s budget, kill_after = 2 x budget + 5 s), both modes:
   a scripted night with a typo, a missing import, a hang, an OOM, a NaN divergence and
   honest edits. Checked: statuses, crash row format, the fix-and-rerun path, kill time,
   fast-fail time, HEAD == last kept commit.
B. The same trivial crash when the agent cannot fix it (p_fix = 0) and with a fix that
   keeps failing (max_fix_attempts = 3 -> give up, log crash).
C. Landscape at scale (30 seeds x 60 experiments, 20% crash edits): crash-kind counts,
   format invariants, HEAD invariant, rewinds.

Usage: python experiments/autoresearch/e8_house_rules.py [--seeds N] [--quick] [--llm ...]
"""
from __future__ import annotations

from _common import SCRATCH, parser, pool_map, write  # noqa: I001

import json

import numpy as np

from rsi.autoresearch import (AutoresearchLoop, Config, LandscapeTask, MockResearchAgent, ResultsLog, ScriptedEdit,
                              landscape_edit_pool)
from rsi.domains.tinylm import TinyLMTask

SCHEDULE = ["lr_up", "typo_lr_up", "missing_import_warmup", "hang_prefetch", "oom_width", "nan_sgd", "batch_down",
            "exploit_grader"]


def check_run(res, task) -> dict:
    rows = ResultsLog.read(res.meta["results_tsv"]).rows()
    nodes = [n for n in res.ledger.nodes() if n.kind == "candidate" and n.status != "invalid"]
    kept = [n for n in res.ledger.nodes() if n.status == "keep"]
    crash_rows = [r for r in rows if r.status == "crash"]
    return {
        "tsv": [(r.commit, f"{r.metric:.6f}", f"{r.memory_gb:.1f}", r.status, r.description) for r in rows],
        "crash_rows_format_ok": all(r.metric == 0.0 and r.memory_gb == 0.0 for r in crash_rows),
        "statuses_valid": all(r.status in ("keep", "discard", "crash") for r in rows),
        "head_is_last_keep": res.best.id == kept[-1].artifact_id and
        res.meta["workspace"]["head"] == kept[-1].meta["commit"],
        "experiments": [{"desc": n.change, "status": n.status, "crash_kind": n.meta.get("crash_kind"),
                         "crash_reason": n.meta.get("crash_reason"), "fix_attempts": n.meta.get("fix_attempts"),
                         "wall_s": n.metrics.get("wall_s")} for n in nodes],
        "kill_after_s": task.budget.hard_timeout(), "rewinds": res.meta["workspace"]["rewinds"],
        "resets": res.meta["workspace"]["resets"], "counters": res.meta["counters"],
    }


def part_a(mode: str) -> dict:
    task = TinyLMTask(budget_s=1.5)
    ag = MockResearchAgent(task.mock_edit_pool(), schedule=SCHEDULE)
    res = AutoresearchLoop(task, ag, Config(max_experiments=len(SCHEDULE), mode=mode, hidden_audit=False,
                                            overwrite=True, tag=f"e8-{mode}"),
                           out_dir=SCRATCH / "e8" / mode).run()
    out = check_run(res, task)
    ex = {e["desc"]: e for e in out["experiments"]}
    hang = next(e for e in out["experiments"] if e["crash_kind"] == "timeout")
    nan = next(e for e in out["experiments"] if e["crash_kind"] == "nan")
    out["checks"] = {
        "typo_fixed_and_rerun": ex["LR 0.006 -> 0.012 (with a typo)"]["fix_attempts"] == 1 and
        ex["LR 0.006 -> 0.012 (with a typo)"]["status"] != "crash",
        "missing_import_fixed": any(e["fix_attempts"] == 1 and e["status"] != "crash" and "Fraction" in e["desc"]
                                    for e in out["experiments"]),
        "hang_killed_near_kill_after": abs(hang["wall_s"] - out["kill_after_s"]) < 2.0,
        "nan_fast_fail_before_budget": nan["wall_s"] < 1.5,
        "oom_logged_as_crash": any(e["crash_kind"] == "oom" and e["status"] == "crash" for e in out["experiments"]),
        "grader_edit": ("rejected" if mode == "hardened" else "ran") if any(
            e["desc"].startswith(("REJECTED", "tune the evaluation")) for e in out["experiments"]) else "missing",
    }
    return out


def part_b() -> dict:
    task = TinyLMTask(budget_s=1.5)
    res1 = AutoresearchLoop(task, MockResearchAgent(task.mock_edit_pool(), schedule=["typo_lr_up"], p_fix=0.0),
                            Config(max_experiments=1, hidden_audit=False, overwrite=True, plot=False),
                            out_dir=SCRATCH / "e8" / "nofix").run()
    pool = task.mock_edit_pool()
    base = next(e for e in pool if e.name == "typo_lr_up")
    stubborn = ScriptedEdit("stubborn_typo", "crash", base.apply,
                            fix=lambda f: {"train.py": f["train.py"].replace("    loss = loss +\n", "    loss = loss +*\n")})
    res2 = AutoresearchLoop(task, MockResearchAgent([stubborn], schedule=["stubborn_typo"]),
                            Config(max_experiments=1, hidden_audit=False, overwrite=True, plot=False,
                                   max_fix_attempts=3), out_dir=SCRATCH / "e8" / "stubborn").run()
    n1 = [n for n in res1.ledger.nodes() if n.kind == "candidate"][0]
    n2 = [n for n in res2.ledger.nodes() if n.kind == "candidate"][0]
    return {"p_fix_0": {"status": n1.status, "fix_attempts": n1.meta["fix_attempts"]},
            "fix_keeps_failing": {"status": n2.status, "fix_attempts": n2.meta["fix_attempts"],
                                  "crash_kind": n2.meta["crash_kind"]}}


def part_c(seed: int) -> dict:
    task = LandscapeTask(seed=seed)
    res = AutoresearchLoop(task, MockResearchAgent(landscape_edit_pool(), seed=seed, crash_rate=0.2),
                           Config(max_experiments=60, hidden_audit=False, plot=False, overwrite=True, seed=seed),
                           out_dir=SCRATCH / "e8" / f"ls_{seed}").run()
    c = check_run(res, task)
    return {"seed": seed, "crash_kinds": res.meta["crash_kinds"], "fixed": res.meta["counters"]["fixed"],
            "format_ok": c["crash_rows_format_ok"], "head_ok": c["head_is_last_keep"], "rewinds": c["rewinds"],
            "n_crash": sum(1 for e in c["experiments"] if e["status"] == "crash")}


def main():
    ap = parser(__doc__.splitlines()[0], seeds=30)
    a = ap.parse_args()
    out = {"config": {"tinylm_budget_s": 1.5, "schedule": SCHEDULE}}
    out["tinylm"] = {m: part_a(m) for m in ("hardened", "faithful")}
    out["give_up"] = part_b()
    cs = pool_map(part_c, list(range(5 if a.quick else a.seeds)), a.workers)
    kinds = {}
    for c in cs:
        for k, v in c["crash_kinds"].items():
            kinds[k] = kinds.get(k, 0) + v
    out["landscape"] = {"runs": len(cs), "crash_kinds_total": kinds, "trivial_fixed_total": sum(c["fixed"] for c in cs),
                        "all_format_ok": all(c["format_ok"] for c in cs), "all_head_ok": all(c["head_ok"] for c in cs),
                        "rewinds_total": sum(c["rewinds"] for c in cs),
                        "crashes_per_run": float(np.mean([c["n_crash"] for c in cs]))}
    checks = {f"{m}:{k}": v for m in ("hardened", "faithful") for k, v in out["tinylm"][m]["checks"].items()}
    checks.update({f"{m}:head_is_last_keep": out["tinylm"][m]["head_is_last_keep"] for m in ("hardened", "faithful")})
    checks.update({f"{m}:crash_rows_format_ok": out["tinylm"][m]["crash_rows_format_ok"] for m in ("hardened", "faithful")})
    checks["give_up_without_fix"] = out["give_up"]["p_fix_0"]["status"] == "crash"
    checks["give_up_after_max_attempts"] = out["give_up"]["fix_keeps_failing"]["status"] == "crash" and \
        out["give_up"]["fix_keeps_failing"]["fix_attempts"] == 3
    checks["landscape_invariants"] = out["landscape"]["all_format_ok"] and out["landscape"]["all_head_ok"]
    out["verdict"] = {"checks": checks, "claim_reproduced": all(v in (True, "rejected", "ran") for v in checks.values())}
    write("e8_house_rules" + ("_quick" if a.quick else ""), out)
    print(json.dumps(out["verdict"], indent=1))


if __name__ == "__main__":
    main()
