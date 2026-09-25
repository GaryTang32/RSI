"""E13 - frame-dependent ("transductive") features: CV cannot see them, the audit frame breaks them.

Claim [spec E13, the xgboost port's commit 9d6ee8b "forbid frame-dependent features in
prepare(), which broke both sol5.6 runs"]: a feature computed from the whole frame passed
to ``featurize`` (counts, frame statistics) means something different on the 20k-row
training frame and on a 50k-row audit frame; CV cannot notice because features are built
once on the full training frame before the folds are split. The port answered with a
prompt rule ("if you ran prepare() on a random half ... would this column change?");
this framework also mechanizes it (``prepare.check_per_row``, hardened mode).

A. Battery (deterministic: fixed folds and model seed): four frame-dependent features, each
   paired with its legal twin that fits the same statistic on ``train`` at module level
   (the rule's "lookups fitted on train"). Measured: CV AUC (what the loop sees), hidden
   iid / shifted AUC (post-hoc audit on 50k-row frames), and whether the mechanical
   per-row check flags it. Prompt-rule-only (faithful) detection is 0 by construction:
   nothing checks the rule.
B. Live (--llm claude:haiku, or the offline --llm scripted): an LLM research agent on the
   tabular task in faithful mode with the per-row rule in its program.md; every version it
   proposed is re-checked with the mechanical check afterwards (how often does the prompt
   rule alone fail?).

Usage: python experiments/autoresearch/e13_frame_dependent.py [--llm sim|claude:haiku] [--seeds N] [--quick]
"""
from __future__ import annotations

from _common import SCRATCH, ci, is_live, parser, plt, research_agent, suffix, usage_of, write  # noqa: I001

import json

import numpy as np

from rsi.autoresearch import AutoresearchLoop, Config
from rsi.domains.tabular import TabularTask

FEAT = "    cols = [np.asarray(frame[c], dtype=float) for c in FEATURES]\n"
BUCKET = '(frame["origin"] * 24 + np.floor(frame["hour"]))'
TRAIN_BUCKET = '(train["origin"] * 24 + np.floor(train["hour"]))'

# name -> (frame-dependent code, (module-level lookup fitted on train, per-row code using it))
PAIRS = {
    "bucket_count": (
        f"np.unique({BUCKET}, return_inverse=True, return_counts=True)[2]"
        f"[np.unique({BUCKET}, return_inverse=True)[1]].astype(float)",
        (f"_K, _C = np.unique({TRAIN_BUCKET}, return_counts=True)\nBUCKET_COUNT = dict(zip(_K.tolist(), _C.tolist()))\n",
         f"np.array([BUCKET_COUNT.get(b, 0) for b in {BUCKET}.tolist()], dtype=float)")),
    "bucket_freq": (
        f"np.unique({BUCKET}, return_inverse=True, return_counts=True)[2]"
        f"[np.unique({BUCKET}, return_inverse=True)[1]] / len(frame['hour'])",
        (f"_K, _C = np.unique({TRAIN_BUCKET}, return_counts=True)\n"
         f"BUCKET_FREQ = dict(zip(_K.tolist(), (_C / len(train['hour'])).tolist()))\n",
         f"np.array([BUCKET_FREQ.get(b, 0.0) for b in {BUCKET}.tolist()], dtype=float)")),
    "age_over_max": ('frame["age"] / frame["age"].max()',
                     ('AGE_MAX = float(train["age"].max())\n', 'frame["age"] / AGE_MAX')),
    "priority_zscore": ('(frame["priority_score"] - frame["priority_score"].mean()) / frame["priority_score"].std()',
                        ('PRIO_MU, PRIO_SD = float(train["priority_score"].mean()), float(train["priority_score"].std())\n',
                         '(frame["priority_score"] - PRIO_MU) / PRIO_SD')),
}


def variant(base, code: str, lookup: str = ""):
    t = base["train.py"].replace(FEAT, FEAT + f"    cols += [{code}]\n", 1)
    if lookup:
        t = t.replace("\n\ndef featurize(frame):", "\n" + lookup + "\n\ndef featurize(frame):", 1)
    return base.with_files({"train.py": t})


def measure(task, art) -> dict:
    f = task.run(art, mode="faithful")
    h = task.run(art, mode="hardened")
    aud = task.audit(art)
    return {"cv": f.metric, "hardened_crash": h.crash_reason, "flagged": bool(h.crash_reason and
                                                                             h.crash_reason.startswith("frame-dependent")),
            "iid": aud.get("test_iid"), "shift": aud.get("test_shift"), "audit_flags": aud.get("audit_flags")}


def battery() -> dict:
    task = TabularTask()
    task.prepare()
    base = task.seed_artifact()
    out = {"baseline": measure(task, base), "pairs": {}}
    for name, (dep, (lookup, per_row)) in PAIRS.items():
        out["pairs"][name] = {"frame_dependent": measure(task, variant(base, dep)),
                              "per_row_twin": measure(task, variant(base, per_row, lookup))}
    return out


