"""RuleWorld domain (GEPA Tier-1 simulator): semantics, ground truth, feedback levels, mock LM."""
import numpy as np

from rsi.core import Artifact
from rsi.domains.ruleworld import RuleWorldReflectionLM, make_domain
from rsi.domains.ruleworld.world import STANDARD
from rsi.gepa import DEFAULT_TEMPLATE, build_prompt


def test_analytic_expectation_matches_simulation():
    d = make_domain(seed=2, slip=0.1)
    w = d.world
    files = w.seed_files()
    asp = w.aspects_of("triage")
    ex = w.examples[d.tasks.splits["evolve"][0]]
    # a messy prompt: general, contradictory and conditioned rules, plus a demo carrying a fact
    files["prompts/triage.md"] = "\n".join([
        files["prompts/triage.md"].rstrip(),
        w.rule_text(asp[0], w.aspects[asp[0]].codes[0]),
        w.rule_text(asp[1], w.aspects[asp[1]].codes[0]),
        w.rule_text(asp[1], w.aspects[asp[1]].codes[1]),
        w.rule_text(asp[2], w.aspects[asp[2]].codes[1], [w.families[0]]),
        d.demo_text(d.tasks.get(ex.id), None, "prompts/triage.md"),
    ]) + "\n"
    art, ids = Artifact(files), d.tasks.splits["val"][:15]
    exp = w.expected(files, ids)
    sims = [np.mean([d.run(art, d.tasks.get(i), seed=s).score for i in ids]) for s in range(400)]
    assert abs(np.mean(sims) - exp) < 0.01


def test_oracle_and_seed_ground_truth():
    d = make_domain(seed=0)
    oracle = d.expected(d.oracle_artifact(), "test")
    assert d.expected(d.seed_artifact(), "test") == 0.0
    assert abs(oracle - (1 - d.world.cfg.slip)) < 1e-9
    assert d.expected(Artifact(d.world.oracle_files(conditioned=False)), "test") < oracle - 0.03


def test_rule_semantics_conditioned_contradiction_fact():
    w = make_domain(seed=0, slip=0.0).world
    conf = next(a for a, x in w.aspects.items() if x.conflict)
    x = w.aspects[conf]
    ex = next(e for e in w.examples.values() if conf in e.aspects and e.family == "government")
    view = w.parse_module("\n".join([w.rule_text(conf, x.codes[0]), w.rule_text(conf, x.codes[1], ["government"])]))
    assert w.outcome_dist(view, ex, conf) == ([(x.codes[1], 1.0)], "conditioned rule")
    dist, why = w.outcome_dist(w.parse_module(w.rule_text(conf, x.codes[0]) + "\n" + w.rule_text(conf, x.codes[1])),
                               ex, conf)
    assert len(dist) == 2 and "contradictory" in why
    fact = w.parse_module(f"Ticket {ex.ticket}: {conf}: {ex.target[conf]}")
    assert w.outcome_dist(fact, ex, conf)[0] == [(ex.target[conf], 1.0)]
    other = next(e for e in w.examples.values() if conf in e.aspects and e.ticket != ex.ticket)
    assert w.outcome_dist(fact, other, conf)[0] == [(STANDARD, 1.0)]


def test_feedback_levels_and_module_records():
    d = make_domain(seed=0)
    task = d.tasks.get(d.tasks.splits["evolve"][0])
    tr = d.run(d.seed_artifact(), task, seed=11)
    assert "Rule: When an order is" in d.feedback_text(task, {a: STANDARD for a in task.meta["aspects"]})
    sym = d.with_feedback("symptom")
    assert "Rule" not in sym.feedback_text(task, {}) and "handled incorrectly" in sym.feedback_text(task, {})
    assert d.with_feedback("score_only").feedback_text(task, {}) == ""
    for comp in d.component_paths():
        rec = d.reflective_record(task, tr, comp)
        assert set(rec) == {"Inputs", "Generated Outputs", "Feedback"} and rec["Feedback"].startswith("Score:")
    assert "Upstream notes" in d.reflective_record(task, tr, "prompts/reply.md")["Inputs"]


def test_mock_reflection_uses_only_prompt_information():
    d = make_domain(seed=0, slip=0.0)
    w = d.world
    task = d.tasks.get(d.tasks.splits["evolve"][0])
    comp = "prompts/triage.md"
    tr = d.run(d.seed_artifact(), task, seed=1)
    rich = d.reflective_record(task, tr, comp)
    llm = RuleWorldReflectionLM(w)
    prompt = build_prompt(DEFAULT_TEMPLATE, d.seed_artifact()[comp].rstrip(), [rich])
    new = w.parse_module(llm.complete(prompt, seed=0).text.strip("`\n"))
    named = [a for a in task.meta["aspects"] if w.aspects[a].module == "triage"]
    assert any(a in new.rules or task.meta["ticket"] in new.facts for a in named)
    # score-only: no rule text in the prompt, so rules can only be guessed from the lexicon
    so = d.with_feedback("score_only").reflective_record(task, tr, comp)
    p2 = build_prompt(DEFAULT_TEMPLATE, d.seed_artifact()[comp].rstrip(), [so])
    assert not any(c in p2 for a in named for c in w.aspects[a].codes)
    hits = 0
    for s in range(60):
        v = w.parse_module(llm.complete(p2, seed=s).text.strip("`\n"))
        hits += sum(1 for a, rules in v.rules.items() if any(c == task.meta["aspects"].get(a) for c, _ in rules))
    assert 0 < hits < 60


def test_rl_vocabulary_and_gold_text():
    d = make_domain(seed=0)
    for comp in d.component_paths():
        voc = d.rl_vocabulary(comp)
        assert len(voc) == len(set(voc)) >= 8 * 3
    task = d.tasks.get(d.tasks.splits["evolve"][1])
    g = d.gold_text(task, "prompts/triage.md") + d.gold_text(task, "prompts/reply.md")
    assert all(c in g for c in task.meta["aspects"].values())
