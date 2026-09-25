"""Independent step checker for the RRSI validation runs (stage-B audit).

Re-derives every recorded step of ``validation/rrsi/<run>/`` from the RAW data only
(per-trial rewards and tokens in ``trace.jsonl`` and ``evals/*.json``, the per-edit
``history.jsonl``) with the spec formulas written out here from scratch. It imports
nothing from ``rsi.rrsi`` (so a bug shared by the loop and ``rsi.rrsi.audit`` cannot hide),
only the domain constructors to learn the sealed ids / questions / answers.

    python experiments/rrsi/validate_rrsi_stepcheck.py [run ...]   # -> <run>/stepcheck.json

Each check is one row {step, check, status (correct|questionable|wrong|unverifiable), evidence}.
The formulas (docs/methods/rrsi.md section 4):

    b_t   = ceil(b_min + (b_max-b_min)/2 (1+cos(pi t/T)))
    S_hat = mean of all k|D| trial rewards (missing -> 0);  C_hat = mean of positive token counts
    dS    = S' - S_t;  dC = (C' - C_t)/C_t
    floor = S* - delta,  S* = max trajectory
    c     = dC <= b0 + b1 dS             if dS > delta
            w_s dS - w_c dC + w_n nu > 0  otherwise
            (raw float comparisons, as the code makes them; the run's Config.tie_eps if it set one)
    winner = argmax S' over admissible (first on ties), else H_t
    T_t   = components of measured edits (rounds < t);  U_t = K \\ T_t
    sigma = 1[S_t - S_{t-w} <= delta] (t >= w)
    g_t(l)= max dS over measured edits of l with t - t_i <= n_prune;  B_t = {l in T_t : g <= 0}
    nu    = |{structural components of the candidate with no accepted edit in rounds < t}|
    delta = z * sd_null;  bootstrap plug-in sd_null = sqrt(2) sqrt(sum_x pvar(r_x)/k) / n
            repeated evaluations: sd_null = stdev(S_r) * sqrt(2)
"""
from __future__ import annotations

import json
import math
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
VAL = ROOT / "validation" / "rrsi"
EPS = 1e-9


def load(run: str):
    out = VAL / run
    ev = [json.loads(l) for l in (out / "trace.jsonl").read_text().splitlines() if l.strip()]
    hist = [json.loads(l) for l in (out / "history.jsonl").read_text().splitlines() if l.strip()]
    cfgj = json.loads((out / "config.json").read_text())
    return out, ev, hist, cfgj


def domain_for(run: str):
    if run == "offline_harnessworld":
        from rsi.domains.harnessworld import make_domain
        return make_domain(seed=0)
    from rsi.domains.agentqa import AgentQADomain, make_suite
    if run == "live_agentqa":
        return AgentQADomain(make_suite(n_evolve=12, n_holdout=12, n_ood_per_family=3, seed=0))
    return AgentQADomain(make_suite(seed=0))


def s_hat(trials: dict) -> float:
    xs = [r for rs in trials.values() for r in rs]
    return sum(xs) / len(xs)


def c_hat(tokens: dict):
    xs = [c for cs in tokens.values() for c in cs if c is not None and c > 0]
    return sum(xs) / len(xs) if xs else None


def budget(t, T, bmin, bmax):
    return int(math.ceil(round(bmin + (bmax - bmin) * 0.5 * (1 + math.cos(math.pi * t / T)), 9)))


class Rows(list):
    def add(self, step, check, ok, evidence, *, bad="wrong"):
        status = ok if isinstance(ok, str) else ("correct" if ok else bad)
        self.append({"step": step, "check": check, "status": status, "evidence": evidence})


