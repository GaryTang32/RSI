"""Regressions for the claims-audit retry round 2 (docs/methods/dream-rsi/claims-audit.md, §6-§7).

Each test fails on the code before retry round 2:

* M12 - the developer can revise pi^m into pi^(m+1) (§3) instead of the strongest version (L2:247):
  ``DevContext.base`` / ``Config.developer_base``, honoured by both developers and stated in the prompt;
* M18 - the Pareto sweep's attainment can follow the paper's unpublished draft, clip(S/G, 0, 1):
  ``ParetoSweepObjective(attainment_mode="ratio")`` / ``Config.pareto_attainment``;
* Q13/Q31/Q32 - ``SimpleTESLassoDomain``: the paper's Lasso protocol with compiled C++ candidates
  (wire format, 17 benchmark shapes, sklearn-path correctness gate, held-out datasets from a directory,
  candidate files read as literals and never executed);
* E11 runs the paper's grid (10 workspaces x 11 calls, W = 10) at the paper's round counts.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import numpy as np
import pytest

from rsi.core.artifact import Artifact
from rsi.domains.discovery import SimpleTESLassoDomain, SyntheticConfig, SyntheticDomain
from rsi.domains.discovery.lasso_cpp import (SIZES, compile_program, load_heldout, make_problem, parse_program,
                                             seed_program)
from rsi.dream import Config, ParametricMutator, ParetoSweepObjective, run, template_code
from rsi.dream.developer import (DevContext, LLMPolicyDeveloper, VersionRecord, base_version, developer_prompt,
                                 mock_developer_llm)
from rsi.dream.objectives import EpisodeResult

ROOT = Path(__file__).resolve().parents[2]
HAS_GXX = shutil.which("g++") is not None


class _Rep:
    """A stand-in PolicyReport: only ``value`` / ``diagnostics`` / ``sweep`` are read by the developers."""

    def __init__(self, value: float) -> None:
        self.value, self.diagnostics, self.sweep = value, {}, None

    def summary(self):
        return {"value": self.value}

    def traces_jsonl(self, rows=None):
        return ""


def _versions():
    code = template_code("adaptive")
    strong = VersionRecord(3, code, _Rep(0.9), label="strong")
    newer = VersionRecord(5, code.replace("0.6", "0.61", 1), _Rep(0.5), label="newer")
    rejected = VersionRecord(6, "", None, label="rejected")
    return strong, newer, rejected


# ------------------------------------------------------------------------------------ M12
def test_developer_base_latest_revises_pi_m_not_the_strongest_version():
    strong, newer, rejected = _versions()
    ctx = DevContext(1, [strong, newer, rejected], [], [], "", "eq1", 4, first_in_phase=False)
    assert ctx.base == "strongest"                                    # default unchanged (L2:247)
    assert base_version(ctx) is strong
    ctx_l = DevContext(1, [strong, newer, rejected], [], [], "", "eq1", 4, first_in_phase=False, base="latest")
    assert base_version(ctx_l) is newer                              # pi^m = the latest EVALUATED version
    assert ParametricMutator(beta_rule=False).revise(ctx_l, seed=1).parent == newer.index
    assert ParametricMutator(beta_rule=False).revise(ctx, seed=1).parent == strong.index
    with pytest.raises(ValueError):
        base_version(DevContext(1, [strong], [], [], "", "eq1", 4, base="oldest"))


def test_llm_developer_edits_pi_m_and_says_so_in_the_prompt():
    strong, newer, _ = _versions()
    dev = LLMPolicyDeveloper(mock_developer_llm(), leakage_check=False)
    rev = dev.revise(DevContext(1, [strong, newer], [], [], "", "eq1", 4, base="latest"), seed=0)
    assert rev.ok and rev.parent == newer.index
    assert rev.meta["calls"][0]["base_code"] == newer.code
    assert "pi^m" in developer_prompt("eq1", 4, base="latest")
    assert "strongest so far" in developer_prompt("eq1", 4)


def test_loop_passes_the_developer_base_to_every_revision():
    seen = []

    class Recording(ParametricMutator):
        def revise(self, ctx, *, seed=0):
            seen.append((ctx.base, base_version(ctx).index, [v.index for v in ctx.versions]))
            return super().revise(ctx, seed=seed)

    dom = SyntheticDomain(SyntheticConfig(seed=5))
    run(dom, config=Config(rounds=2, W=4, branch_count=4, refine_count=3, M=4, sandbox="inprocess", trace=False,
                           agent_workers=1, developer_base="latest"), developer=Recording())
    assert len(seen) == 3 and all(b == "latest" for b, _, _ in seen)
    # pi^m -> pi^(m+1): each revision starts from the version written just before it
    assert [base for _, base, _ in seen] == [idx[-1] for _, _, idx in seen]


# ------------------------------------------------------------------------------------ M18
def _ep(best, root=0.5, ceiling=1.0, oos=False, **kw):
    return EpisodeResult("w", 0.6, {}, {}, oos, 4, 2, [2, 2], best, root, ceiling, 10, 4, **kw)


def test_pareto_ratio_attainment_follows_the_unpublished_draft():
    shift, ratio = ParetoSweepObjective(), ParetoSweepObjective(attainment_mode="ratio")
    e = _ep(0.9)
    assert shift.attainment(e) == pytest.approx(0.8)                  # (0.9 - 0.5) / (1.0 - 0.5)
    assert ratio.attainment(e) == pytest.approx(0.9)                  # clip(S / G, 0, 1)
    assert ratio.attainment(_ep(0.5)) == pytest.approx(0.5)           # the root alone attains root / G
    assert ratio.attainment(_ep(0.9, error="policy crashed")) == 0.0      # disqualified
    assert ratio.attainment(_ep(0.9, oos=True)) == 0.0                     # out of support: no replay reward
    with pytest.raises(ValueError):
        ratio.attainment(_ep(-1.2, root=-1.5, ceiling=-1.1))          # S/G is undefined for negative scores
    with pytest.raises(ValueError):
        ParetoSweepObjective(attainment_mode="auc").attainment(e)


def test_config_routes_the_pareto_attainment_to_the_replay_objective():
    from rsi.dream import DreamRSILoop

    dom = SyntheticDomain(SyntheticConfig(seed=1))
    loop = DreamRSILoop(dom.as_task(), dom.mock_agent(),
                        config=Config(objective="pareto", pareto_attainment="ratio", sandbox="inprocess", trace=False))
    assert loop.replay.objective.attainment_mode == "ratio"


# ------------------------------------------------------------------------------- lasso_cpp
def test_lasso_cpp_problem_shapes_and_determinism():
    assert len(SIZES) == 17
    for n, p, gen in [(20, 30, "gaussian"), (15, 40, "sparse"), (20, 10, "dense_sol"), (25, 12, "corr_low"),
                      (25, 12, "corr_high")]:
        X, y = make_problem(n, p, gen, 7)
        X2, y2 = make_problem(n, p, gen, 7)
        assert X.shape == (n, p) and y.shape == (n,)
        assert np.array_equal(X, X2) and np.array_equal(y, y2)
    with pytest.raises(ValueError):
        make_problem(5, 5, "uniform", 0)


def test_lasso_cpp_programs_are_read_as_literals_never_executed(tmp_path):
    marker = tmp_path / "ran.txt"
    code = f"import pathlib\npathlib.Path({str(marker)!r}).write_text('x')\nCPP_CODE = 'int main(){{}}'\n"
    cpp, flags = parse_program(code)
    assert cpp == "int main(){}" and flags == [] and not marker.exists()
    with pytest.raises(ValueError):
        parse_program("CPP_CODE = open('x').read()\n")               # not a literal
    with pytest.raises(ValueError):
        parse_program("CPP_CODE = ''\nCOMPILE_FLAGS = ['-o', '/tmp/x']\n")   # flag outside the allow-list
    assert parse_program(seed_program())[1] == []


@pytest.mark.skipif(not HAS_GXX, reason="needs g++")
def test_lasso_cpp_grades_compiled_solvers(tmp_path):
    small = [(30, 20, "gaussian"), (20, 40, "corr_low"), (40, 30, "sparse")]
    dom = SimpleTESLassoDomain(sizes=small, cache_dir=str(tmp_path / "bin"), timing_runs=1)
    ok = dom.evaluate_program(dom.seed_artifact())
    assert ok.fail_class == "ok" and ok.score > 0
    assert ok.diagnostics["max_gap"] <= 1e-6 and len(ok.diagnostics["problems"]) == 3
    zeros = seed_program().replace("out[(size_t)k * p + j] = beta[j];", "out[(size_t)k * p + j] = 0.0;")
    bad = dom.evaluate_program(Artifact({"solver.py": zeros}))
    assert bad.fail_class == "correctness" and bad.score == 0.0
    broken = dom.evaluate_program(Artifact({"solver.py": "CPP_CODE = 'int main( {'\n"}))
    assert broken.fail_class == "compile_other" and "compilation failed" in broken.error
    crash = dom.evaluate_program(Artifact({"solver.py": "CPP_CODE = 'int main(){ return 3; }'\n"}))
    assert crash.fail_class == "compile_other" and "crashed" in crash.error
    # held-out split: only the datasets present in the directory, the rest listed as missing
    rng = np.random.RandomState(0)
    X = rng.randn(25, 15)
    np.savez(tmp_path / "dna.npz", X=X, y=X[:, 0] + 0.1 * rng.randn(25))
    ho = SimpleTESLassoDomain(heldout_dir=str(tmp_path), cache_dir=str(tmp_path / "bin"), heldout_reps=1)
    data, missing = load_heldout(str(tmp_path))
    assert list(data) == ["dna"] and "gisette" in missing and "rcv1" in missing
    ev = ho.evaluate_split(ho.seed_artifact(), "holdout")
    assert ev.fail_class == "ok" and [r["problem"] for r in ev.diagnostics["problems"]] == ["dna"]
    assert "rcv1" in ev.diagnostics["missing"]
    assert SimpleTESLassoDomain(heldout_dir=str(tmp_path / "none")).evaluate_split(
        dom.seed_artifact(), "holdout").fail_class == "env_error"


def test_lasso_cpp_has_no_offline_mock_agent():
    with pytest.raises(NotImplementedError):
        SimpleTESLassoDomain().mock_agent()


@pytest.mark.skipif(not HAS_GXX, reason="needs g++")
def test_compile_cache_is_keyed_by_code_and_flags(tmp_path):
    cpp = "int main(){ return 0; }"
    a, e1 = compile_program(cpp, [], cache_dir=str(tmp_path))
    b, e2 = compile_program(cpp, ["-DX=1"], cache_dir=str(tmp_path))
    assert e1 is None and e2 is None and a != b
    assert compile_program(cpp, [], cache_dir=str(tmp_path))[0] == a


# ------------------------------------------------------------------------------------ E11
def test_e11_runs_the_paper_grid_at_the_paper_round_counts():
    sys.path.insert(0, str(ROOT / "experiments" / "dream-rsi"))
    import e11_paper_grid as e11

    for d, st in e11.SETTINGS.items():
        b, r = st["grid"]
        assert st["W"] == b                                             # every workspace in parallel
        if d == "synthetic_flash":
            assert b * (r + 1) == 640                                   # 32 x 20 [paper:§4 p.7]
        else:
            assert (b, b * (r + 1)) == (10, 110)                        # 10 x 11 = 110 [paper:§4 p.7]
        assert st["rounds"] == (10 if d in ("sumdiff", "circlepack", "autocorr") else 5)   # §4.1 / §4.2
        cfg = e11.config_of(d, 0, False)
        # the loop clips every plan to the hard caps: they must not cut the paper's grid (the first E11 run
        # ran the Flash grid 32 x 20 as 12 x 13 = 156 calls per round)
        assert cfg.hard_max_branch >= b and cfg.hard_max_refine >= r
        assert cfg.round_cap == b * (r + 1)
