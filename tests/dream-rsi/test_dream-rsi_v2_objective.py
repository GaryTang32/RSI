"""The default replay objective is paper v2's Eq. 1: per-world hypervolume over the rho sweep minus lambda * k*/n."""
import pytest

from rsi.domains.discovery import SyntheticConfig, SyntheticDomain
from rsi.dream import Config, DreamRSILoop, EpisodeResult, HypervolumeObjective, run


def _ep(N: int, k: int, best: float, beta: float, world: str = "w", size: int = 10, **kw) -> EpisodeResult:
    return EpisodeResult(world, beta, {}, {}, False, N, k, [N // max(1, k)] * k if k else [], best, 0.0, 1.0, size, 4,
                         **kw)


def test_world_value_is_the_hypervolume_minus_the_parallelism_penalty():
    obj = HypervolumeObjective(lam=0.1)
    # rho sweep on one world (B = 10): (n, a) = (0, 0), (2, 0.5), (5, 0.9), (10, 0.9) -> frontier (2, .5), (5, .9)
    eps = [_ep(0, 0, None, 0.0), _ep(2, 1, 0.5, 0.25), _ep(5, 2, 0.9, 0.5), _ep(10, 5, 0.9, 1.0)]
    hv = (0.5 - 0) * (10 - 2) / 10 + (0.9 - 0.5) * (10 - 5) / 10            # paper §3, a_0 = 0
    penalty = (1 / 2 + 2 / 5 + 5 / 10) / 4                                  # zero-call replay adds 0
    assert obj.world_value(eps) == pytest.approx(hv - 0.1 * penalty)


def test_value_is_the_mean_over_worlds_and_disqualified_worlds_rank_last():
    obj = HypervolumeObjective()
    a = {b: [_ep(5, 1, 0.8, b, "a")] for b in obj.beta_grid}
    two = {b: [_ep(5, 1, 0.8, b, "a"), _ep(2, 2, 0.2, b, "b")] for b in obj.beta_grid}
    va, vb = obj.world_value(a[0.0] * 5), obj.world_value([two[b][1] for b in obj.beta_grid])
    assert obj.sweep(two)["reward"] == pytest.approx((va + vb) / 2)
    bad = [_ep(5, 1, 0.8, b, error="boom") for b in obj.beta_grid]
    honest_worst = [_ep(10, 10, None, b) for b in obj.beta_grid]
    assert obj.world_value(bad) < obj.world_value(honest_worst)


def test_config_defaults_to_v2_with_the_paper_rho_grid():
    cfg = Config(rounds=1, sandbox="inprocess")
    assert cfg.objective == "v2"
    dom = SyntheticDomain(SyntheticConfig(seed=1))
    loop = DreamRSILoop(dom, dom.mock_agent(), config=cfg)
    assert isinstance(loop.replay.objective, HypervolumeObjective)
    assert loop.replay.sweep_grid == (0.0, 0.25, 0.5, 0.75, 1.0)
    with pytest.raises(ValueError, match="unknown objective"):
        DreamRSILoop(dom, dom.mock_agent(), config=Config(objective="eq2", sandbox="inprocess"))


def test_a_default_run_selects_versions_by_the_v2_objective():
    res = run(SyntheticDomain(SyntheticConfig(seed=4)),
              config=Config(rounds=3, W=3, branch_count=4, refine_count=3, M=3, sandbox="inprocess", seed=3))
    phases = [r["dream"] for r in res.trajectory if r.get("dream")]
    assert res.meta["config"]["objective"] == "v2" and phases
    for p in phases:   # the selected version is the argmax of the v2 values, incumbent included
        assert p["values"][p["selected"]] == max(p["values"])
