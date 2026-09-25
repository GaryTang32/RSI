"""Prompts and gene rendering (spec §6).

* :func:`render_gene` - the paper's shared injection template ("Domain keywords:
  ... Summary: ... Strategy: 1. ... AVOID: ..."), with ablation switches for X1-X2
  (keywords only, + summary, + strategy, AVOID only, appended raw failure log);
* :func:`render_skill` - the long "Skill" form (a SKILL.md with sections) built
  from the same experience, used as the baseline representation in X1;
* ``GENE_WRITER_*`` - the gist of the GEP executor prompt adapted to "write one
  strategy gene from this failure" (§6.1); ``REUSE_PROMPT`` / ``HUB_MATCHED_BLOCK``
  (§6.2); ``DISTILL_PROMPT`` (§6.4, with its "Prefer --version" validation
  advice in faithful mode, replaced by a discriminative requirement in safe mode);
* :func:`parse_gene` - JSON reply -> :class:`~rsi.evomap.assets.Gene`.
"""
from __future__ import annotations

import json
import re
from typing import Optional, Sequence

from rsi.core.llm import extract_json

from .assets import CATEGORIES, Gene

GENE_TEMPLATE_PARTS = ("keywords", "summary", "strategy", "avoid")


def render_gene(g: Gene, parts: Sequence[str] = GENE_TEMPLATE_PARTS, *, failure_log: str = "",
                show_id: bool = True) -> str:
    lines = ["## Strategy gene" + (f" {g.id}" if show_id else "")]
    if "keywords" in parts and g.signals_match:
        kws = [s for s in g.signals_match if not s.startswith(("problem:", "area:"))]
        lines.append("Domain keywords: " + ", ".join(kws))
    if "summary" in parts and g.summary:
        lines.append("Summary: " + g.summary)
    if "strategy" in parts and g.strategy:
        lines.append("Strategy:")
        lines += [f"{i}. {s}" for i, s in enumerate(g.strategy, 1)]
    if "avoid" in parts and g.avoid:
        lines.append("AVOID:")
        lines += [f"- {a}" for a in g.avoid]
    if failure_log:
        lines.append("Previous failure log (verbatim):")
        lines.append(failure_log)
    return "\n".join(lines) + "\n"


SKILL_BOILERPLATE = [
    ("Overview", "This skill packages the complete working knowledge collected while solving problems of this kind. "
                 "It is intended as documentation: read the whole document before starting, and consult the "
                 "reference sections as needed. The procedures below were written after reviewing the full "
                 "trajectory, including exploratory dead ends, tooling notes and environment details."),
    ("When to use", "Use this skill whenever the task description, the error output or the surrounding code suggests "
                    "the problem class described below. The skill is broad on purpose so that it covers related "
                    "situations; not every section will apply to every task."),
    ("Background", "Problems of this class are common in production code. They usually arise at the boundary between "
                   "components, where assumptions about inputs, formats and edge cases differ. Historically, teams "
                   "addressed them with ad-hoc fixes; this document consolidates the approaches that worked and the "
                   "context needed to apply them, including background on standards, conventions and history."),
    ("Environment notes", "The original trajectory ran in a Python 3 environment with the standard library only. "
                          "Tests were executed with a plain script runner. Paths, file names and helper scripts from "
                          "the original environment are listed in the references section for completeness."),
    ("Worked example", "In the original run, the first attempt passed the visible checks but failed hidden cases. "
                       "The agent then inspected the failing inputs, compared them with the specification, revised "
                       "the implementation, re-ran the visible checks and finally submitted the corrected version."),
    ("References", "See the standard library documentation for the relevant modules, the project style guide, the "
                   "original issue thread, and the list of auxiliary scripts: check_inputs.py, run_examples.py, "
                   "compare_outputs.py. Additional notes and transcripts are archived with the run."),
]


def render_skill(g: Gene, *, extra_sections: Sequence[tuple[str, str]] = SKILL_BOILERPLATE, repeat: int = 2) -> str:
    """The long 'Skill' representation: same experience, ~10x the tokens, embedded in documentation."""
    title = g.summary or g.id
    out = [f"# SKILL: {title}", ""]
    for i in range(repeat):
        for name, text in extra_sections:
            out += [f"## {name}" + (f" ({i + 1})" if repeat > 1 else ""), text, ""]
        if i == 0:
            out += ["## Procedure", "The procedure that worked, with commentary:"]
            for k, s in enumerate(g.strategy, 1):
                out.append(f"{k}. {s} This step matters because skipping it was a source of errors in the original "
                           f"run; apply it carefully and document the outcome.")
            if g.avoid:
                out += ["", "## Pitfalls observed"]
                out += [f"- {a} (observed during the original trajectory)" for a in g.avoid]
            out += ["", "## Keywords", ", ".join(g.signals_match), ""]
    return "\n".join(out) + "\n"


def truncate_tokens(text: str, max_tokens: int) -> str:
    """Matched-budget fragment: the first ~max_tokens tokens (4 chars/token)."""
    return text[: max(0, 4 * max_tokens)]


GENE_WRITER_SYSTEM = ("You are a protocol-bound evolution engine. Compliance overrides optimality. "
                      "You distil what worked (or what would fix a failure) into ONE compact, reusable strategy gene.")

