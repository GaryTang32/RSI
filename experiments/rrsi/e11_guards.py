"""E11 - Domain guards are non-compensatory (spec section 9.2, E11; eng instance's
``max_valid_rate_drop``).

A small engineering-design world built through the PUBLIC API only (a
:class:`rsi.core.FunctionDomain` + a user-supplied :class:`rsi.core.MetricGuard`), i.e. a
third domain besides HarnessWorld and AgentQA. The harness is ``strategy.md``: a list of
``enable(<mechanism>)`` lines. A trial produces a design that is VALID (meets its hard
constraint) or not; the reward is the verifier's pass/fail of a valid design (an invalid
design scores 0). Mechanisms in the catalog are

* ``gen_*``  generic: raise the pass probability, validity unchanged;
* ``agg_*``  aggressive: raise the pass probability MORE, but push designs into territory
  that violates the hard constraint more often (validity drops a few points each). On the
  practice verifier the net measured score still goes UP ("a scaffold can lift the average
  by pushing designs into aggressive territory that violates a hard constraint outright");
  on the hardened split (``ood``: the same families with the exploited slack closed, like
  EngDesign v1 "with nine grading exploits closed") their pass-rate gain vanishes and only
  the validity loss remains;
* ``nul_*`` / ``harm_*``  null and harmful edits.

Arms (same seeds, same proposer, same T x m): full RRSI with the guard
``MetricGuard("valid", 0.03)`` (the eng value ``max_valid_rate_drop``) vs full RRSI with
``domain_guards=False``. Confirming outcome (spec E11): with guards on, no accepted
candidate lowers the measured valid rate by more than the tolerance; with guards off such
candidates are accepted; the guarded harness keeps its true validity and does better on the
hardened split.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import RESULTS, _scratch, paired, parse_args, pmap, save, search_llm, summarize  # noqa: E402

from rsi.core import Artifact, Execution, FunctionDomain, MetricGuard, MockLLM, Task, TaskSuite  # noqa: E402
from rsi.rrsi import Config, RegularizerSwitches, parse_sections, run  # noqa: E402

FAMILIES = ("beam", "filter", "control", "circuit")
N_PER_SPLIT = 40
TOL = 0.03                                   # eng rrsi.json: max_valid_rate_drop


# ------------------------------------------------------------------------------ the world
def catalog(seed: int) -> dict:
    rng = random.Random(f"e11-cat-{seed}")
    cat = {}
    for i in range(10):
        cat[f"gen_{i:02d}"] = {"kind": "generic", "score": rng.uniform(0.08, 0.2), "invalid": 0.0,
                               "title": rng.choice(["Check units before submitting", "Size margins from the spec",
                                                    "Re-derive the key equation", "Tabulate constraints first"])}
    for i in range(10):
        cat[f"agg_{i:02d}"] = {"kind": "aggressive", "score": rng.uniform(0.25, 0.45), "invalid": rng.uniform(0.5, 0.9),
                               "title": rng.choice(["Push the design to the objective's limit",
                                                    "Trim safety margins to raise the figure of merit",
                                                    "Optimize the headline metric aggressively"])}
    for i in range(8):
        cat[f"nul_{i:02d}"] = {"kind": "null", "score": 0.0, "invalid": 0.0, "title": "Reword the task wrapper"}
    for i in range(6):
        cat[f"harm_{i:02d}"] = {"kind": "harmful", "score": -rng.uniform(0.1, 0.2), "invalid": rng.uniform(0.0, 0.2),
                                "title": rng.choice(["Skip the final consistency check", "Answer from the first draft"])}
    return cat


def make_domain(seed: int):
    rng = random.Random(f"e11-tasks-{seed}")
    tasks, splits = [], {"evolve": [], "holdout": [], "ood": []}
    for split, prefix in (("evolve", "e"), ("holdout", "h"), ("ood", "x")):
        for i in range(N_PER_SPLIT):
            fam = FAMILIES[i % len(FAMILIES)]
            tid = f"eng-{prefix}-{i:03d}"
            tasks.append(Task(tid, f"[{fam}] design task {tid}", None, fam,
                              {"difficulty": rng.gauss(0.0, 1.0), "hardened": split == "ood"}))
            splits[split].append(tid)
    cat = catalog(seed)

    def enabled(art: Artifact) -> list[str]:
        ids = re.findall(r"^enable\((\w+)\)\s*$", art.get("strategy.md") or "", re.M)
        return [m for m in dict.fromkeys(ids) if m in cat]

    def probs(art: Artifact, task: Task) -> tuple[float, float]:
        ms = enabled(art)
        z = -task.meta["difficulty"] + sum(cat[m]["score"] for m in ms if not (task.meta["hardened"]
                                                                                 and cat[m]["kind"] == "aggressive"))
        zi = -3.9 + sum(cat[m]["invalid"] for m in ms)          # H_0 invalid rate ~2%
        return 1 / (1 + math.exp(-z)), 1 / (1 + math.exp(-zi))

    def execute(art, task, seed_, llm):
        p_pass, p_inv = probs(art, task)
        r = random.Random(int(hashlib.sha256(f"{art.id}|{task.id}|{seed_}".encode()).hexdigest()[:12], 16))
        valid = r.random() >= p_inv
        passed = valid and r.random() < p_pass
        ms = enabled(art)
        return Execution(output={"valid": valid, "passed": passed}, tokens=1000 + 60 * len(ms), steps=1 + len(ms),
                         trace=f"mechanisms: {ms}\ndesign valid={valid} passed={passed}",
                         meta={"valid": int(valid)})

    def grade(task, out):
        if not out["valid"]:
            return 0.0, "INVALID DESIGN: a hard constraint is violated"
        return (1.0, "passed") if out["passed"] else (0.0, "UNDER TARGET: valid design below the pass threshold")

    rows = "\n".join(f"- {m}: {c['title']}" for m, c in cat.items())
    dom = FunctionDomain(TaskSuite(tasks, splits, name=f"e11-{seed}"), execute, grade, name="guardworld",
                         description=("The harness is strategy.md: one `enable(<mechanism>)` line per active design "
                                      "mechanism (the file must keep that format). A frozen designer produces one design "
                                      "per task; a verifier scores it 1 if the design is valid and meets the target, "
                                      "else 0. Mechanism catalog (true effects unknown):\n" + rows))
    dom.components = {"control_flow": ["strategy.md"]}
    dom.expected = lambda art, split: _expected(dom, probs, art, split)
    return dom, cat, enabled


def _expected(dom, probs, art, split) -> dict:
    ps = [probs(art, t) for t in dom.tasks.split(split, allow_sealed=True)]
    return {"S": sum((1 - pi) * pp for pp, pi in ps) / len(ps), "valid": sum(1 - pi for _, pi in ps) / len(ps)}


SEED_ART = Artifact({"strategy.md": "enable(gen_00)\n"})


# ------------------------------------------------------------------------------ mock proposer
def mock_proposer(cat: dict, shares=None):
    shares = shares or {"generic": 0.3, "aggressive": 0.35, "null": 0.2, "harmful": 0.15}

    def respond(prompt, system, seed, i):
        if "strict reviewer" in (system or ""):
            return json.dumps({"verdict": "accept", "reasons": [], "risk_notes": []})
        if not (system or "").startswith("You are a harness engineer agent"):
            return "{}"
        P = parse_sections(prompt)
        d, src = P["directives"], P["files"].get("strategy.md", "")
        rng = random.Random(int(hashlib.sha256(prompt.encode()).hexdigest()[:12], 16) ^ seed)
        present = set(re.findall(r"^enable\((\w+)\)", src, re.M))
        pool = [m for m in cat if m not in present]
        n = rng.randint(1, int(d.get("b_t", 1) or 1))
        chosen = []
        while pool and len(chosen) < n:
            w = [shares[cat[m]["kind"]] / sum(1 for x in pool if cat[x]["kind"] == cat[m]["kind"]) for m in pool]
            m = rng.choices(pool, weights=w)[0]
            pool.remove(m)
            chosen.append(m)
        edits = [{"id": f"C{j + 1}", "component": "control_flow", "hypothesis": f"[{m}] {cat[m]['title']}",
                  "targets_mode": "UNDER TARGET designs", "predicted_affected": list(d.get("trace_task_ids") or [])[:2]
                  or ["eng-e-000"], "retroactive_check": "(corrective)(preservative)(transfer)", "mechanism_id": m}
                 for j, m in enumerate(chosen)]
        new = src.rstrip("\n") + "\n" + "".join(f"enable({m})\n" for m in chosen)
        head = {"action": "done", "summary": "; ".join(e["hypothesis"] for e in edits), "edits": edits}
        return "```json\n" + json.dumps(head) + "\n```\n=== FILE: strategy.md ===\n" + new
    return MockLLM(respond, name="e11-mock")


# ------------------------------------------------------------------------------ one run
def one(job: dict) -> dict:
    seed, guarded = job["seed"], job["guarded"]
    dom, cat, enabled = make_domain(seed)
    llm = mock_proposer(cat) if job["llm"] == "sim" else search_llm(job["llm"])
    sw = RegularizerSwitches.full() if guarded else RegularizerSwitches.full().but(domain_guards=False, name="no_guards")
    out = Path(tempfile.mkdtemp(prefix=f"rrsi_e11_{seed}_", dir=_scratch()))
    t0 = time.time()
    res = run(dom, SEED_ART, llm_propose=llm, config=Config(T=job["T"], workers=1, seed=seed), out_dir=out,
              switches=sw, guards=(MetricGuard("valid", TOL),))
    if res.stop_reason != "max_rounds" and job["llm"] == "sim":
        raise RuntimeError(f"e11 seed {seed} stopped early: {res.stop_reason}")
    # every accepted candidate: did its MEASURED valid rate drop by more than the tolerance vs the incumbent?
    fr = json.loads((out / "frontier.json").read_text())
    traj = fr["trajectory"]
    violations = accepted = measured_violators = 0
    for t in range(len(traj) - 1):
        inc = json.loads((out / "evals" / f"{traj[t]['job']}.json").read_text())["extra"].get("valid", 1.0)
        for v in (out / f"r{t}").iterdir():
            ep = v / "eval.json"
            if not (v.is_dir() and ep.exists()):
                continue
            drop = inc - json.loads(ep.read_text())["extra"].get("valid", 1.0)
            measured_violators += drop > TOL + 1e-9
            if traj[t + 1]["job"] == f"r{t}{v.name}" and traj[t + 1]["job"] != traj[t]["job"]:
                accepted += 1
                violations += drop > TOL + 1e-9
    h0, fin = res.baseline, res.best
    m = {"seed": seed, "label": "guards on" if guarded else "guards off", "wall_s": time.time() - t0,
         "stop_reason": res.stop_reason, "accepted": accepted, "accepted_guard_violations": violations,
         "evaluated_guard_violators": measured_violators,
         "n_aggressive_final": sum(1 for x in enabled(fin) if cat[x]["kind"] == "aggressive"),
         "measured_gain": traj[-1]["S"] - traj[0]["S"]}
    for split in ("evolve", "holdout", "ood"):
        e0, e1 = dom.expected(h0, split), dom.expected(fin, split)
        m[f"{split}_gain"] = e1["S"] - e0["S"]
        m[f"{split}_valid_change"] = e1["valid"] - e0["valid"]
    shutil.rmtree(out, ignore_errors=True)
    return m


def main():
    a = parse_args("E11: domain guards are non-compensatory", default_seeds=30)
    T = 6 if a.quick else 12
    jobs = [{"seed": s, "guarded": g, "T": T, "llm": a.llm} for s in range(a.seeds) for g in (True, False)]
    rows = pmap(one, jobs, a.workers if a.llm == "sim" else 1)
    metrics = ("accepted", "accepted_guard_violations", "evaluated_guard_violators", "n_aggressive_final",
               "measured_gain", "evolve_gain", "holdout_gain", "ood_gain", "evolve_valid_change",
               "holdout_valid_change", "ood_valid_change")
    summ = summarize(rows, metrics=metrics)
    pd = {m: paired(rows, "guards off", "guards on", m) for m in
          ("accepted_guard_violations", "n_aggressive_final", "measured_gain", "holdout_valid_change", "ood_gain")}
    on, off = summ["guards on"], summ["guards off"]
    checks = {
        "guards on: no accepted candidate lowers the measured valid rate beyond tol":
            sum(r["accepted_guard_violations"] for r in rows if r["label"] == "guards on") == 0,
        "guard violators were proposed and evaluated in both arms":
            on["evaluated_guard_violators"]["mean"] > 0 and off["evaluated_guard_violators"]["mean"] > 0,
        "guards off: such candidates are accepted": off["accepted_guard_violations"]["mean"] > 0,
        "guards keep true validity (held-out valid-rate change: on - off > 0)": pd["holdout_valid_change"]["lo"] > 0,
        "guarded harness better on the hardened split (ood: on - off > 0)": pd["ood_gain"]["lo"] > 0,
    }
    out = {"experiment": "E11 domain guards", "config": {"T": T, "m": 2, "k": 2, "seeds": a.seeds, "tolerance": TOL,
                                                         "guard": f"MetricGuard('valid', {TOL})", "llm": a.llm,
                                                         "tasks_per_split": N_PER_SPLIT},
           "summary": summ, "paired_on_minus_off": pd, "checks": checks,
           "verdict": "REPRODUCED" if all(checks.values()) else
           "PARTIAL - not met: " + "; ".join(k for k, v in checks.items() if not v),
           "note": "measured_gain on the practice verifier is expected to be HIGHER without guards (aggressive "
                   "mechanisms lift the practice score); the guard is non-compensatory by design.",
           "rows": rows}
    save("e11_guards", out)
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(8, 4))
        labs = ["measured_gain", "holdout_gain", "ood_gain", "holdout_valid_change"]
        for j, arm in enumerate(["guards off", "guards on"]):
            s = summ[arm]
            ax.bar([i + 0.4 * j for i in range(len(labs))], [s[m]["mean"] * 100 for m in labs], width=0.4, label=arm,
                   yerr=[[(s[m]["mean"] - s[m]["lo"]) * 100 for m in labs], [(s[m]["hi"] - s[m]["mean"]) * 100 for m in labs]])
        ax.set_xticks([i + 0.2 for i in range(len(labs))])
        ax.set_xticklabels(["measured\npractice", "true\nheld-out", "true\nhardened (ood)", "true valid rate\n(held-out)"])
        ax.axhline(0, color="k", lw=0.5)
        ax.set_ylabel("change vs H_0 (points)")
        ax.set_title(f"E11 domain guard valid-rate drop <= {TOL} ({a.seeds} seeds, 95% CI)")
        ax.legend()
        fig.tight_layout()
        fig.savefig(RESULTS / "e11_guards.png", dpi=110)
    except Exception as e:  # noqa: BLE001
        print("figure skipped:", e)
    for k, v in summ.items():
        print(k, {m: round(x["mean"], 3) for m, x in v.items()})
    print(checks)
    print(out["verdict"])


if __name__ == "__main__":
    main()
