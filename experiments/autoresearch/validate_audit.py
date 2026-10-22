"""Stage-B independent step audit of the autoresearch validation runs, re-derived from the raw trace events.

Usage: python experiments/autoresearch/validate_audit.py [run ...]   # default: all runs
"""
import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VAL = ROOT / "validation" / "autoresearch"
TIE = 1e-9
DEBRIS = re.compile(r"^(```[a-zA-Z]*|Co-Authored-By:.*|Claude-Session:.*|Signed-off-by:.*)$")
FALLBACK_DELTA = 0.034542  # offline-run band (same machine and budget) if the run is uncalibrated
SEALED = re.compile(r"test_iid|test_shift|load_split|RESULT_FILE|DATA_DIR|_bpb_locked|audit")
ALIAS = {"OPTIMIZER": ("SGD", "sgd", "adam", "Adam"), "WARMUP_RATIO": ("warmup",),
         "LR": ("learning rate",), "WEIGHT_DECAY": ("weight_decay", "weight decay", "L2")}
GROUNDED = "proposal grounded in history (no re-proposal of a falsified idea)"


def _consts(src: str) -> dict:
    return {m.group(1): m.group(2) for line in src.splitlines()
            if (m := re.match(r"^([A-Z][A-Z0-9_]+)\s*=\s*(.+?)\s*(#.*)?$", line))}


