"""The live demo's infrastructure check flags every lost model call recorded in a trace, and nothing else."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "experiments" / "demo"))

from live_demo import infra_errors  # noqa: E402


def _trace(tmp_path, *events):
    lines = [json.dumps({"seq": i, "kind": k, "round": 1, "t": 0.0, "data": d}) for i, (k, d) in enumerate(events)]
    (tmp_path / "trace.jsonl").write_text("\n".join(lines) + "\n")
    return tmp_path


def test_rrsi_shaped_failures_are_flagged(tmp_path):
    cap = "You've hit your session limit · resets 11am (UTC)"
    run = _trace(tmp_path,
                 ("critic", {"candidate": "r0A", "llm": {"replies": [{"text": "", "error": "timeout"}]}}),
                 ("critic", {"candidate": "r0B", "llm": {"replies": [{"text": "", "error": cap}]}}),
                 ("analysis", {"llm_calls": [{"role": "digester", "error": "infra: cache miss in offline replay mode"}]}),
                 ("note", {"stage": "eval_invalid", "candidate": "r1A", "detail": "4/8 trials missing (infrastructure)"}),
                 ("gate", {"candidate": "r1A", "reason": "eval_invalid",
                           "math": {"gate_failure": "eval_invalid", "detail": "4/8 trials missing (infrastructure)"}}),
                 ("monitor", {"version": "r0A", "sealed": {"holdout": {"S": 0.5, "missing": 2}}}),
                 ("proposal", {"candidate": "r2A", "error": f"llm error: {cap}"}))
    errs = infra_errors(run, {"splits": {"ood": {"final": {"S": 0.1, "missing": 3}}}})
    text = "\n".join(errs)
    for want in ("timeout", "session limit", "cache miss", "eval_invalid", "monitor r0A holdout: 2 trials missing",
                 "proposal round 1 error: llm error", "re-score final ood: 3 trials missing"):
        assert want in text, (want, errs)
    assert sum("eval_invalid" in e for e in errs) == 2      # the note and the gate


def test_prompts_and_replies_that_mention_errors_are_not_flagged(tmp_path):
    run = _trace(tmp_path,
                 ("proposal", {"candidate": "c1", "prompt": "handle 'session limit' and 'llm error' gracefully",
                               "reply": "infra: retry on timeout", "error": None}),
                 ("critic", {"candidate": "c1", "llm": {"replies": [{"text": "mentions session limit", "error": None}]}}),
                 ("analysis", {"llm_calls": [{"role": "analyst", "error": None}],
                               "report": {"error": "analyst output unusable; heuristic fallback"}}),
                 ("eval", {"candidate": "c1", "summary": {"S": 1.0, "missing": 0}}))
    assert infra_errors(run) == []
