"""Reviewer tests: RRSI on a NEW domain it has never seen (a plain FunctionDomain), the sealed-split
discipline, cross-process determinism, and regressions for bugs found in review (held-out monitor
de-duplication, reserved-slot check with the critic ablated, repair only removing objected edits,
single-pass paired transfer, torn JSONL lines)."""
import hashlib
import json
import os
import random
import subprocess
import sys
from pathlib import Path

from rsi.core import Artifact, FunctionDomain, MockLLM, Task, TaskSuite
from rsi.rrsi import Config, RegularizerSwitches, RRSIRun, drive, paired_transfer, parse_sections, run
from rsi.rrsi.history import History

ROOT = Path(__file__).resolve().parents[1]

# ------------------------------------------------------------------ a brand-new problem
WORDS = ["Alpha", "  beta", "GAMMA  ", "Delta", " epsilon ", "ZETA", "eta ", " Theta", "IOTA", "kappa  "]


def _suite() -> TaskSuite:
    tasks = []
    for i in range(60):
        raw = WORDS[i % len(WORDS)] + ("  " if i % 3 == 0 else "") + str(i)
        fam = "names" if i < 40 else "codes"
        if fam == "codes":
            raw = "  CODE-" + raw.strip().upper()
        tasks.append(Task(f"q{i:02d}", raw, raw.strip().lower(), fam))
    return TaskSuite(tasks, {"evolve": [f"q{i:02d}" for i in range(20)],
                             "holdout": [f"q{i:02d}" for i in range(20, 40)],
                             "ood": [f"q{i:02d}" for i in range(40, 60)]}, name="normalize")


def _execute(artifact, task, seed, llm):
    """The harness is solve.py; a flaky frozen "model" corrupts 30% of outputs (seeded)."""
    ns: dict = {}
    exec(artifact["solve.py"], ns)                                   # noqa: S102 - test harness
    out = ns["solve"](task.input)
    rng = random.Random(f"{task.id}|{seed}")
    return out + "?" if rng.random() < 0.3 else out


def make_domain() -> FunctionDomain:
    return FunctionDomain(_suite(), _execute, lambda task, out: float(out == task.target), name="normalize",
                          description="solve.py defines solve(text) -> normalized text; exact-match grading.")


SEED = Artifact({"solve.py": "def solve(text):\n    return text.strip()\n"})
GENERIC = "def solve(text):\n    return text.strip().lower()\n"
LOWER = "def solve(text):\n    return text.lower()\n"


def _edit(comp, hyp):
    return {"id": "C1", "component": comp, "hypothesis": hyp, "targets_mode": "unnormalized output",
            "predicted_affected": ["q00", "q03"], "retroactive_check": "(corrective) (preservative) (transfer)"}


def _reply(files: dict, comp: str, hyp: str) -> str:
    head = json.dumps({"action": "done", "summary": hyp, "edits": [_edit(comp, hyp)]})
    return "```json\n" + head + "\n```\n" + "\n".join(f"=== FILE: {p} ===\n{t}" for p, t in files.items())


def proposer(prompt, system, seed, i):
    if "strict reviewer" in (system or ""):
        return json.dumps({"verdict": "accept", "reasons": [], "risk_notes": []})
    P = parse_sections(prompt)
    d, src = P["directives"], P["files"].get("solve.py", "")
    if "REPAIR ROUND" in P["task"]:                                  # drop the leaked table, ship the generic fix
        return _reply({"solve.py": GENERIC}, "control_flow", "strip and lowercase every output")
    if d.get("t") == 0 and d.get("variant") == "A":                  # a leaky lookup table of evolve answers
        table = {t.input: t.target for t in _suite().split("evolve")[:6]}
        leak = f"ANSWERS = {json.dumps(table)}\n\n" + LOWER.replace("return text.lower()",
                                                                   "return ANSWERS.get(text, text.lower())")
        return _reply({"solve.py": leak}, "memory", "remember practice answers")
    if ".lower()" not in src:
        return _reply({"solve.py": LOWER}, "control_flow", "lowercase outputs")
    return _reply({"solve.py": src + "\n# normalization is complete\n"}, "prompt", "document the contract")


