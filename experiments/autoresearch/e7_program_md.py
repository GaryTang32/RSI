"""E7 - one chain, one idea at a time; the strategy improves only when a person rewrites program.md.

Claim [doc]: "The strategy is improved by a person, by editing the instructions (program.md, a
lightweight 'skill')"; "one chain, one idea at a time; the strategy only improves when a person
rewrites the instructions". The lever is program.md alone: same task, same budget, same seeds,
same agent - only the ProgramSpec differs:

  upstream   karpathy/autoresearch program.md (adapted to the framework-run loop)
  xgboost    + the xgboost port's research discipline (hypothesis + classification, no
             near-duplicates, synthesis every 10 experiments, switch family on a plateau)
  simplify   + "prefer simplifications and removals"

Measured per night: best metric after N experiments (and its true quality on the landscape),
diversity of the edited knobs, share of edits that remove lines, keep rate; plus the check that
the loop itself never changes program.md (the version recorded on every ledger node is constant).

--llm claude:haiku  the real comparison: an LLM research agent reads the rendered program.md.
--llm sim (default) the program-blind control: the offline scripted MockLLM answers through the same
                    LLM-agent code path but ignores the prompt, so all presets must give identical
                    nights (any live difference is then attributable to program.md alone).

Usage: python experiments/autoresearch/e7_program_md.py [--llm sim|claude:haiku] [--seeds N] [--quick] [--task landscape|tinylm]
"""
from __future__ import annotations

from _common import SCRATCH, ci, is_live, parser, plt, propose_llm, suffix, usage_of, write  # noqa: I001

import json

import numpy as np

from rsi.autoresearch import AutoresearchLoop, Config, LandscapeTask, LLMResearchAgent, ProgramSpec
from rsi.core import RewriteEditor

PRESETS = ("upstream", "xgboost", "simplify")
KNOB_RE = r"^([A-Z_][A-Z0-9_]*)\s*="


def make_task(name: str):
    if name == "tinylm":
        from rsi.domains.tinylm import TinyLMTask

        return TinyLMTask(budget_s=2.0)
    return LandscapeTask()


def night(preset: str, seed: int, n: int, llm_spec: str, task_name: str) -> dict:
    import re

    task = make_task(task_name)
    # the offline control uses the scripted MockLLM (same seed for every preset); live uses the cached CLI
    llm = propose_llm(llm_spec if is_live(llm_spec) else "scripted", pool=task.mock_edit_pool(), seed=seed)
    agent = LLMResearchAgent(RewriteEditor(llm))
    res = AutoresearchLoop(task, agent, Config(max_experiments=n, seed=seed, overwrite=True, hidden_audit=False,
                                               plot=False, tag=f"e7-{preset}-{seed}"),
                           program=ProgramSpec.preset(preset),
                           out_dir=SCRATCH / "e7" / f"{task_name}_{preset}_{seed}_{llm_spec.replace(':', '_')}",
                           llms=[llm]).run()
    nodes = [nd for nd in res.ledger.nodes() if nd.kind == "candidate" and nd.status not in ("invalid", "duplicate")]
    knobs, removals = set(), 0
    for nd in nodes:
        for line in (nd.diff or "").splitlines():
            if line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
                m = re.match(KNOB_RE, line[1:].strip())
                if m:
                    knobs.add(m.group(1))
        add = sum(1 for l in (nd.diff or "").splitlines() if l.startswith("+") and not l.startswith("+++"))
        rem = sum(1 for l in (nd.diff or "").splitlines() if l.startswith("-") and not l.startswith("---"))
        removals += int(rem > add)
    versions = {nd.meta.get("program_version") for nd in res.ledger.nodes()}
    a = res.meta["analysis"]
    return {"preset": preset, "seed": seed, "best": a["best"], "baseline": a["baseline"],
            "improvement": a["improvement"], "keep_rate": a["keep_rate"], "n_crash": a["n_crash"],
            "true_best": task.truth(res.best) if hasattr(task, "truth") else None,
            "knobs_touched": sorted(knobs), "n_knobs_touched": len(knobs),
            "removal_share": removals / max(1, len(nodes)), "program_versions": sorted(v for v in versions if v),
            "descriptions": [nd.change for nd in nodes], "usage": usage_of([llm])}


