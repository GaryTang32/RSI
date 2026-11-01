"""The examples/ scripts run end to end offline (no API key, no network) and print their summary."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
OFFLINE_NOTE = "offline simulated model: numbers show the loop working, not real gains"


def load(rel: str):
    path = EXAMPLES / rel
    spec = importlib.util.spec_from_file_location(f"rsi_example_{path.stem}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def check_output(out: str, run_dir: Path) -> None:
    assert "SUMMARY" in out
    assert OFFLINE_NOTE in out
    assert (run_dir / "trace.jsonl").exists()


def test_01_prompt_from_csv(tmp_path, capsys):
    res = load("01_prompt_from_csv/improve_prompt.py").main(["--out", str(tmp_path / "run")])
    check_output(capsys.readouterr().out, tmp_path / "run")
    assert set(res["report"]["splits"]) == {"evolve", "val", "holdout"}
    assert res["delta"] > 0  # the stand-in model is noisy on purpose
    assert "Lesson:" in res["best_prompt"]  # the mock reflection LM folded grader feedback into the prompt


def test_01_rejects_unknown_labels(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("text,label\nhello,greeting\n")
    with pytest.raises(SystemExit, match="greeting"):
        load("01_prompt_from_csv/improve_prompt.py").main(["--csv", str(bad), "--out", str(tmp_path / "run")])


def test_02_agent_harness(tmp_path, capsys):
    res = load("02_agent_harness/improve_harness.py").main(["--out", str(tmp_path / "run"), "--rounds", "3"])
    out = capsys.readouterr().out
    check_output(out, tmp_path / "run")
    assert "kept edits" in out and Path(res["trace"]).name == "TRACE.md"
    assert set(res["report"]["splits"]) == {"evolve", "holdout", "ood"}


@pytest.mark.slow
def test_03_training_script(tmp_path, capsys):
    data = tmp_path / "tinylm_data"  # built here, never in ~/.cache
    res = load("03_training_script/improve_training_script.py").main(
        ["--out", str(tmp_path / "run"), "--data", str(data), "--experiments", "3"]
    )
    out = capsys.readouterr().out
    check_output(out, tmp_path / "run")
    assert (data / "visible").exists()
    assert res["noise"]["delta"] > 0 and res["noise"]["n"] >= 2
    assert res["rows"][0]["description"] == "baseline" and len(res["rows"]) == 4
