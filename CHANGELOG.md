# Changelog

All notable changes to this project are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).
Before 1.0, minor versions may change the API.

## [Unreleased]

## [0.1.1] - 2026-11-08

Fixes for all 29 findings of the November 2026 code review and three follow-ups, plus README and docs
improvements. Upgrading from 0.1.0 needs no code changes, with the exceptions listed under Changed.

### Fixed
- Spend caps now work for every model on the API backend: prices cover all current Claude models (only Haiku was
  priced, so Sonnet and Opus calls were recorded as $0 and `max_usd` never stopped a run). An unpriced model warns.
- `TaskSuite` rejects repeated task ids. Ids reused across families (`q0, q1` in each) silently moved sealed OOD
  tasks into the loop's `evolve` split. An explicit overlap between a decision split and a sealed split now warns.
- `FunctionDomain` turns a failed model call (outage, usage limit, offline cache miss) into an `infra:` error, so the
  trial counts as missing and is retried instead of being graded and cached as a wrong answer
  (`raise_on_llm_error=False` keeps the old behaviour).
- Caches no longer replay replies made with other backend settings: the `claude` CLI's `effort` and a non-default
  API `max_tokens` are part of the cache key, and the trial cache covers `task.meta`, numpy arrays by value, and ids
  that map to the same cache file. The API backend also reports `stop_reason`, so truncated replies are visible.
- EvoMap's command policy expands combined short flags, so `python -Ic "..."` no longer slips past a blocked `-c`.
  SafeHub rejects bundles whose workspace files would be written outside the scratch directory, runs validation in a
  subprocess by default when its reference model is a real Claude backend, and the in-process simulation executor no
  longer allows `os` or `sys`.
- RRSI's agentic digester shell is read-only and confined to its traces folder: no chaining, redirection, expansion,
  writing options (`find -delete`, `sort -o`, sed `w`/`r`/`e`, awk `system`), or paths outside the folder (the run's
  own trace holds sealed-split shadow scores), and it runs without the caller's environment variables.
- Harness²: in sequential mode both editors of a round see the same snapshot of notes and pool, so the DOM editor no
  longer sees the INV editor's notes from the same round. The leak tripwire ignores verbatim copies of the task's own
  text, so a `"score":` key in user data no longer aborts the stream; a grader score anywhere else still does.
- SoL-Pi: every paid Ralph repair is checked (`max_iters` counts repairs, so up to `max_iters + 1` exit checks); the
  relative capability floor stays below a negative base; "solved" means reaching the top of the domain's
  `score_range` (1 by default).
- Meta-Harness: the leakage screen matches path terms (`/tests`, `results/`) inside paths, screens the configured
  `search_split`, and flags only string tables an edit adds or grows, not ones the seed already had.
- Dream-RSI: `selector="guarded"` no longer crashes when a policy revision is rejected; `llm_develop` given as a
  spec string (`"claude:sonnet"`) is resolved and cached. `rsi.improve` resolves every `llm_*` spec string the same
  way.
- autoresearch: with `workers > 1`, shadow audits wait until no run is in flight, so in-flight spend is no longer
  booked as monitor spend and excluded from `max_usd` / `max_wall_s`.
- RRSI: `reevaluate()` refuses before measuring when later rounds exist, and keeps a stored evaluation unless the
  re-measurement is valid.
- `BootstrapRigor` no longer accepts any gain when an arm has a single sample (a one-value bootstrap has no
  variance); it then accepts only a gain beyond a supplied noise band. `keep_rule="rigor"` with `repeats < 2` warns.
- Diffs split lines on `\n` only, so a form feed or other Unicode line break can no longer hide an added line from
  the leakage critic.
- Traces survive a kill mid-write: a resumed run starts a fresh line, and `rsi inspect` skips (and counts) a torn line.
- `Artifact.to_dir` rejects `.` / empty path components and a name that is also another name's directory before
  writing anything; `rsi improve --set T` without `=` is a usage error.
- autoresearch: `REWIND` to a commit kept before a validation resample re-scores it instead of crashing.
- RRSI: `truncate()` also drops the held-out monitor rows of removed rounds.
- SoL-Pi: ledger node ids include the round from round 2 on (round 1 ids unchanged), candidates dropped by
  `validate_composition` are labelled `composition_dropped`, and byte-identical frozen candidates share one holdout
  verdict instead of crashing the round.
- Harness²: `best_of_n` grades the streamed tasks themselves, so custom tasks no longer crash the report.
- EvoMap: `rsi-taskcheck --n 0` (or a negative n) checks one task instead of crashing.
- RRSI: `readjudicate()` drops the held-out monitor row of a winner it replaces and logs the new winner's score; an
  unchanged winner keeps its row (monitor rows now record their artifact).
- autoresearch: `MultiChainLauncher` refuses to run concurrent chains that share an LLM object when a chain has
  `max_usd` (each chain would count the others' spend), and warns otherwise; build separate LLMs per chain.
- Harness²: the leak tripwire also accepts a marker inside a quotation of the task's own text that is cut off (as in a
  trace that truncates the task), not only an exact copy; a grader score elsewhere is still a leak.

### Changed
Behaviour that may affect code written against 0.1.0:
- `TaskSuite` raises `ValueError` on repeated task ids; give ids a family prefix (`dates-0`, `units-0`).
- `FunctionDomain` raises a failed model call as an `infra:` error (counted missing, retried, never cached); pass
  `raise_on_llm_error=False` to read `LLMResponse.error` yourself.
- Some caches re-run once: trial-cache entries of tasks that have `meta`, and model-call cache entries made with the
  `claude` CLI's `effort` or a non-default API `max_tokens`, now have different keys.
- `BootstrapRigor` with one sample per arm accepts only a gain beyond a supplied noise band, and `keep_rule="rigor"`
  with `repeats < 2` warns because it rejects every candidate (the default is 3).
- SoL-Pi's `max_iters` counts Ralph repairs, so up to `max_iters + 1` exit checks; "solved" follows the domain's
  `score_range` (unchanged for the default 0-1 range).
- `MultiChainLauncher` refuses concurrent chains that share an LLM when a chain has `max_usd`.
- `Artifact.to_dir` rejects `.` / empty path components such as `d//x`, which used to be written as `d/x`.
- SafeHub validates real-model bundles in a subprocess by default, and the in-process simulation executor no longer
  imports `os` or `sys`.
- Parallel autoresearch writes shadow-audit records once no run is in flight.

Project:
- The codebase is formatted and linted with ruff (CI checks both); a pre-commit config runs the same checks.
- Slow tests are marked, so `pytest -m "not slow"` gives a quick local subset.
- README: a new first screen (tagline, results chart, three-step quick start, results at a glance).

### Added
- README section 5, "How to use": what to ask, the setup questions, what the skill does, the files a run writes
  and how to read them (also on the docs site's Install and use page).
- README section 6, "Adopt it in your own project": which files to copy into your own repository, a tested copy
  script, and the three edits to make afterwards.
- README section 6: a "Slim core" option that copies only the skill and the package and trims the built-in demo
  problems (tested in a clean environment).
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

[Unreleased]: https://github.com/GaryTang32/RSI/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/GaryTang32/RSI/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/GaryTang32/RSI/releases/tag/v0.1.0