def main():
    ap = parser(__doc__.splitlines()[0], seeds=2)
    ap.add_argument("--task", default="landscape", choices=("landscape", "tinylm"))
    a = ap.parse_args()
    live = is_live(a.llm)
    n = (5 if a.quick else 12) if live else (8 if a.quick else 20)
    seeds = list(range(1 if a.quick else a.seeds))
    runs = [night(p, s, n, a.llm, a.task) for p in PRESETS for s in seeds]
    out = {"config": {"task": a.task, "presets": PRESETS, "experiments": n, "seeds": seeds, "llm": a.llm,
                      "program_versions": {p: ProgramSpec.preset(p).version for p in PRESETS}}, "presets": {}}
    for p in PRESETS:
        rs = [r for r in runs if r["preset"] == p]
        out["presets"][p] = {"improvement": ci([r["improvement"] for r in rs]),
                             "true_best": ci([r["true_best"] for r in rs if r["true_best"] is not None]),
                             "n_knobs_touched": ci([r["n_knobs_touched"] for r in rs]),
                             "removal_share": ci([r["removal_share"] for r in rs]),
                             "keep_rate": ci([r["keep_rate"] for r in rs]), "runs": rs}
    P = out["presets"]
    verdict = {"improvement": {p: P[p]["improvement"]["mean"] for p in PRESETS},
               "n_knobs_touched": {p: P[p]["n_knobs_touched"]["mean"] for p in PRESETS},
               "removal_share": {p: P[p]["removal_share"]["mean"] for p in PRESETS},
               "one_program_version_per_night": all(len(r["program_versions"]) == 1 for r in runs)}
    if live:
        verdict["usage"] = [r["usage"] for r in runs]
        verdict["claim_reproduced"] = "see the per-preset numbers (live comparison, few seeds: descriptive only)"
    else:
        same = all(json.dumps([r["descriptions"] for r in P[p]["runs"]]) ==
                   json.dumps([r["descriptions"] for r in P["upstream"]["runs"]]) for p in PRESETS)
        verdict["program_blind_control_identical_across_presets"] = same
        verdict["claim_reproduced"] = (
            "control only: a program-blind agent gives identical nights under every program.md "
            f"({'confirmed' if same else 'NOT identical - check the harness'}), and the loop never changes "
            "program.md within a night; the comparison itself needs --llm claude:haiku")
    out["verdict"] = verdict
    name = "e7_program_md" + ("" if a.task == "landscape" else f"_{a.task}") + suffix(a.llm, a.quick)
    out["figure"] = str(figure(out, name))
    write(name, out)
    print(json.dumps(verdict, indent=1))


def figure(out, name):
    from _common import RESULTS

    p = plt()
    fig, axes = p.subplots(1, 2, figsize=(10, 4))
    P = out["presets"]
    for ax, key, lab in ((axes[0], "improvement", "improvement over baseline"),
                         (axes[1], "n_knobs_touched", "distinct knobs edited")):
        m = [P[q][key]["mean"] for q in PRESETS]
        ax.bar(range(len(PRESETS)), m, color=["#5f6368", "#1a73e8", "#1e8e3e"])
        for i, q in enumerate(PRESETS):
            ax.scatter([i] * len(P[q]["runs"]), [r[key] for r in P[q]["runs"]], color="k", s=10, zorder=3)
        ax.set_xticks(range(len(PRESETS)))
        ax.set_xticklabels(PRESETS)
        ax.set_ylabel(lab)
    fig.suptitle(f"E7: only program.md differs ({out['config']['llm']} agent, {out['config']['task']})", fontsize=10)
    fig.tight_layout()
    path = RESULTS / f"{name}.png"
    RESULTS.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=110)
    return path


if __name__ == "__main__":
    main()
