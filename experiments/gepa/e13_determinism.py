"""E13: reruns are byte-identical and a killed run resumes onto the uninterrupted trajectory (RuleWorld, AgentQA).

Run: python experiments/gepa/e13_determinism.py [--llm sim|claude:haiku] [--seeds N] [--quick]
"""
from __future__ import annotations

import functools
import json
import shutil
import tempfile
from pathlib import Path

from _common import parse_args, pool_map, reflection_llm, save
from rsi.core import Ledger
from rsi.domains.ruleworld import make_domain
from rsi.gepa import Config, run


class Kill(Exception):
    pass


def _go(dk: str, seed: int, llm: str, out: Path, kill_at=None):
    """One run of domain ``dk`` into ``out`` (resuming what is there); None when killed at iteration ``kill_at``."""
    if dk == "ruleworld":
        d = make_domain(seed=seed)
        art, task, cfg = d.seed_artifact(), None, Config(max_metric_calls=1500, seed=seed, use_merge=True)
        propose = reflection_llm(llm, d.world)
    else:
        from rsi.domains.agentqa import AgentQADomain, SimModel, make_suite
        from rsi.gepa import two_module_harness
        suite = make_suite(n_evolve=8, n_val=8, n_holdout=4, n_ood_per_family=1, seed=seed)
        d, art = AgentQADomain(suite), two_module_harness()
        cfg = Config(max_metric_calls=120, seed=seed, use_merge=True)
        task, propose = SimModel(suite), reflection_llm(llm, kind="agentqa")

    def killer(event, payload):
        if kill_at is not None and event == "iteration_start" and payload["i"] == kill_at:
            raise Kill()
    try:
        return run(d, art, llm_task=task, llm_propose=propose, config=cfg, out_dir=out, callbacks=[killer])
    except Kill:
        return None


def _state(p: Path) -> dict:
    s = json.loads((p / "state.json").read_text())
    s["extra"].pop("usage", None)
    return s


def _ledger(p: Path) -> list:
    return [{k: v for k, v in n.to_json().items() if k != "t"} for n in Ledger(p / "ledger.jsonl").nodes()]


def _log(p: Path) -> bytes:
    return (p / "run_log.jsonl").read_bytes()


def _ids(res) -> list:
    return [c.id for c in res.state.candidates]


def job(spec):
    dk, seed, kills, llm = spec
    tmp = Path(tempfile.mkdtemp(prefix="gepa_e13_"))
    go = functools.partial(_go, dk, seed, llm)
    try:
        a, b = go(tmp / "a"), go(tmp / "b")
        n_it = a.state.i + 1
        resumes = []
        for k in kills:
            k = min(k, n_it - 1)
            if k < 1:
                continue
            p = tmp / f"kill{k}"
            go(p, kill_at=k)
            r = go(p)
            resumes.append({"kill_at": k, "resumed_at": r.meta["resumed_at"],
                            "same_state": _state(p) == _state(tmp / "a"), "same_log": _log(p) == _log(tmp / "a"),
                            "same_candidates": _ids(r) == _ids(a)})
        k, p = min(kills[0], n_it - 1), tmp / "ref"
        go(p, kill_at=k)
        st = json.loads((p / "state.json").read_text())
        st["extra"]["rng"], st["extra"]["sampler"] = None, None      # the reference persists neither
        (p / "state.json").write_text(json.dumps(st))
        r = go(p)
        return {"domain": dk, "seed": seed, "iterations": n_it, "n_candidates": len(a.state.candidates),
                "rerun_same_log": _log(tmp / "a") == _log(tmp / "b"), "rerun_same_candidates": _ids(a) == _ids(b),
                "rerun_same_ledger": _ledger(tmp / "a") == _ledger(tmp / "b"), "resumes": resumes,
                "reference_style_resume": {"kill_at": k, "same_candidates": _ids(r) == _ids(a),
                                           "same_log": _log(p) == _log(tmp / "a")}}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    a = parse_args("E13: determinism and resume", default_seeds=5)
    kills = [3, 10, 25]
    jobs = [("ruleworld", s, kills, a.llm) for s in range(a.seeds)]
    jobs += [("agentqa", s, [2, 6], a.llm) for s in range(1 if (a.quick or a.live) else 2)]
    rows = pool_map(job, jobs, a.workers)
    reruns = all(r["rerun_same_log"] and r["rerun_same_candidates"] and r["rerun_same_ledger"] for r in rows)
    resumes = [x for r in rows for x in r["resumes"]]
    ok_resume = all(x["same_state"] and x["same_log"] and x["same_candidates"] for x in resumes)
    ref_div = sum(1 for r in rows if not r["reference_style_resume"]["same_candidates"])
    out = {"experiment": "E13 determinism and resume", "llm": a.llm, "config": {"seeds": a.seeds, "kills": kills},
           "raw": rows, "all_reruns_identical": reruns, "n_resumes": len(resumes),
           "all_resumes_identical": ok_resume, "reference_style_resumes_diverged": f"{ref_div}/{len(rows)}",
           "verdict": (f"{len(rows)} runs (RuleWorld GEPA+Merge and AgentQA two-module): reruns byte-identical: "
                       f"{reruns}; {len(resumes)} kill/resume cycles give the identical final state.json, run_log and "
                       f"candidate sequence: {ok_resume}. Resuming without the persisted RNG/sampler state (the "
                       f"reference's behaviour) diverged from the uninterrupted run in {ref_div}/{len(rows)} cases. "
                       f"Claim (design goal): {'REPRODUCED' if reruns and ok_resume else 'NOT reproduced'}.")}
    if a.live:
        out["note"] = "live LLM: rerun determinism holds only through the CachedLLM replay"
    save("e13_determinism", out, a.out)
    print(out["verdict"])


if __name__ == "__main__":
    main()
