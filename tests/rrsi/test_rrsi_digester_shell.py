"""The agentic digester's read-only shell cannot leave its traces dir, write files, run programs or read secrets."""

from __future__ import annotations

import subprocess

import pytest

from rsi.rrsi.agentic import _bash_ok, _readonly_env


@pytest.fixture
def traces(tmp_path):
    root = tmp_path / "r1" / "analysis" / "rendered"
    root.mkdir(parents=True)
    (root / "t1.txt").write_text("step 1\nGRADING: FAILED\n")
    (tmp_path / "trace.jsonl").write_text('{"sealed": {"holdout": {"S": 0.41}}}\n')
    return root


@pytest.mark.parametrize(
    "cmd",
    [
        "grep -n 'FAILED' t1.txt | head -30",
        "grep -c step *.txt",
        "awk '{print $1}' t1.txt | sort | uniq -c",
        "sed -n '1,5p' t1.txt",
        "find . -name '*.txt'",
        "cat t1.txt | tr a-z A-Z | wc -l",
        "jq -r '.a' t1.txt",
        "ls",
        "grep -E 'a|b' t1.txt",
        "sed -n '10,40p' t1.txt",
        "sed 's/FAILED/F/g;s/step/S/' t1.txt",
        "sed -n '/GRADING/,$p' t1.txt",
        "grep -rn FAILED .",
        "awk 'NR==2 {print $2}' t1.txt",
    ],
)
def test_read_only_pipelines_inside_the_traces_dir_are_allowed(traces, cmd):
    assert _bash_ok(cmd, traces)


@pytest.mark.parametrize(
    "cmd",
    [
        "grep sealed ../../../trace.jsonl",  # the run's own trace holds sealed-split shadow scores
        "cat /etc/passwd",
        "grep -r sealed ..",
        "find .. -name trace.jsonl",
        "find / -name '*.jsonl'",
        "cat ~/.bashrc",
        "cat $HOME/.bashrc",
        'grep x "$HOME/x"',
        "find . -name '*.txt' -delete",
        "find . -exec cat {} +",
        "sort -o t1.txt t1.txt",
        "sort -ro t1.txt t1.txt",
        "sed 'w out.txt' t1.txt",
        "sed -n '1p;w out' t1.txt",
        "awk 'BEGIN{system(\"id\")}'",
        "awk '{print | \"sh\"}' t1.txt",
        "awk 'BEGIN{print ENVIRON[\"HOME\"]}'",
        "uniq t1.txt out.txt",
        "jq --rawfile x ../../../trace.jsonl -n '$x'",
        "grep a t1.txt; cat /etc/passwd",
        "grep a t1.txt && id",
        "cat < /etc/passwd",
        "rm -rf .",
        "python -c 1",
        "grep a t1.txt || id",
        "grep -r sealed .*",  # .* can match ..
        "cat .?",
        "grep -f/etc/passwd t1.txt",  # a path attached to a short option
        "grep --file=/etc/passwd t1.txt",
        "sed '1r /etc/passwd' t1.txt",  # address + read command
        "sed 's/a/b/e' t1.txt",  # the execute flag
        "sed 's/a/b/w out' t1.txt",
        "awk '@include \"/etc/passwd\"' t1.txt",
    ],
)
def test_escapes_writes_and_command_execution_are_rejected(traces, cmd):
    assert not _bash_ok(cmd, traces)


def test_the_shell_environment_carries_no_secrets(monkeypatch, traces):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    env = _readonly_env()
    assert "ANTHROPIC_API_KEY" not in env and "PATH" in env
    out = subprocess.run("env", shell=True, capture_output=True, text=True, env=env).stdout
    assert "sk-secret" not in out
