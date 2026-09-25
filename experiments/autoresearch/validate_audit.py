"""Stage-B independent step audit of the autoresearch validation runs.

Re-derives every step of a run from the RAW events only (it never reads the
loop's own gate arithmetic as the reference):

* incumbent chain: round_start.incumbent == previous decision.incumbent_after;
* gate: reference = incumbent values from round_start, candidate = last
  experiment/fix eval metric; strict keep <=> (ref - cand) > 1e-9 (min direction);
* results.tsv row: exact 5-column format, 6 dp, crash / rejected -> 0.000000 / 0.0;
* proposal: the claimed change vs the actual diff (changed constants), unclaimed
  lines, sealed-split / task-literal leakage patterns in the diff or reply;
* the proposer's view: analysis text == results.tsv prefix at that time, no
  sealed-split numbers (monitor values) in any prompt;
* noise band: delta = 2 * sqrt(2) * sd(values, ddof=1);
* monitor: exactly one per new incumbent, same artifact as the kept candidate;
* usage: sum of proposal usage == run_end usage == cache meter.

    python experiments/autoresearch/validate_audit.py [run ...]   # default: all runs
"""
from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VAL = ROOT / "validation" / "autoresearch"
TIE = 1e-9
DEBRIS = re.compile(r"^(```[a-zA-Z]*|Co-Authored-By:.*|Claude-Session:.*|Signed-off-by:.*)$")
NOISE_DELTA = {"run": None, "fallback": 0.034542}   # offline-run band (same machine and budget) if uncalibrated
SEALED = re.compile(r"test_iid|test_shift|load_split|RESULT_FILE|DATA_DIR|_bpb_locked|audit")


