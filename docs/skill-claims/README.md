# Skill claim ledger

Evidence for every factual claim in `.claude/skills/rsi-improve/` (SKILL.md and `methods/*.md`), checked on 25-26 Oct 2026:
2,176 claims, all matching the skill, 77 of them corrected in the skill during the check (3 on 25 Oct 2026, when the
Dream-RSI docs moved to the paper's v2).

| Ledger | Claims | Corrected | Sources |
|---|---|---|---|
| `general` | 37 | 11 | SKILL.md's shared sections vs the `rsi` code and docs |
| `rrsi` | 301 | 6 | arXiv 2609.24972v3 (v1 where it differs) |
| `metaharness` | 261 | 12 | arXiv 2603.28052v1 |
| `solpi` | 302 | 9 | arXiv 2609.20519v1 (HTML and PDF text) |
| `gepa` | 288 | 8 | arXiv 2507.19457v2 |
| `autoresearch` | 269 | 8 | karpathy/autoresearch @228791fb; Rehearse, arXiv 2607.27687 |
| `dream` | 303 | 11 | arXiv 2609.14858v2 with Listings 1-2 (v1 where tagged) |
| `evomap` | 415 | 12 | arXiv 2604.15097v4, 2605.25815v4, 2608.23200v4; GEP spec 1.14.0; Evolver README; evomap.ai skill.md and wiki |

One JSON object per line in `ledger/<method>.jsonl`:

- `id`, `where` (file and section or SKILL.md line at the time of the check), `claim` (short paraphrase)
- `kind`: `paper` (quote from a source text), `code` / `repo-doc` (quote from this repo), `absent` (the skill says the
  source is silent; `searched` lists the terms that found nothing), `skill-ref` (a SKILL.md line backed by its reference)
- `source`, `quote` (verbatim; `...` joins pieces that occur in order), `verdict` (`match`, or `fixed` when the skill was
  corrected to this claim; `note` then gives the old wording)

## Re-checking

```
python docs/skill-claims/check.py                   # code, repo-doc and skill-ref rows; paper rows skipped
python docs/skill-claims/check.py --corpus DIR      # also paper rows, from DIR/<method>/<file>
```

The paper texts are not committed (third-party text). `sources.json` lists each file's origin, size and sha256;
arXiv HTML was converted with `html2txt.py`, and the checker warns when a corpus file differs from the one used.
A `code` row failing after a code change means the skill may be stale for that claim: update the skill, then the row.
