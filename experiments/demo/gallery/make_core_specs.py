"""Specs for the shared core, the live demo runs and the verification layers (writes specs/core-live-verification.json)."""
import ast, io, json, re, subprocess, tokenize
from pathlib import Path
V = Path(__file__).resolve().parent; R = V.parents[2]; D = R   # data-branch files restored at their own paths
r2 = lambda x, n=3: round(float(x), n)
charts = []

# ---- Core: noise calibration
cal = json.load(open(D / "results/core-qa/core_calibration.json"))["summary"]
ks = [2, 3, 5]
def acc(key, dom="agentqa"):
    return [{"x": f"k = {k}", "y": r2(cal[f"{dom}/k{k}"][key]["mean"]), "lo": r2(cal[f"{dom}/k{k}"][key]["lo"]), "hi": r2(cal[f"{dom}/k{k}"][key]["hi"])} for k in ks]
charts.append(dict(id="core-null-keeps", method="Core", type="bar", source="results/core-qa/core_calibration.json",
    title="How often each keep rule accepts an unchanged artifact",
    takeaway=f"Re-scoring the same harness twice and keeping any gain accepts pure noise {acc('strict_accept')[0]['y']:.0%}-{max(p['y'] for p in acc('strict_accept')):.0%} of the time. "
             f"A noise band cuts that to {acc('min_gain_accept')[0]['y']:.0%} at k = 2, and the small-k correction to {acc('min_gain_corrected_accept')[0]['y']:.0%}.",
    x={"label": "Trials per task (k)", "kind": "category"}, y={"label": "Share of null pairs kept", "min": 0, "max": 0.6, "format": ".0%"},
    series=[{"name": "Keep any gain (strict)", "points": acc("strict_accept")},
            {"name": "Noise band (bootstrap delta)", "points": acc("min_gain_accept")},
            {"name": "Noise band, small-k corrected", "points": acc("min_gain_corrected_accept")}],
    note="AgentQA with a simulated model, 30 seeds x 40 null pairs per k; bars show 95% CIs."))
charts.append(dict(id="core-floor-clearance", method="Core", type="line", source="results/core-qa/core_calibration.json",
    title="Does the noise floor hold an unchanged artifact 97.5% of the time?",
    takeaway=f"RRSI's floor S* - delta lets an unchanged harness through {acc('clearance')[0]['y']:.1%} of the time at k = 2, not the 97.5% its code comment claims: "
             f"the bootstrap underestimates variance by (k-1)/k. With the correction it clears {acc('clearance_corrected')[0]['y']:.1%}.",
    x={"label": "Trials per task (k)", "kind": "linear"}, y={"label": "Unchanged artifact clears the floor", "min": 0.88, "max": 1.0, "format": ".1%"},
    series=[{"name": "Bootstrap delta (as in RRSI)", "points": [dict(p, x=k) for p, k in zip(acc("clearance"), ks)]},
            {"name": "Small-k corrected delta", "points": [dict(p, x=k) for p, k in zip(acc("clearance_corrected"), ks)]},
            {"name": "Predicted by the (k-1)/k bias", "points": [{"x": k, "y": r2(cal[f"agentqa/k{k}"]["predicted_clearance"])} for k in ks]}],
    refLines=[{"axis": "y", "value": 0.975, "label": "claimed 97.5%"}], note="AgentQA with a simulated model; bands are 95% CIs."))
charts.append(dict(id="core-delta-ratio", method="Core", type="line", source="results/core-qa/core_calibration.json",
    title="Bootstrap noise band vs the band from repeated evaluations",
    takeaway="One evaluation's bootstrap gives a band about 0.70x / 0.81x / 0.90x the width of the one measured by re-running the artifact (k = 2 / 3 / 5), "
             "tracking the predicted sqrt((k-1)/k) on both domains.",
    x={"label": "Trials per task (k)", "kind": "linear"}, y={"label": "delta_bootstrap / delta_repeat", "min": 0.6, "max": 1.05, "format": ".2f"},
    series=[{"name": "AgentQA", "points": [dict(p, x=k) for p, k in zip(acc("delta_ratio"), ks)]},
            {"name": "Bernoulli toy", "points": [dict(p, x=k) for p, k in zip(acc("delta_ratio", "bernoulli"), ks)]},
            {"name": "sqrt((k-1)/k)", "points": [{"x": k, "y": r2(((k - 1) / k) ** 0.5)} for k in ks]}],
    refLines=[{"axis": "y", "value": 1.0, "label": "unbiased"}], note="simulated domain: shows the mechanism, not a benchmark"))