def live_arm(args) -> dict:
    seed, n, llm_spec = args
    task = TabularTask()
    agent, llms = research_agent(llm_spec, task.mock_edit_pool(), seed=seed)
    res = AutoresearchLoop(task, agent, Config(max_experiments=n, mode="faithful", seed=seed, overwrite=True,
                                               tag=f"e13-{seed}", hidden_audit=True, audit_discards=True),
                           out_dir=SCRATCH / "e13" / f"live_{seed}_{llm_spec.replace(':', '_')}", llms=llms).run()
    rows = res.meta["audit"]
    flagged = [r for r in rows if r.get("audit_flags") and "frame-dependent" in r["audit_flags"]]
    return {"seed": seed, "n_audited": len(rows), "n_frame_dependent": len(flagged),
            "frame_dependent": [{k: r.get(k) for k in ("status", "description", "metric", "test_iid", "test_shift")}
                                for r in flagged], "usage": usage_of(llms)}


def main():
    ap = parser(__doc__.splitlines()[0], seeds=1)
    a = ap.parse_args()
    if is_live(a.llm):
        n = 4 if a.quick else 10
        runs = [live_arm((s, n, a.llm)) for s in range(a.seeds)]
        verdict = {"frame_dependent_versions": sum(r["n_frame_dependent"] for r in runs),
                   "versions_audited": sum(r["n_audited"] for r in runs), "usage": [r["usage"] for r in runs]}
        write("e13_frame_dependent" + suffix(a.llm, a.quick),
              {"config": {"experiments": n, "seeds": list(range(a.seeds)), "llm": a.llm, "mode": "faithful"},
               "runs": runs, "verdict": verdict})
        print(json.dumps(verdict, indent=1))
        return
    B = battery()
    base = B["baseline"]
    rows = {}
    for name, pr in B["pairs"].items():
        d, t = pr["frame_dependent"], pr["per_row_twin"]
        rows[name] = {"cv_gain_dependent": d["cv"] - base["cv"], "cv_gain_twin": t["cv"] - base["cv"],
                      "iid_gain_dependent": d["iid"] - base["iid"], "iid_gain_twin": t["iid"] - base["iid"],
                      "shift_gain_dependent": d["shift"] - base["shift"], "shift_gain_twin": t["shift"] - base["shift"],
                      "flagged_dependent": d["flagged"], "flagged_twin": t["flagged"]}
    dep_minus_twin_iid = [r["iid_gain_dependent"] - r["iid_gain_twin"] for r in rows.values()]
    verdict = {
        "mechanical_detection_rate": float(np.mean([r["flagged_dependent"] for r in rows.values()])),
        "mechanical_false_positive_rate": float(np.mean([r["flagged_twin"] for r in rows.values()])),
        "prompt_rule_only_detection_rate": 0.0,        # faithful mode: nothing enforces the rule
        "max_abs_cv_difference_dependent_vs_twin": float(max(abs(r["cv_gain_dependent"] - r["cv_gain_twin"])
                                                             for r in rows.values())),
        "iid_auc_dependent_minus_twin": dict(zip(rows, dep_minus_twin_iid)),
        "worst_iid_damage": float(min(dep_minus_twin_iid)),
        "pairs": rows,
    }
    verdict["claim_reproduced"] = (
        f"partly: the mechanical per-row check flags {verdict['mechanical_detection_rate']:.0%} of the frame-dependent "
        f"features and {verdict['mechanical_false_positive_rate']:.0%} of their per-row twins; CV cannot tell a "
        f"feature from its twin (max |dCV| {verdict['max_abs_cv_difference_dependent_vs_twin']:.4f}); on the larger "
        f"audit frame the worst frame-dependent feature loses {-verdict['worst_iid_damage']:.4f} hidden iid AUC vs its "
        "twin, the others are benign here - the damage is real but small in this synthetic world, and none of these "
        "features raises CV (no labels reach featurize, so the port's CV inflation cannot occur)")
    out = {"config": {"task": "tabular", "pairs": list(PAIRS), "deterministic": True, "llm": a.llm},
           "battery": B, "verdict": verdict}
    name = "e13_frame_dependent" + suffix(a.llm, a.quick)
    out["figure"] = str(figure(rows, name))
    write(name, out)
    print(json.dumps({k: v for k, v in verdict.items() if k != "pairs"}, indent=1))


def figure(rows, name):
    from _common import RESULTS

    p = plt()
    fig, ax = p.subplots(figsize=(9, 4))
    xs = np.arange(len(rows))
    for off, key, col, lab in ((-0.3, "cv_gain", "#9aa0a6", "CV AUC gain (what the loop sees)"),
                               (-0.1, "iid_gain", "#1a73e8", "hidden iid AUC gain (50k-row audit frame)"),
                               (0.1, "shift_gain", "#e37400", "hidden shifted AUC gain")):
        ax.bar(xs + off, [r[f"{key}_dependent"] - r[f"{key}_twin"] for r in rows.values()], 0.2, color=col, label=lab)
    ax.axhline(0, color="grey", lw=0.8)
    ax.set_xticks(xs)
    ax.set_xticklabels([f"{k}\n(flagged: {'yes' if r['flagged_dependent'] else 'no'})" for k, r in rows.items()],
                       fontsize=8)
    ax.set_ylabel("frame-dependent minus per-row twin (AUC)")
    ax.set_title("E13: frame-dependent features vs their per-row twins", fontsize=10)
    ax.legend(fontsize=7)
    fig.tight_layout()
    path = RESULTS / f"{name}.png"
    RESULTS.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=110)
    return path


if __name__ == "__main__":
    main()
