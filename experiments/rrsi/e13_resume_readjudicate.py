"""E13: evidence is auditable and resumable: hard kills resumed in a fresh process, readjudication at a new delta.

Run: python experiments/rrsi/e13_resume_readjudicate.py [--quick] [--seeds N]
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from _common import parse_args, pmap, save, search_llm, verdict

from rsi.core import Ledger
from rsi.rrsi import Config, Killed, RRSIRun, build_gates, drive, select_round
from rsi.rrsi.evaluate import Measurement

# (event, first round at/after which to kill, variant or None): the kill fires at the first matching event
KILLS = [("baseline", None, None), ("drafted", 3, "B"), ("evaluated", 2, None), ("recorded", 4, "A"),
         ("settled", 5, None)]
FACTORS = (0.5, 2.0)


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


def resume_in_subprocess(kind: str, seed: int, out: Path, llm_spec: str, T: int, hash_seed: int) -> str:
    """Resume the killed run in a new OS process (different PYTHONHASHSEED); returns the stop reason."""
    env = dict(os.environ, PYTHONHASHSEED=str(hash_seed))
    p = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--resume-worker", kind, str(seed), str(out),
                        llm_spec, str(T)], capture_output=True, text=True, env=env, timeout=3600)
    if p.returncode != 0:
        raise RuntimeError(f"resume worker failed: {p.stderr[-2000:]}")
    return p.stdout.strip().splitlines()[-1]


def snapshot(d: Path) -> dict:
    led = Ledger(d / "ledger.jsonl")
    nodes = {n.id: {k: v for k, v in n.to_json().items() if k != "t"} for n in led.nodes()}
    return {"history": (d / "history.jsonl").read_text(), "attribution": (d / "attribution.jsonl").read_text(),
            "frontier": json.loads((d / "frontier.json").read_text()),
            "decisions": {p.parent.name: p.read_text() for p in sorted(d.glob("r*/decisions.json"))},
            "ledger_nodes": nodes}


def kill_and_resume(kind: str, seed: int, T: int, llm: str, root: Path, ref: dict) -> dict:
    """Kill a fresh run at each KILLS point, resume it in a subprocess, compare the ledgers with the reference."""
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
        stop = resume_in_subprocess(kind, seed, d, llm, T, hash_seed=7919 + 31 * seed + len(kills))
        snap = snapshot(d)
        kills[f"{ev}@{t}{v or ''}"] = {"killed": killed, "stop": stop, "resumed_in": "subprocess",
                                       "identical": {k: snap[k] == ref[k] for k in ref}}
    return kills


def readjudicate(kind: str, seed: int, T: int, llm: str, root: Path, ref_dir: Path) -> dict:
    """Truncate a copy after round T // 2 and re-run Algorithm 2 on its stored measurements at delta x factor."""
    out = {}
    t_mid = T // 2
    for factor in FACTORS:
        d = root / f"readj_{factor}"
        shutil.copytree(ref_dir, d)
        rr = make_run(kind, seed, d, llm, T)
        delta = rr.delta()
        before = json.loads((d / f"r{t_mid}" / "decisions.json").read_text())
        rr.truncate(t_mid)
        roll = rr.measurer.n_rollouts
        after = [x.to_json() for x in rr.readjudicate(t_mid, rr.cfg.replace(delta=delta * factor))]
        keys = ("S", "C", "delta_S", "delta_C")
        out[f"delta x{factor}"] = {
            "delta_old": delta, "delta_new": delta * factor, "rollouts_spent": rr.measurer.n_rollouts - roll,
            "measurements_unchanged": all(tuple(old[k] for k in keys) == tuple(new[k] for k in keys)
                                          for old, new in zip(before, after)),
            "verdicts_changed": sum(old["admissible"] != new["admissible"] for old, new in zip(before, after)),
            "before": [(x["variant"], x["admissible"], x["reason"][:80]) for x in before],
            "after": [(x["variant"], x["admissible"], x["reason"][:80]) for x in after]}
    return out