def _consts(src: str) -> dict:
    out = {}
    for line in src.splitlines():
        m = re.match(r"^([A-Z][A-Z0-9_]+)\s*=\s*(.+?)\s*(#.*)?$", line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def _diff_lines(diff: str) -> tuple[list[str], list[str]]:
    add = [l[1:] for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++")]
    rem = [l[1:] for l in diff.splitlines() if l.startswith("-") and not l.startswith("---")]
    return add, rem


def _changed_consts(diff: str) -> dict:
    add, rem = _diff_lines(diff)
    a, r = _consts("\n".join(add)), _consts("\n".join(rem))
    return {k: (r.get(k), a.get(k)) for k in set(a) | set(r) if a.get(k) != r.get(k)}


def _num(s):
    try:
        return float(eval(s, {}, {}))  # noqa: S307 - constants from our own files
    except Exception:  # noqa: BLE001
        return s


def audit_run(out: Path) -> dict:
    ev = [json.loads(l) for l in (out / "trace.jsonl").read_text().splitlines() if l.strip()]
    tsv = (out / "results.tsv").read_text().splitlines()
    rows = [l.split("\t") for l in tsv[1:]]
    by: dict = {}
    for e in ev:
        by.setdefault(e["round"], []).append(e)
    steps: list[dict] = []
    issues: list[str] = []

    def step(rnd, name, verdict, evidence):
        steps.append({"round": rnd, "step": name, "verdict": verdict, "evidence": evidence})
        if verdict in ("wrong", "questionable"):
            issues.append(f"r{rnd} {name}: {evidence}")

    start = next(e["data"] for e in ev if e["kind"] == "run_start")
    cfg = start["config"]
    # ---- baseline
    b_evals = [e["data"] for e in by[0] if e["kind"] == "eval"]
    base = next(e["data"] for e in by[0] if e["kind"] == "baseline")
    ok = (len(b_evals) == 1 and b_evals[0]["run"]["metric"] == base["summary"]["S"]
          and rows[0][3] == "keep" and rows[0][4] == "baseline"
          and rows[0][1] == f"{base['summary']['S']:.6f}")
    step(0, "baseline first (unmodified seed, status keep, row 1)", "correct" if ok else "wrong",
         f"seed {start['seed']}, val_bpb {base['summary']['S']:.6f}, row {rows[0]}")
    rec = b_evals[0]["run"].get("record", {})
    step(0, "fixed budget, warm-up excluded (locked clock)",
         "correct" if rec.get("budget_exhausted") and not rec.get("budget_overrun") else "questionable",
         f"training_seconds {rec.get('training_seconds', 0):.3f} for budget {rec.get('budget')}; "
         f"wall {b_evals[0]['run']['wall_s']} s; steps {rec.get('num_steps')}")
    noise = [e["data"] for e in by[0] if e["kind"] == "noise"]
    NOISE_DELTA["run"] = noise[0]["delta"] if noise else None
    if noise:
        v = noise[0]["values"]
        m = sum(v) / len(v)
        sd = math.sqrt(sum((x - m) ** 2 for x in v) / (len(v) - 1))
        want = 2 * math.sqrt(2) * sd
        step(0, "noise band delta = z*sqrt(2)*sd", "correct" if abs(want - noise[0]["delta"]) < 1e-12 else "wrong",
             f"values {[round(x, 4) for x in v]} -> delta {want:.5f} (trace {noise[0]['delta']:.5f})")
    mons0 = [e["data"] for e in by[0] if e["kind"] == "monitor"]
    step(0, "shadow audit of baseline (write-only)", "correct" if len(mons0) == 1 else "wrong",
         json.dumps(mons0[0]["sealed"]) if mons0 else "none")

    inc_commit = base["commit"]
    inc_vals = [base["summary"]["S"]]
    row_i = 1
    all_prompts = []
    mon_values = []
    for m in mons0:
        mon_values += [v["S"] for v in m["sealed"].values()]
    usage_sum = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    rounds = sorted(k for k in by if k)
    for r in rounds:
        E = by[r]
        rs = next(e["data"] for e in E if e["kind"] == "round_start")
        # --- state carried correctly
        ok = rs["incumbent"]["commit"] == inc_commit and rs["incumbent"]["values"] == inc_vals
        step(r, "round_start state (incumbent carried from previous decision)", "correct" if ok else "wrong",
             f"incumbent {rs['incumbent']['commit']} {rs['incumbent']['values']}; expected {inc_commit} {inc_vals}")
        # --- what the proposer saw
        an = next(e["data"]["text"] for e in E if e["kind"] == "analysis")
        seen_tsv = an.split("--- results.tsv (tail) ---\n", 1)[1].split("\n--- git log", 1)[0].strip().splitlines()
        want_tsv = tsv[: 1 + row_i][-(cfg["config"]["history_rows"] + 1):]
        hist_ok = seen_tsv[1:] == want_tsv[1:] if len(want_tsv) > 1 else True
        leak = [x for x in mon_values if f"{x:.4f}" in an]
        step(r, "proposer context (results.tsv history, kept log, no sealed numbers)",
             "correct" if hist_ok and not leak else "wrong",
             f"{len(seen_tsv) - 1} tsv rows shown (expected {len(want_tsv) - 1}); sealed numbers in context: {leak}")
        props = [e["data"] for e in E if e["kind"] == "proposal"]
        evals = [e["data"] for e in E if e["kind"] == "eval"]
        crit = [e["data"] for e in E if e["kind"] == "critic"]
        gates = [e["data"] for e in E if e["kind"] == "gate"]
        dec = next(e["data"] for e in E if e["kind"] == "decision")
        mons = [e["data"] for e in E if e["kind"] == "monitor"]
        for p in props:
            for k in usage_sum:
                usage_sum[k] += (p.get("usage") or {}).get(k, 0) or 0
            all_prompts.append(p.get("prompt") or "")
            # sealed leakage into the prompt the proposer saw
            pl = [x for x in mon_values if f"{x:.4f}" in (p.get("prompt") or "")]
            if pl:
                step(r, f"prompt of {p['candidate']} free of sealed numbers", "wrong", f"found {pl}")
        # --- claimed change vs actual diff (first proposal = the idea; fixes separately)
        for p in props:
            ch = _changed_consts(p["diff"] or "")
            add, rem = _diff_lines(p["diff"] or "")
            claim = p.get("change") or ""
            if p["stage"] == "propose":
                alias = {"OPTIMIZER": ("SGD", "sgd", "adam", "Adam"), "WARMUP_RATIO": ("warmup",),
                         "LR": ("learning rate",), "WEIGHT_DECAY": ("weight_decay", "weight decay", "L2")}
                named = [k for k in ch if k in claim]
                unclaimed = [k for k in ch if k not in claim and not any(a in claim for a in alias.get(k, ()))]
                other = [l for l in add + rem if not re.match(r"^[A-Z][A-Z0-9_]+\s*=", l)]
                vals_ok = True
                for k in ch:                       # prose claims: the new value must appear in the claim
                    nv = ch[k][1]
                    if k in unclaimed or nv is None:
                        continue
                    if k not in named and not re.search(r"(?<![\d.])" + re.escape(str(nv).strip("'\"")) + r"(?![\d])",
                                                        claim) and not isinstance(_num(nv), str):
                        vals_ok = False
                for k in named:
                    new = ch[k][1]
                    # claimed "A -> B": B must be the new value
                    mm = re.search(re.escape(k) + r"\s*[^0-9A-Za-z'\"(]*\s*([^\s]+)\s*(?:->|→)\s*([^\s,)]+)", claim)
                    if mm and new is not None and _num(mm.group(2).strip(",")) != _num(new):
                        vals_ok = False
                sealed = SEALED.findall("\n".join(add))
                if other and not ch:
                    v = "unverifiable-structural"
                else:
                    v = "correct" if (vals_ok and not unclaimed) else "questionable"
                debris = [l for l in add if DEBRIS.match(l.strip())]
                if debris:
                    v = "questionable"
                ev_txt = (f"claim '{claim[:90]}'; changed consts {ch}; unclaimed consts {unclaimed}; "
                          f"non-constant lines +{len([l for l in add if l in other])}/-{len([l for l in rem if l in other])}")
                if other and ch:
                    ev_txt += f"; extra lines: {[l[:60] for l in other][:4]}"
                if debris:
                    ev_txt += f"; UNCLAIMED reply debris added: {debris}"
                if len(named) > 1:
                    ev_txt += f"; {len(named)} knobs in one experiment"
                if sealed:
                    ev_txt += f"; SEALED tokens in added code: {sealed}"
                step(r, f"claimed change == actual diff ({p['candidate']} attempt {p['attempt']})",
                     "questionable" if v == "questionable" else "correct",
                     ev_txt + (" [structural edit: checked by hand]" if v == "unverifiable-structural" else ""))
            else:
                fdeb = [l for l in add if DEBRIS.match(l.strip())]
                step(r, f"fix proposal {p['candidate']} ({p['stage']})",
                     "correct" if (add or rem) and not fdeb else "questionable",
                     f"diff +{len(add)}/-{len(rem)} lines: {[l[:50] for l in (rem + add)][:4]}"
                     + (f"; fix ADDED reply debris {fdeb}" if fdeb else ""))
        # --- grounding: does the idea repeat one already falsified in the history the proposer saw?
        pp = [p for p in props if p["stage"] == "propose" and not p["error"]]
        if pp:
            claim = (pp[-1].get("change") or "").strip()
            prior = [x for x in rows[:row_i] if x[4].strip() == claim and x[3] in ("discard", "crash")]
            mrev = re.match(r"^(\w+) (.+?) -> (.+)$", claim)
            rev = None
            if mrev:
                back = f"{mrev.group(1)} {mrev.group(3)} -> {mrev.group(2)}"
                rev = next((x for x in rows[:row_i] if x[4].strip() == back and x[3] == "keep"), None)
            if prior and not rev:
                # retried on a different incumbent (the kept chain moved since): a legitimate re-test
                moved = any(x[3] == "keep" for x in rows[rows.index(prior[-1]) + 1:row_i])
                if moved:
                    step(r, "proposal grounded in history (no re-proposal of a falsified idea)", "correct",
                         f"re-test of '{claim}' (discarded as {prior[-1][0]}) on a NEW incumbent")
                    prior = []
            if prior or rev:
                why = (f"same description already {prior[-1][3]} as {prior[-1][0]}" if prior else "") + \
                      (f"; reverts kept change {rev[0]} '{rev[4]}'" if rev else "")
                step(r, "proposal grounded in history (no re-proposal of a falsified idea)", "questionable",
                     why.strip("; ") + (" (on a different incumbent)" if prior else ""))
            elif not any(x["step"].startswith("proposal grounded") and x["round"] == r for x in steps):
                step(r, "proposal grounded in history (no re-proposal of a falsified idea)", "correct",
                     f"'{claim[:70]}' not previously discarded/crashed; hypothesis: {(pp[-1].get('hypothesis') or '')[:120]}")
        for c in crit:
            step(r, f"scope guard ({c['stage']})", "correct",
                 f"accept={c['accept']} objections={c['objections']}")
        # --- evals / crash handling
        run_evals = [x for x in evals if x["role"] in ("experiment",) or x["role"].startswith("fix")]
        for x in evals:
            rr = x["run"]
            tag = "crash" if rr["crashed"] else "ok"
            txt = (f"{x['candidate']} ({x['role']}): metric {rr['metric']}, wall {rr['wall_s']} s, {tag}"
                   + (f" [{rr['crash_reason']}]" if rr["crashed"] else "")
                   + (" KILLED" if rr["killed"] else ""))
            v = "correct"
            if rr["killed"] and rr["wall_s"] < start["config"]["budget"]["kill_after"] - 0.5:
                v = "wrong"
            if not rr["crashed"] and (rr.get("record") or {}).get("budget_overrun"):
                v = "questionable"
            step(r, "training run (fixed budget, locked record)", v, txt)
        status = dec["status"]
        # --- gate recomputation
        if run_evals and status in ("keep", "discard") and not run_evals[-1]["run"]["crashed"]:
            cand = run_evals[-1]["run"]["metric"]
            ref = sum(inc_vals) / len(inc_vals)
            gain = ref - cand
            want = gain > TIE
            g = gates[0] if gates else None
            v = "correct" if (g is not None and g["accept"] == want and (status == "keep") == want) else "wrong"
            if status == "keep":
                band = NOISE_DELTA.get("run") or NOISE_DELTA["fallback"]
                step(r, "kept change is a real improvement (gain vs noise band)",
                     "correct" if gain > band else "unverifiable",
                     f"gain {gain:+.4f} vs noise band {band:.4f}"
                     + ("" if gain > band else " -> single run cannot tell a real gain from luck"))
            step(r, "keep rule (strict, recomputed from raw evals)", v,
                 f"ref {ref:.6f} - cand {cand:.6f} = {gain:+.6f} -> {'keep' if want else 'discard'}; "
                 f"loop: gate {g['accept'] if g else None}, status {status}")
        elif status == "crash":
            last = run_evals[-1]["run"] if run_evals else None
            v = "correct" if (last is not None and last["crashed"] and not gates) else "wrong"
            step(r, "crash => no keep, reset", v, f"last run crashed={last and last['crashed']} "
                                                 f"({last and last['crash_reason']}); gates {len(gates)}")
        elif status == "rejected":
            v = "correct" if (not run_evals and crit and not crit[-1]["accept"]) else "wrong"
            step(r, "rejected before running", v, f"runs {len(run_evals)}; objections {crit[-1]['objections'] if crit else None}")
        # --- results.tsv row
        if status in ("keep", "discard", "crash", "rejected"):
            row = rows[row_i]
            row_i += 1
            want_status = "discard" if status == "rejected" else status
            if status in ("crash", "rejected"):
                want_metric, want_mem = "0.000000", "0.0"
            else:
                want_metric = f"{run_evals[-1]['run']['metric']:.6f}"
                want_mem = f"{max(x['run']['memory_gb'] for x in run_evals[-1:]):.1f}"
            ok = (len(row) == 5 and row[3] == want_status and row[1] == want_metric and row[2] == want_mem
                  and "\t".join(row) == dec["results_tsv_row"])
            step(r, "results.tsv row (5 columns, 6 dp)", "correct" if ok else "wrong", "\t".join(row))
        # --- branch update
        moved = dec["incumbent_before"] != dec["incumbent_after"]
        step(r, "branch: keep advances, else reset", "correct" if moved == (status == "keep") else "wrong",
             f"{dec['incumbent_before']} -> {dec['incumbent_after']} ({status})")
        if status == "keep":
            inc_commit = dec["incumbent_after"]
            inc_vals = [run_evals[-1]["run"]["metric"]]
        st = [e["data"] for e in E if e["kind"] == "state"][-1]
        step(r, "state after decision", "correct" if st["incumbent"]["commit"] == inc_commit else "wrong",
             f"incumbent {st['incumbent']['commit']}, best {st['best']:.6f}, keeps {st['n_keeps']}")
        # --- monitor
        if status == "keep":
            ok = len(mons) == 1 and mons[0]["artifact"] == props[-1]["artifact"]
            step(r, "shadow audit of the new incumbent (1x, same artifact)", "correct" if ok else "wrong",
                 json.dumps(mons[0]["sealed"]) if mons else "missing")
            for m in mons:
                mon_values += [v["S"] for v in m["sealed"].values()]
        elif mons:
            step(r, "no shadow audit on non-keep", "wrong", f"{len(mons)} monitor events")
    end = next((e["data"] for e in ev if e["kind"] == "run_end"), {})
    u = end.get("usage") or {}
    ok = (u.get("calls", 0) == usage_sum["calls"] and abs(u.get("cost_usd", 0) - usage_sum["cost_usd"]) < 1e-6
          and u.get("input_tokens", 0) == usage_sum["input_tokens"])
    step(None, "usage accounting (sum of proposals == run_end meter)", "correct" if ok else "wrong",
         f"proposals {usage_sum} vs run_end {u}")
    step(None, "results.tsv fully accounted", "correct" if row_i == len(rows) else "wrong",
         f"{row_i} rows derived vs {len(rows)} in file")
    counts: dict = {}
    for s in steps:
        counts[s["verdict"]] = counts.get(s["verdict"], 0) + 1
    return {"run": out.name, "steps": steps, "counts": counts, "issues": issues, "usage_from_proposals": usage_sum}


def main(names):
    res = {}
    for n in names:
        a = audit_run(VAL / n)
        res[n] = a
        (VAL / n / "audit_steps.json").write_text(json.dumps(a, indent=1, default=str))
        print(n, a["counts"])
        for i in a["issues"]:
            print("   ", i)
    return res


if __name__ == "__main__":
    main(sys.argv[1:] or ["tinylm_offline", "tinylm_offline_crashfix", "tinylm_live", "tinylm_live_b"])


def round_table(run: str) -> str:
    """Compact per-round markdown table from audit_steps.json (for AUDIT.md)."""
    a = json.loads((VAL / run / "audit_steps.json").read_text())
    ev = [json.loads(l) for l in (VAL / run / "trace.jsonl").read_text().splitlines()]
    tsv = [l.split("\t") for l in (VAL / run / "results.tsv").read_text().splitlines()[1:]]
    by: dict = {}
    for s in a["steps"]:
        by.setdefault(s["round"], []).append(s)
    lines = ["| r | idea (claimed) | runs (val_bpb) | recomputed gate | status / row | steps ok | flags |",
             "|---|---|---|---|---|---|---|"]
    for r in sorted(k for k in by if k):
        S = by[r]
        dec = next(e["data"] for e in ev if e["kind"] == "decision" and e["round"] == r)
        evals = [e["data"] for e in ev if e["kind"] == "eval" and e["round"] == r]
        runs = ", ".join(("crash" if x["run"]["crashed"] else f"{x['run']['metric']:.4f}")
                         + ("(fix)" if x["role"].startswith("fix") else "") for x in evals) or "not run"
        gate = next((s["evidence"].split(" -> ")[0].split(" = ")[-1] for s in S if s["step"].startswith("keep rule")),
                    "-")
        bad = [s for s in S if s["verdict"] != "correct"]
        flags = "; ".join(f"{s['verdict']}: {s['step'].split(' (')[0]}" for s in bad)
        lines.append(f"| {r} | {dec['description'][:60]} | {runs} | {gate} | {dec['status']} | "
                     f"{sum(s['verdict'] == 'correct' for s in S)}/{len(S)} | {flags} |")
    return "\n".join(lines)