def _diff_lines(diff: str) -> tuple[list[str], list[str]]:
    add = [line[1:] for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++")]
    rem = [line[1:] for line in diff.splitlines() if line.startswith("-") and not line.startswith("---")]
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


def claim_check(p: dict) -> tuple[str, str]:
    """Does the proposal's claimed change match its diff (changed constants and their new values)?"""
    ch = _changed_consts(p["diff"] or "")
    add, rem = _diff_lines(p["diff"] or "")
    claim = p.get("change") or ""
    named = [k for k in ch if k in claim]
    unclaimed = [k for k in ch if k not in claim and not any(a in claim for a in ALIAS.get(k, ()))]
    other = [line for line in add + rem if not re.match(r"^[A-Z][A-Z0-9_]+\s*=", line)]
    vals_ok = True
    for k in ch:  # prose claims: the new value must appear in the claim
        nv = ch[k][1]
        if k in unclaimed or nv is None:
            continue
        if k not in named and not re.search(r"(?<![\d.])" + re.escape(str(nv).strip("'\"")) + r"(?![\d])",
                                            claim) and not isinstance(_num(nv), str):
            vals_ok = False
    for k in named:  # claimed "A -> B": B must be the new value
        new = ch[k][1]
        mm = re.search(re.escape(k) + r"\s*[^0-9A-Za-z'\"(]*\s*([^\s]+)\s*(?:->|→)\s*([^\s,)]+)", claim)
        if mm and new is not None and _num(mm.group(2).strip(",")) != _num(new):
            vals_ok = False
    sealed = SEALED.findall("\n".join(add))
    structural = bool(other and not ch)
    debris = [line for line in add if DEBRIS.match(line.strip())]
    questionable = not structural and not (vals_ok and not unclaimed) or bool(debris)
    ev_txt = (f"claim '{claim[:90]}'; changed consts {ch}; unclaimed consts {unclaimed}; "
              f"non-constant lines +{len([x for x in add if x in other])}/-{len([x for x in rem if x in other])}")
    if other and ch:
        ev_txt += f"; extra lines: {[x[:60] for x in other][:4]}"
    if debris:
        ev_txt += f"; UNCLAIMED reply debris added: {debris}"
    if len(named) > 1:
        ev_txt += f"; {len(named)} knobs in one experiment"
    if sealed:
        ev_txt += f"; SEALED tokens in added code: {sealed}"
    if structural and not debris:
        ev_txt += " [structural edit: checked by hand]"
    return ("questionable" if questionable else "correct"), ev_txt


def audit_run(out: Path) -> dict:
    ev = [json.loads(line) for line in (out / "trace.jsonl").read_text().splitlines() if line.strip()]
    tsv = (out / "results.tsv").read_text().splitlines()
    rows = [line.split("\t") for line in tsv[1:]]
    by: dict = {}
    for e in ev:
        by.setdefault(e["round"], []).append(e)
    steps: list[dict] = []
    issues: list[str] = []

    def step(rnd, name, verdict, evidence):
        steps.append({"round": rnd, "step": name, "verdict": verdict, "evidence": evidence})
        if verdict in ("wrong", "questionable"):
            issues.append(f"r{rnd} {name}: {evidence}")

    def data(events, kind):
        return [e["data"] for e in events if e["kind"] == kind]

    start = next(e["data"] for e in ev if e["kind"] == "run_start")
    cfg = start["config"]
    b_evals = data(by[0], "eval")
    base = data(by[0], "baseline")[0]
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
    noise = data(by[0], "noise")
    noise_delta = noise[0]["delta"] if noise else None
    if noise:
        v = noise[0]["values"]
        m = sum(v) / len(v)
        want = 2 * math.sqrt(2) * math.sqrt(sum((x - m) ** 2 for x in v) / (len(v) - 1))
        step(0, "noise band delta = z*sqrt(2)*sd", "correct" if abs(want - noise[0]["delta"]) < 1e-12 else "wrong",
             f"values {[round(x, 4) for x in v]} -> delta {want:.5f} (trace {noise[0]['delta']:.5f})")
    mons0 = data(by[0], "monitor")
    step(0, "shadow audit of baseline (write-only)", "correct" if len(mons0) == 1 else "wrong",
         json.dumps(mons0[0]["sealed"]) if mons0 else "none")

    inc_commit, inc_vals, row_i = base["commit"], [base["summary"]["S"]], 1
    mon_values = [v["S"] for m in mons0 for v in m["sealed"].values()]
    usage_sum = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    for r in sorted(k for k in by if k):
        E = by[r]
        rs = data(E, "round_start")[0]
        ok = rs["incumbent"]["commit"] == inc_commit and rs["incumbent"]["values"] == inc_vals
        step(r, "round_start state (incumbent carried from previous decision)", "correct" if ok else "wrong",
             f"incumbent {rs['incumbent']['commit']} {rs['incumbent']['values']}; expected {inc_commit} {inc_vals}")
        # what the proposer saw
        an = next(e["data"]["text"] for e in E if e["kind"] == "analysis")
        seen_tsv = an.split("--- results.tsv (tail) ---\n", 1)[1].split("\n--- git log", 1)[0].strip().splitlines()
        want_tsv = tsv[: 1 + row_i][-(cfg["config"]["history_rows"] + 1):]
        hist_ok = seen_tsv[1:] == want_tsv[1:] if len(want_tsv) > 1 else True
        leak = [x for x in mon_values if f"{x:.4f}" in an]
        step(r, "proposer context (results.tsv history, kept log, no sealed numbers)",
             "correct" if hist_ok and not leak else "wrong",
             f"{len(seen_tsv) - 1} tsv rows shown (expected {len(want_tsv) - 1}); sealed numbers in context: {leak}")
        props, evals, crit, gates = data(E, "proposal"), data(E, "eval"), data(E, "critic"), data(E, "gate")
        dec = data(E, "decision")[0]
        mons = data(E, "monitor")
        for p in props:
            for k in usage_sum:
                usage_sum[k] += (p.get("usage") or {}).get(k, 0) or 0
            pl = [x for x in mon_values if f"{x:.4f}" in (p.get("prompt") or "")]
            if pl:
                step(r, f"prompt of {p['candidate']} free of sealed numbers", "wrong", f"found {pl}")
        for p in props:  # the first proposal is the idea, later ones are fixes
            if p["stage"] == "propose":
                step(r, f"claimed change == actual diff ({p['candidate']} attempt {p['attempt']})", *claim_check(p))
            else:
                add, rem = _diff_lines(p["diff"] or "")
                fdeb = [line for line in add if DEBRIS.match(line.strip())]
                step(r, f"fix proposal {p['candidate']} ({p['stage']})",
                     "correct" if (add or rem) and not fdeb else "questionable",
                     f"diff +{len(add)}/-{len(rem)} lines: {[x[:50] for x in (rem + add)][:4]}"
                     + (f"; fix ADDED reply debris {fdeb}" if fdeb else ""))
        grounding(r, props, rows, row_i, steps, step)
        for c in crit:
            step(r, f"scope guard ({c['stage']})", "correct", f"accept={c['accept']} objections={c['objections']}")
        run_evals = [x for x in evals if x["role"] in ("experiment",) or x["role"].startswith("fix")]
        for x in evals:
            rr = x["run"]
            txt = (f"{x['candidate']} ({x['role']}): metric {rr['metric']}, wall {rr['wall_s']} s, "
                   f"{'crash' if rr['crashed'] else 'ok'}"
                   + (f" [{rr['crash_reason']}]" if rr["crashed"] else "") + (" KILLED" if rr["killed"] else ""))
            v = "correct"
            if rr["killed"] and rr["wall_s"] < start["config"]["budget"]["kill_after"] - 0.5:
                v = "wrong"
            if not rr["crashed"] and (rr.get("record") or {}).get("budget_overrun"):
                v = "questionable"
            step(r, "training run (fixed budget, locked record)", v, txt)
        status = dec["status"]
        if run_evals and status in ("keep", "discard") and not run_evals[-1]["run"]["crashed"]:
            cand = run_evals[-1]["run"]["metric"]
            ref = sum(inc_vals) / len(inc_vals)
            gain = ref - cand
            want = gain > TIE
            g = gates[0] if gates else None
            v = "correct" if (g is not None and g["accept"] == want and (status == "keep") == want) else "wrong"
            if status == "keep":
                band = noise_delta or FALLBACK_DELTA
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
            step(r, "rejected before running", v,
                 f"runs {len(run_evals)}; objections {crit[-1]['objections'] if crit else None}")
        if status in ("keep", "discard", "crash", "rejected"):
            row = rows[row_i]
            row_i += 1
            if status in ("crash", "rejected"):
                want_metric, want_mem = "0.000000", "0.0"
            else:
                want_metric = f"{run_evals[-1]['run']['metric']:.6f}"
                want_mem = f"{max(x['run']['memory_gb'] for x in run_evals[-1:]):.1f}"
            ok = (len(row) == 5 and row[3] == ("discard" if status == "rejected" else status)
                  and row[1] == want_metric and row[2] == want_mem and "\t".join(row) == dec["results_tsv_row"])
            step(r, "results.tsv row (5 columns, 6 dp)", "correct" if ok else "wrong", "\t".join(row))
        moved = dec["incumbent_before"] != dec["incumbent_after"]
        step(r, "branch: keep advances, else reset", "correct" if moved == (status == "keep") else "wrong",
             f"{dec['incumbent_before']} -> {dec['incumbent_after']} ({status})")
        if status == "keep":
            inc_commit = dec["incumbent_after"]
            inc_vals = [run_evals[-1]["run"]["metric"]]
        st = data(E, "state")[-1]
        step(r, "state after decision", "correct" if st["incumbent"]["commit"] == inc_commit else "wrong",
             f"incumbent {st['incumbent']['commit']}, best {st['best']:.6f}, keeps {st['n_keeps']}")
        if status == "keep":
            ok = len(mons) == 1 and mons[0]["artifact"] == props[-1]["artifact"]
            step(r, "shadow audit of the new incumbent (1x, same artifact)", "correct" if ok else "wrong",
                 json.dumps(mons[0]["sealed"]) if mons else "missing")
            mon_values += [v["S"] for m in mons for v in m["sealed"].values()]
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


def grounding(r: int, props: list[dict], rows: list[list[str]], row_i: int, steps: list[dict], step) -> None:
    """Does the round's idea repeat one already falsified in the history the proposer saw?"""
    pp = [p for p in props if p["stage"] == "propose" and not p["error"]]
    if not pp:
        return
    claim = (pp[-1].get("change") or "").strip()
    prior = [x for x in rows[:row_i] if x[4].strip() == claim and x[3] in ("discard", "crash")]
    mrev = re.match(r"^(\w+) (.+?) -> (.+)$", claim)
    rev = None
    if mrev:
        back = f"{mrev.group(1)} {mrev.group(3)} -> {mrev.group(2)}"
        rev = next((x for x in rows[:row_i] if x[4].strip() == back and x[3] == "keep"), None)
    if prior and not rev and any(x[3] == "keep" for x in rows[rows.index(prior[-1]) + 1:row_i]):
        # retried on a different incumbent (the kept chain moved since): a legitimate re-test
        step(r, GROUNDED, "correct", f"re-test of '{claim}' (discarded as {prior[-1][0]}) on a NEW incumbent")
        prior = []
    if prior or rev:
        why = (f"same description already {prior[-1][3]} as {prior[-1][0]}" if prior else "") + \
              (f"; reverts kept change {rev[0]} '{rev[4]}'" if rev else "")
        step(r, GROUNDED, "questionable", why.strip("; ") + (" (on a different incumbent)" if prior else ""))
    elif not any(x["step"].startswith("proposal grounded") and x["round"] == r for x in steps):
        step(r, GROUNDED, "correct", f"'{claim[:70]}' not previously discarded/crashed; "
                                     f"hypothesis: {(pp[-1].get('hypothesis') or '')[:120]}")


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
