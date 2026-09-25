"""Domain-agnostic proposer constitution (the released code ships one ``SKILL.md`` +
``PATTERNS.md`` per domain; this is the generic version with the numbers filled in
from the run's :class:`~rsi.rrsi.config.Config`). A domain may override it with a
``rrsi_constitution()`` method returning ``(skill_md, patterns_md)``, and
:func:`rsi.rrsi.run` accepts ``constitution=(skill_md, patterns_md)``.

The "How your work is judged" section is the proposer's reward, so it describes the
rules that are actually in force: for full RRSI (the default) the text is the one the
released constitutions use; an ablation arm (:class:`~rsi.rrsi.switches.RegularizerSwitches`)
gets only the paragraphs of its active mechanisms, e.g. the unregularized baseline is
told "keep if measured higher", not about a floor, a cost rule or exploration it will
never meet (otherwise a live proposer in a baseline arm would be steered by
regularizers that are switched off).

As in the released SKILL.md files, the rules are stated SYMBOLICALLY ("beta0 + beta1 x
(gain)", "w_s x (gain) - w_c x ... + w_n x (novelty)"); only m and k are numbers. The
proposer is never told the numeric weights, the noise band or the scores it must beat.
``Config.proposer_numbers=True`` (an extension) fills in the numeric beta/w values instead.
"""
from __future__ import annotations

HEADER = """# Proposer Constitution

You evolve the harness (scaffold) of an AI system around a FROZEN model/policy.
Only the harness evolves; the model, the tasks and the grader are frozen.

## How your work is judged (this is your reward)

"""

ROUND_TMPL = """Your edits ACCUMULATE round after round on a single incumbent harness. Each round
draws {m} independent candidate harnesses from the same incumbent; each candidate is
{screen}evaluated on the
FULL evolve set with {k} trial(s) per task. The measured score S is the mean reward
(missing or crashed trials count as failures). The incumbent's own evaluation is the
trace source for the next round.

"""
SCREEN_CRITIC = "screened by a leakage critic BEFORE any evaluation is spent and then "
SCREEN_NONE = ""

ADMISSIBLE = """A candidate replaces the incumbent only if it is ADMISSIBLE; among admissible ones the
highest S wins:

"""
FLOOR_S_STAR = """- Noise-adjusted floor. S' must be at least S* minus delta, where S* is the best
  incumbent score ever seen and delta is a noise band measured by re-evaluating the
  unchanged base harness. A candidate can never walk the line downhill through
  regressions small enough to look like noise.
"""
FLOOR_S_T = """- Noise-adjusted floor. S' must be at least the CURRENT incumbent's S minus delta, where
  delta is a noise band measured by re-evaluating the unchanged base harness.
"""
COST_ON = """- Cost rule for a real gain. If S' exceeds the incumbent by MORE than delta, the
  relative growth in mean policy tokens per trial must stay within
  {beta0} + {beta1} x (gain): a bigger measured gain buys a bigger cost increase, a
  small gain buys little, and a gain that also SAVES tokens always passes.
"""
COST_OFF = """- A gain of MORE than delta over the incumbent is admissible whatever it costs in tokens.
"""
BAND = {
    "shaped": """- Inside the noise band. A candidate whose gain is within delta is kept only if
  {w_s} x (gain) - {w_c} x (relative cost change) + {w_n} x (novelty) > 0, where novelty
  counts STRUCTURAL components ({structural}) the incumbent has never had an accepted
  edit on. A neutral candidate survives by cutting tokens or by landing a working,
  non-regressing structural mechanism, never by a coin-flip gain.
""",
    "strict": """- Inside the noise band. A candidate whose gain is within delta is kept only if its
  measured S is higher than the incumbent's.
""",
    "reject": """- Inside the noise band. A candidate whose gain is within delta is never kept.
""",
    "admit": """- Inside the noise band. A candidate whose gain is within delta is admissible.
""",
}
GREEDY = """A candidate replaces the incumbent only if its measured S is HIGHER than the incumbent's
measured S; among such candidates the highest S wins.
"""
ARGMAX = """The candidate with the highest measured S replaces the incumbent, whatever its score.
"""

PROPOSAL_FULL = """
Two regularizers act on WHAT you may propose:

- Edit budget b_t. The number of independent edits one candidate may bundle is capped
  and anneals over the run (several early, few late), so late-round measurements
  attribute to a single component.
- History, exploration and pruning. Every measured edit is recorded with its
  component, hypothesis, score change, cost change and verdict. A rejected mechanism is
  negative evidence: do not redraw it unchanged. When the incumbent has not moved by
  more than delta for several rounds, a candidate slot is RESERVED for a component the
  run has never exercised. Components exercised without a strictly improving edit in
  the recent window are listed as COMPONENTS TO PRUNE: removing the machinery
  accumulated there is itself a legitimate edit.
"""
BUDGET_ANNEAL = """- Edit budget b_t. The number of independent edits one candidate may bundle is capped
  and anneals over the run (several early, few late), so late-round measurements
  attribute to a single component.
"""
BUDGET_CONST = """- Edit budget b_t. The number of independent edits one candidate may bundle is capped
  at b_t (given in the context each round).
"""
HIST = {
    "full": """- History. Every measured edit is recorded with its component, hypothesis, score change,
  cost change and verdict. A rejected mechanism is negative evidence: do not redraw it
  unchanged.
""",
    "accepted_only": """- History. The edits that were accepted into the harness are listed with their component,
  hypothesis, score change and cost change.
""",
    "none": "",
}
EXPLORE = """- Exploration. When the incumbent has not moved by more than delta for several rounds, a
  candidate slot is RESERVED for a component the run has never exercised.
"""
PRUNE = """- Pruning. Components exercised without a strictly improving edit in the recent window
  are listed as COMPONENTS TO PRUNE: removing the machinery accumulated there is itself a
  legitimate edit.
"""