# ---- Core: package size
def kinds(path):
    src = path.read_text(errors="replace"); lines = src.splitlines()
    blank = sum(1 for l in lines if not l.strip()); com, doc = set(), set()
    for t in tokenize.generate_tokens(io.StringIO(src).readline):
        if t.type == tokenize.COMMENT and t.line.strip().startswith("#"): com.add(t.start[0])
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.body:
            b = n.body[0]
            if isinstance(b, ast.Expr) and isinstance(getattr(b, "value", None), ast.Constant) and isinstance(b.value.value, str):
                doc.update(range(b.lineno, b.end_lineno + 1))
    d = len(com | doc); return len(lines) - blank - d, d, blank
files = subprocess.run(["git", "-C", str(R), "ls-files", "rsi"], capture_output=True, text=True).stdout.split()
agg = {}
for f in files:
    if not f.endswith(".py"): continue
    p = f.split("/"); k = p[1] if len(p) > 2 else "top level"
    a = agg.setdefault(k, [0, 0, 0])
    for i, v in enumerate(kinds(R / f)): a[i] += v
order = sorted(agg, key=lambda k: -sum(agg[k]))
tot = [sum(a[i] for a in agg.values()) for i in range(3)]
charts.append(dict(id="core-package-size", method="Core", type="stacked", source="repo:rsi",
    title="Where the package's lines go",
    takeaway=f"{sum(tot):,} Python lines, {tot[0] / sum(tot):.0%} of them code. The test problems in domains/ are the largest part ({sum(agg['domains']):,}); "
             f"the shared core is only {sum(agg['core']):,}, each method builds its own loop on it.",
    x={"label": "Subpackage of rsi/", "kind": "category"}, y={"label": "Lines", "min": 0, "format": ".0f"},
    series=[{"name": n, "points": [{"x": k, "y": agg[k][i]} for k in order]} for i, n in enumerate(["Code", "Docstrings and comments", "Blank"])]))

# ---- Live demo
runs = {r["run"]: r for r in json.load(open(D / "demo/data.json"))}
def it_chart(run, cid, title, takeaway, lower=False):
    r = runs[run]; rows = r["rows"]
    sealed = list(rows[0]["sealed"])
    nm = {"holdout": "Held-out (sealed)", "ood": "OOD (sealed)", "test_iid": "Hidden test (sealed)", "test_shift": "Shifted test (sealed)"}
    s = [{"name": "Practice (in loop)" if not lower else "Validation (in loop)", "points": [{"x": w["iter"], "y": r2(w["S"])} for w in rows]}]
    s += [{"name": nm[k], "points": [{"x": w["iter"], "y": r2(w["sealed"][k])} for w in rows]} for k in sealed]
    charts.append(dict(id=cid, method="Live demo", type="line", source="demo/data.json", title=title, takeaway=takeaway,
        x={"label": "Iteration", "kind": "linear"},
        y=({"label": "val_bpb (lower is better)", "lowerIsBetter": True, "format": ".2f"} if lower else {"label": "Accuracy (1 trial per task)", "min": 0, "max": 1, "format": ".2f"}),
        series=s, note="Claude Haiku as agent and proposer. Sealed splits come from a write-only shadow monitor the loop never sees."))
it_chart("rrsi", "live-rrsi-iterations", "RRSI on AgentQA: practice and sealed scores per round",
         "Two kept rounds lift practice from 0.17 to 1.00 while sealed held-out rises 0.50 to 0.88 and OOD 0.50 to 0.75; both round-3 proposals failed the cost rule and were discarded.")
it_chart("metaharness_hard", "live-mh-hard-iterations", "Meta-Harness on hard AgentQA: the router appears at iteration 3",
         "Iteration 3's hybrid-task-router combined two earlier candidates and reached 1.0 on practice and held-out in the loop (OOD 0.67); later candidates did not beat it.")
it_chart("metaharness", "live-mh-iterations", "Meta-Harness on standard AgentQA: one keep, then a plateau",
         "Smart answer extraction lifted practice 0.33 to 0.50 and held-out 0.50 to 0.63 at iteration 1; every later proposal scored lower and was kept only as an alternative.")
it_chart("autoresearch", "live-ar-iterations", "autoresearch on a tiny language model: kept edits and hidden tests",
         "Raising the learning rate twice and widening the hidden layer cut validation bpb 2.956 to 2.637; hidden and shifted tests fell with it. Six later edits lost and were reset.", lower=True)
gains = []
for run, lab in (("rrsi", "RRSI, standard"), ("metaharness", "Meta-Harness, standard"), ("metaharness_hard", "Meta-Harness, hard"), ("rrsi_hard", "RRSI, hard")):
    t = runs[run]["transfer"]
    for sp, sl in (("evolve", "practice"), ("holdout", "held-out"), ("ood", "OOD")):
        v = t[sp]["final"]["vs"]; gains.append({"x": f"{lab}: {sl}", "y": r2(v["mean_diff"]), "lo": r2(v["lo"]), "hi": r2(v["hi"])})
