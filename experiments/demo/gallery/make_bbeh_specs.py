"""Gallery specs for BIG-Bench Hard / Extra Hard (writes specs/bbeh.json) from results/bbeh/* and results/bbh/pilot.json."""
import json
from pathlib import Path

V = Path(__file__).resolve().parent; R = V.parents[2]
r3 = lambda x: round(float(x), 3)
load = lambda p: json.loads((R / p).read_text())
m = load("results/bbeh/merged.json")
A, X = m["arms"], m["paired_extra"]
bbeh_pilot, bbh_pilot = load("results/bbeh/pilot.json"), load("results/bbh/pilot.json")
ledger = lambda p: [json.loads(l) for l in (R / p).read_text().splitlines() if l.strip()]
NOTE = ("Claude Haiku as agent and proposer; 12 BBEH families picked by the pilot (6 practice, 6 unseen); "
        "BBEH's official answer check, ported")
NAMES = {"seed": "Seed harness", "rrsi": "RRSI", "gepa": "GEPA", "metaharness": "Meta-Harness",
         "harness2": "Harness² k = 1", "bestofn": "Best-of-7"}
iv = lambda v: {"y": r3(v["mean_diff"]), "lo": r3(v["lo"]), "hi": r3(v["hi"])}

charts = [
 dict(id="bbeh-arms", method="BBEH", type="bar", source="results/bbeh/merged.json",
      title="BBEH: accuracy of each arm's final harness",
      takeaway=f"Only RRSI moved off the seed ({A['seed']['acc'][0]:.3f} to {A['rrsi']['acc'][0]:.3f}); GEPA and Meta-Harness kept "
               f"the seed. On the 36-task stream best-of-7 reached {A['bestofn']['acc'][0]:.3f}, Harness² {A['harness2']['acc'][0]:.3f}.",
      x={"label": "Arm", "kind": "category"}, y={"label": "Accuracy", "min": 0, "max": 1, "format": ".2f"},
      series=[{"name": "Accuracy", "points": [
          dict(x=f"{NAMES[a]} (n={A[a]['n']})", y=r3(A[a]["acc"][0]), lo=r3(A[a]["acc"][1]), hi=r3(A[a]["acc"][2]))
          for a in NAMES]}],
      note=NOTE + "; seed/RRSI/GEPA/Meta-Harness: 120 held-out + unseen-family examples x 2 trials; Harness² and best-of-7: "
                  "36 of them, 1 trial; 95% bootstrap CIs"),
 dict(id="bbeh-gains", method="BBEH", type="interval", source="results/bbeh/merged.json",
      title="BBEH: paired gains over the seed, and Harness² vs best-of-7",
      takeaway=f"RRSI's +{A['rrsi']['vs_seed']['mean_diff']:.3f} is the only interval that excludes zero. Harness² trailed "
               f"best-of-7 at matched calls by {-X['harness2_minus_bestofn']['mean_diff']:.2f} "
               f"[{X['harness2_minus_bestofn']['lo']:+.2f}, {X['harness2_minus_bestofn']['hi']:+.2f}], an interval that still includes zero.",
      x={"label": "Comparison", "kind": "category"}, y={"label": "Accuracy difference (paired by example)", "format": "+.2f"},
      series=[{"name": "Difference", "points": [
          dict(x="RRSI - seed (n=120)", **iv(A["rrsi"]["vs_seed"])),
          dict(x="GEPA - seed (n=120)", **iv(A["gepa"]["vs_seed"])),
          dict(x="Meta-Harness - seed (n=120)", **iv(A["metaharness"]["vs_seed"])),
          dict(x="Harness² - its first rollout (n=36)", **iv(X["harness2_submitted_minus_H0_base1"])),
          dict(x="Best-of-7 - its first rollout (n=36)", **iv(X["bestofn_submitted_minus_H0_base1"])),
          dict(x="Harness² - best-of-7 (n=36)", **iv(X["harness2_minus_bestofn"]))]}],
      refLines=[{"axis": "y", "value": 0, "label": "no change"}], note=NOTE),
]

fams = sorted(m["families"]["seed"], key=lambda f: m["families"]["seed"][f])
practice = set(bbeh_pilot["suggestion"]["practice"])
lab = lambda f: f"{f} ({'practice' if f in practice else 'unseen'})"
charts.append(dict(id="bbeh-families", method="BBEH", type="bar", source="results/bbeh/merged.json",
    title="BBEH: seed vs RRSI by task family",
    takeaway="RRSI's answer-format edit lifted shuffled_objects from 0.55 to 0.75 and geometric_shapes from 0.25 to 0.30; "
             "the other ten families did not move.",
    x={"label": "Task family", "kind": "category"}, y={"label": "Accuracy", "min": 0, "max": 1, "format": ".2f"},
    series=[{"name": NAMES[a], "points": [{"x": lab(f), "y": r3(m["families"][a][f])} for f in fams]} for a in ("seed", "rrsi")],
    note=NOTE + "; 10 examples x 2 trials per family"))

