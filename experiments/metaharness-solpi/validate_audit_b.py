"""Stage-B independent audit of the Meta-Harness / SoL-Pi validation runs.

This does NOT reuse the stage-A audit (``validate_metaharness_solpi.audit_mh / audit_sp``). Every number is
re-derived from the raw records the loops wrote (trace.jsonl raw trial scores, the experience store's per-task
records and sources, the ledger, the firewall sink) and - for the offline SoL-Pi run - by re-evaluating the
frozen candidates with a fresh evaluator. One verdict per step:

    correct | questionable | wrong | unverifiable

Usage::

    python experiments/metaharness-solpi/validate_audit_b.py            # all runs present
    python experiments/metaharness-solpi/validate_audit_b.py <run> ...

Writes ``validation/metaharness-solpi/audit_b/<run>.json`` and prints a compact table.
"""
from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUT = ROOT / "validation" / "metaharness-solpi"
EPS = 1e-9


def load(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0


class Steps:
    def __init__(self):
        self.rows: list[dict] = []

    def add(self, rnd, step, verdict, evidence):
        assert verdict in ("correct", "questionable", "wrong", "unverifiable"), verdict
        self.rows.append({"round": rnd, "step": step, "verdict": verdict, "evidence": evidence})

    def counts(self):
        c = {"correct": 0, "questionable": 0, "wrong": 0, "unverifiable": 0}
        for r in self.rows:
            c[r["verdict"]] += 1
        return c


# ============================================================================ Meta-Harness
def _src(store: Path, name: str) -> dict[str, str]:
    d = store / "candidates" / name / "src"
    return {str(p.relative_to(d)): p.read_text() for p in sorted(d.rglob("*")) if p.is_file()} if d.exists() else {}


def _udiff(a: dict, b: dict) -> tuple[list[str], str]:
    import difflib
    changed, out = [], []
    for f in sorted(set(a) | set(b)):
        if a.get(f) != b.get(f):
            changed.append(f)
            out += difflib.unified_diff((a.get(f) or "").splitlines(), (b.get(f) or "").splitlines(), lineterm="")
    return changed, "\n".join(out)


def _added(diff: str) -> str:
    return "\n".join(l[1:] for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++"))


def _pareto(pts):
    return [p for p in pts if not any(o[1] >= p[1] and o[2] <= p[2] and (o[1] > p[1] or o[2] < p[2]) for o in pts)]


def _ctx_mean(cost: dict) -> float:
    nz = [float(v) for v in cost.values() if v]
    return sum(nz) / len(nz) if nz else 0.0


def _agentqa_tasks():
    from rsi.domains.agentqa import make_suite
    s = make_suite(n_evolve=10, n_holdout=6, n_ood_per_family=1, seed=0)
    return {sp: s.split(sp, allow_sealed=True) for sp in s.splits}


def _memo_tasks():
    from rsi.domains.memoclassify import make_domain
    d = make_domain(seed=0)
    return d, {sp: d.tasks.split(sp, allow_sealed=True) for sp in d.tasks.splits}


def audit_mh(run: str) -> dict:
    rd = OUT / run
    ev = load(rd / "trace.jsonl")
    store = rd / "store"
    led = {n["meta"].get("system"): n for n in load(rd / "ledger.jsonl") if n.get("meta")}
    cfg = ev[0]["data"]["config"]
    cost_metric = cfg["cost_metric"]
    st = Steps()
    meta = {n: json.loads((store / "candidates" / n / "meta.json").read_text())
            for n in sorted(p.name for p in (store / "candidates").iterdir())}
    is_live = "agentqa" in run
    tasks = _agentqa_tasks() if is_live else _memo_tasks()[1]
    sealed_text = []
    for sp in ("holdout", "ood", "test"):
        for t in tasks.get(sp, []):
            inp = t.input if isinstance(t.input, str) else json.dumps(t.input, default=str)
            if len(inp) >= 30:
                sealed_text.append((sp, t.id, inp[:60]))
    evolve_ids = {t.id for t in tasks["evolve"]}

    # ---- independent per-system score/cost from the store's per-task records (raw trials)
    def recompute(name):
        d = store / "candidates" / name / "eval" / "search" / "per_task"
        if not d.exists():
            return None
        per, cost = {}, {}
        for p in sorted(d.glob("*.json")):
            trs = json.loads(p.read_text())
            tid = trs[0]["task_id"]
            per[tid] = sum(t["score"] for t in trs) / max(cfg["trials"], len(trs))
            vals = [t["meta"].get(cost_metric) for t in trs if t["meta"].get(cost_metric) is not None]
            if cost_metric == "tokens" or not vals:
                vals = [float(t["tokens"]) for t in trs]
            cost[tid] = mean(vals)
        return {"per": per, "score": mean(per.values()), "cost": _ctx_mean(cost)}

    rec = {n: recompute(n) for n in meta}
    best_by_round = {}
    # ---- baselines
    for e in ev:
        if e["kind"] == "baseline":
            d = e["data"]
            n = d["candidate"]
            r = rec[n]
            tr_score = mean(mean(v) for v in d["trials"].values())
            ok = (abs(r["score"] - d["summary"]["S"]) < 1e-3 and abs(tr_score - r["score"]) < 1e-9
                  and abs(r["cost"] - d["context_cost"]) < 1e-6 and set(r["per"]) <= evolve_ids)
            st.add(0, f"baseline eval {n}", "correct" if ok else "wrong",
                   f"S={r['score']:.4f} from {len(r['per'])} raw per-task records (trace trials mean {tr_score:.4f}); "
                   f"context cost {r['cost']:.1f} (mean of non-zero per-unit {cost_metric}); units all in evolve")
    rounds = sorted({e["round"] for e in ev if e["kind"] == "round_start"})
    all_names_by_iter = {n: m.get("iteration", 0) for n, m in meta.items()}
    last_best = None
    for e in ev:
        if e["kind"] == "state" and e["round"] == 0:
            last_best = (e["data"]["frontier"]["best"] or {}).get("system")
    mon = {e["round"]: e["data"] for e in ev if e["kind"] == "monitor"}
    for t in rounds:
        rev = [e for e in ev if e["round"] == t]
        rs = next(e["data"] for e in rev if e["kind"] == "round_start")
        prior = [n for n, it in all_names_by_iter.items() if it < t]
        # (1) state at round start: frontier recomputed from systems of earlier iterations
        pts = [(n, rec[n]["score"], rec[n]["cost"]) for n in prior if rec[n] is not None]
        fr = sorted(_pareto(pts), key=lambda x: (-x[1], x[2], x[0]))
        ok = ({p["system"] for p in rs["frontier"]["pareto"]} == {p[0] for p in fr} and
              (rs["frontier"]["best"] or {}).get("system") == fr[0][0] and
              set(rs["view"]["visible_systems"]) == set(prior) and rs["n_evaluated"] ==
              sum(1 for n in prior if meta[n].get("kind") == "candidate" and rec[n] is not None))
        st.add(t, "round_start state (frontier, visible systems, budget)", "correct" if ok else "wrong",
               f"frontier recomputed from {len(pts)} prior systems = {[p[0] for p in fr]}; view exposes "
               f"{rs['view']['n_files']} files / {rs['view']['chars']} chars of {len(prior)} systems "
               f"(by kind {rs['view']['by_kind']})")
        # (2) proposer input: what was read, and never sealed data
        an = next(e["data"] for e in rev if e["kind"] == "analysis")
        sess = store / "sessions" / f"iter{t:03d}"
        prompt = (sess / "prompt.md").read_text() if (sess / "prompt.md").exists() else ""
        smeta = json.loads((sess / "meta.json").read_text())
        leaked = [f"{sp}:{tid}" for sp, tid, s in sealed_text if s in prompt]
        bad_paths = [p for p in smeta.get("files_read", []) if p.startswith("results/") or "frontier.json" == p
                     or "finalized" in p]
        n_tr = (an.get("files_read_by_kind") or {}).get("traces", 0)
        v_tr = rs["view"]["by_kind"].get("traces", 0)
        verdict = "correct"
        note = ""
        if leaked or bad_paths:
            verdict = "wrong"
        elif n_tr < v_tr:
            verdict = "questionable"
            note = (f" the renderer's budget dropped {v_tr - n_tr} of {v_tr} raw-trace files (the paper's proposer "
                    f"decides itself what to read; here a priority renderer does)")
        st.add(t, "proposer input (history read, no sealed data)", verdict,
               f"read {an.get('files_read')} of {rs['view']['n_files']} files (traces {n_tr}/{v_tr}); prompt "
               f"{len(prompt)} chars contains 0 of {len(sealed_text)} sealed task inputs" if not leaked else
               f"SEALED INPUTS IN PROMPT: {leaked[:5]}" + note + (f"; forbidden paths read {bad_paths}" if bad_paths
                                                                 else ""))
        # (3) per proposal: claimed base visible, actual diff, harness file really changed, leakage scan
        props = [e["data"] for e in rev if e["kind"] == "proposal" and e["data"]["candidate"] != "(none)"]
        for p in props:
            n = p["candidate"]
            base = p.get("parent")
            ok_base = base in prior
            a, b = _src(store, base) if base in meta else {}, _src(store, n)
            changed, diff = _udiff(a, b)
            add = _added(diff)
            live_files = set(a)
            real = [f for f in changed if f in live_files]
            leaks = []
            for sp in ("evolve", "holdout", "ood", "test"):
                for tk in tasks.get(sp, []):
                    inp = tk.input if isinstance(tk.input, str) else json.dumps(tk.input, default=str)
                    frag = inp[:40]
                    if len(frag) >= 25 and frag in add:
                        leaks.append(f"{sp}:{tk.id}: question text")
                    tgt = str(tk.target)
                    if is_live and len(tgt) >= 3 and re.search(r"(?<![\w.])" + re.escape(tgt) + r"(?![\w.])", add):
                        leaks.append(f"{sp}:{tk.id}: literal answer {tgt!r}")
                    if tk.id in add:
                        leaks.append(f"{sp}:{tk.id}: task id")
            claimed = p.get("change") or p.get("hypothesis") or ""
            if not ok_base or not changed:
                v, ev_ = "wrong", f"base {base!r} visible={ok_base}; changed files {changed}"
            elif not real:
                v, ev_ = "wrong", f"only NEW files {changed}: the harness that runs is the base unchanged (dead code)"
            elif leaks:
                v, ev_ = "questionable", f"task-specific content in added lines: {leaks[:6]}"
            else:
                v, ev_ = "correct", (f"base {base} (visible); actual diff touches {real} "
                                     f"(+{add.count(chr(10)) + 1 if add else 0} lines); no task ids / question text / "
                                     f"literal answers in added lines")
            st.add(t, f"proposal {n}: claim vs actual diff", v, ev_ + f" | claim: {claimed[:140]}")
        # (4) gate = admissibility; eval recomputed
        gates = {e["data"]["candidate"]: e["data"] for e in rev if e["kind"] == "gate"}
        evals = {e["data"]["candidate"]: e["data"] for e in rev if e["kind"] == "eval"}
        for n, g in gates.items():
            ok = (g["accept"] == (n in evals)) and (g["accept"] == (meta[n].get("status") == "evaluated"))
            st.add(t, f"gate (admissibility) {n}", "correct" if ok else "wrong",
                   f"validated={g['math'].get('validated')} -> {'evaluated' if n in evals else 'not evaluated'}; "
                   f"store status {meta[n].get('status')}")
        for n, d in evals.items():
            r = rec[n]
            tr_score = mean(mean(v) for v in d["trials"].values())
            lg = led.get(n, {})
            ok = (abs(tr_score - r["score"]) < 1e-9 and abs((lg.get("score") or -1) - r["score"]) < 1e-9 and
                  abs((lg.get("cost") or -1) - r["cost"]) < 1e-6 and set(r["per"]) <= evolve_ids and
                  d["summary"]["split"] == cfg["search_split"])
            st.add(t, f"eval {n}", "correct" if ok else "wrong",
                   f"S={r['score']:.4f} (raw trials; ledger {lg.get('score')}), cost {r['cost']:.1f} "
                   f"(ledger {lg.get('cost')}); split {d['summary']['split']}, {len(r['per'])} units all in evolve")
        # (5) decision: frontier recomputed after the iteration, deltas, incumbent
        dec = next(e["data"] for e in rev if e["kind"] == "decision")
        now = [n for n, it in all_names_by_iter.items() if it <= t and rec[n] is not None]
        pts2 = [(n, rec[n]["score"], rec[n]["cost"]) for n in now]
        fr2 = sorted(_pareto(pts2), key=lambda x: (-x[1], x[2], x[0]))
        pre = rs["frontier"]["best"]["score"]
        post = fr2[0][1]
        errs = []
        for n, pc in dec["per_candidate"].items():
            if "score" not in pc:
                continue
            onf = n in {x[0] for x in fr2}
            dom = sorted(o for o, s2, c2 in pts2 if o != n and s2 >= rec[n]["score"] and c2 <= rec[n]["cost"]
                         and (s2 > rec[n]["score"] or c2 < rec[n]["cost"]))
            if onf != pc["on_frontier"] or dom[:6] != pc["dominated_by"]:
                errs.append(f"{n}: frontier/dominance mismatch")
            if abs(pc["delta_vs_pre_best_pts"] - round(100 * (rec[n]["score"] - pre), 2)) > 1e-6:
                errs.append(f"{n}: delta_pre")
            if pc["delta_logged_release"] != round(round(100 * rec[n]["score"], 1) - 100 * post, 1):
                errs.append(f"{n}: release delta {pc['delta_logged_release']}")
        if dec["incumbent_after"] != fr2[0][0]:
            errs.append("incumbent")
        tie = [x[0] for x in pts2 if abs(x[1] - fr2[0][1]) < EPS and abs(x[2] - fr2[0][2]) < EPS and x[0] != fr2[0][0]]
        changed_inc = dec["incumbent_before"] != dec["incumbent_after"]
        v = "wrong" if errs else "correct"
        ev_ = (f"frontier after it {t}: {[x[0] for x in fr2]}; incumbent {dec['incumbent_before']} -> "
               f"{dec['incumbent_after']} (recomputed {fr2[0][0]}, S {pre:.4f} -> {post:.4f})")
        if not errs and changed_inc and tie:
            v = "questionable"
            ev_ += (f"; the new incumbent TIES {tie} exactly on (score, cost) and wins only by the name tie-break "
                    f"(no improvement)")
        st.add(t, "decision (frontier, deltas, incumbent)", v, ev_ + (f"; errors {errs}" if errs else ""))
        # (6) monitor: only on a new incumbent, sealed splits only, never read back
        m = mon.get(t)
        if changed_inc:
            ok = m is not None and m["version"] == dec["incumbent_after"] and \
                set(m["sealed"]) <= {"holdout", "ood"}
            st.add(t, "shadow monitor on new incumbent", "correct" if ok else "wrong",
                   (f"{m['version']}: " + ", ".join(f"{s} {v['S']:.3f}" for s, v in m['sealed'].items()))
                   if m else "missing")
        elif m is not None:
            st.add(t, "shadow monitor", "wrong", "monitor ran without an incumbent change")
        best_by_round[t] = {"incumbent": dec["incumbent_after"], "search": post,
                            "sealed": {s: v["S"] for s, v in (m or {}).get("sealed", {}).items()} if m else None}
    # (7) finalize: once, after the last iteration, on baselines + Pareto + per-unit best
    fin = [e for e in ev if e["kind"] == "note" and "finalize" in e["data"].get("what", "")]
    last_iter_seq = max(e["seq"] for e in ev if e["round"] in rounds)
    names = [n for n in meta if rec[n] is not None]
    pts = [(n, rec[n]["score"], rec[n]["cost"]) for n in names]
    per_unit = {}
    for n in names:
        pu_cost = json.loads((store / "candidates" / n / "eval" / "search" / "scores.json").read_text())["per_unit_cost"]
        for u, s in rec[n]["per"].items():
            per_unit.setdefault(u, []).append((n, s, pu_cost.get(u, 0.0)))
    pub = {max(v, key=lambda x: (x[1], -x[2], x[0]))[0] for v in per_unit.values()}
    base_names = [n for n in names if meta[n].get("kind") == "baseline"]
    expect = set(base_names) | {x[0] for x in _pareto(pts)} | pub
    if fin:
        d = fin[0]["data"]
        res_dirs = {p.name for sp in (store / "results").iterdir() for p in sp.iterdir()}
        ok = (len(fin) == 1 and fin[0]["seq"] > last_iter_seq and set(d["systems"]) == expect and
              res_dirs == expect and d["status"] == "complete")
        st.add(None, "finalize (test once, on baselines + Pareto + per-unit best)", "correct" if ok else "wrong",
               f"systems {sorted(d['systems'])} (recomputed {sorted(expect)}); results/ holds exactly these; "
               f"one finalize event after the last iteration; status {d['status']}")
    # (8) spend
    rep = json.loads((rd / "report.json").read_text())
    prop_usd = sum((e["data"].get("proposer_usage") or {}).get("cost_usd", 0.0) for e in ev if e["kind"] == "analysis")
    eval_usd = sum(e["data"]["summary"].get("usd", 0.0) for e in ev if e["kind"] in ("eval", "baseline"))
    sp = rep.get("spend")
    if sp:
        loop = sp["loop"]["usd"]
        cache = sp["cache_ground_truth"]["usd"]
        mon_usd = sp["shadow_monitor"]["usd"]
        gap = cache - loop - mon_usd - sp.get("report", 0.0)
        ok = gap >= -0.002 and abs(prop_usd + eval_usd - loop) < 0.02
        st.add(None, "cost accounting", "correct" if ok else "questionable",
               f"proposer ${prop_usd:.4f} + evals ${eval_usd:.4f} (trace) vs loop meter ${loop:.4f}; monitor "
               f"${mon_usd:.4f}; cache ground truth ${cache:.4f}; unmetered gap ${gap:.4f} (forked smokes)")
    else:
        st.add(None, "cost accounting", "correct", "offline: $0 (deterministic MemoLM, MockProposer)")
    return {"run": run, "method": "metaharness", "counts": st.counts(), "steps": st.rows, "incumbents": best_by_round}


# ============================================================================ SoL-Pi
def _gate(spec: dict, b: dict, c: dict) -> tuple[bool, str]:
    cap = all(c[m] >= b[m] * (1 - tol) - 1e-12 for m, tol in spec["capability"])
    eff = any(((b[m] - c[m]) / b[m] if b[m] > 0 else 0.0) > spec["min_gain"] for m in spec["efficiency"])
    return cap and eff, ("accepted" if cap and eff else "capability below floor" if not cap else "no efficiency gain")


def _apply_harness_diff(base_files: dict, diff: str) -> dict:
    """Rebuild harness.json from a trace diff (the whole file is small, so the '+' side is the new file)."""
    lines = diff.splitlines()
    if not any(l.startswith("+++ b/harness.json") for l in lines):
        return None
    new, on = [], False
    for l in lines:
        if l.startswith("+++ b/harness.json"):
            on = True
            continue
        if on and l.startswith("--- a/"):
            break
        if on and l.startswith("@@"):
            continue
        if on and (l.startswith("+") or l.startswith(" ")):
            new.append(l[1:])
    try:
        return json.loads("\n".join(new))
    except ValueError:
        return None


def audit_sp(run: str) -> dict:
    rd = OUT / run
    ev = load(rd / "trace.jsonl")
    st = Steps()
    rs0 = ev[0]["data"]
    spec = rs0["gate_spec"]
    ideas = {i["id"]: i for i in rs0["idea_pool"]}
    offline = "offline" in run
    from rsi.domains.agentworld import make_domain
    dom = (make_domain(seed=0, n_train=8, n_accept=6, n_final=8, n_test=0) if offline else
           make_domain(seed=0, n_train=3, n_accept=3, n_final=3, n_test=0))
    evolve_ids = {t.id for t in dom.tasks.split("evolve")}
    base_ev = next(e for e in ev if e["kind"] == "baseline")
    bm = base_ev["data"]["metrics"]["agg"]
    tr_score = mean(x for v in base_ev["data"]["trials"].values() for x in v)
    st.add(1, "baseline on the training screen", "correct" if abs(tr_score - bm["score"]) < 1e-9 and
           set(base_ev["data"]["per_task"]) == evolve_ids else "wrong",
           f"S={bm['score']:.4f} from {len(base_ev['data']['trials'])} raw trials, tokens {bm['tokens']:.0f}, "
           f"cost {bm['cost']:.4f}; tasks = the whole evolve split ({len(evolve_ids)})")
    # oracle
    an = next(e["data"] for e in ev if e["kind"] == "analysis" and "oracle" in e["data"])
    ranked = sorted(an["oracle"].items(), key=lambda kv: (-kv[1], kv[0]))
    n_lin = rs0["config"]["n_lineages"]
    ok = [i for i, _ in ranked[:n_lin]] == an["chosen"]
    st.add(1, "oracle ranking + selection", "correct" if ok else "wrong",
           f"top-{n_lin} by (-estimate, id) = {[f'{i}:{v:.3f}' for i, v in ranked[:n_lin]]}")
    # lineage iterations
    rounds = sorted({e["round"] for e in ev if e["kind"] == "round_start" and
                     e["data"].get("phase") == "lineage iteration"})
    last_gate_fail_art: dict[str, str] = {}
    gates = {}
    frozen_names = {}
    for r in rounds:
        rev = [e for e in ev if e["round"] == r]
        rs = next(e["data"] for e in rev if e["kind"] == "round_start")
        idea = rs["idea"]["id"]
        it = rs["lineage_iteration"]
        ro = next((e["data"] for e in rev if e["kind"] == "eval" and e["data"]["candidate"].startswith("rollouts(")), None)
        base_short = base_ev["data"]["artifact"]
        want = last_gate_fail_art.get(idea, base_short)
        got = ro["candidate"][len("rollouts("):-1] if ro else None
        ok = got == want and set(ro["per_task"]) <= evolve_ids
        st.add(r, f"{idea}.{it} 01 rollouts", "correct" if ok else "wrong",
               f"rollouts of {got} (expected {want}: base, or the last gate-failed candidate after a route-back); "
               f"{len(ro['per_task'])} tasks, all evolve")
        pr = next(e["data"] for e in rev if e["kind"] == "proposal")
        # 03/04 proposal: diff real, parent base
        if pr.get("error"):
            exhausted = pr.get("exhausted")
            st.add(r, f"{idea}.{it} 03/04 proposal", "correct" if not pr["files_changed"] else "wrong",
                   f"no candidate: {pr['error'][:160]} (Ralph errors {len(pr.get('ralph_errors', []))}); "
                   f"{'grid exhausted -> lineage ends' if exhausted else 'iteration abandoned'}")
        else:
            add = _added(pr["diff"])
            forbidden = [w for w in ("holdout", "heldout", "configfix", "datalookup", "ood/") if w in add.lower()]
            ids = [t for t in evolve_ids if t in add]
            v = "correct" if pr["files_changed"] and not forbidden and not ids else "wrong" if not pr["files_changed"] \
                else "questionable"
            st.add(r, f"{idea}.{it} 03/04 proposal", v,
                   f"change {pr['change'][:80]!r}, variant {pr.get('variant')}; actual diff touches "
                   f"{pr['files_changed']}; Ralph repairs {pr.get('ralph_repairs')}; held-out references "
                   f"{forbidden}, task ids {ids[:3]}")
        cr = next((e["data"] for e in rev if e["kind"] == "critic"), None)
        if cr is not None:
            st.add(r, f"{idea}.{it} 05 review", "correct" if cr["accept"] else "unverifiable",
                   ("pass: " if cr["accept"] else "REJECT: ") + str(cr.get("note"))[:300])
        g = next((e["data"] for e in rev if e["kind"] == "gate"), None)
        sv = next((e["data"] for e in rev if e["kind"] == "eval" and not e["data"]["candidate"].startswith("rollouts(")),
                  None)
        if g is not None:
            m = sv["metrics"]["agg"]
            s_raw = mean(x for v in sv["trials"].values() for x in v)
            acc, why = _gate(spec, bm, m)
            eff = {k: (bm[k] - m[k]) / bm[k] for k in spec["efficiency"]}
            ok = acc == g["accept"] and why == g["reason"] and abs(s_raw - m["score"]) < 1e-9 and \
                g["math"]["capability"]["score"]["base"] == bm["score"] and set(sv["per_task"]) == evolve_ids
            v = "correct" if ok else "wrong"
            txt = (f"base S {bm['score']:.4f} vs cand S {m['score']:.4f} (raw trials {s_raw:.4f}, floor "
                   f"{bm['score'] * (1 - spec['capability'][0][1]):.4f}); savings " +
                   ", ".join(f"{k} {100 * s:+.1f}%" for k, s in eff.items()) +
                   f" (need > {100 * spec['min_gain']:.0f}%) -> {why} (loop: {g['reason']})")
            if ok and acc and any(s < 0 for s in eff.values()):
                v = "questionable"
                txt += "; PASSES while another efficiency metric gets WORSE (the exists-rule of the gate)"
            st.add(r, f"{idea}.{it} 06 screen + dual gate", v, txt)
            gates[f"{idea}.{it}"] = (acc, m)
            if not acc:
                last_gate_fail_art[idea] = sv["summary"]["artifact"]
        dec = next(e["data"] for e in rev if e["kind"] == "decision")
        exp = ("frozen" if g is not None and g["accept"] else "gate_failed" if g is not None else
               "rejected" if cr is not None and not cr["accept"] else "abandoned")
        ok = dec["outcome"] == exp
        st.add(r, f"{idea}.{it} decision", "correct" if ok else "wrong",
               f"{dec['outcome']} (expected {exp}); {dec['why'][:160]}")
        if dec["outcome"] == "frozen":
            frozen_names[idea] = pr
    # firewall + composition
    fw_round = max(e["round"] for e in ev if e["kind"] == "round_start")
    fev = [e for e in ev if e["round"] == fw_round]
    sink_p = rd / "firewall_r1" / "heldout.jsonl"
    sink = load(sink_p) if sink_p.exists() else []
    fgates = [e["data"] for e in fev if e["kind"] == "gate"]
    fw_ev = None
    if offline:
        from rsi.core import Evaluator
        from rsi.domains.agentworld import MockAgentLLM
        from rsi.solpi.gate import metrics_from_eval
        fw_ev = Evaluator(dom, MockAgentLLM("A"), workers=2, allow_sealed=True)
        base_art = dom.seed_artifact()
        hb = metrics_from_eval(fw_ev.evaluate(base_art, "holdout", k=1)).agg
    for fg in fgates:
        s = next((x for x in sink if x["candidate"] == fg["candidate"]), None)
        hm, hbm = fg["math"]["heldout_metrics"], fg["math"]["heldout_base_metrics"]
        acc, why = _gate(spec, hbm, hm)
        ok = s is not None and s["passed"] == fg["accept"] == acc and s["metrics"]["agg"] == hm
        extra = ""
        if offline and ok:
            idea = fg["candidate"].split(":")[0]
            h = _apply_harness_diff({}, frozen_names[idea]["diff"])
            art = dom.seed_artifact().with_files({"harness.json": json.dumps(h, indent=1)})
            m2 = metrics_from_eval(fw_ev.evaluate(art, "holdout", k=1)).agg
            acc2, _ = _gate(spec, hb, m2)
            re_ok = acc2 == acc and abs(m2["tokens"] - hm["tokens"]) < 1e-6 and abs(hb["tokens"] - hbm["tokens"]) < 1e-6
            ok = ok and re_ok
            extra = f"; independently re-evaluated on holdout: tokens {m2['tokens']:.0f} (sink {hm['tokens']:.0f})"
        kind = fg["math"].get("idea_kind_ground_truth")
        st.add(fw_round, f"firewall {fg['candidate'][:40]}", "correct" if ok else "wrong",
               f"held-out S {hm['score']:.4f} vs base {hbm['score']:.4f}; tokens {100 * (hbm['tokens'] - hm['tokens']) / hbm['tokens']:+.1f}%, cost "
               f"{100 * (hbm['cost'] - hm['cost']) / hbm['cost']:+.1f}% saving -> {why}; sink agrees; kind={kind}"
               + extra)
    dec = next(e["data"] for e in fev if e["kind"] == "decision")
    kept_ideas = [fg["candidate"].split(":")[0] for fg in fgates if fg["accept"]]
    if kept_ideas:
        union = {}
        for i in kept_ideas:
            union.update(_apply_harness_diff({}, frozen_names[i]["diff"])["extensions"])
        comp = _apply_harness_diff({}, dec["diff"])
        ok = comp is not None and comp["extensions"] == union and dec["kept"] == ",".join(kept_ideas)
        cm, bs = dec["composed_screen"], dec["base_screen"]
        best_single = min(((i, gates[f"{i}.{n}"][1]) for i in kept_ideas for n in range(8) if f"{i}.{n}" in gates
                           and gates[f"{i}.{n}"][0]), key=lambda x: x[1]["cost"])
        worse = cm["cost"] > best_single[1]["cost"]
        v = "correct" if ok else "wrong"
        txt = (f"composed = union of {kept_ideas} (verified); screen S {cm['score']:.3f}, tokens "
               f"{100 * (bs['tokens'] - cm['tokens']) / bs['tokens']:+.1f}%, cost {100 * (bs['cost'] - cm['cost']) / bs['cost']:+.1f}% saving")
        if ok and worse:
            v = "questionable"
            txt += (f"; the composed stack costs MORE than survivor {best_single[0]} alone "
                    f"(cost {best_single[1]['cost']:.4f} vs {cm['cost']:.4f}) and is not re-gated")
        st.add(fw_round, "composition", v, txt)
    else:
        st.add(fw_round, "composition", "correct" if dec["kept"] is None else "wrong",
               "no survivor: incumbent stays the base")
    # sealed isolation: no lineage eval touched a non-evolve task
    lin_tasks = {t for e in ev if e["kind"] == "eval" and e["round"] in rounds for t in e["data"]["per_task"]}
    st.add(None, "no sealed evaluation inside lineages", "correct" if lin_tasks <= evolve_ids else "wrong",
           f"{len(lin_tasks)} distinct tasks evaluated by lineages, all in evolve")
    # spend
    rep = json.loads((rd / "report.json").read_text())
    sp = rep.get("spend")
    if sp:
        prop_usd = sum((e["data"].get("proposer_usage") or {}).get("cost_usd", 0.0) for e in ev if e["kind"] == "proposal")
        loop, cache = sp["loop"]["usd"], sp["cache_ground_truth"]["usd"]
        st.add(None, "cost accounting", "correct" if abs(loop - cache) < 0.002 else "questionable",
               f"proposer+implementer (trace) ${prop_usd:.4f}; reviewer = loop - proposer = ${loop - prop_usd:.4f}; "
               f"loop meter ${loop:.4f} = cache ground truth ${cache:.4f}")
    else:
        st.add(None, "cost accounting", "correct", "offline: $0 (MockAgentLLM, LibraryProposer)")
    return {"run": run, "method": "solpi", "counts": st.counts(), "steps": st.rows}


def main(argv):
    runs = argv or sorted(p.name for p in OUT.iterdir() if p.is_dir() and (p / "trace.jsonl").exists())
    dest = OUT / "audit_b"
    dest.mkdir(exist_ok=True)
    tot = {"correct": 0, "questionable": 0, "wrong": 0, "unverifiable": 0}
    for run in runs:
        res = audit_mh(run) if run.startswith("mh_") else audit_sp(run)
        (dest / f"{run}.json").write_text(json.dumps(res, indent=1, default=str))
        print(f"== {run}: {res['counts']}")
        for r in res["steps"]:
            if r["verdict"] != "correct":
                print(f"   [{r['verdict']}] r{r['round']} {r['step']}: {r['evidence'][:260]}")
        for k in tot:
            tot[k] += res["counts"][k]
    print("TOTAL", tot, sum(tot.values()))


if __name__ == "__main__":
    main(sys.argv[1:])