charts.append(dict(id="live-gains-forest", method="Live demo", type="interval", source="demo/data.json",
    title="Gain over the seed after each live run, with 95% intervals",
    takeaway="Re-scored with fresh trials, RRSI's standard-suite gains on practice (+0.67) and held-out (+0.44) exclude zero, as do Meta-Harness's (+0.29, +0.44). "
             "The hard-suite Meta-Harness gains are large (+0.31 to +0.33) but their intervals reach zero; hard RRSI kept the seed.",
    x={"label": "Run and split", "kind": "category"}, y={"label": "Accuracy gain (final - seed)", "format": "+.2f"},
    series=[{"name": "Gain", "points": gains}], refLines=[{"axis": "y", "value": 0, "label": "no change"}],
    note="Paired bootstrap over tasks; 6-12 tasks per split, so intervals are wide."))
st = {}
for run, lab in (("rrsi", "RRSI, standard"), ("rrsi_hard", "RRSI, hard"), ("metaharness", "Meta-Harness, standard"), ("metaharness_hard", "Meta-Harness, hard"), ("autoresearch", "autoresearch")):
    c = {"Kept": 0, "Kept as alternative": 0, "Rejected": 0}
    for w in runs[run]["rows"]:
        for cand in w.get("candidates", []):
            s = cand.get("state"); c["Kept" if s == "incumbent" else "Kept as alternative" if s in ("frontier", "population") else "Rejected"] += 1
    st[lab] = c
charts.append(dict(id="live-candidate-fates", method="Live demo", type="stacked", source="demo/data.json",
    title="What happened to every candidate the model proposed",
    takeaway="RRSI and autoresearch reject most proposals (cost rule, noise floor, or no gain); Meta-Harness has no keep rule, so every candidate stays in its population or frontier.",
    x={"label": "Run", "kind": "category"}, y={"label": "Candidates", "min": 0, "format": ".0f"},
    series=[{"name": k, "points": [{"x": lab, "y": st[lab][k]} for lab in st]} for k in ("Kept", "Kept as alternative", "Rejected")]))
reps = json.load(open(D / "demo/replicates.json"))
charts.append(dict(id="live-mh-replicates", method="Live demo", type="interval", source="demo/replicates.json",
    title="Meta-Harness held-out gain across four live replicates",
    takeaway="All four standard-suite Meta-Harness runs improved held-out accuracy, by " + ", ".join(f"{r['scores']['holdout']['vs']['mean_diff']:+.2f}" for r in reps) + "; every interval excludes zero.",
    x={"label": "Replicate", "kind": "category"}, y={"label": "Held-out gain (final - seed)", "format": "+.2f"},
    series=[{"name": "Held-out gain", "points": [{"x": f"{r['label']} ({r['note']})", "y": r2(r["scores"]["holdout"]["vs"]["mean_diff"]), "lo": r2(r["scores"]["holdout"]["vs"]["lo"]), "hi": r2(r["scores"]["holdout"]["vs"]["hi"])} for r in reps]}],
    refLines=[{"axis": "y", "value": 0, "label": "no change"}]))
spend = [(lab, runs[run]) for run, lab in (("rrsi", "RRSI, standard"), ("rrsi_hard", "RRSI, hard"), ("metaharness", "Meta-Harness, standard"), ("metaharness_hard", "Meta-Harness, hard"), ("autoresearch", "autoresearch"))]
charts.append(dict(id="live-spend", method="Live demo", type="stacked", source="demo/data.json",
    title="Dollars spent per live run",
    takeaway="Each improvement run cost under $9 with Claude Haiku, including attempts cut short by usage caps; the fresh re-scoring behind the result tables is shown separately.",
    x={"label": "Run", "kind": "category"}, y={"label": "US dollars", "min": 0, "format": "$.2f"},
    series=[{"name": "Improvement run", "points": [{"x": lab, "y": r2(r["usd_full"], 2)} for lab, r in spend]},
            {"name": "Fresh re-score", "points": [{"x": lab, "y": r2(r.get("usd_rescore", 0), 2)} for lab, r in spend]}]))

# ---- Verification
txt = (R / "docs/reports/claims-summary.md").read_text()
rows = []
for line in txt.splitlines():
    m = re.match(r"\| \[([^\]]+)\]\([^)]*\) \| (\d+) \| (\d+)[^|]*\| (\d+)[^|]*\| (\d+)[^|]*\| (\d+)[^|]*\| (\d+)", line)
    if m: rows.append((m.group(1), *map(int, m.groups()[1:])))
