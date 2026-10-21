"""Claims-audit retry 2: developer base version, Pareto ratio attainment, the C++ Lasso domain and the E11 grid."""
import shutil
from types import SimpleNamespace

import numpy as np
import pytest

from rsi.core import Artifact
from rsi.domains.discovery import SimpleTESLassoDomain, SyntheticConfig, SyntheticDomain
from rsi.domains.discovery.lasso_cpp import (SIZES, compile_program, load_heldout, make_problem, parse_program,
                                             seed_program)
from rsi.dream import Config, DreamRSILoop, EpisodeResult, ParametricMutator, ParetoSweepObjective, run, template_code
from rsi.dream.developer import (DevContext, LLMPolicyDeveloper, VersionRecord, base_version, developer_prompt,
                                 mock_developer_llm)

needs_gxx = pytest.mark.skipif(shutil.which("g++") is None, reason="needs g++")


def _report(value: float) -> SimpleNamespace:
    """The ``PolicyReport`` fields the developers read."""
    return SimpleNamespace(value=value, diagnostics={}, sweep=None, summary=lambda: {"value": value},
                           traces_jsonl=lambda rows=None: "")


def _versions() -> tuple[VersionRecord, VersionRecord, VersionRecord]:
    code = template_code("adaptive")
    return (VersionRecord(3, code, _report(0.9), label="strong"),
            VersionRecord(5, code.replace("0.6", "0.61", 1), _report(0.5), label="newer"),
            VersionRecord(6, "", None, label="rejected"))


def test_developer_base_latest_revises_pi_m_not_the_strongest_version():
    strong, newer, rejected = _versions()
    ctx = DevContext(1, [strong, newer, rejected], [], [], "", "eq1", 4, first_in_phase=False)
    assert ctx.base == "strongest" and base_version(ctx) is strong  # default (L2:247)
    ctx_l = DevContext(1, [strong, newer, rejected], [], [], "", "eq1", 4, first_in_phase=False, base="latest")
    assert base_version(ctx_l) is newer  # pi^m: the latest evaluated version
    assert ParametricMutator(beta_rule=False).revise(ctx_l, seed=1).parent == newer.index
    assert ParametricMutator(beta_rule=False).revise(ctx, seed=1).parent == strong.index
    with pytest.raises(ValueError):
        base_version(DevContext(1, [strong], [], [], "", "eq1", 4, base="oldest"))


def test_llm_developer_edits_pi_m_and_says_so_in_the_prompt():
    strong, newer, _ = _versions()
    dev = LLMPolicyDeveloper(mock_developer_llm(), leakage_check=False)
    rev = dev.revise(DevContext(1, [strong, newer], [], [], "", "eq1", 4, base="latest"), seed=0)
    assert rev.ok and rev.parent == newer.index and rev.meta["calls"][0]["base_code"] == newer.code
    assert "pi^m" in developer_prompt("eq1", 4, base="latest")
    assert "strongest so far" in developer_prompt("eq1", 4)


def test_loop_passes_the_developer_base_to_every_revision():
    seen = []

    class Recording(ParametricMutator):
        def revise(self, ctx, *, seed=0):
            seen.append((ctx.base, base_version(ctx).index, ctx.versions[-1].index))
            return super().revise(ctx, seed=seed)

    run(SyntheticDomain(SyntheticConfig(seed=5)),
        config=Config(rounds=2, W=4, branch_count=4, refine_count=3, M=4, sandbox="inprocess", trace=False,
                      agent_workers=1, developer_base="latest"), developer=Recording())
    assert len(seen) == 3 and all(b == "latest" for b, _, _ in seen)
    assert all(base == newest for _, base, newest in seen)  # pi^m -> pi^(m+1): start from the newest version


def _ep(best: float, root: float = 0.5, ceiling: float = 1.0, oos: bool = False, **kw) -> EpisodeResult:
    return EpisodeResult("w", 0.6, {}, {}, oos, 4, 2, [2, 2], best, root, ceiling, 10, 4, **kw)


