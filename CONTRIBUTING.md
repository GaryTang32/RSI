# Contributing

Thanks for helping. This project turns recursive self-improvement papers into tested code and a Claude Code skill, so
two things matter more than usual: changes must keep the loop's safeguards intact, and every claim about a paper must
be backed by a source.

## Set up

```bash
git clone https://github.com/GaryTang32/RSI && cd RSI
python -m venv .venv && source .venv/bin/activate        # Python 3.11+
pip install -e ".[science,dev]"
pytest                                                   # offline and deterministic, ~7 min
pytest -m "not slow"                                     # the quick subset while you work
```

Tests never call a real model: `pytest` deselects the `live` marker. To run the live checks against a real model
(they cost money and need the `claude` CLI or `ANTHROPIC_API_KEY`), use `pytest -m live`.

## Before you open a pull request

- `pytest` passes.
- `ruff check` and `ruff format --check` pass on `rsi tests experiments scripts docs/skill-claims` (or install the
  hooks once: `pip install pre-commit && pre-commit install`). Do not reformat the artifact files listed under
  `extend-exclude` in `pyproject.toml`: models read them as text and their hashes identify recorded runs.
- `python docs/skill-claims/check.py` reports `failed=0` if you touched the skill, a method or code the skill quotes.
- New behaviour has a test. Bug fixes have a test that fails without the fix.
- User-facing changes are listed under "Unreleased" in [`CHANGELOG.md`](CHANGELOG.md).

Keep pull requests focused: one fix or feature per PR is easier to review than a bundle.

## What goes where

| You want to... | Look at |
|---|---|
| fix a method | `rsi/<method>/`, its tests in `tests/<method>/`, its doc in `docs/methods/<method>/` |
| add a test problem | `rsi/domains/` (see `agentqa` for a harness domain, `bbh` for a downloaded benchmark) |
| change the shared loop (grader lock, noise band, sealed splits) | `rsi/core/`; see [`docs/guide/ARCHITECTURE.md`](docs/guide/ARCHITECTURE.md) |
| change the Claude Code skill | `.claude/skills/rsi-improve/` and the claim ledger (below) |
| record an experiment | `experiments/<method>/`; outputs go to the `data` branch, never to `main` |

## Rules that keep results trustworthy

- **Do not weaken a safeguard silently.** The grader stays outside the artifact, sealed splits (`holdout`, `ood`,
  `test`) stay unreadable to the loop, and keep rules state their noise handling. A change to any of these needs a
  test and a note in the PR.
- **Cite paper claims.** Every factual claim in the skill has a row in `docs/skill-claims/ledger/<method>.jsonl` with
  a verbatim quote from the paper or the code. If you change what the skill says, update the row; if you change code
  the skill quotes, `check.py` will tell you which row to update.
- **Report results honestly.** "No change beat the noise band" is a valid result. Do not present a rejected candidate
  or a single lucky run as a gain, and say when an interval includes zero.
- **No datasets or recorded runs in `main`.** Benchmarks are downloaded on first use and checked against a sha256
  manifest; run outputs live on the `data` branch.
- **Third-party code keeps its licence and attribution** (see the notices next to ported code).

## Adding a method

1. Write `docs/methods/<method>/paper-spec.md` from the paper, citing sections.
2. Implement it in `rsi/<method>/` on the shared core (`rsi.core`), expose it through `rsi.improve(..., method=...)`.
3. Add offline tests that run it on a simulated domain, and document where it departs from the paper in
   `docs/methods/<method>/implementation.md`.
4. Add a method reference under `.claude/skills/rsi-improve/methods/` and its claim-ledger rows.

## Reporting bugs and asking questions

Use the issue templates. For anything security-related, follow [`SECURITY.md`](SECURITY.md) instead of opening a
public issue.

By contributing you agree that your contributions are licensed under the [MIT License](LICENSE) and to follow the
[Code of Conduct](CODE_OF_CONDUCT.md).
