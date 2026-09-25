"""E13 - Deterministic, resumable, auditable runs.

Design goal [spec E13, weakness 15]: identical trees across reruns with the mock, and a
killed run that resumes onto exactly the uninterrupted trajectory. The reference code
does not guarantee this (unseeded uuid4 iteration ids; RNG and batch-sampler state not
persisted). Here iteration ids are seed-derived and the RNG, sampler, merge scheduler
and selector state are persisted in ``state.json``.

For each seed, on RuleWorld (GEPA+Merge, B = 1500) and on AgentQA (two-module harness,
SimModel, B = 120): (1) two identical runs -> byte-identical ``run_log.jsonl``, identical
candidate ids and ledgers (timestamps excluded); (2) kill at iteration k (several k) and
resume -> identical final ``state.json`` (usage excluded) and ``run_log.jsonl``;
(3) resume without the persisted RNG / sampler state (reference behaviour) -> does the
trajectory diverge?

    python experiments/gepa/e13_determinism.py [--llm sim|claude:haiku] [--seeds N] [--quick]
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import parse_args, pool_map, reflection_llm, save  # noqa: E402

from rsi.domains.ruleworld import make_domain  # noqa: E402
from rsi.gepa import Config, run  # noqa: E402

ARGS = None


class Kill(Exception):
    pass


def _setup(domain_kind: str, seed: int):
    if domain_kind == "ruleworld":
        d = make_domain(seed=seed)
        return d, d.seed_artifact(), None, (lambda: reflection_llm(ARGS.llm, d.world)), \
            Config(max_metric_calls=1500, seed=seed, use_merge=True)
    from rsi.domains.agentqa import AgentQADomain, SimModel, make_suite
    from rsi.gepa import two_module_harness
    suite = make_suite(n_evolve=8, n_val=8, n_holdout=4, n_ood_per_family=1, seed=seed)
    dom = AgentQADomain(suite)
    return dom, two_module_harness(), (lambda: SimModel(suite)), (lambda: reflection_llm(ARGS.llm, kind="agentqa")), \
        Config(max_metric_calls=120, seed=seed, use_merge=True)


def _go(dk, seed, out, kill_at=None):
    d, art, task_llm, refl, cfg = _setup(dk, seed)

    def killer(event, payload):
        if kill_at is not None and event == "iteration_start" and payload["i"] == kill_at:
            raise Kill()
    try:
        return run(d, art, llm_task=task_llm() if task_llm else None, llm_propose=refl(), config=cfg, out_dir=out,
                   callbacks=[killer])
    except Kill:
        return None


def _state(p: Path) -> dict:
    s = json.loads((p / "state.json").read_text())
    s["extra"].pop("usage", None)
    return s


def _ledger(p: Path) -> list:
    from rsi.core import Ledger
    return [{k: v for k, v in n.to_json().items() if k != "t"} for n in Ledger(p / "ledger.jsonl").nodes()]


def job(spec):
    dk, seed, kills = spec
    tmp = Path(tempfile.mkdtemp(prefix="gepa_e13_"))
    try:
        a = _go(dk, seed, tmp / "a")
        b = _go(dk, seed, tmp / "b")
        same_log = (tmp / "a" / "run_log.jsonl").read_bytes() == (tmp / "b" / "run_log.jsonl").read_bytes()
        same_ids = [c.id for c in a.state.candidates] == [c.id for c in b.state.candidates]
        same_ledger = _ledger(tmp / "a") == _ledger(tmp / "b")
        n_it = a.state.i + 1
        resumes = []
        for k in kills:
            k = min(k, n_it - 1)
            if k < 1:
                continue
            p = tmp / f"kill{k}"
            _go(dk, seed, p, kill_at=k)
            r = _go(dk, seed, p)
            resumes.append({"kill_at": k, "resumed_at": r.meta["resumed_at"],
                            "same_state": _state(p) == _state(tmp / "a"),
                            "same_log": (p / "run_log.jsonl").read_bytes() == (tmp / "a" / "run_log.jsonl").read_bytes(),
                            "same_candidates": [c.id for c in r.state.candidates] == [c.id for c in a.state.candidates]})
        # reference-style resume: drop RNG + sampler snapshots
        k = min(kills[0], n_it - 1)
        p = tmp / "ref"
        _go(dk, seed, p, kill_at=k)
        st = json.loads((p / "state.json").read_text())
        st["extra"]["rng"], st["extra"]["sampler"] = None, None
        (p / "state.json").write_text(json.dumps(st))
        r = _go(dk, seed, p)
        ref = {"kill_at": k, "same_candidates": [c.id for c in r.state.candidates] == [c.id for c in a.state.candidates],
               "same_log": (p / "run_log.jsonl").read_bytes() == (tmp / "a" / "run_log.jsonl").read_bytes()}
        return {"domain": dk, "seed": seed, "iterations": n_it, "n_candidates": len(a.state.candidates),
                "rerun_same_log": same_log, "rerun_same_candidates": same_ids, "rerun_same_ledger": same_ledger,
                "resumes": resumes, "reference_style_resume": ref}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    global ARGS
    ARGS = a = parse_args("E13: determinism and resume", default_seeds=5)
    kills = [3, 10, 25]
    jobs = [("ruleworld", s, kills) for s in range(a.seeds)]
    n_aq = 1 if (a.quick or a.live) else 2
    jobs += [("agentqa", s, [2, 6]) for s in range(n_aq)]
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
