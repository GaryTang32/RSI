"""Regression tests for four low-severity core review findings (L1, L2, L4, L5)."""

import json
import warnings

import pytest

from rsi.cli import main
from rsi.core import Artifact, LeakageCritic
from rsi.core.critic import added_lines
from rsi.core.gates import BootstrapRigor, GateContext, Scored
from rsi.trace import RunTracer, inspect, load_trace

# --- L1: BootstrapRigor with a single sample on an arm -------------------------------------------------------


def test_bootstrap_rigor_rejects_tiny_gain_with_one_sample():
    g = BootstrapRigor()
    v = g.check(Scored(0.501, samples=[0.501]), Scored(0.5, samples=[0.5]), GateContext())
    assert not v and "<2 samples" in v.reason
    one_vs_many = g.check(Scored(0.501, samples=[0.501]), Scored(0.5, samples=[0.5, 0.5, 0.5]), GateContext())
    assert not one_vs_many


def test_bootstrap_rigor_single_sample_uses_supplied_noise_band():
    g, lo = BootstrapRigor(), BootstrapRigor(lower_is_better=True)
    cand, inc = Scored(0.6, samples=[0.6]), Scored(0.5, samples=[0.5])
    assert g.check(cand, inc, GateContext(delta=0.05))  # gain 0.1 clears the band
    assert not g.check(cand, inc, GateContext(delta=0.2))  # gain inside the band
    assert not g.check(Scored(0.5, samples=[0.5]), Scored(0.6, samples=[0.6]), GateContext(delta=0.05))
    assert lo.check(Scored(0.5, samples=[0.5]), Scored(0.6, samples=[0.6]), GateContext(delta=0.05))
    # two or more samples per arm: the bootstrap path is unchanged
    assert g.check(Scored(0.8, samples=[0.8, 0.81]), Scored(0.5, samples=[0.5, 0.51]), GateContext())


# --- L2: diffs split on "\n" only ---------------------------------------------------------------------------


@pytest.mark.parametrize("sep", ["\x0c", "\x0b", "\x1c", "\x1d", "\x1e", "\x85", " ", " ", "\r"])
def test_leakage_critic_sees_through_exotic_line_breaks(sep):
    c = LeakageCritic(terms=["Paris_answer"])
    old = Artifact({"h.py": "a\n"})
    new = old.with_files({"h.py": f"a\nx = 1{sep}\n++Paris_answer\n"})
    d = old.diff(new)
    assert "No newline" not in d
    assert c.denylist_hits(d) == ["Paris_answer"]
    assert old.diff_size(new) == 2


def test_diff_unchanged_for_ordinary_text():
    old = Artifact({"f.txt": "a\nb\nc\n", "g.txt": "x\ny"})
    new = old.with_files({"f.txt": "a\nB\nc\n", "g.txt": "x\nz", "h.txt": ""})
    assert old.diff(new) == (
        "--- a/f.txt\n+++ b/f.txt\n@@ -1,3 +1,3 @@\n a\n-b\n+B\n c\n"
        "--- a/g.txt\n+++ b/g.txt\n@@ -1,2 +1,2 @@\n x\n-y\n\\ No newline at end of file\n"
        "+z\n\\ No newline at end of file\n"
    )
    assert added_lines(old.diff(new)) == "B\nz"


# --- L4: torn trace lines ------------------------------------------------------------------------------------


def test_trace_survives_torn_last_line_and_resume(tmp_path):
    t = RunTracer(tmp_path, "rrsi")
    t.event("run_start", seed="s")
    t.event("note", 0, what="x")
    p = tmp_path / "trace.jsonl"
    p.write_text(p.read_text()[:-15])  # killed mid-write: torn last line, no newline
    RunTracer(tmp_path, "rrsi").event("run_start", seed="s2")
    lines = p.read_text().split("\n")
    assert json.loads(lines[-2])["data"] == {"seed": "s2"}  # the resumed record starts on its own line
    with pytest.warns(UserWarning, match="skipped 1 unparseable"):
        recs = load_trace(tmp_path)
    assert [r["kind"] for r in recs] == ["run_start", "run_start"]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        inspect(tmp_path)  # `rsi inspect` no longer crashes
    assert (tmp_path / "TRACE.md").exists()


# --- L5: Artifact.to_dir names, `rsi improve --set` ----------------------------------------------------------


@pytest.mark.parametrize(
    "files",
    [
        {"a.md": "1", "a.md/b": "2"},
        {"a.md/b": "2", "a.md": "1", "z": "3"},
        {"z": "1", ".": "x"},
        {"z": "1", "d/./x": "x"},
        {"z": "1", "d//x": "x"},
        {"z": "1", "d/": "x"},
    ],
)
def test_to_dir_rejects_conflicting_names_before_any_write(tmp_path, files):
    out = tmp_path / "out"
    with pytest.raises(ValueError):
        Artifact(files).to_dir(out)
    assert not out.exists()


def test_to_dir_still_writes_nested_names(tmp_path):
    Artifact({"a/b.md": "1", "a/c/d.md": "2", ".hidden": "3"}).to_dir(tmp_path / "o")
    assert (tmp_path / "o" / "a" / "c" / "d.md").read_text() == "2"


@pytest.mark.parametrize("bad", ["T", "=1"])
def test_cli_set_without_equals_is_a_usage_error(capsys, tmp_path, bad):
    with pytest.raises(SystemExit) as e:
        main(["improve", "--out", str(tmp_path), "--set", bad])
    assert e.value.code == 2
    assert "argument --set: expected key=value" in capsys.readouterr().err
