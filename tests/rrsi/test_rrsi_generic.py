"""RRSI on a new FunctionDomain (text normalization), sealed-split discipline, cross-process determinism, and
review regressions (held-out monitor, reserved slot without a critic, repair, torn JSONL)."""
import json
import os
import random
import subprocess
import sys
from pathlib import Path

from rsi.core import Artifact, FunctionDomain, MockLLM, Task, TaskSuite
from rsi.domains.harnessworld import HarnessWorldMockLLM, make_domain
from rsi.rrsi import Config, RegularizerSwitches, RRSICritic, RRSIRun, drive, paired_transfer, parse_sections, run
from rsi.rrsi.history import History

from rrsi_helpers import ACCEPT, edit, edit_rows, hw_domain, mechanism, read_json, read_jsonl, reply, spy_splits

ROOT = Path(__file__).resolve().parents[2]
WORDS = ["Alpha", "  beta", "GAMMA  ", "Delta", " epsilon ", "ZETA", "eta ", " Theta", "IOTA", "kappa  "]
SEED = Artifact({"solve.py": "def solve(text):\n    return text.strip()\n"})
GENERIC = "def solve(text):\n    return text.strip().lower()\n"
LOWER = "def solve(text):\n    return text.lower()\n"


def _suite() -> TaskSuite:
    tasks = []
    for i in range(60):
        raw = WORDS[i % len(WORDS)] + ("  " if i % 3 == 0 else "") + str(i)
        fam = "names" if i < 40 else "codes"
        if fam == "codes":
            raw = "  CODE-" + raw.strip().upper()
        tasks.append(Task(f"q{i:02d}", raw, raw.strip().lower(), fam))
    ids = [t.id for t in tasks]
    return TaskSuite(tasks, {"evolve": ids[:20], "holdout": ids[20:40], "ood": ids[40:]}, name="normalize")


def _execute(artifact, task, seed, llm):
    ns: dict = {}
    exec(artifact["solve.py"], ns)                                   # noqa: S102 - the harness is the artifact
    out = ns["solve"](task.input)
    return out + "?" if random.Random(f"{task.id}|{seed}").random() < 0.3 else out   # a flaky frozen "model"


def normalize_domain() -> FunctionDomain:
    return FunctionDomain(_suite(), _execute, lambda task, out: float(out == task.target), name="normalize",
                          description="solve.py defines solve(text) -> normalized text; exact-match grading.")


def _done(src: str, comp: str, hyp: str) -> str:
    e = edit(comp, hyp, targets_mode="unnormalized output", predicted_affected=["q00", "q03"])
    return reply({"solve.py": src}, [e], summary=hyp)


def proposer(prompt, system, seed, i):
    if "strict reviewer" in (system or ""):
        # as in the code, a literal answer table is the LLM review's to catch (REJECT rule 1), not the precheck's
        added = "\n".join(l for l in prompt.split("=== DIFF ===")[-1].splitlines() if l.startswith("+"))
        if "ANSWERS = {" in added:
            return json.dumps({"verdict": "reject", "reasons": ["LEAKAGE: hard-codes practice answers"],
                               "risk_notes": []})
        return ACCEPT
    P = parse_sections(prompt)
    d, src = P["directives"], P["files"].get("solve.py", "")
    if "REPAIR ROUND" in P["task"]:
        return _done(GENERIC, "control_flow", "strip and lowercase every output")
    if d.get("t") == 0 and d.get("variant") == "A":                  # a leaky lookup table of evolve answers
        table = {t.input: t.target for t in _suite().split("evolve")[:6]}
        leak = f"ANSWERS = {json.dumps(table)}\n\n" + LOWER.replace("return text.lower()",
                                                                   "return ANSWERS.get(text, text.lower())")
        return _done(leak, "memory", "remember practice answers")
    if ".lower()" not in src:
        return _done(LOWER, "control_flow", "lowercase outputs")
    return _done(src + "\n# normalization is complete\n", "prompt", "document the contract")


def test_rrsi_runs_unchanged_on_a_new_function_domain(tmp_path):
    dom = normalize_domain()
    calls = spy_splits(dom)
    res = run(dom, SEED, llm_propose=MockLLM(proposer), config=Config(T=4, workers=1), out_dir=tmp_path / "n")
    assert {n for n, _ in calls} <= {"evolve"}, calls
    assert res.stop_reason == "max_rounds" and len(res.trajectory) == 5
    assert res.meta["delta"] > 0                                     # calibrated by the within-task bootstrap
    assert "strip().lower()" in res.best["solve.py"] and "ANSWERS" not in res.best["solve.py"]
    a0 = read_json(tmp_path / "n" / "r0" / "A" / "critic_a0.json")
    assert a0["verdict"] == "reject" and a0["stage"] == "llm"        # evolve answers caught before evaluation
    recs = read_jsonl(tmp_path / "n" / "history.jsonl")
    assert {r["component"] for r in recs if r.get("edit_id")} <= set(res.meta["K"])
    assert {"proposer", "critic"} <= set(res.usage)
    rep = paired_transfer(dom, None, {"H0": res.baseline, "final": res.best}, k=4)
    for split in ("holdout", "ood"):
        assert rep["splits"][split]["final"]["S"] > rep["splits"][split]["H0"]["S"] + 0.3
    assert rep["non_regression"]["final"] and rep["fresh_rollouts_in_win_tie_loss_pass"] == 0


