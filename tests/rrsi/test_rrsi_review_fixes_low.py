"""RRSI low-severity review fixes (L7): ``truncate`` also drops the held-out monitor rows of removed rounds."""

from rrsi_helpers import accepting, read_json, read_jsonl
from test_rrsi_generic import GENERIC, LOWER, SEED, _done, normalize_domain

import rsi.rrsi.loop as L
from rsi.core import MockLLM
from rsi.rrsi import Config, RRSIRun, drive, parse_sections


def _llm(seq):
    @accepting
    def always_better(prompt, system, seed, i):
        t = parse_sections(prompt)["directives"].get("t")
        return _done(seq[min(t or 0, len(seq) - 1)], "control_flow", f"step {t}")

    return MockLLM(always_better)


def _run(out, seq, T=2):
    cfg = Config(T=T, m=1, workers=1, delta=0.0, heldout_monitor=True)
    return RRSIRun(normalize_domain(), SEED, out_dir=out, llm_propose=_llm(seq), config=cfg)


def test_truncate_drops_monitor_rows_of_removed_rounds_and_rerun_logs_the_new_incumbent(tmp_path, monkeypatch):
    """L7: rows are keyed by t+1 (the trajectory entry round t produced); truncate(t) keeps rows t' <= t+1."""
    out = tmp_path / "m"
    r = _run(out, [LOWER, GENERIC + "\n# a\n"])
    drive(r)
    assert [x["t"] for x in read_jsonl(out / "heldout_monitor.jsonl")] == [1, 2]
    r.truncate(0)
    assert [x["t"] for x in read_jsonl(out / "heldout_monitor.jsonl")] == [1]
    holdout_evals = []
    real = L.Evaluator.evaluate

    def spy(self, artifact, split="evolve", *a, **kw):
        holdout_evals.append((split, artifact.id))
        return real(self, artifact, split, *a, **kw)

    monkeypatch.setattr(L.Evaluator, "evaluate", spy)
    r2 = _run(out, [LOWER, GENERIC + "\n# b\n"])
    drive(r2)
    new_inc = read_json(out / "frontier.json")["trajectory"][2]["artifact_id"]
    assert ("holdout", new_inc) in holdout_evals  # the re-run round's incumbent is scored, not skipped
    rows = read_jsonl(out / "heldout_monitor.jsonl")
    assert [x["t"] for x in rows] == [1, 2]
    traj = read_json(out / "frontier.json")["trajectory"]
    assert rows[1]["S_evolve"] == traj[2]["S"]
