"""SoL-Pi low-severity review fixes: per-round lineage node ids, ``composition_dropped`` status (L8) and
byte-identical frozen candidates sharing one firewall verdict (L10)."""

import json

from solpi_testkit import VERBOSITY_IDEAS, VERBOSITY_SEED, verbosity_domain

from rsi.core import Artifact, Execution, FunctionDomain, Task, TaskSuite
from rsi.solpi import Config, Idea
from rsi.solpi import run as sp_run
from rsi.solpi.research import MechanismProposal


class _SetKeys:
    """One variant per lineage: set ``keys[idea.id]`` in config.json; then exhausted."""

    def __init__(self, keys):
        self.keys = keys

    def propose(self, idea, base, evidence, history):
        if history:
            return MechanismProposal(None, error="single variant", meta={"exhausted": True})
        cfg = json.loads(base["config.json"])
        cfg.update(self.keys[idea.id])
        return MechanismProposal(base.with_files({"config.json": json.dumps(cfg, sort_keys=True)}), change=idea.id)

    def fix(self, idea, prop, error):
        return MechanismProposal(None, error=error, meta={"exhausted": True})


def _ab_domain() -> FunctionDomain:
    """``a`` or ``b`` alone saves tokens at no loss; both together fail every task."""
    tasks, splits = [], {"evolve": [], "holdout": []}
    for split, n in (("evolve", 6), ("holdout", 4)):
        for i in range(n):
            tasks.append(Task(f"{split}-{i}", {}, None, "f"))
            splits[split].append(tasks[-1].id)

    def execute(artifact, task, seed, llm):
        cfg = json.loads(artifact["config.json"])
        a, b = bool(cfg.get("a")), bool(cfg.get("b"))
        tokens = 1000 - 200 * a - 200 * b
        return Execution(output=not (a and b), tokens=tokens, cost_usd=tokens * 1e-6, steps=1)

    return FunctionDomain(TaskSuite(tasks, splits), execute, lambda t, o: 1.0 if o else 0.0, name="ab")


AB_SEED = Artifact({"config.json": json.dumps({}, sort_keys=True)})


def test_lineage_node_ids_include_the_round_after_round_one(tmp_path):
    """L8: with rounds=2 round 2's lineage nodes used to overwrite round 1's (``sp_{idea}_{iteration}``)."""

    class Trim(_SetKeys):
        def propose(self, idea, base, evidence, history):
            if history:
                return MechanismProposal(None, error="single variant", meta={"exhausted": True})
            cfg = json.loads(base["config.json"])
            cfg["verbosity"] -= 1
            return MechanismProposal(base.with_files({"config.json": json.dumps(cfg, sort_keys=True)}), change="t")

    prop = Trim({})
    res = sp_run(
        verbosity_domain(),
        VERBOSITY_SEED,
        ideas=VERBOSITY_IDEAS[:1],
        proposer=prop,
        config=Config(n_lineages=1, max_iters=1, rounds=2),
        out_dir=tmp_path,
    )
    assert len(res.meta["rounds"]) == 2
    lineage = [n for n in res.ledger.nodes() if n.kind == "lineage"]
    n_iters = sum(len(lr["iterations"]) for r in res.meta["rounds"] for lr in r["lineages"])
    assert n_iters == 2 and len(lineage) == 2
    assert [n.id for n in lineage] == ["sp_G1_0", "sp_r2_G1_0"]  # round-1 ids are unchanged
    assert [n.meta["round"] for n in lineage] == [1, 2]


def test_composition_dropped_candidates_are_not_labelled_heldout_rejected(tmp_path):
    """L8: a survivor of the firewall dropped by ``validate_composition`` gets its own status."""
    ideas = [Idea("A", "C", "set a"), Idea("B", "C", "set b")]
    res = sp_run(
        _ab_domain(),
        AB_SEED,
        ideas=ideas,
        proposer=_SetKeys({"A": {"a": 1}, "B": {"b": 1}}),
        config=Config(n_lineages=2, max_iters=1, validate_composition=True),
        out_dir=tmp_path,
    )
    r = res.meta["rounds"][0]
    assert r["heldout_passed"] == {"A:A": True, "B:B": True}
    assert len(r["dropped_by_composition_check"]) == 1 and len(r["survivor_ideas"]) == 1
    status = {n.change.split(":")[0]: n.status for n in res.ledger.nodes() if n.kind == "frozen"}
    dropped = r["dropped_by_composition_check"][0]
    assert status[dropped] == "composition_dropped"
    assert status[r["survivor_ideas"][0]] == "survivor"


def test_byte_identical_frozen_candidates_share_one_firewall_verdict(tmp_path):
    """L10: two lineages freezing the same artifact used to abort run_round with RuntimeError at the firewall."""
    ideas = [Idea("A", "C", "set a"), Idea("A2", "C", "also set a")]
    res = sp_run(
        _ab_domain(),
        AB_SEED,
        ideas=ideas,
        proposer=_SetKeys({"A": {"a": 1}, "A2": {"a": 1}}),
        config=Config(n_lineages=2, max_iters=1),
        out_dir=tmp_path,
    )
    r = res.meta["rounds"][0]
    assert r["heldout_passed"] == {"A:A": True, "A2:A2": True}
    assert sorted(r["survivor_ideas"]) == ["A", "A2"]
    rows = (tmp_path / "firewall_r1" / "heldout.jsonl").read_text().splitlines()
    assert len(rows) == 1  # the held-out split is still touched once per distinct artifact
    assert json.loads(res.best["config.json"]) == {"a": 1}