GENE_WRITER_PROMPT = """\
An agent attempted a task and we want a reusable STRATEGY GENE for this class of task: a compact strategy
card another agent (possibly on a different model) can follow on UNSEEN tasks of the same kind.

Signals (what describes the situation): {signals}

Public task description:
{task}

What happened (public checks / trace excerpt):
{trace}
{hub_block}
Rules:
- The gene must be GENERAL: never include task ids, literal expected outputs, or values specific to this one task.
- signals_match: 2-5 short keywords/patterns that identify the task class (they will be substring-matched).
- strategy: 3-6 ordered, actionable, imperative steps (NOT summaries).
- avoid: 1-3 failure-aware warnings ("AVOID: ...").
- category: one of repair | optimize | innovate | explore.
- validation: a list of commands that would detect a regression of this fix in the task workspace{validation_hint}.
- Total length under 230 tokens.

Reply with ONE JSON object in a ```json fence:
{{"id": "gene_<descriptive_snake_name>", "category": "...", "signals_match": [...], "summary": "...",
  "strategy": [...], "avoid": [...], "validation": [...]}}
"""

HUB_MATCHED_BLOCK = """
Hub Matched Solution (STRONG REFERENCE):
- Asset: {asset_id}
- Gene: {gene_id}
- Summary: {summary}
- Payload: ```json
{payload}
```
Use this as your primary approach if applicable. Adapt to local context.
"""

REUSE_PROMPT = """\
GEP -- REUSE MODE (Search-First)
You are applying a VERIFIED solution from the hub.
1. Read the capsule details below. 2. Apply the fix to the local codebase, adapting paths/names.
3. Run validation to confirm it works. 4. If passed, solidify. 5. If failed, ROLLBACK and report.
IMPORTANT: Do NOT reinvent. Apply faithfully.
{payload}
"""

DISTILL_SYSTEM = "You are a Gene synthesis engine."

DISTILL_PROMPT = """\
You are a Gene synthesis engine: distill successful evolution capsules into a high-quality, reusable Gene that
other AI agents can discover, fetch, and execute.
Rules: id "gene_distilled_<kebab>"; summary 30-200 chars (marketplace-quality); signals_match 3-7 generic
snake_case signals; strategy 5-10 steps, each a clear imperative sentence starting with a verb; avoid 1-3 items.
{validation_rule}
QUALITY BAR: Imagine this Gene will be published on a marketplace for thousands of AI agents.

Successful capsules:
{capsules}

Reply with one JSON object in a ```json fence with keys id, category, summary, signals_match, strategy, avoid,
validation.
"""

DISTILL_VALIDATION_FAITHFUL = ('Validation MUST be LIGHT. Do NOT emit the test suite. Prefer "python --version" (env '
                               'sanity) or "python <committed-light-script>.py". Good: "python --version".')
DISTILL_VALIDATION_SAFE = ("Validation MUST be DISCRIMINATIVE: commands that FAIL on the workspace before the fix and "
                           "PASS after it (e.g. the task's public test script). Never use --version, print-only "
                           "scripts or empty lists.")


def gene_writer_prompt(signals: Sequence[str], task_text: str, trace: str, *, hub_block: str = "",
                       validation_hint: str = "") -> str:
    return GENE_WRITER_PROMPT.format(signals=", ".join(signals), task=task_text[:3000], trace=trace[-2500:],
                                     hub_block=hub_block, validation_hint=validation_hint)


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", s.lower()).strip("_")[:60] or "gene"


def parse_gene(text: str, *, default_id: str = "gene_generated", default_signals: Sequence[str] = (),
               default_validation: Sequence[str] = ()) -> Optional[Gene]:
    """Parse a gene JSON reply; returns None when nothing usable is found."""
    try:
        d = extract_json(text)
    except ValueError:
        return None
    if not isinstance(d, dict):
        return None
    strategy = [str(s).strip() for s in (d.get("strategy") or []) if str(s).strip()]
    if not strategy:
        return None
    gid = str(d.get("id") or default_id)
    gid = gid if gid.startswith("gene_") else "gene_" + _slug(gid)
    gid = re.sub(r"[^A-Za-z0-9_\-]", "_", gid)[:100]
    sm = [str(s) for s in (d.get("signals_match") or default_signals) if str(s).strip()]
    cat = d.get("category") if d.get("category") in CATEGORIES else "repair"
    val = d.get("validation")
    val = [str(v) for v in val] if isinstance(val, list) else list(default_validation)
    avoid = [str(a).removeprefix("AVOID:").strip() for a in (d.get("avoid") or []) if str(a).strip()]
    return Gene(id=gid, category=cat, signals_match=sm[:8], strategy=strategy[:10], avoid=avoid[:5],
                summary=str(d.get("summary") or strategy[0])[:300], validation=val[:5],
                preconditions=[str(p) for p in (d.get("preconditions") or [])][:4])


def gene_payload(g: Gene) -> str:
    return json.dumps({k: v for k, v in g.to_dict().items()
                       if k in ("id", "summary", "signals_match", "strategy", "avoid", "validation")}, indent=1)