def check_run(run: str) -> dict:
    out, ev, hist, cfgj = load(run)
    cfg, K, K_str = cfgj["config"], cfgj["K"], cfgj["K_str"]
    # post-fix runs record Config.tie_eps (default 0.0: raw float comparisons, as in the released code);
    # runs made before the fix used rsi.core's 1e-9 tie tolerance
    post_fix = "tie_eps" in cfg
    eps = float(cfg["tie_eps"]) if post_fix else EPS
    rows = Rows()
    dom = domain_for(run)
    # ---- sealed material: ids, question texts and answers of holdout / ood ----------------
    sealed_ids, sealed_q, sealed_ans = set(), [], set()
    evolve_ans = {}
    for s in ("holdout", "ood"):
        for tid in dom.tasks.splits.get(s, []):
            task = dom.tasks.tasks[tid]
            sealed_ids.add(tid)
            q = str(task.input)
            if len(q) > 40:
                sealed_q.append(q[:60])
            tgt = getattr(task, "target", None)
            if tgt is not None and len(str(tgt)) >= 5:
                sealed_ans.add(str(tgt))
    evolve_q = set()
    for tid in dom.tasks.splits.get("evolve", []):
        tgt = getattr(dom.tasks.tasks[tid], "target", None)
        evolve_q.add(str(dom.tasks.tasks[tid].input)[:60])
        if tgt is not None:
            evolve_ans[tid] = str(tgt)
    dup = sorted(q for q in sealed_q if q in evolve_q)
    sealed_q = [q for q in sealed_q if q not in evolve_q]     # a duplicated question is legitimately visible
    rows.add("domain", "sealed splits are disjoint from evolve (question text)", not dup,
             f"questions present in BOTH evolve and a sealed split: {dup}" if dup else "disjoint")

    # ---- segments (a resumed run has several run_start events) ----------------------------
    starts = [i for i, e in enumerate(ev) if e["kind"] == "run_start"]
    last_seg = ev[starts[-1]:]
    base = next(e["data"] for e in ev if e["kind"] == "baseline")
    noise = next(e["data"] for e in ev if e["kind"] == "noise")
    delta = noise["delta"]

    # ---- baseline --------------------------------------------------------------------------
    S0, C0 = s_hat(base["trials"]), c_hat(base["tokens"])
    disk = json.loads((out / "evals" / "base.json").read_text())
    rows.add("baseline", "S_hat(H0) from raw trials == traced S == evals/base.json",
             abs(S0 - base["summary"]["S"]) < 1e-9 and abs(S0 - disk["S"]) < 1e-9,
             f"S={S0:.6f} traced={base['summary']['S']:.6f} disk={disk['S']:.6f}")
    rows.add("baseline", "C_hat(H0) from raw tokens",
             abs((C0 or 0) - (base["summary"]["C"] or 0)) < 1e-6, f"C={C0} traced={base['summary']['C']}")

    # ---- noise band ------------------------------------------------------------------------
    cal = noise["calibration"]
    if cal["method"].startswith("repeated"):
        Ss = []
        for j in cal["jobs"]:
            d = json.loads((out / "evals" / f"{j}.json").read_text())
            Ss.append(sum(r for tr in d["per_task"].values() for r in tr["rewards"]) /
                      sum(len(tr["rewards"]) for tr in d["per_task"].values()))
        sd = statistics.stdev(Ss) * math.sqrt(2)
        rows.add("noise", "delta = z * stdev(S_r) * sqrt(2) over R repeated base evaluations",
                 abs(cfg["delta_z"] * sd - delta) < 1e-5,
                 f"S_r={[round(x, 4) for x in Ss]} sd_null={sd:.6f} delta={cfg['delta_z'] * sd:.6f} traced={delta}")
        rows.add("noise", "delta estimated from R=3 evaluations at k=1 (spec 8.10: 2-3-sample stdev is itself noisy)",
                 "questionable", f"R={len(Ss)}; the 95% CI of an sd from 3 samples spans ~0.52x..6.3x")
    else:
        n = len(base["trials"])
        k = cfg["k"]
        var = sum(statistics.pvariance(r) / len(r) for r in base["trials"].values())
        sd_plug = math.sqrt(2) * math.sqrt(var) / n
        rel = abs(sd_plug - cal["sd_null"]) / max(sd_plug, 1e-12)
        rows.add("noise", "bootstrap sd_null vs closed-form plug-in (within-task resampling)",
                 rel < 0.05, f"plug-in {sd_plug:.6f} vs traced {cal['sd_null']:.6f} (rel {rel:.3f}); delta={delta}")
        unbiased = math.sqrt(2) * math.sqrt(sum(statistics.variance(r) / len(r) for r in base["trials"].values())) / n
        rows.add("noise", "plug-in within-task bootstrap under-covers at small k (faithful to code; E10)",
                 "questionable", f"k={k}: unbiased sd_null {unbiased:.6f} vs plug-in {sd_plug:.6f} "
                                 f"(delta would be {2 * unbiased:.4f} instead of {delta:.4f})")

    # ---- rounds ---------------------------------------------------------------------------
    rounds = sorted({e["round"] for e in last_seg if e["round"] is not None and e["kind"] == "round_start"})
    traj = [S0]
    trajC = [C0]
    S_star = S0
    inc_name, inc_S, inc_C = "H0", S0, C0
    cand_S = {}
    for t in rounds:
        es = [e for e in last_seg if e["round"] == t]
        es_all = [e for e in ev if e["round"] == t]
        rs = next(e["data"] for e in es if e["kind"] == "round_start")
        step = f"r{t}"
        # b_t
        b = budget(t, cfg["T"], cfg["b_min"], cfg["b_max"])
        rows.add(step, "b_t = ceil(cosine anneal)", b == rs["b_t"], f"expected {b}, traced {rs['b_t']}")
        # incumbent, S*, trajectory
        rows.add(step, "S_t / S* / trajectory chain", abs(rs["incumbent"]["S"] - inc_S) < 1e-9 and
                 abs(rs["S_star"] - S_star) < 1e-9 and len(rs["trajectory_S"]) == t + 1,
                 f"S_t={inc_S:.4f} ({inc_name}) S*={S_star:.4f}; traced S_t={rs['incumbent']['S']:.4f} "
                 f"S*={rs['S_star']:.4f}")
        # sigma
        w = cfg["w"]
        sig = int(t >= w and traj[t] - traj[t - w] <= delta + eps)
        rows.add(step, "sigma_t = 1[S_t - S_{t-w} <= delta]", sig == rs["sigma_t"],
                 f"expected {sig}, traced {rs['sigma_t']}" + (f" (S_t-S_t-w={traj[t] - traj[t - w]:+.4f})" if t >= w else ""))
        # T_t, U_t from the per-edit history of rounds < t
        prior = [r for r in hist if r.get("edit_id") and r["t"] < t]
        meas = [r for r in prior if r.get("delta_S") is not None and r.get("component") in K]
        T_t = {r["component"] for r in meas}
        U_t = [c for c in K if c not in T_t]
        rows.add(step, "T_t (measured components) and U_t = K \\ T_t",
                 sorted(T_t) == sorted(rs["tried_T_t"]) and U_t == rs["untried_U_t"],
                 f"T_t={sorted(T_t)} U_t={U_t}")
        res_exp = [chr(65 + v) for v in range(cfg["m"]) if sig and U_t and v >= cfg["m"] - cfg["m_draft"]]
        rows.add(step, "reserved variants = last m_draft when sigma=1 and U_t != {}",
                 res_exp == list(rs["reserved_variants"]), f"expected {res_exp}, traced {rs['reserved_variants']}")
        # B_t
        g = {c: -math.inf for c in T_t}
        for r in meas:
            if t - r["t"] <= cfg["n_prune"]:
                g[r["component"]] = max(g[r["component"]], r["delta_S"])
        B = sorted(c for c in g if g[c] <= 0)
        rows.add(step, "B_t = {l in T_t : g_t(l) <= 0}",
                 B == sorted(p["component"] for p in rs["prune_B_t"]), f"B_t={B}; g={ {c: (None if v == -math.inf else round(v, 4)) for c, v in g.items()} }")
        empty_b = [c for c in B if not any(r.get("accepted") and r.get("component") == c for r in prior)]
        if empty_b:
            rows.add(step, "B_t members with no accepted machinery to prune (spec 8.3: advisory, empty lists)",
                     "questionable", f"{empty_b}")
        # ---- analysis F_t: evidence = the incumbent's own stored evolve evaluation -----------
        an = next((e["data"] for e in es if e["kind"] == "analysis"), None)
        if an is not None:
            read = list((an.get("traces_read") or {}).get("fail", {})) + list((an.get("traces_read") or {}).get("win", {}))
            blob = json.dumps(an.get("llm_calls") or []) + json.dumps(an.get("report") or {})
            hits = [i for i in sealed_ids if re.search(rf"(?<![\w-]){re.escape(i)}(?![\w-])", blob)]
            hits += [q for q in sealed_q if q in blob]
            rows.add(step, "analyst read only evolve traces of the incumbent; no sealed id/question in its I/O",
                     not hits and all(x in evolve_ans or x.startswith("evolve") or x.startswith("hw-e") for x in read),
                     f"{len(read)} traces read; sealed hits={hits[:3]}")
            fm = (an.get("report") or {}).get("failure_modes") or []
            solved_modes = [m.get("mode") for m in fm if all((an.get("traces_read") or {}).get("fail", {}).get(x, 0) >= 1.0
                                                              for x in (m.get("affected_tasks") or ["?"]))]
            rows.add(step, "F_t failure modes contain no mode made only of SOLVED tasks", not solved_modes,
                     f"modes over solved tasks: {solved_modes}" if solved_modes else f"{len(fm)} failure mode(s)")
            n_fail = min(cfg["n_fail_traces"], len(base["trials"]))
            rows.add(step, "trace selection = n_fail lowest + n_success highest (not in fails) of the incumbent",
                     len((an.get("traces_read") or {}).get("fail", {})) == n_fail,
                     f"fail={len((an.get('traces_read') or {}).get('fail', {}))} (expected {n_fail}), "
                     f"win={len((an.get('traces_read') or {}).get('win', {}))}")
        # ---- proposals, critic, tagging ----------------------------------------------------
        cands = sorted({e["data"]["candidate"] for e in es_all if e["kind"] in ("proposal", "gate")})
        for c in cands:
            props = [e["data"] for e in es_all if e["kind"] == "proposal" and e["data"]["candidate"] == c]
            crit = [e["data"] for e in es_all if e["kind"] == "critic" and e["data"]["candidate"] == c]
            tag = next((e["data"] for e in es_all if e["kind"] == "note" and e["data"].get("stage") == "tagging"
                        and e["data"]["candidate"] == c), None)
            gate = next((e["data"] for e in es if e["kind"] == "gate" and e["data"]["candidate"] == c), None)
            evl = next((e["data"] for e in es if e["kind"] == "eval" and e["data"]["candidate"] == c), None)
            cs = f"{step}/{c}"
            # sealed information in any input the proposer / critic saw
            blob = json.dumps([p.get("prompt") for p in props] + [x.get("llm") for x in crit])
            hits = [i for i in sealed_ids if re.search(rf"(?<![\w-]){re.escape(i)}(?![\w-])", blob)]
            hits += [q for q in sealed_q if q in blob]
            rows.add(cs, "no holdout/ood id or question in proposer/critic inputs", not hits, f"hits={hits[:5]}")
            # what the proposer was shown: directives, L_t, B_t, E_t (initial call of this segment)
            for p in props[:1]:
                pr = p.get("prompt") or ""
                sec = dict(re.findall(r"--- CONTEXT: (\w+) ---\n(.*?)(?=\n--- CONTEXT: |\Z)", pr, re.S))
                try:
                    dirs = json.loads(sec.get("round_directives", "{}"))
                except json.JSONDecodeError:
                    dirs = {}
                ok_dirs = (dirs.get("b_t") == rs["b_t"] and dirs.get("untried") == rs["untried_U_t"]
                           and bool(dirs.get("reserved_slot")) == (c[-1] in rs["reserved_variants"])
                           and sorted(dirs.get("prune_components") or []) == sorted(x["component"] for x in rs["prune_B_t"]))
                rows.add(cs, "proposer saw b_t, U_t, reserved flag, B_t of this round", ok_dirs,
                         f"prompt directives b_t={dirs.get('b_t')} reserved={dirs.get('reserved_slot')} "
                         f"prune={dirs.get('prune_components')}")
                numbers = {k: dirs[k] for k in ("delta", "S_star", "S_incumbent", "T") if k in dirs}
                if cfg.get("proposer_numbers"):
                    rows.add(cs, "proposer saw S*, delta (Config.proposer_numbers extension)",
                             abs(dirs.get("S_star", -1) - rs["S_star"]) < 1e-5 and abs(dirs.get("delta", -1) - delta) < 1e-5,
                             f"{numbers}")
                else:
                    # the released proposer context never shows the noise band, S* or the incumbent's score
                    rows.add(cs, "proposer was NOT shown delta, S*, S_t or T (code: symbolic rules only)",
                             not numbers if post_fix else "questionable",
                             f"numeric thresholds in the prompt: {numbers}" + ("" if post_fix else
                             " (pre-fix code: claims audit N2, fixed)"))
                hs = sec.get("edit_history", "")
                try:
                    shown = json.loads(hs.split("\n", 1)[1]) if "\n" in hs else []
                except json.JSONDecodeError:
                    shown = None
                # the code renders EVERY record, the BASELINE row included (it counts as unmeasured)
                recs = [r for r in hist if (r.get("edit_id") and r["t"] < t) or r.get("outcome") == "BASELINE"]
                exp, unm = [], 0
                for r in reversed(recs):
                    if r.get("delta_S") is None:
                        unm += 1
                        if unm > 4:
                            continue
                    exp.append(r)
                    if len(exp) >= cfg["history_render_n"]:
                        break
                exp = [(r["t"], r["variant"], r.get("edit_id")) for r in reversed(exp)]
                got = None if shown is None else [(r.get("t"), r.get("variant"), r.get("edit_id")) for r in shown]
                ok_hist = got == exp
                if not ok_hist and not post_fix and got == [x for x in exp if x[2] is not None]:
                    ok_hist = "questionable"                       # pre-fix render dropped BASELINE (claims N5)
                rows.add(cs, "proposer saw the evidence-aware history L_t (all measured edits of rounds < t, "
                             "BASELINE row included as in the code)", ok_hist,
                         f"shown {len(got or [])} rows, expected {len(exp)}")
            # redraws of ideas falsified earlier (mocks tag ideas as [aq:...] / [xxx_nn])
            done_all = [p for p in props if str(p.get("outcome", "")).startswith("done")]
            if done_all:
                ids = set()
                for e in done_all[-1].get("declared_edits") or []:
                    h = str(e.get("hypothesis") or "")
                    if not h.startswith("prune:"):
                        ids.update(re.findall(r"\[((?:aq:[a-z_]+)|(?:[a-z]{3}_\d\d))\]", h))
                solo = {x for r in hist if r["t"] < t and r.get("outcome") == "REJECTED" and r.get("bundle") == 1
                        for x in re.findall(r"\[((?:aq:[a-z_]+)|(?:[a-z]{3}_\d\d))\]", str(r.get("hypothesis")))}
                redraw = sorted(ids & solo)
                if redraw:
                    rows.add(cs, "no unchanged redraw of an idea falsified ALONE earlier (L_t header rule)",
                             "questionable", f"redrawn: {redraw}")
            # budget on the final declared edits
            done = [p for p in props if str(p.get("outcome", "")).startswith("done")]
            if done:
                n_ed = len(done[-1].get("declared_edits") or [])
                rows.add(cs, "declared edits <= b_t", n_ed <= rs["b_t"], f"{n_ed} edits, b_t={rs['b_t']}")
            bounces = [p for p in props if "bounce" in str(p.get("outcome", "")) or
                       str(p.get("outcome", "")).startswith("rejected")]
            # final accepted text must not carry evolve answers literally (>= 3 chars, in added lines)
            if tag is not None and evl is not None:
                diff = (done[-1] if done else props[-1]).get("diff") or ""
                added = "\n".join(l for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++"))
                leaked = [f"{tid}={a}" for tid, a in evolve_ans.items()
                          if len(a) >= 3 and re.search(rf"(?<![\d.]){re.escape(a)}(?![\d.])", added)]
                ids_in = [tid for tid in evolve_ans if tid in added]
                rows.add(cs, "evaluated diff carries no evolve answer / task id", not leaked and not ids_in,
                         f"answers={leaked[:4]} ids={ids_in[:4]}")
            # claimed change vs actual diff: every declared (non-prune) mock idea must be visible in the diff
            if tag is not None and evl is not None and done:
                diff = done[-1].get("diff") or ""
                added = "\n".join(l for l in diff.splitlines() if l.startswith("+"))
                marks = {"aq:self_consistency": "[rrsi:sc3]", "aq:python_tool": "[rrsi:tool]",
                         "aq:checker": "[rrsi:checker]", "aq:answer_lookup": "[rrsi:lookup]",
                         "aq:python_skill": "skills/python.md"}
                missing = []
                for e in done[-1].get("declared_edits") or []:
                    h = str(e.get("hypothesis") or "")
                    if h.startswith("prune:"):
                        continue
                    for idea in re.findall(r"\[((?:aq:[a-z_]+)|(?:[a-z]{3}_\d\d))\]", h):
                        mark = marks.get(idea, None if idea.startswith("aq:") else idea)
                        if mark and mark not in added:
                            missing.append(idea)
                if any(re.search(r"\[(aq:[a-z_]+|[a-z]{3}_\d\d)\]", str(e.get("hypothesis"))) for e in
                       done[-1].get("declared_edits") or []):
                    rows.add(cs, "every declared edit is present in the diff (no phantom credit in L_t)", not missing,
                             f"declared but absent from the diff: {missing}" if missing else "all present")
            # critic before evaluation
            final_accept = bool(crit) and crit[-1].get("accept")
            if evl is not None:
                rows.add(cs, "evaluated only after a final critic accept", final_accept,
                         f"critic trail={[('A' if x.get('accept') else 'R') + ':' + str(x.get('stage')) for x in crit]}")
            elif crit and not final_accept:
                rows.add(cs, "critic-rejected candidate never evaluated", evl is None,
                         f"{len(crit)} review(s), last={crit[-1].get('stage')}")
            # ---- evaluation & Algorithm 2 --------------------------------------------------
            if gate is None:
                continue
            m = gate.get("math") or {}
            if evl is None:
                rows.add(cs, "unevaluated candidate is inadmissible", not gate["accept"],
                         f"gate reason={gate['reason'][:80]}")
                continue
            S1, C1 = s_hat(evl["trials"]), c_hat(evl["tokens"])
            dj = json.loads((out / "evals" / f"{evl['summary']['job']}.json").read_text())
            rows.add(cs, "S' from raw trials == traced == evals/<job>.json",
                     abs(S1 - evl["summary"]["S"]) < 1e-9 and abs(S1 - dj["S"]) < 1e-9 and abs(S1 - m["S_prime"]) < 1e-9,
                     f"S'={S1:.6f} traced={evl['summary']['S']:.6f}")
            dS = S1 - inc_S
            dC = (C1 - inc_C) / inc_C if (C1 and inc_C) else 0.0
            nu_comps = {r["component"] for r in hist if r["t"] == t and r["variant"] == c[-1] and r.get("edit_id")}
            acc_before = {r["component"] for r in hist if r.get("accepted") and r.get("edit_id") and r["t"] < t}
            nu = sum(1 for x in nu_comps if x in K_str and x not in acc_before)
            floor_ok = S1 >= S_star - delta - eps
            if dS > delta + eps:
                branch, lim = "cost_rule", cfg["beta0"] + cfg["beta1"] * dS
                c_ok = dC <= lim + eps
                why = f"dC={dC:+.4f} <= {lim:.4f}"
            else:
                branch = "shaped"
                sh = cfg["w_s"] * dS - cfg["w_c"] * dC + cfg["w_n"] * nu
                c_ok = sh > eps
                why = f"shaped={cfg['w_s']}*{dS:+.4f} - {cfg['w_c']}*{dC:+.4f} + {cfg['w_n']}*{nu} = {sh:+.3f}"
            adm = floor_ok and c_ok
            cand_S[c] = (S1, C1, adm)
            rows.add(cs, "Algorithm 2 recomputed (floor on S*, cost/shaped rule, nu)",
                     adm == gate["accept"] and abs(dS - m["dS"]) < 1e-9 and abs(dC - m["dC"]) < 1e-9 and nu == m["nu"],
                     f"S'={S1:.4f} S_t={inc_S:.4f} dS={dS:+.4f} dC={dC:+.4f} floor={S_star - delta:.4f} "
                     f"({'ok' if floor_ok else 'FAIL'}) {branch}: {why} nu={nu} -> {'adm' if adm else 'rej'}; "
                     f"loop said {'adm' if gate['accept'] else 'rej'}")
            hrec = [r for r in hist if r["t"] == t and r["variant"] == c[-1] and r.get("edit_id")]
            rows.add(cs, "history record matches (S, dS, dC, outcome)",
                     all(abs(r["S"] - round(S1, 6)) < 1e-6 and abs(r["delta_S"] - round(dS, 6)) < 1e-6 for r in hrec)
                     and all((r["outcome"] in ("ACCEPTED", "LOST")) == adm for r in hrec),
                     f"{[(r['edit_id'], r['component'], r['outcome']) for r in hrec]}")
        # ---- decision -----------------------------------------------------------------------
        dec = next(e["data"] for e in es if e["kind"] == "decision")
        adm = [(c, v[0]) for c, v in cand_S.items() if v[2] and c.startswith(step)]
        win = None
        for c, s in adm:                                            # first on ties, variant order
            if win is None or s > win[1] + eps:
                win = (c, s)
        rows.add(step, "winner = argmax S' over admissible (else keep H_t)",
                 (win[0] if win else None) == dec["kept"], f"expected {win[0] if win else None}, loop kept {dec['kept']}")
        if win:
            inc_name, inc_S, inc_C = win[0], cand_S[win[0]][0], cand_S[win[0]][1]
            S_star = max(S_star, inc_S)
        rows.add(step, "S* after = max(S*, S_{t+1})", abs(dec["S_star_after"] - S_star) < 1e-9,
                 f"S*={S_star:.4f} traced={dec['S_star_after']:.4f}")
        traj.append(inc_S)
        trajC.append(inc_C)
        cand_S = {k: v for k, v in cand_S.items() if not k.startswith(step)}
    # ---- monitor ----------------------------------------------------------------------------
    mons = [e for e in ev if e["kind"] == "monitor"]
    kept = ["H0"] + [next(e["data"]["kept"] for e in last_seg if e["kind"] == "decision" and e["round"] == t)
                     for t in rounds]
    kept = [k for k in kept if k]
    rows.add("monitor", "one shadow evaluation per new incumbent (H0 + each keep)",
             sorted({m["data"]["version"] for m in mons}) == sorted(set(kept)),
             f"monitor versions={[m['data']['version'] for m in mons]}; keeps={kept}")
    summ = {s: sum(1 for r in rows if r["status"] == s) for s in ("correct", "questionable", "wrong", "unverifiable")}
    res = {"run": run, "summary": summ, "rows": rows}
    (out / "stepcheck.json").write_text(json.dumps(res, indent=1, default=str))
    return res


def main() -> None:
    runs = sys.argv[1:] or ["offline_agentqa", "offline_harnessworld", "live_agentqa"]
    for r in runs:
        res = check_run(r)
        print(r, res["summary"])
        for row in res["rows"]:
            if row["status"] != "correct":
                print("  ", row["status"].upper(), row["step"], "|", row["check"], "|", row["evidence"][:220])


if __name__ == "__main__":
    main()
