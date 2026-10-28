"""ClaudeCLI stops the process on an account usage limit when RSI_EXIT_ON_USAGE_LIMIT is set."""
import json
import subprocess
import sys
import textwrap


def test_usage_limit_exits_75(tmp_path):
    fake = tmp_path / "claude"
    reply = tmp_path / "reply.json"
    reply.write_text(json.dumps({"is_error": True, "result": "You've hit your 5-hour limit · resets 7pm (UTC)"}))
    fake.write_text(f"#!/bin/sh\ncat >/dev/null\ncat {reply}\n")
    fake.chmod(0o755)
    code = textwrap.dedent(f"""
        from rsi.core.llm import ClaudeCLI
        r = ClaudeCLI("haiku", executable={str(fake)!r}, retries=1).complete("hi")
        print("returned", r.error)
    """)
    run = lambda env: subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    import os
    base = {k: v for k, v in os.environ.items() if k != "RSI_EXIT_ON_USAGE_LIMIT"}
    assert run({**base, "RSI_EXIT_ON_USAGE_LIMIT": "1"}).returncode == 75
    off = run(base)
    assert off.returncode == 0 and "returned" in off.stdout and "limit" in off.stdout