def counterfactual_flips(kind: str, seed: int, T: int, llm: str, ref_dir: Path) -> tuple[dict, int]:
    """Verdicts of every stored round replayed at delta x factor: (flips per factor, decisions in total)."""
    rr = make_run(kind, seed, ref_dir, llm, T)
    traj = json.loads((ref_dir / "frontier.json").read_text())["trajectory"]
    flips = dict.fromkeys(FACTORS, 0)
    total = 0
    for t in range(T):
        inc = Measurement.load(ref_dir / "evals" / f"{traj[t]['job']}.json")
        S_star = max(x["S"] for x in traj[: t + 1])
        cands = rr._load_round_candidates(t)
        counts = rr.history.incumbent_component_counts(before_t=t)

        def verdicts(delta: float) -> list:
            return [d.admissible for d in select_round(cands, inc, S_star, delta, rr.cfg, counts,
                                                       build_gates(rr.cfg, rr.sw), taxonomy=rr.tax, t=t)[1]]
        base = verdicts(rr.delta())
        total += len(base)
        for f in flips:
            flips[f] += sum(x != y for x, y in zip(base, verdicts(rr.delta() * f)))
    return flips, total


def one(job: dict) -> dict:
    kind, seed, T, llm = job["kind"], job["seed"], job["T"], job["llm"]
    root = Path(tempfile.mkdtemp(prefix=f"e13_{kind}_{seed}_"))
    ref_dir = root / "ref"
    drive(make_run(kind, seed, ref_dir, llm, T))
    kills = kill_and_resume(kind, seed, T, llm, root, snapshot(ref_dir))
    readj = readjudicate(kind, seed, T, llm, root, ref_dir)
    flips, total = counterfactual_flips(kind, seed, T, llm, ref_dir)
    shutil.rmtree(root, ignore_errors=True)
    return {"kind": kind, "seed": seed, "T": T, "kills": kills, "readjudicate": readj,
            "counterfactual_flips": {f"delta x{f}": n for f, n in flips.items()}, "decisions_total": total}


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--resume-worker":           # child process of resume_in_subprocess
        kind, seed, out, llm, T = sys.argv[2], int(sys.argv[3]), Path(sys.argv[4]), sys.argv[5], int(sys.argv[6])
        print(drive(make_run(kind, seed, out, llm, T)))
        return
    a = parse_args("E13: kill/resume and readjudicate", default_seeds=5)
    T_hw, T_aq = (6, 4) if a.quick else (10, 6)
    jobs = [{"kind": "harnessworld", "seed": s, "T": T_hw, "llm": a.llm} for s in range(a.seeds)]
    jobs += [{"kind": "agentqa", "seed": s, "T": T_aq, "llm": a.llm} for s in range(min(2, a.seeds))]
    rows = pmap(one, jobs, a.procs)
    kills = [v for r in rows for v in r["kills"].values()]
    readj = [v for r in rows for v in r["readjudicate"].values()]
    checks = {"every kill actually interrupted the run": all(v["killed"] for v in kills),
              "resumed ledgers identical to uninterrupted runs": all(all(v["identical"].values()) for v in kills),
              "readjudicate spends no evaluation": all(v["rollouts_spent"] == 0 for v in readj),
              "readjudicate leaves measurements unchanged (only Algorithm 2 outcomes change)":
                  all(v["measurements_unchanged"] for v in readj)}
    out = {"experiment": "E13 kill/resume + readjudicate", "config": {"T_harnessworld": T_hw, "T_agentqa": T_aq,
                                                                      "kills": KILLS, "llm": a.llm},
           "checks": checks, "verdict": verdict(checks, "NOT MET: "), "rows": rows}
    save("e13_resume_readjudicate", out)
    print(json.dumps(checks, indent=1))
    for r in rows:
        print(r["kind"], r["seed"], {k: all(v["identical"].values()) for k, v in r["kills"].items()},
              {k: v["verdicts_changed"] for k, v in r["readjudicate"].items()}, r["counterfactual_flips"],
              r["decisions_total"])


if __name__ == "__main__":
    main()