def test_rrsi_runs_unchanged_on_a_new_function_domain(tmp_path):
    dom = make_domain()
    calls = []
    orig = dom.tasks.split

    def spy(name, *, allow_sealed=False):
        calls.append((name, allow_sealed))
        return orig(name, allow_sealed=allow_sealed)
    dom.tasks.split = spy
    res = run(dom, SEED, llm_propose=MockLLM(proposer), config=Config(T=4, workers=1), out_dir=tmp_path / "n")
    dom.tasks.split = orig
    # the loop only ever read the decision split (the critic's leakage terms read evolve explicitly)
    assert {n for n, _ in calls} <= {"evolve"}, calls
    assert res.stop_reason == "max_rounds" and len(res.trajectory) == 5
    assert res.meta["delta"] > 0                                     # calibrated by the within-task bootstrap
    assert "strip().lower()" in res.best["solve.py"] and "ANSWERS" not in res.best["solve.py"]
    a0 = json.loads((tmp_path / "n" / "r0" / "A" / "critic_a0.json").read_text())
    assert a0["verdict"] == "reject" and a0["stage"] == "precheck"   # evolve answers caught before evaluation
    recs = [json.loads(line) for line in (tmp_path / "n" / "history.jsonl").read_text().splitlines()]
    assert {r["component"] for r in recs if r.get("edit_id")} <= set(res.meta["K"])
    assert {"proposer", "critic"} <= set(res.usage)
    rep = paired_transfer(dom, None, {"H0": res.baseline, "final": res.best}, k=4)
    assert rep["splits"]["holdout"]["final"]["S"] > rep["splits"]["holdout"]["H0"]["S"] + 0.3
    assert rep["splits"]["ood"]["final"]["S"] > rep["splits"]["ood"]["H0"]["S"] + 0.3
    assert rep["non_regression"]["final"] and rep["fresh_rollouts_in_win_tie_loss_pass"] == 0


def test_unregularized_arm_keeps_the_leak_on_the_new_domain(tmp_path):
    dom = make_domain()
    res = run(dom, SEED, llm_propose=MockLLM(proposer), config=Config(T=1, k=2, workers=1),
              out_dir=tmp_path / "u", switches=RegularizerSwitches.none())
    assert "ANSWERS" in res.best["solve.py"] and res.method == "rrsi[unregularized]"


# ------------------------------------------------------------------ determinism across processes
_DET = """
import sys, json, hashlib, tempfile, pathlib
sys.path.insert(0, %r)
from rsi.domains.harnessworld import HarnessWorldMockLLM, make_domain
from rsi.rrsi import Config, run
dom = make_domain(seed=1, n_evolve=24, n_holdout=24, n_ood_per_family=6)
d = tempfile.mkdtemp()
res = run(dom, dom.seed_artifact(), llm_propose=HarnessWorldMockLLM(dom.world),
          config=Config(T=8, workers=1, record_timestamps=False), out_dir=d)
print(hashlib.sha256(pathlib.Path(d, "history.jsonl").read_bytes()).hexdigest(), res.best.id)
from rsi.domains.agentqa import AgentQADomain, SimModel, make_suite      # AgentQA: sc3 majority-vote ties
from rsi.rrsi.mocks import AgentQAMockLLM
suite = make_suite(seed=0)
d = tempfile.mkdtemp()
res = run(AgentQADomain(suite), AgentQADomain.seed_artifact(), llm_task=SimModel(suite), llm_propose=AgentQAMockLLM(),
          config=Config(T=4, workers=4, record_timestamps=False), out_dir=d)
print(hashlib.sha256(pathlib.Path(d, "history.jsonl").read_bytes()).hexdigest(), res.best.id)
""" % str(ROOT)


def test_offline_run_is_identical_across_processes_and_hash_seeds():
    outs = set()
    for hs in ("0", "12345"):
        env = dict(os.environ, PYTHONHASHSEED=hs)
        p = subprocess.run([sys.executable, "-c", _DET], capture_output=True, text=True, env=env, timeout=120)
        assert p.returncode == 0, p.stderr
        outs.add(p.stdout.strip())
    assert len(outs) == 1, outs


# ------------------------------------------------------------------ regressions
def test_heldout_monitor_logs_every_accepted_round(tmp_path):
    dom = make_domain()
    seq = [LOWER, GENERIC, GENERIC + "\n# a\n", GENERIC + "\n# b\n"]

    def always_better(prompt, system, seed, i):
        if "strict reviewer" in (system or ""):
            return json.dumps({"verdict": "accept", "reasons": [], "risk_notes": []})
        d = parse_sections(prompt)["directives"]
        return _reply({"solve.py": seq[min(d.get("t", 0), 3)]}, "control_flow", f"step {d.get('t')}")
    r = RRSIRun(dom, SEED, out_dir=tmp_path / "m", llm_propose=MockLLM(always_better),
                config=Config(T=2, m=1, workers=1, delta=0.0, heldout_monitor=True))
    drive(r)
    winners = [json.loads((tmp_path / "m" / f"r{t}" / "summary.json").read_text())["winner"] for t in range(2)]
    assert winners == ["A", "A"]
    rows = [json.loads(x) for x in (tmp_path / "m" / "heldout_monitor.jsonl").read_text().splitlines()]
    assert [x["t"] for x in rows] == [1, 2]


