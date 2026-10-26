"""Gallery specs for Harness² (writes specs/harness2.json) from results/harness2/{compare_live,compare_sim}.json."""
import json
from collections import Counter
from pathlib import Path

V = Path(__file__).resolve().parent; R = V.parents[2]
r3 = lambda x: round(float(x), 3)
live = json.loads((R / "results/harness2/compare_live.json").read_text())
sim = json.loads((R / "results/harness2/compare_sim.json").read_text())
S, A = live["summary"], live["arms"]
k1, k3 = A["harness2_k1"], A["harness2_k3"]
ids = sorted(set(k1["per_task"]) & set(k3["per_task"]))
ci = lambda t: {"y": r3(t[0]), "lo": r3(t[1]), "hi": r3(t[2])}
NOTE = "Claude Haiku as agent, improver and selector; 20 AgentQA held-out/OOD tasks; labels used only after the loop"
charts = [
 dict(id="h2-live-accuracy", method="Harness²", type="bar", source="results/harness2/compare_live.json",
      title="Harness² with no grader: accuracy on a 20-task stream",
      takeaway=f"Without reading any score, Harness² lifted accuracy from {S['seed (H0 base1)'][0]:.2f} (the seed harness) to "
               f"{S['harness2_k1']['acc'][0]:.2f} at k = 1 and {S['harness2_k3']['acc'][0]:.2f} at k = 3.",
      x={"label": "Arm", "kind": "category"}, y={"label": "Accuracy (graded after the loop)", "min": 0, "max": 1, "format": ".2f"},
      series=[{"name": "Accuracy", "points": [dict(x="Seed harness (1 rollout)", **ci(S["seed (H0 base1)"])),
                                               dict(x="Harness² k = 1 (4 rollouts)", **ci(S["harness2_k1"]["acc"])),
                                               dict(x="Harness² k = 3 (12 rollouts)", **ci(S["harness2_k3"]["acc"]))]}],
      note=NOTE + "; bars show 95% bootstrap CIs over tasks"),
 dict(id="h2-live-gains", method="Harness²", type="interval", source="results/harness2/compare_live.json",
      title="Paired gains: Harness² vs the seed, and k = 3 vs k = 1",
      takeaway=f"k = 3's gain over the seed (+{S['harness2_k3']['vs_seed']['mean_diff']:.2f}) and over k = 1 (+{S['k3_minus_k1']['mean_diff']:.2f}) "
               f"exclude zero; k = 1's +{S['harness2_k1']['vs_seed']['mean_diff']:.2f} reaches zero.",
      x={"label": "Comparison", "kind": "category"}, y={"label": "Accuracy difference (paired by task)", "format": "+.2f"},
      series=[{"name": "Difference", "points": [
          {"x": n, "y": r3(v["mean_diff"]), "lo": r3(v["lo"]), "hi": r3(v["hi"])} for n, v in
          (("k = 1 - seed", S["harness2_k1"]["vs_seed"]), ("k = 3 - seed", S["harness2_k3"]["vs_seed"]), ("k = 3 - k = 1", S["k3_minus_k1"]))]}],
      refLines=[{"axis": "y", "value": 0, "label": "no change"}], note=NOTE),
]
def fates(arm):
    c = Counter(("fixed" if (p["base"], p["submitted"]) == (0, 1) else "kept" if (p["base"], p["submitted"]) == (1, 1)
                 else "broke" if (p["base"], p["submitted"]) == (1, 0) else "still wrong") for t in ids for p in [arm["per_task"][t]])
    return c
f1, f3 = fates(k1), fates(k3)
charts.append(dict(id="h2-live-task-fates", method="Harness²", type="stacked", source="results/harness2/compare_live.json",
    title="What each arm did to the seed's answers, task by task",
    takeaway=f"k = 3 fixed {f3['fixed']} of the seed's {f3['fixed'] + f3['still wrong']} failures and broke {f3['broke']} of its successes; "
             f"k = 1 fixed {f1['fixed']} and broke {f1['broke']}.",
    x={"label": "Arm", "kind": "category"}, y={"label": "Tasks", "min": 0, "format": ".0f"},
    series=[{"name": n, "points": [{"x": "Harness² k = 1", "y": f1[key]}, {"x": "Harness² k = 3", "y": f3[key]}]}
            for n, key in (("Still correct", "kept"), ("Broke", "broke"), ("Fixed", "fixed"), ("Still wrong", "still wrong"))], note=NOTE))
fams = sorted(k1["report"]["families"])
charts.append(dict(id="h2-live-families", method="Harness²", type="bar", source="results/harness2/compare_live.json",
    title="Accuracy by task family, seed vs Harness²",
    takeaway="k = 3 brought numeric, lists and strings tasks to 1.00 and number theory from 0.00 to 0.67; dates were already solved.",
    x={"label": "Task family", "kind": "category"}, y={"label": "Accuracy", "min": 0, "max": 1, "format": ".2f"},
    series=[{"name": "Seed harness", "points": [{"x": f"{f} (n={k1['report']['families'][f]['n']})", "y": r3(k1["report"]["families"][f]["base"])} for f in fams]},
            {"name": "Harness² k = 1", "points": [{"x": f"{f} (n={k1['report']['families'][f]['n']})", "y": r3(k1["report"]["families"][f]["submitted"])} for f in fams]},
            {"name": "Harness² k = 3", "points": [{"x": f"{f} (n={k1['report']['families'][f]['n']})", "y": r3(k3["report"]["families"][f]["submitted"])} for f in fams]}],
    note=NOTE + "; 3-7 tasks per family"))
order = [("seed", "Seed"), ("harness2_k1", "Harness² k = 1"), ("harness2_k3", "Harness² k = 3"), ("best_of_4", "Best-of-4"),
         ("best_of_12", "Best-of-12"), ("gepa_matched", "GEPA (matched calls)")]
arms = sim["arms"]
charts.append(dict(id="h2-sim-arms", method="Harness²", type="bar", source="results/harness2/compare_sim.json",
    title="Offline: Harness² vs best-of-n with the same selector, and GEPA",
    takeaway=f"With the simulated model, Harness² gained (k = 1 {arms['harness2_k1']['accuracy']:.2f}, k = 3 {arms['harness2_k3']['accuracy']:.2f} vs "
             f"seed {arms['seed']['accuracy']:.2f}) while best-of-4/12 with the same selector did not ({arms['best_of_4']['accuracy']:.2f}, "
             f"{arms['best_of_12']['accuracy']:.2f}): extra rollouts alone did not help.",
    x={"label": "Arm", "kind": "category"}, y={"label": "Accuracy", "min": 0, "max": 1, "format": ".2f"},
    series=[{"name": "Accuracy", "points": [{"x": lab, "y": r3(arms[k]["accuracy"]), "lo": r3(arms[k]["ci95"][0]), "hi": r3(arms[k]["ci95"][1])}
                                             for k, lab in order if k in arms]}],
    note="simulated domain: shows the mechanism, not a benchmark; the mock improver knows which edits the simulated model rewards; "
         "GEPA trains on a separate labelled split"))
(V / "specs/harness2.json").write_text(json.dumps({"charts": charts}, indent=1, ensure_ascii=False))
print(len(charts), "charts")
