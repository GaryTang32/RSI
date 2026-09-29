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
