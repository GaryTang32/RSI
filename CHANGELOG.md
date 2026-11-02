# Changelog

All notable changes to this project are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).
Before 1.0, minor versions may change the API.

## [Unreleased]

### Changed
- The codebase is formatted and linted with ruff (CI checks both); a pre-commit config runs the same checks.
- Slow tests are marked, so `pytest -m "not slow"` gives a quick local subset.

### Added
- README section 5, "How to use": what to ask, the setup questions, what the skill does, the files a run writes
  and how to read them (also on the docs site's Install and use page).
- `ROADMAP.md` (including the API friction found while writing the examples) and `.github/SUPPORT.md`.
- `examples/`: three runnable examples (a prompt from a CSV with GEPA, an agent harness with RRSI, a training script
  with autoresearch), offline by default, live with `--llm claude:haiku`.
- Claude Code plugin: `/plugin install rsi --marketplace GaryTang32/RSI` installs the skill as `/rsi:rsi-improve`.
- Documentation site (MkDocs Material) with getting started, methodology, every method's docs, the benchmarks, the
  evidence, an API reference and the chart gallery; built on every PR and published to GitHub Pages from `main`.
- Docstrings with arguments, returns and examples for `rsi.improve`, `rsi.recommend`, `transfer_report`,
  `ImprovementResult`, `FunctionDomain` and `TaskSuite.from_tasks`.
- Release workflow can publish to PyPI through trusted publishing (off until configured).
- `scripts/check_versions.py` keeps the version in `pyproject.toml`, the plugin manifest, `CITATION.cff` and the
  changelog in step (run in CI and on release).

### Fixed
- SoL-Pi's `Run` no longer shares one mutable default `Config` across calls.

## [0.1.0] - 2026-11-01

First tagged release.

### Added
- `rsi` package: eight recursive self-improvement methods rebuilt from their papers on one shared core: RRSI, GEPA,
  autoresearch, Meta-Harness, SoL-Pi, Dream-RSI, EvoMap and Harness².
- Shared core: LLM backends (`claude` CLI, Anthropic API, offline simulator) with a reply cache; artifacts; task suites
  with sealed `holdout`, `ood` and `test` splits; a locked grader; noise bands; keep rules; paired bootstrap transfer
  reports; an audit trail per round.
- `rsi.improve()`, `rsi.recommend()`, `rsi.transfer_report()` and the `rsi` command (`methods`, `improve`, `inspect`,
  `recommend`).
- Test domains with known ground truth (`agentqa`, `agentworld`, `discovery`, `tinylm`, ...) and two public benchmarks
  downloaded on first use and checked against a sha256 manifest (`bbh`, `bbeh`).
- The `rsi-improve` Claude Code skill: a question-based setup, per-method references, and a claim ledger pairing 2,410
  claims in the skill with verbatim quotes from the papers and code (`docs/skill-claims/check.py` re-checks them).
- A paper-claims audit (475 claims), per-method paper specs and implementation notes, and a 66-chart gallery.
- Contribution guide, code of conduct, security policy, citation file, issue and PR templates, CI and a release
  workflow.

### Changed
- Requires Python 3.11 or newer (3.10 reached end of life in October 2026; on 3.10 a harness that swallows every
  exception could not be stopped).

### Fixed
- Non-editable installs (`pip install` from git) now include the package's data files: seed-harness prompts, benchmark
  manifests and EvoMap's schemas.

[Unreleased]: https://github.com/GaryTang32/RSI/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/GaryTang32/RSI/releases/tag/v0.1.0