def test_pareto_ratio_attainment_follows_the_unpublished_draft():
    shift, ratio = ParetoSweepObjective(), ParetoSweepObjective(attainment_mode="ratio")
    assert shift.attainment(_ep(0.9)) == pytest.approx(0.8)  # (0.9 - 0.5) / (1.0 - 0.5)
    assert ratio.attainment(_ep(0.9)) == pytest.approx(0.9)  # clip(S / G, 0, 1)
    assert ratio.attainment(_ep(0.5)) == pytest.approx(0.5)  # the root alone attains root / G
    assert ratio.attainment(_ep(0.9, error="policy crashed")) == 0.0
    assert ratio.attainment(_ep(0.9, oos=True)) == 0.0  # out of support: no replay reward
    with pytest.raises(ValueError):
        ratio.attainment(_ep(-1.2, root=-1.5, ceiling=-1.1))  # S/G is undefined for negative scores
    with pytest.raises(ValueError):
        ParetoSweepObjective(attainment_mode="auc").attainment(_ep(0.9))


def test_config_routes_the_pareto_attainment_to_the_replay_objective():
    dom = SyntheticDomain(SyntheticConfig(seed=1))
    loop = DreamRSILoop(dom.as_task(), dom.mock_agent(),
                        config=Config(objective="pareto", pareto_attainment="ratio", sandbox="inprocess", trace=False))
    assert loop.replay.objective.attainment_mode == "ratio"


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
    assert parse_program(code) == ("int main(){}", []) and not marker.exists()
    for bad in ("CPP_CODE = open('x').read()\n",  # not a literal
                "CPP_CODE = ''\nCOMPILE_FLAGS = ['-o', '/tmp/x']\n"):  # a flag outside the allow-list
        with pytest.raises(ValueError):
            parse_program(bad)
    assert parse_program(seed_program())[1] == []


@needs_gxx
def test_lasso_cpp_grades_compiled_solvers(tmp_path):
    dom = SimpleTESLassoDomain(sizes=[(30, 20, "gaussian"), (20, 40, "corr_low"), (40, 30, "sparse")],
                               cache_dir=str(tmp_path / "bin"), timing_runs=1)
    ok = dom.evaluate_program(dom.seed_artifact())
    assert ok.fail_class == "ok" and ok.score > 0
    assert ok.diagnostics["max_gap"] <= 1e-6 and len(ok.diagnostics["problems"]) == 3
    zeros = seed_program().replace("out[(size_t)k * p + j] = beta[j];", "out[(size_t)k * p + j] = 0.0;")
    bad = dom.evaluate_program(Artifact({"solver.py": zeros}))
    assert bad.fail_class == "correctness" and bad.score == 0.0
    for cpp, error in (("int main( {", "compilation failed"), ("int main(){ return 3; }", "crashed")):
        ev = dom.evaluate_program(Artifact({"solver.py": f"CPP_CODE = '{cpp}'\n"}))
        assert ev.fail_class == "compile_other" and error in ev.error
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


@needs_gxx
def test_compile_cache_is_keyed_by_code_and_flags(tmp_path):
    cpp = "int main(){ return 0; }"
    a, e1 = compile_program(cpp, [], cache_dir=str(tmp_path))
    b, e2 = compile_program(cpp, ["-DX=1"], cache_dir=str(tmp_path))
    assert e1 is None and e2 is None and a != b
    assert compile_program(cpp, [], cache_dir=str(tmp_path))[0] == a


def test_e11_runs_the_paper_grid_at_the_paper_round_counts(dream_script):
    e11 = dream_script("e11_paper_grid")
    for d, st in e11.SETTINGS.items():
        b, r = st["grid"]
        assert st["W"] == b  # every workspace in parallel
        if d == "synthetic_flash":
            assert b * (r + 1) == 640  # 32 x 20 [paper §4 p.7]
        else:
            assert (b, b * (r + 1)) == (10, 110)  # 10 x 11 [paper §4 p.7]
        assert st["rounds"] == (10 if d in ("sumdiff", "circlepack", "autocorr") else 5)  # §4.1 / §4.2
        cfg = e11.config_of(d, 0, False)
        assert cfg.hard_max_branch >= b and cfg.hard_max_refine >= r  # the hard caps must not cut the grid
        assert cfg.round_cap == b * (r + 1)
