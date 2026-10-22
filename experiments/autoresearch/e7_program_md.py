"""E7 - one chain, one idea at a time; the strategy improves only when a person rewrites program.md.

Usage: python experiments/autoresearch/e7_program_md.py [--llm sim|claude:haiku] [--seeds N] [--quick] [--task tinylm]
"""
from _common import (SCRATCH, ci, is_live, parser, plt, propose_llm, report, save_fig, slug, suffix,  # noqa: I001
                     usage_of)

import json
import re

from rsi.autoresearch import AutoresearchLoop, Config, LandscapeTask, LLMResearchAgent, ProgramSpec
from rsi.core import RewriteEditor
from rsi.domains.tinylm import TinyLMTask

PRESETS = ("upstream", "xgboost", "simplify")
KNOB_RE = r"^([A-Z_][A-Z0-9_]*)\s*="


def make_task(name: str):
    return TinyLMTask(budget_s=2.0) if name == "tinylm" else LandscapeTask()


def night(preset: str, seed: int, n: int, llm_spec: str, task_name: str) -> dict:
    task = make_task(task_name)
    # the offline control is the scripted MockLLM (same seed for every preset); live is the cached CLI
    llm = propose_llm(llm_spec if is_live(llm_spec) else "scripted", pool=task.mock_edit_pool(), seed=seed)
    cfg = Config(max_experiments=n, seed=seed, overwrite=True, hidden_audit=False, plot=False,
                 tag=f"e7-{preset}-{seed}")
    res = AutoresearchLoop(task, LLMResearchAgent(RewriteEditor(llm)), cfg, program=ProgramSpec.preset(preset),
                           out_dir=SCRATCH / "e7" / f"{task_name}_{preset}_{seed}_{slug(llm_spec)}", llms=[llm]).run()
    nodes = [nd for nd in res.ledger.nodes() if nd.kind == "candidate" and nd.status not in ("invalid", "duplicate")]
    knobs, removals = set(), 0
    for nd in nodes:
        lines = (nd.diff or "").splitlines()
        added = [line for line in lines if line.startswith("+") and not line.startswith("+++")]
        removed = [line for line in lines if line.startswith("-") and not line.startswith("---")]
        knobs |= {m.group(1) for line in added + removed if (m := re.match(KNOB_RE, line[1:].strip()))}
        removals += int(len(removed) > len(added))
    versions = {nd.meta.get("program_version") for nd in res.ledger.nodes()}
    a = res.meta["analysis"]
    return {"preset": preset, "seed": seed, "best": a["best"], "baseline": a["baseline"],
            "improvement": a["improvement"], "keep_rate": a["keep_rate"], "n_crash": a["n_crash"],
            "true_best": task.truth(res.best) if hasattr(task, "truth") else None,
            "knobs_touched": sorted(knobs), "n_knobs_touched": len(knobs),
            "removal_share": removals / max(1, len(nodes)), "program_versions": sorted(v for v in versions if v),
            "descriptions": [nd.change for nd in nodes], "usage": usage_of([llm])}


def main() -> None:
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
    report(name, out)


def figure(out: dict, name: str):
    p = plt()
    fig, axes = p.subplots(1, 2, figsize=(10, 4))
    P = out["presets"]
    for ax, key, lab in ((axes[0], "improvement", "improvement over baseline"),
                         (axes[1], "n_knobs_touched", "distinct knobs edited")):
        ax.bar(range(len(PRESETS)), [P[q][key]["mean"] for q in PRESETS], color=["#5f6368", "#1a73e8", "#1e8e3e"])
        for i, q in enumerate(PRESETS):
            ax.scatter([i] * len(P[q]["runs"]), [r[key] for r in P[q]["runs"]], color="k", s=10, zorder=3)
        ax.set_xticks(range(len(PRESETS)))
        ax.set_xticklabels(PRESETS)
        ax.set_ylabel(lab)
    fig.suptitle(f"E7: only program.md differs ({out['config']['llm']} agent, {out['config']['task']})", fontsize=10)
    return save_fig(fig, name)


if __name__ == "__main__":
    main()