labs = [r[0] for r in rows]
charts.append(dict(id="ver-claims-audit", method="Verification", type="stacked", source="repo:docs/reports/claims-summary.md",
    title="475 paper claims, each tested against the code and experiments",
    takeaway=f"{sum(r[2] for r in rows)} reproduce and {sum(r[3] for r in rows)} partly; {sum(r[5] for r in rows)} need frontier models, GPUs or the original benchmarks. "
             f"Only {sum(r[4] for r in rows)} do not reproduce and {sum(r[6] for r in rows)} are contradicted, mostly by the paper's own data or code.",
    x={"label": "Method", "kind": "category"}, y={"label": "Paper claims", "min": 0, "format": ".0f"},
    series=[{"name": "Reproduced", "points": [{"x": r[0], "y": r[2]} for r in rows]},
            {"name": "Partial", "points": [{"x": r[0], "y": r[3]} for r in rows]},
            {"name": "Not testable here", "points": [{"x": r[0], "y": r[5]} for r in rows]},
            {"name": "Not reproduced or contradicted", "points": [{"x": r[0], "y": r[4] + r[6]} for r in rows]}]))
vt = (D / "validation/README.md").read_text(); vrows = []
for line in vt.splitlines():
    m = re.match(r"\| ([A-Za-z][^|]*?) \| (\d+) \| (\d+) \| (\d+) \| (\d+) \| (\d+) \| \$", line)
    if m: vrows.append((m.group(1), *map(int, m.groups()[1:])))
charts.append(dict(id="ver-step-audit", method="Verification", type="stacked", source="validation/README.md",
    title="Every step of every validation run, re-derived by an auditor",
    takeaway=f"{sum(r[2] for r in vrows):,} of {sum(r[1] for r in vrows):,} steps verified correct. The {sum(r[4] for r in vrows)} wrong steps came from defects such as a mock bug or a parser fence; no gate arithmetic was miscomputed.",
    x={"label": "Method", "kind": "category"}, y={"label": "Loop steps", "min": 0, "format": ".0f"},
    series=[{"name": n, "points": [{"x": r[0], "y": r[i]} for r in vrows]} for i, n in ((2, "Correct"), (3, "Questionable"), (4, "Wrong"), (5, "Unverifiable"))]))
led = {}
for m, lab in (("rrsi", "RRSI"), ("metaharness", "Meta-Harness"), ("solpi", "SoL-Pi"), ("gepa", "GEPA"), ("autoresearch", "autoresearch"), ("dream", "Dream-RSI"), ("evomap", "EvoMap"), ("general", "SKILL.md")):
    rs = [json.loads(l) for l in (R / f"docs/skill-claims/ledger/{m}.jsonl").read_text().splitlines() if l.strip()]
    led[lab] = rs
kk = (("paper", "Quoted from the paper"), ("code", "Quoted from our code"), ("repo-doc", "Quoted from our docs"))
charts.append(dict(id="ver-skill-ledger", method="Verification", type="stacked", source="repo:docs/skill-claims/ledger",
    title="2,159 skill claims, each backed by a verbatim quote",
    takeaway="Every factual claim in the Claude Code skill is paired with a quote a script found in its source: "
             f"{sum(r['kind'] == 'paper' for v in led.values() for r in v)} from the papers, {sum(r['kind'] == 'code' for v in led.values() for r in v)} from the code.",
    x={"label": "Skill file", "kind": "category"}, y={"label": "Claims", "min": 0, "format": ".0f"},
    series=[{"name": n, "points": [{"x": lab, "y": sum(r["kind"] == k for r in v)} for lab, v in led.items()]} for k, n in kk] +
           [{"name": "Silence in the paper, or a skill cross-reference", "points": [{"x": lab, "y": sum(r["kind"] in ("absent", "skill-ref") for r in v)} for lab, v in led.items()]}]))
charts.append(dict(id="ver-skill-corrections", method="Verification", type="bar", source="repo:docs/skill-claims/ledger",
    title="Skill claims corrected to match their source",
    takeaway=f"{sum(r['verdict'] == 'fixed' for v in led.values() for r in v)} of 2,159 claims were wrong or imprecise and were fixed: 12 each in Meta-Harness and EvoMap, 11 in SKILL.md's shared sections (spend caps, noise band, sealed splits).",
    x={"label": "Skill file", "kind": "category"}, y={"label": "Corrected claims", "min": 0, "format": ".0f"},
    series=[{"name": "Corrected", "points": [{"x": lab, "y": sum(r["verdict"] == "fixed" for r in v)} for lab, v in led.items()]}]))
(V / "specs/core-live-verification.json").write_text(json.dumps({"charts": charts}, indent=1, ensure_ascii=False))
print(len(charts), "charts")
