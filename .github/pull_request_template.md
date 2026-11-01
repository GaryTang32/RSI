## What and why

<!-- What this changes and why. Link the issue if there is one. -->

## How it was checked

<!-- Tests added or run, offline runs, live runs (with cost), screenshots for docs or charts. -->

## Checklist

- [ ] `pytest` passes
- [ ] `ruff check rsi tests experiments scripts` passes
- [ ] `python docs/skill-claims/check.py` reports `failed=0` (if the skill, a method or quoted code changed)
- [ ] New behaviour has a test; a bug fix has a test that fails without it
- [ ] No safeguard is weakened (locked grader, sealed splits, noise band), or the change says why
- [ ] `CHANGELOG.md` updated under "Unreleased" for user-facing changes
- [ ] No datasets or recorded runs added to `main`
