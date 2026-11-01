# RSI: Recursive Self-Improvement Agent Skills

Every few weeks a new paper shows a model improving its own prompts, harness or training code. This project turns
those recursive self-improvement (RSI) papers into tools you can use on your own task:

- **A Python package, `rsi`.** Eight methods (RRSI, GEPA, autoresearch, Meta-Harness, SoL-Pi, Dream-RSI, EvoMap,
  Harness²) rebuilt from their papers on one shared core, so they can be compared on the same tasks, grader and budget.
- **A Claude Code skill, `rsi-improve`.** It tells Claude how to apply any of those methods to your problem: set it up,
  measure the noise first, pick a method, run it with an audit trail, and report the result honestly.
- **The evidence.** Every factual claim in the skill is paired with a verbatim quote from its paper or code, and each
  paper's claims were tested against the code.

<div class="grid cards" markdown>

- **[Install and use](getting-started.md)**: the package, the Claude Code skill (or plugin), and a first offline run.
- **[The guarded loop](methodology.md)**: locked grader, noise band, sealed splits, and what to expect.
- **[Methods](methods/rrsi/README.md)**: one section per method, with its paper spec, implementation notes and audit.
- **[API reference](api.md)**: `rsi.improve`, `rsi.transfer_report`, the `Domain` and `TaskSuite` types.

</div>

```bash
pip install "rsi @ git+https://github.com/GaryTang32/RSI"
rsi improve --method rrsi --problem agentqa --llm sim --out runs/first   # offline, about 10 s, no API key
```

Source, issues and releases: [GaryTang32/RSI](https://github.com/GaryTang32/RSI). MIT licensed.
