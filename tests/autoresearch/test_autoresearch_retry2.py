"""Tests for the retry-round-2 experiment helpers (experiments/autoresearch/r2_*.py).

They pin down the measurement code behind the second-attempt verdicts in
docs/methods/autoresearch/claims-audit.md section 5: the X4 measurement-only evaluator variant
(the held-out val window is scored on the same weights and never reaches the agent), the X3 MLX-walk
configurations, the X6 proposal classifier and the X7 float32 port.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

EXP = Path(__file__).resolve().parents[2] / "experiments" / "autoresearch"
if str(EXP) not in sys.path:
    sys.path.insert(0, str(EXP))

from rsi.domains.tinylm.task import DOMAIN_DIR  # noqa: E402

TRAIN = (DOMAIN_DIR / "train.py").read_text()


def test_val_reuse_variant_scores_the_other_window_on_the_same_weights(tmp_path):
    import r2_val_reuse_tinylm as x4

    task = x4.ValReuseTask(sel_epoch=0, budget_s=0.3)
    art = task.seed_artifact()
    task.env = {"RSI_AR_VAL_ALT_EPOCH": "0"}          # alt == sel window: must reproduce val_bpb exactly
    out = task.run(art, seed=0, mode="hardened")
    assert out.metric is not None
    h = task.held_out[-1]
    assert h["val_alt"] == pytest.approx(out.metric, abs=1e-12)
    assert h["val_alt_offset"] == 0 and h["test_iid"] is not None
    task.env = {"RSI_AR_VAL_ALT_EPOCH": "1"}          # the disjoint window (bytes 32768-65535)
    out = task.run(art, seed=0, mode="hardened")
    h = task.held_out[-1]
    assert h["val_alt_offset"] == 32768 and h["val_alt"] != pytest.approx(out.metric, abs=1e-6)
    # the agent-facing record is the hardened one: no hidden numbers outside record["audit"]
    assert set(out.record) >= {"val_bpb", "audit"} and "val_alt" not in out.record


def test_val_reuse_variant_changes_only_the_audit_branch():
    import r2_val_reuse_tinylm as x4

    src, var = (DOMAIN_DIR / "prepare.py").read_text(), x4.variant_prepare()
    assert var.replace(x4._ALT, "") == src
    assert 'if _CONST["MODE"] == "audit"' in x4._ALT


def test_mlx_walk_configs_follow_the_preregistration():
    import re

    import r2_mlx_walk as x3

    ch = x3.chains(TRAIN)

    def knobs(src):
        return tuple(re.search(rf"^{k}\s*=\s*([^#\n]+)", src, re.M).group(1).strip()
                     for k in ("DEPTH", "HIDDEN", "BATCH_SIZE", "LR"))

    assert knobs(ch["new"]["base_d8"]) == ("8", "128", "32", "0.003")
    assert knobs(ch["new"]["m1_batch16"]) == ("8", "128", "16", "0.003")
    assert knobs(ch["new"]["m2_lr0.006"]) == ("8", "128", "16", "0.006")
    assert knobs(ch["new"]["m3_depth4_width64"]) == ("4", "64", "16", "0.006")      # width follows depth
    assert knobs(ch["new"]["m3alt_depth4_width128"]) == ("4", "128", "16", "0.006")
    assert knobs(ch["old"]["old_m3_depth1"]) == ("1", "128", "16", "0.006")
    g = x3.paired_test([3.0, 3.1, 3.2], [2.9, 3.0, 3.1])
    assert g["mean_gain"] == pytest.approx(0.1) and g["pass"]


def test_proposal_classifier_separates_knobs_structure_and_docstrings():
    import r2_live_nights as x6

    knob = TRAIN.replace("LR = 0.003", "LR = 0.006")
    doc = TRAIN.replace("A byte-level MLP", "A small byte-level MLP")
    arch = TRAIN.replace("            h = self._act(z)\n", "            h = self._act(z) + (h if h.shape == z.shape else 0)\n")
    assert x6.structure(TRAIN) == x6.structure(knob) == x6.structure(doc)
    assert x6.structure(arch) != x6.structure(TRAIN)
    c0, c1 = x6.constants(TRAIN), x6.constants(knob)
    assert {k: v for k, v in c1.items() if c0.get(k) != v} == {"LR": "0.006"}


def test_float32_port_adds_one_line_and_keeps_the_constants():
    import r2_backend_port as x7

    ported = x7.port(TRAIN)
    assert ported.count("astype(np.float32)") == 1 and len(ported.splitlines()) == len(TRAIN.splitlines()) + 1
    assert x7.constants(ported) == x7.constants(TRAIN)
    moved = x7.with_constants(ported, {"LR": "0.024", "ACTIVATION": "'relu'"})
    assert x7.constants(moved)["LR"] == "0.024" and x7.constants(moved)["ACTIVATION"] == "'relu'"
    assert x7.PORT_LINE in moved


def test_x5b_discouraged_measure_follows_the_preregistration():
    import r2_live_nights as x5

    def row(par, ch):
        return {"changed_constants": ch, "parent_constants": par}

    assert x5.discouraged(row({"CONTEXT": "6"}, {"CONTEXT": "12"}))
    assert x5.discouraged(row({"BATCH_SIZE": "8"}, {"BATCH_SIZE": "48"}))
    assert x5.discouraged(row({"WARMDOWN_RATIO": "0.5"}, {"WARMDOWN_RATIO": "0.0"}))
    assert not x5.discouraged(row({"BATCH_SIZE": "32"}, {"BATCH_SIZE": "8"}))       # shrinking is encouraged
    assert not x5.discouraged(row({"LR": "0.003", "ACTIVATION": '"tanh"'}, {"LR": "0.005", "ACTIVATION": '"relu"'}))
    assert not x5.discouraged(row({"WARMDOWN_RATIO": "0.5"}, {"WARMDOWN_RATIO": "0.25"}))
    assert x5.discouraged({"changed_constants": None}) is None                       # a turn without a candidate


def test_x4b_crossover_cancels_a_window_term():
    import r2_val_reuse_tinylm as x4

    # a pure window term (+w on even seeds, -w on odd seeds) has opt == 0; a common shift survives
    r = x4.crossover([0.03, 0.031, 0.029, 0.03], [-0.03, -0.029, -0.031, -0.03])
    assert abs(r["opt"]) < 1e-9 and not r["pass"] and r["window_term"] == pytest.approx(-0.03)
    r = x4.crossover([0.035, 0.036, 0.034, 0.035], [-0.025, -0.024, -0.026, -0.025])
    assert r["opt"] == pytest.approx(0.005) and r["pass"]


# ---------------------------------------------------------------- review follow-ups (X4c, X3d, X6b, X7 re-analysis)
def test_x7_divergence_aware_counts_nan_runs_as_failed_transfers():
    import math

    import r2_backend_port as x7

    nan = float("nan")
    rows = [{"backend": "float64", "seed": 0, "final_constants": {"LR": "0.048"}, "transfer_ratio": 0.79,
             "native": {"base": [3.4] * 5, "final": [2.8] * 5, "gain": 0.6},
             "transferred": {"base": [3.2] * 5, "final": [nan, 2.7, 2.7, nan, nan], "gain": 0.5}}]
    r = x7.divergence_aware(rows)
    o = r["per_night"][0]
    assert o["failed_transferred_runs"] == 3 and r["failed_transferred_runs"] == 3 and r["transferred_runs"] == 5
    # failures count as zero gain: (0.5 + 0.5 + 0 + 0 + 0) / 5 = 0.2, ratio 0.2 / 0.6
    assert o["transferred_gain_failures_as_zero"] == pytest.approx(0.2)
    assert o["transfer_ratio_failures_as_zero"] == pytest.approx(0.2 / 0.6)
    assert not math.isnan(o["transfer_ratio_failures_as_zero"])


def test_x4c_windows_variant_scores_four_disjoint_windows_and_selects_one():
    import r2_val_reuse_windows as x4c

    src, var = (DOMAIN_DIR / "prepare.py").read_text(), x4c.variant_prepare()
    assert var.replace(x4c._WIN, "") == src and 'if _CONST["MODE"] == "audit"' in x4c._WIN
    task = x4c.WindowsTask("T1", budget_s=0.3)
    out = task.run(task.seed_artifact(), seed=0, mode="hardened")
    w = task.scored[-1]["windows"]
    assert set(w) == {"V0", "V1", "T0", "T1"} and len({round(v, 9) for v in w.values()}) == 4
    assert out.metric == pytest.approx(w["T1"], abs=1e-12)          # the loop sees only the selected window
    assert set(out.record["audit"]) >= {"windows"} and "windows" not in out.record


def test_x4c_balanced_test_cancels_a_fixed_window_term():
    import r2_val_reuse_windows as x4c

    wins = ["V0", "V1", "T0", "T1"] * 3
    term = {"V0": 0.03, "V1": -0.01, "T0": -0.01, "T1": -0.01}         # sums to zero over the windows
    r = x4c.balanced_test([term[w] + e for w, e in zip(wins, [0.001, -0.001, 0.0] * 4)], wins)
    assert abs(r["tau"]) < 1e-9 and not r["pass"] and r["df"] == 8
    r = x4c.balanced_test([term[w] + 0.02 + e for w, e in zip(wins, [0.001, -0.001, 0.0] * 4)], wins)
    assert r["tau"] == pytest.approx(0.02) and r["pass"]
    o = x4c.o_value({"V0": 3.0, "V1": 3.0, "T0": 3.0, "T1": 3.0}, {"V0": 2.0, "V1": 2.2, "T0": 2.2, "T1": 2.2}, "V0")
    assert o["O"] == pytest.approx(0.2)


def test_x3d_failed_runs_score_as_zero_gain():
    import r2_machine_transfer as x3d

    assert x3d.zero_gain_mean([2.0, None, float("nan"), 2.0], 3.0) == pytest.approx(2.5)
    p = {"seed": 0, "on_fast": {"base": [3.0, 3.0], "slow_final": [2.8, None], "fast_final": [2.6, 2.6]},
         "on_slow": {"base": [3.5, 3.5], "slow_final": [3.0, 3.0], "fast_final": [3.2, 3.2]}}
    r = x3d.per_seed(p)
    assert r["D"] == pytest.approx(2.9 - 2.6) and r["failed_runs_transferred"] == 1
    assert r["rho"] == pytest.approx(0.1 / 0.4) and r["reverse"]["D"] == pytest.approx(0.2)


def test_x6b_switches_to_the_live_agent_at_the_stuck_point(tmp_path):
    import r2_live_nights as x6

    tsv = "commit\tval_bpb\tmemory_gb\tstatus\tdescription\n" + "".join(
        f"c{i}\t3.0\t0.1\t{s}\td{i}\n" for i, s in enumerate(["keep", "discard", "keep", "discard", "crash", "discard"]))
    assert x6.tail_nonkeeps(tsv) == 3
    assert x6.tail_nonkeeps(tsv + "c9\t3.0\t0.1\tdiscard\td9\n") == 4

    class Stub:
        def __init__(self, tag):
            self.tag, self.n = tag, 0

        def propose(self, ctx):
            self.n += 1
            return self.tag

    class Ctx:
        def __init__(self, t, e):
            self.results_tsv, self.experiment = t, e

    scripted, live = Stub("scripted"), Stub("live")
    ag = x6.StuckSwitchAgent(scripted, live, n_live=2, stop_dir=tmp_path)
    assert ag.propose(Ctx(tsv, 6)) == "scripted" and ag.switched_at is None
    stuck = tsv + "c9\t3.0\t0.1\tdiscard\td9\n"
    assert ag.propose(Ctx(stuck, 7)) == "live" and ag.switched_at == 7 and not (tmp_path / "STOP").exists()
    assert ag.propose(Ctx(stuck + "c10\t2.9\t0.1\tkeep\td10\n", 8)) == "live"      # stays live after a keep
    assert (tmp_path / "STOP").exists() and live.n == 2 and scripted.n == 1


def test_x6b_switch_agent_meters_the_live_backend_once():
    import r2_live_nights as x6

    from rsi.autoresearch import MockResearchAgent
    from rsi.autoresearch.loop import agent_llms, make_agent
    from rsi.core.llm import MockLLM
    from rsi.domains.tinylm import TinyLMTask

    task = TinyLMTask(budget_s=0.3)
    llm = MockLLM(lambda *a, **k: "")
    ag = x6.StuckSwitchAgent(MockResearchAgent(task.mock_edit_pool()), make_agent(task, llm), n_live=1,
                             stop_dir=Path("/nonexistent"))
    assert agent_llms(ag) == [llm]            # was [llm, llm]: the X6b night's usage was double-counted