RULES = """
## The overfitting trap (read first)

The harness is evolved on the SAME tasks it is scored on. Task-specific fixes are
tempting and worthless: the outcome is judged on held-out and never-seen tasks where
memorized knowledge is useless. Litmus test for every change: "would this help a
competent operator on MANY unfamiliar tasks of this kind?"

## Hard rules (violations are auto-rejected)

1. Edits, counted by independence, not by line count. An edit is one independent,
   attributable change; dependent parts are ONE edit. Ship at most this round's
   EDIT BUDGET b_t. Larger subsystems may be built ACROSS rounds ("phase 1 of N").
2. No task-specific content: never write task ids, task names, entity names,
   expected outputs or answers, or task-identifying triggers into any file.
3. Never touch the grader or try to detect/influence it.
4. Mechanism over wording: prefer control flow, output plumbing, information routing,
   tools or state over rewording prompts, unless prose is the right lever.
5. Don't break the contract (entry points, signatures, output format).
6. Don't disable safety mechanisms without a working replacement.
7. Unattended robustness: the harness runs on every task; an unhandled exception
   kills the whole candidate.
8. Never jeopardize termination: every added loop or retry is bounded.
9. English only.
"""

PATTERNS_MD = """# Pattern Library (reference, not an allowlist)

- Tool use for exact work: route computation, counting, parsing and lookups through a
  deterministic tool and feed its output back, instead of asking the model to do it
  "in its head". Trap: tool output that is never read back.
- Output contract: state the exact final-answer format once, in one place; extract the
  answer robustly. Trap: formatting rules that only match the practice tasks.
- Bounded verification: at most one targeted re-check pass with an explicit exit.
  Trap: "keep improving until correct" loops.
- Context budget: keep what the model needs, truncate head/tail of long tool output.
- Skills and memory: entity-free general procedures only. Trap: caching answers,
  file contents or per-task solutions keyed by task identity (that is memorization).
- Sub-calls: one bounded extra model call for a narrow sub-problem. Trap: they tend to
  HURT small policies and multiply cost.
- Pruning: machinery that stopped earning its place (no measured gain in the recent
  window) should be removed; removal is a legitimate, attributable edit.
- A mechanism refuted at one cost is not thereby refuted at another.
"""


def _is_full_proposal(sw) -> bool:
    return (sw.budget_anneal and sw.history_conditioning == "full" and sw.stall_exploration
            and sw.prune_directives)


def default_constitution(cfg, taxonomy, switches=None) -> tuple[str, str]:
    """``(SKILL.md, PATTERNS.md)`` describing the rules in force under ``switches``
    (default: full RRSI, the released constitution's reward section). The acceptance
    weights appear as symbols, as in the released SKILL.md, unless ``cfg.proposer_numbers``."""
    from .switches import RegularizerSwitches
    sw = switches or RegularizerSwitches.full()
    if getattr(cfg, "proposer_numbers", False):
        weights = dict(beta0=cfg.beta0, beta1=cfg.beta1, w_s=cfg.w_s, w_c=cfg.w_c, w_n=cfg.w_n)
    else:
        weights = dict(beta0="beta0", beta1="beta1", w_s="w_s", w_c="w_c", w_n="w_n")
    fmt = dict(m=cfg.m, k=cfg.k, structural=" / ".join(taxonomy.K_str) or "none", **weights)
    parts = [HEADER, ROUND_TMPL.format(screen=SCREEN_CRITIC if sw.critic else SCREEN_NONE, **fmt)]
    if sw.selection == "greedy":
        parts.append(GREEDY)
    elif sw.selection == "argmax":
        parts.append(ARGMAX)
    else:
        parts.append(ADMISSIBLE)
        if sw.floor == "S_star":
            parts.append(FLOOR_S_STAR)
        elif sw.floor == "S_t":
            parts.append(FLOOR_S_T)
        parts.append((COST_ON if sw.cost_rule else COST_OFF).format(**fmt))
        parts.append(BAND.get(sw.within_band, BAND["shaped"]).format(**fmt))
    if _is_full_proposal(sw):
        parts.append(PROPOSAL_FULL)
    else:
        items = [BUDGET_ANNEAL if sw.budget_anneal else BUDGET_CONST, HIST.get(sw.history_conditioning, "")]
        if sw.stall_exploration:
            items.append(EXPLORE)
        if sw.prune_directives:
            items.append(PRUNE)
        items = [x for x in items if x]
        parts.append("\nWhat you are shown and how much you may bundle:\n\n" + "".join(items))
    parts.append(RULES)
    return "".join(parts), PATTERNS_MD