def test_answer_denylist_is_an_opt_in_precheck_extension(tmp_path):
    res = run(normalize_domain(), SEED, llm_propose=MockLLM(proposer), out_dir=tmp_path / "x",
              config=Config(T=1, workers=1, precheck_answers=True))
    a0 = read_json(tmp_path / "x" / "r0" / "A" / "critic_a0.json")
    assert a0["verdict"] == "reject" and a0["stage"] == "precheck"
    assert any("hard-coded evaluation data" in r for r in a0["reasons"])
    assert "ANSWERS" not in res.best["solve.py"]


def test_unregularized_arm_keeps_the_leak_on_the_new_domain(tmp_path):
    res = run(normalize_domain(), SEED, llm_propose=MockLLM(proposer), config=Config(T=1, k=2, workers=1),
              out_dir=tmp_path / "u", switches=RegularizerSwitches.none())
    assert "ANSWERS" in res.best["solve.py"] and res.method == "rrsi[unregularized]"


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
        p = subprocess.run([sys.executable, "-c", _DET], capture_output=True, text=True, timeout=120,
                           env=dict(os.environ, PYTHONHASHSEED=hs))
        assert p.returncode == 0, p.stderr
        outs.add(p.stdout.strip())
    assert len(outs) == 1, outs


def test_heldout_monitor_logs_every_accepted_round(tmp_path):
    seq = [LOWER, GENERIC, GENERIC + "\n# a\n", GENERIC + "\n# b\n"]

    def always_better(prompt, system, seed, i):
        if "strict reviewer" in (system or ""):
            return ACCEPT
        t = parse_sections(prompt)["directives"].get("t")
        return _done(seq[min(t or 0, 3)], "control_flow", f"step {t}")
    r = RRSIRun(normalize_domain(), SEED, out_dir=tmp_path / "m", llm_propose=MockLLM(always_better),
                config=Config(T=2, m=1, workers=1, delta=0.0, heldout_monitor=True))
    drive(r)
    assert [read_json(tmp_path / "m" / f"r{t}" / "summary.json")["winner"] for t in range(2)] == ["A", "A"]
    assert [x["t"] for x in read_jsonl(tmp_path / "m" / "heldout_monitor.jsonl")] == [1, 2]


def test_reserved_slot_checked_on_the_diff_even_without_critic(tmp_path):
    dom, out = hw_domain(4), tmp_path / "c"
    sw = RegularizerSwitches.full().but(critic=False, name="no_critic")
    r = RRSIRun(dom, dom.seed_artifact(), out_dir=out, llm_propose=HarnessWorldMockLLM(dom.world),
                config=Config(T=8, workers=1, delta=0.5), switches=sw)        # huge band -> stall from t = w
    drive(r)
    reserved = []
    for t in range(8):
        dirs = read_json(out / f"r{t}" / "directives.json")
        if dirs["sigma_t"] and dirs["explore"]["untried"]:
            assert (out / f"r{t}" / "B" / "critic_a0.json").exists()  # the slot check ran without a critic
            prep = read_json(out / f"r{t}" / "B" / "prep.json")
            if not prep.get("gate_failure"):
                reserved.append(any(e["component"] in dirs["explore"]["untried"] for e in prep["edits"]))
    assert reserved and all(reserved)


def test_mock_repair_removes_only_objected_edits():
    dom = hw_domain(0, n_evolve=30, n_holdout=30, n_ood_per_family=8)
    llm = HarnessWorldMockLLM(dom.world)
    obf, gen, lit = (mechanism(dom, k) for k in ("obfuscated_leak", "generic", "leak"))
    e_obf, e_gen, e_lit = (llm._edit(i, m, "m", ["hw-e-000"]) for i, m in enumerate((obf, gen, lit)))
    precheck = f"precheck: hard-coded evaluation data '{dom.world.by_id[lit.leak_keys[0]].target}'"
    assert llm._objected(lit, e_lit, precheck)
    assert not llm._objected(obf, e_obf, precheck)                   # a missed leak stays
    assert llm._objected(obf, e_obf, f"LEAKAGE: mechanism {obf.id} injects a lookup table")
    assert not llm._objected(gen, e_gen, "RESERVED EXPLORATION SLOT: none is")


def test_history_survives_a_torn_last_line(tmp_path):
    h = History(tmp_path / "h.jsonl", timestamps=False)
    h.append_candidate(0, "A", edit_rows(prompt="x", memory="y"), "LOST", 0.01, 0.0, False, 0.5, 10.0, None)
    with open(tmp_path / "h.jsonl", "a") as f:
        f.write('{"t": 1, "variant": "B", "edit_')                  # a crash mid-write
    assert len(h.records()) == 2 and h.tried() == {"prompt", "memory"}
    h.append_candidate(1, "B", edit_rows(skill="z"), "LOST", 0.02, 0.0, False, 0.5, 10.0, None)
    assert [r["component"] for r in h.records()] == ["prompt", "memory", "skill"]


def test_catalog_text_never_names_sealed_ood_families():
    dom = make_domain(seed=3)
    ood = dom.world.cfg.ood_families
    for m in dom.world.catalog.values():
        assert "aimed at" not in m.description or not any(f in m.description.split("aimed at")[1] for f in ood)
    assert dom.describe() == make_domain(seed=3).describe()


def test_critic_precheck_blocks_grader_tampering():
    crit = RRSICritic(normalize_domain())
    tamper = SEED.with_files({"solve.py": "import rsi.core.domain as d\nd.FunctionDomain.grade = lambda *a: (1.0, '')\n"
                                          "def solve(text):\n    return text\n"})
    v = crit.review(SEED.diff(tamper))
    assert v["verdict"] == "reject" and v["stage"] == "precheck" and "grader" in v["reasons"][0]
    assert crit.review(tamper.diff(SEED))["verdict"] == "accept"      # removing it is not tampering
