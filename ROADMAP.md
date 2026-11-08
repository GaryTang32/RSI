# Roadmap

Where the project is going next. Items are grouped by theme, not by date. If you want to work on one, open an issue
first so we can agree on the approach; items marked **good first issue** are small and self-contained.

## Easier to use

Found while writing `examples/`:

- **A generic offline model.** `rsi improve --llm sim` works for the built-in domains, but `rsi.get_llm("sim")` raises
  and there is no simulated task model for a user's own domain. Add one (clearly labelled as a mechanics check) so any
  `FunctionDomain` can be tried offline.
- **Cost without boilerplate.** A `FunctionDomain` whose `execute_fn` returns a plain value records 0 tokens, so every
  cost column reads 0. Count tokens from the model's usage automatically.
- **Kept edits in the result.** RRSI's accepted edits are only in `history.jsonl` (rounds counted from 0, while
  `trajectory` counts from 1). Expose them on `ImprovementResult` with documented outcome names.
- **One way to say "beat the noise".** GEPA takes `acceptance="noise_margin"` plus a delta you compute; autoresearch's
  `keep_rule="rigor"` ignores the delta from `noise_runs` and only gate rules such as `MinGain()` use it. Give every
  method the same noise-band option, measured at the granularity it compares on (GEPA compares 3-example minibatches).
- **Clear counters.** `meta["analysis"]["n_keep"]` counts the baseline as a keep; GEPA's `n_accepted` counts children
  accepted into the pool, not edits in `res.best`. Rename or document them. **good first issue**
- **Total spend in one place.** `res.usage` leaves out the noise measurement and `transfer_report`, and adding the task
  and proposer meters double-counts when they are the same object. **good first issue**
- **Consistent reruns.** Reusing an `out_dir` resumes for GEPA but fails without `overwrite=True` for autoresearch.
- **A headroom check in the API.** Example 01 stops before the loop when the seed is already within the noise band of
  the top score; `rsi.improve` could warn the same way for every method.
- **`transfer_report` splits.** The default `("evolve", "holdout", "ood")` silently skips a `val` or `test` split;
  warn, or score every sealed split by default. **good first issue**

## Quality

- Type-check `rsi/core` with mypy in CI, then widen.
- Docstrings for the rest of the public API (each method's `Config` and `run`), so the API reference is complete.
  **good first issue**
- A plugin icon for the Claude Code plugin listing. **good first issue**

## Evidence

- A stronger benchmark comparison: a public benchmark with headroom (BBEH), a proposer stronger than the task model,
  a larger search set, and a check of whether the gates reject real gains. The first BBEH run (recorded on the `data`
  branch) showed most methods keeping the seed with a 24-example search set and Haiku proposing its own edits.
- Re-check the per-method docs that still describe earlier paper versions (`docs/methods/evomap`,
  `docs/methods/dream-rsi`).

## Distribution

- Publish to PyPI (the release workflow is ready once trusted publishing is configured).
- Submit the plugin to a public plugin directory once the icon and listing are in place.
