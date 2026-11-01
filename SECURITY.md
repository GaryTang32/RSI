# Security

## Read this before running a loop

The `rsi` package runs code that a language model wrote. Treat every run as running untrusted code:

- **Harness code runs in your Python process.** In harness domains such as `agentqa`, `bbh` and `bbeh`, the evolved
  `harness.py` is executed with `exec` inside the evaluating process.
- **The tool sandbox is not a security boundary.** `rsi.core.sandbox` runs model-written Python in a subprocess with a
  timeout, resource limits, a scratch directory and a scrubbed environment. That limits accidents, not attacks.
- **Coding-agent proposers edit files.** Meta-Harness's agent proposer runs `claude -p` with file tools, confined to
  the run directory by the CLI's own permissions.
- **Model calls cost money** and, with the `claude` CLI, count toward your plan's usage limits.

So: run live loops in a container, VM or other disposable environment; do not run them where your shell has secrets
or credentials it does not need; always set a spend limit; and read a candidate's diff before using it outside the
loop.

## Supported versions

| Version | Supported |
|---|---|
| latest release and `main` | yes |
| older releases | no |

## Reporting a vulnerability

Please do **not** open a public issue. Report it privately through GitHub:
**Security → Report a vulnerability** on this repository (private vulnerability reporting). Include what you found, how
to reproduce it, and the impact you expect. You should get a first response within a week. Please give us a reasonable
time to fix it before disclosing it publicly.

In scope: anything in this repository that lets model-written code or a crafted dataset escape the limits described
above, leak a sealed split or a grader verdict to the loop, or run code on download. Out of scope: the risks described
in "Read this before running a loop", which are inherent to the method.
