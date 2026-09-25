"""E13 - Evidence is auditable and resumable.

(a) Kill / resume. For each seed an uninterrupted reference run is compared with runs that
    are hard-killed (an exception raised from a hook) at different points - after the
    baseline, after drafting a variant, after evaluating it, between history records, after a
    round settled - and then resumed by a fresh process (new objects, same directory). The
    ledgers must be identical: history.jsonl and attribution.jsonl byte-for-byte (timestamps
    off), frontier.json, every round's decisions.json, and the final state of every node of
    the rsi.core Ledger tree.
(b) Readjudicate. Rounds after t are truncated and Algorithm 2 is re-run on round t's STORED
    measurements with a different delta (x0.5 and x2): no evaluation may be spent, the
    candidates' (S, C, dS, dC) must be unchanged, and only verdicts / outcomes / H_{t+1} may
    change. A counterfactual replay reports how many of the run's decisions would flip.

Domains: HarnessWorld (mock proposer) and AgentQA (SimModel + scripted proposer).
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import parse_args, pmap, save, search_llm  # noqa: E402

from rsi.core import Ledger  # noqa: E402
from rsi.rrsi import Config, Killed, RRSIRun, build_gates, drive, select_round  # noqa: E402
from rsi.rrsi.evaluate import Measurement  # noqa: E402

# (event, first round at/after which to kill, variant or None): the kill fires at the first matching event
KILLS = [("baseline", None, None), ("drafted", 3, "B"), ("evaluated", 2, None), ("recorded", 4, "A"),
         ("settled", 5, None)]


def make_run(kind: str, seed: int, out: Path, llm_spec: str, T: int, hooks=None) -> RRSIRun:
    cfg = Config(T=T, seed=seed, record_timestamps=False, workers=1 if kind == "harnessworld" else 4)
    if kind == "harnessworld":
        from rsi.domains.harnessworld import make_domain
        dom = make_domain(seed=seed)
        return RRSIRun(dom, dom.seed_artifact(), out_dir=out, llm_propose=search_llm(llm_spec, dom.world),
                       config=cfg, hooks=hooks)
    from rsi.domains.agentqa import AgentQADomain, SimModel, make_suite
    suite = make_suite(seed=seed)
    dom = AgentQADomain(suite)
    return RRSIRun(dom, AgentQADomain.seed_artifact(), out_dir=out, llm_task=SimModel(suite),
                   llm_propose=search_llm(llm_spec, agentqa=True), config=cfg, hooks=hooks)


def snapshot(d: Path) -> dict:
    led = Ledger(d / "ledger.jsonl")
    nodes = {n.id: {k: v for k, v in n.to_json().items() if k != "t"} for n in led.nodes()}
    return {"history": (d / "history.jsonl").read_text(), "attribution": (d / "attribution.jsonl").read_text(),
            "frontier": json.loads((d / "frontier.json").read_text()),
            "decisions": {p.parent.name: p.read_text() for p in sorted(d.glob("r*/decisions.json"))},
            "ledger_nodes": nodes}


def one(job: dict) -> dict:
    kind, seed, T, llm = job["kind"], job["seed"], job["T"], job["llm"]
    root = Path(tempfile.mkdtemp(prefix=f"e13_{kind}_{seed}_"))
    ref_dir = root / "ref"
    r = make_run(kind, seed, ref_dir, llm, T)
    drive(r)
    ref = snapshot(ref_dir)
    kills = {}
    for ev, t, v in KILLS:
        d = root / f"kill_{ev}"

        def hook(event, t_=t, v_=v, **info):
            if (t_ is None or info.get("t", -1) >= t_) and (v_ is None or info.get("variant") == v_):
                raise Killed(f"{event} {info}")
        rk = make_run(kind, seed, d, llm, T, hooks={ev: hook})
        killed = False
        try:
            drive(rk)
        except Killed:
            killed = True
        rr = make_run(kind, seed, d, llm, T)          # fresh process
        stop = drive(rr)
        snap = snapshot(d)
        kills[f"{ev}@{t}{v or ''}"] = {"killed": killed, "stop": stop,
                                       "identical": {k: snap[k] == ref[k] for k in ref}}
    # readjudicate on a copy of the reference run
    re = {}
    t_mid = T // 2
    for factor in (0.5, 2.0):
        d = root / f"readj_{factor}"
        shutil.copytree(ref_dir, d)
        rr = make_run(kind, seed, d, llm, T)
        delta = rr.delta()
        before = json.loads((d / f"r{t_mid}" / "decisions.json").read_text())
        rr.truncate(t_mid)
        roll = rr.measurer.n_rollouts
        after = [x.to_json() for x in rr.readjudicate(t_mid, rr.cfg.replace(delta=delta * factor))]
        same_meas = all((b["S"], b["C"], b["delta_S"], b["delta_C"]) == (a_["S"], a_["C"], a_["delta_S"], a_["delta_C"])
                        for b, a_ in zip(before, after))
        re[f"delta x{factor}"] = {"delta_old": delta, "delta_new": delta * factor, "rollouts_spent": rr.measurer.n_rollouts - roll,
                                 "measurements_unchanged": same_meas,
                                 "verdicts_changed": sum(b["admissible"] != a_["admissible"] for b, a_ in zip(before, after)),
                                 "before": [(b["variant"], b["admissible"], b["reason"][:80]) for b in before],
                                 "after": [(a_["variant"], a_["admissible"], a_["reason"][:80]) for a_ in after]}
    # counterfactual replay of every stored round under x0.5 / x2 delta (verdicts only)
    rr = make_run(kind, seed, ref_dir, llm, T)
    fr = json.loads((ref_dir / "frontier.json").read_text())
    flips = {0.5: 0, 2.0: 0}
    total = 0
    for t in range(T):
        inc = Measurement.load(ref_dir / "evals" / f"{fr['trajectory'][t]['job']}.json")
        S_star = max(x["S"] for x in fr["trajectory"][: t + 1])
        cands = rr._load_round_candidates(t)
        counts = rr.history.incumbent_component_counts(before_t=t)
        base = [d.admissible for d in select_round(cands, inc, S_star, rr.delta(), rr.cfg, counts,
                                                   build_gates(rr.cfg, rr.sw), taxonomy=rr.tax, t=t)[1]]
        total += len(base)
        for f in flips:
            alt = [d.admissible for d in select_round(cands, inc, S_star, rr.delta() * f, rr.cfg, counts,
                                                      build_gates(rr.cfg, rr.sw), taxonomy=rr.tax, t=t)[1]]
            flips[f] += sum(x != y for x, y in zip(base, alt))
    shutil.rmtree(root, ignore_errors=True)
    return {"kind": kind, "seed": seed, "T": T, "kills": kills, "readjudicate": re,
            "counterfactual_flips": {f"delta x{f}": n for f, n in flips.items()}, "decisions_total": total}


def main():
    a = parse_args("E13: kill/resume and readjudicate", default_seeds=5)
    T_hw, T_aq = (6, 4) if a.quick else (10, 6)
    jobs = [{"kind": "harnessworld", "seed": s, "T": T_hw, "llm": a.llm} for s in range(a.seeds)]
    jobs += [{"kind": "agentqa", "seed": s, "T": T_aq, "llm": a.llm} for s in range(min(2, a.seeds))]
    rows = pmap(one, jobs, a.workers if a.llm == "sim" else 1)
    all_identical = all(all(v["identical"].values()) for r in rows for v in r["kills"].values())
    all_killed = all(v["killed"] for r in rows for v in r["kills"].values())
    no_rollouts = all(v["rollouts_spent"] == 0 for r in rows for v in r["readjudicate"].values())
    same_meas = all(v["measurements_unchanged"] for r in rows for v in r["readjudicate"].values())
    checks = {"every kill actually interrupted the run": all_killed,
              "resumed ledgers identical to uninterrupted runs": all_identical,
              "readjudicate spends no evaluation": no_rollouts,
              "readjudicate leaves measurements unchanged (only Algorithm 2 outcomes change)": same_meas}
    out = {"experiment": "E13 kill/resume + readjudicate", "config": {"T_harnessworld": T_hw, "T_agentqa": T_aq,
                                                                      "kills": KILLS, "llm": a.llm},
           "checks": checks, "verdict": "REPRODUCED" if all(checks.values()) else
           "NOT MET: " + "; ".join(k for k, v in checks.items() if not v), "rows": rows}
    save("e13_resume_readjudicate", out)
    print(json.dumps(checks, indent=1))
    for r in rows:
        print(r["kind"], r["seed"], {k: all(v["identical"].values()) for k, v in r["kills"].items()},
              {k: v["verdicts_changed"] for k, v in r["readjudicate"].items()}, r["counterfactual_flips"], r["decisions_total"])


if __name__ == "__main__":
    main()