rr = [r for r in ledger("results/bbeh/runs/rrsi/ledger.jsonl") if r.get("score") is not None and r["id"] != "H0"]
mh = [r for r in ledger("results/bbeh/runs/metaharness/ledger.jsonl") if r.get("score") is not None and r["id"] != "mh_seed"]
mh_seed = next(r["score"] for r in ledger("results/bbeh/runs/metaharness/ledger.jsonl") if r["id"] == "mh_seed")
rr_seed = next(r["score"] for r in ledger("results/bbeh/runs/rrsi/ledger.jsonl") if r["id"] == "H0")
pts = ([{"x": f"RRSI {r['id']}" + (" (kept)" if r["status"] == "ACCEPTED" else ""), "y": r3(r["score"])} for r in rr] +
       [{"x": "Meta-Harness " + r["id"].removeprefix("mh_cand_").replace("_", " "), "y": r3(r["score"])} for r in mh])
n_smoke = sum(r.get("status") == "smoke_fail" for r in ledger("results/bbeh/runs/rrsi/ledger.jsonl"))
charts.append(dict(id="bbeh-search-candidates", method="BBEH", type="bar", source="results/bbeh/runs/metaharness/ledger.jsonl",
    title="BBEH search: every scored candidate on the 24-example practice set",
    takeaway=f"The same seed scored {rr_seed:.2f} in RRSI's measurement and {mh_seed:.3f} in Meta-Harness's: with 24 examples the "
             f"noise is as large as most candidates' differences. RRSI kept one candidate; {n_smoke} more failed its smoke check.",
    x={"label": "Candidate", "kind": "category"}, y={"label": "Practice-set accuracy", "min": 0, "max": 1, "format": ".2f"},
    series=[{"name": "Candidate score", "points": pts}],
    refLines=[{"axis": "y", "value": r3(rr_seed), "label": "seed, RRSI's run"}, {"axis": "y", "value": r3(mh_seed), "label": "seed, Meta-Harness's run"}],
    note="RRSI (results/bbeh/runs/rrsi/ledger.jsonl) and Meta-Harness both score at k = 2 on the same 24 evolve examples; candidates that failed to run are omitted"))

def pilot_chart(cid, p, bench, src, takeaway, band):
    acc = p["family_accuracy"]
    fs = sorted(acc, key=lambda f: (acc[f], f))
    return dict(id=cid, method="BBEH", type="bar", source=src,
        title=f"{bench} pilot: seed accuracy per family ({p.get('n_per_family', 5)} examples each)",
        takeaway=takeaway, x={"label": "Task family", "kind": "category"},
        y={"label": "Seed accuracy", "min": 0, "max": 1, "format": ".2f"},
        series=[{"name": "Seed harness", "points": [{"x": f, "y": r3(acc[f])} for f in fs]}],
        refLines=[{"axis": "y", "value": v, "label": l} for v, l in band],
        note="Claude Haiku, one call per example; pilot examples are never used again")

bm, ba = bbh_pilot["family_accuracy"], bbeh_pilot["family_accuracy"]
charts.append(pilot_chart("bbh-pilot-ceiling", bbh_pilot, "BBH", "results/bbh/pilot.json",
    f"Haiku averaged {sum(bm.values()) / len(bm):.2f} on BIG-Bench Hard with a one-call harness, {sum(v == 1 for v in bm.values())} of "
    f"{len(bm)} families at 10/10: no room for a method to show a gain, so the experiment moved to BBEH.", []))
charts.append(pilot_chart("bbeh-pilot", bbeh_pilot, "BBEH", "results/bbeh/pilot.json",
    f"On BIG-Bench Extra Hard the same harness averaged {sum(ba.values()) / len(ba):.2f}; the 12 families between 0.15 and 0.75 "
    "became the practice and unseen sets.", [(0.15, "pilot band"), (0.75, "pilot band")]))

(V / "specs" / "bbeh.json").write_text(json.dumps({"charts": charts}, ensure_ascii=False, indent=1) + "\n")
print(len(charts), "charts -> specs/bbeh.json")