def test_reserved_slot_checked_on_the_diff_even_without_critic(tmp_path):
    from rsi.domains.harnessworld import HarnessWorldMockLLM, make_domain as hw
    dom = hw(seed=4, n_evolve=24, n_holdout=24, n_ood_per_family=6)
    sw = RegularizerSwitches.full().but(critic=False, name="no_critic")
    r = RRSIRun(dom, dom.seed_artifact(), out_dir=tmp_path / "c", llm_propose=HarnessWorldMockLLM(dom.world),
                config=Config(T=8, workers=1, delta=0.5), switches=sw)        # huge band -> stall from t = w
    drive(r)
    reserved = []
    for t in range(8):
        dirs = json.loads((tmp_path / "c" / f"r{t}" / "directives.json").read_text())
        if dirs["sigma_t"] and dirs["explore"]["untried"]:
            p = tmp_path / "c" / f"r{t}" / "B" / "critic_a0.json"
            assert p.exists()                                        # the slot check ran without a critic
            prep = json.loads((tmp_path / "c" / f"r{t}" / "B" / "prep.json").read_text())
            if not prep.get("gate_failure"):
                reserved.append(any(e["component"] in dirs["explore"]["untried"] for e in prep["edits"]))
    assert reserved and all(reserved)


def test_mock_repair_removes_only_objected_edits():
    from rsi.domains.harnessworld import HarnessWorldMockLLM, make_domain as hw
    dom = hw(seed=0, n_evolve=30, n_holdout=30, n_ood_per_family=8)
    llm = HarnessWorldMockLLM(dom.world)
    obf = next(m for m in dom.world.catalog.values() if m.kind == "obfuscated_leak")
    lit = next(m for m in dom.world.catalog.values() if m.kind == "leak")
    gen = next(m for m in dom.world.catalog.values() if m.kind == "generic" and m.id not in dom.world.seed_ids)
    e_obf = llm._edit(0, obf, "m", ["hw-e-000"])
    e_gen = llm._edit(1, gen, "m", ["hw-e-000"])
    e_lit = llm._edit(2, lit, "m", ["hw-e-000"])
    target = dom.world.by_id[lit.leak_keys[0]].target
    assert llm._objected(lit, e_lit, f"precheck: hard-coded evaluation data '{target}'")
    assert not llm._objected(obf, e_obf, f"precheck: hard-coded evaluation data '{target}'")   # missed leak stays
    assert llm._objected(obf, e_obf, f"LEAKAGE: mechanism {obf.id} injects a lookup table")
    assert not llm._objected(gen, e_gen, "RESERVED EXPLORATION SLOT: none is")


def test_history_survives_a_torn_last_line(tmp_path):
    h = History(tmp_path / "h.jsonl", timestamps=False)
    h.append_candidate(0, "A", [{"id": "C1", "component": "prompt", "hypothesis": "x"},
                                {"id": "C2", "component": "memory", "hypothesis": "y"}], "LOST", 0.01, 0.0, False,
                       0.5, 10.0, None)
    with open(tmp_path / "h.jsonl", "a") as f:
        f.write('{"t": 1, "variant": "B", "edit_')                  # a crash mid-write
    assert len(h.records()) == 2 and h.tried() == {"prompt", "memory"}
    h.append_candidate(1, "B", [{"id": "C1", "component": "skill", "hypothesis": "z"}], "LOST", 0.02, 0.0, False,
                       0.5, 10.0, None)
    assert [r["component"] for r in h.records()] == ["prompt", "memory", "skill"]


def test_catalog_text_never_names_sealed_ood_families():
    from rsi.domains.harnessworld import make_domain as hw
    dom = hw(seed=3)
    ood = dom.world.cfg.ood_families
    for m in dom.world.catalog.values():
        assert "aimed at" not in m.description or not any(f in m.description.split("aimed at")[1] for f in ood)
    digest = hashlib.sha256(dom.describe().encode()).hexdigest()
    assert digest == hashlib.sha256(hw(seed=3).describe().encode()).hexdigest()


def test_critic_precheck_blocks_grader_tampering():
    from rsi.rrsi import RRSICritic
    crit = RRSICritic(make_domain())
    tamper = SEED.with_files({"solve.py": "import rsi.core.domain as d\nd.FunctionDomain.grade = lambda *a: (1.0, '')\n"
                                          "def solve(text):\n    return text\n"})
    v = crit.review(SEED.diff(tamper))
    assert v["verdict"] == "reject" and v["stage"] == "precheck" and "grader" in v["reasons"][0]
    assert crit.review(tamper.diff(SEED))["verdict"] == "accept"      # removing it is not tampering
