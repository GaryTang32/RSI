"""Independent audit of a traced RRSI run: re-derive every step of ``trace.jsonl`` with the paper's formulas."""
from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Optional

from ..trace import load_trace

EPS = 1e-9


class _Audit:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def add(self, check: str, status: str, detail: str = "", t: Optional[int] = None,
            candidate: Optional[str] = None, **extra: Any) -> None:
        self.rows.append({"check": check, "status": status, "round": t, "candidate": candidate, "detail": detail,
                          **extra})

    def eq(self, check: str, got, want, t=None, candidate=None, tol: float = 1e-6, detail: str = "") -> bool:
        if got is None or want is None:
            self.add(check, "unverifiable", f"missing value (got={got}, want={want}) {detail}", t, candidate)
            return False
        if isinstance(want, (int, float)) and isinstance(got, (int, float)):
            ok = abs(float(got) - float(want)) <= tol
        else:
            ok = got == want
        self.add(check, "pass" if ok else "fail", f"recorded={got!r} re-derived={want!r} {detail}".strip(), t,
                 candidate)
        return ok


def _mean_trials(trials: dict) -> Optional[float]:
    vals = [x for rs in trials.values() for x in rs]
    return sum(vals) / len(vals) if vals else None


def _plugin_sd_null(trials: dict) -> Optional[float]:
    """sqrt(2 * sum_i var_i / k) / n with the plug-in (population) variance of each task."""
    rows = [rs for rs in trials.values() if len(rs) >= 2]
    if not rows or len(rows) != len(trials):
        return None
    v = 0.0
    for rs in rows:
        k = len(rs)
        mu = sum(rs) / k
        v += sum((x - mu) ** 2 for x in rs) / k / k
    return math.sqrt(2.0 * v) / len(rows)


def budget_formula(t: int, T: int, b_min: int, b_max: int, rounding: str = "ceil") -> int:
    """Eq. (anneal), written out independently of :mod:`rsi.rrsi.schedule`."""
    if rounding == "floor_at_bmin_last" and t >= T - 1:
        return b_min
    return int(math.ceil(round(b_min + (b_max - b_min) * 0.5 * (1.0 + math.cos(math.pi * t / T)), 9)))


def _first(evs: list[dict], kind: str, **match) -> Optional[dict]:
    return next((e["data"] for e in evs if e["kind"] == kind and all(e["data"].get(k) == v for k, v in match.items())),
                None)


def _audit_noise(A: _Audit, base: Optional[dict], noise: Optional[dict]) -> Optional[float]:
    if noise is None:
        return None
    cal = noise.get("calibration") or {}
    if cal:
        A.eq("delta = z * sd_null", cal.get("delta"), round(cal["z"] * cal["sd_null"], 6), tol=2e-6)
        if str(cal.get("method", "")).startswith("bootstrap") and base is not None:
            sd = _plugin_sd_null(base.get("trials") or {})
            if sd is None:
                A.add("bootstrap sd_null vs plug-in formula", "unverifiable", "k < 2: nothing to resample")
            else:
                rel = abs(cal["sd_null_bootstrap"] - sd) / sd if sd > 0 else abs(cal["sd_null_bootstrap"])
                A.add("bootstrap sd_null vs plug-in formula", "pass" if rel < 0.1 else "fail",
                      f"bootstrap={cal['sd_null_bootstrap']:.5f} plug-in={sd:.5f} (rel diff {rel:.3f}; "
                      "Monte-Carlo tolerance 10%)")
        elif str(cal.get("method", "")).startswith("repeated"):
            Ss = cal.get("S_per_eval") or []
            if len(Ss) >= 2:
                mu = sum(Ss) / len(Ss)
                sd = math.sqrt(sum((x - mu) ** 2 for x in Ss) / (len(Ss) - 1)) * math.sqrt(2.0)
                A.eq("repeated-evaluation sd_null = stdev(S_r) * sqrt(2)", cal.get("sd_null"), round(sd, 6),
                     tol=2e-6)
        if cal.get("warning"):
            A.add("noise band is non-degenerate", "fail", cal["warning"])
    return noise.get("delta")


def _audit_candidate(A: _Audit, t: int, c: str, evs: list[dict], rs: dict, cfg: dict, sw: dict, inc_S, S_star,
                     delta: float, eps: float, full_rules: bool) -> None:
    props = [e["data"] for e in evs if e["kind"] == "proposal" and e["data"]["candidate"] == c]
    dropped = (_first(evs, "note", stage="dropped before evaluation", candidate=c) or {}).get("gate_failure")
    for p in props:
        if str(p.get("outcome", "")) == "done: no file changes":
            A.add("proposer shipped nothing (done() with no edits and no file changes)", "info",
                  f"{p.get('call_kind') or 'call'}; the candidate was dropped as "
                  f"{dropped or 'no_proposal'}", t, c)
    for p in props:
        if not str(p.get("outcome", "")).startswith("done: accepted"):
            continue
        n = len(p.get("declared_edits") or [])
        A.add("shipped edits <= b_t", "pass" if n <= rs["b_t"] else "fail", f"{n} edits, b_t={rs['b_t']}", t, c)
        if p.get("reserved_slot") and rs.get("untried_U_t"):
            comps = [str(x).lower() for x in p.get("components") or []]
            # aliases count only when the run accepted them (older traces always did)
            alias = {"tool": "client_tool", "client_tool": "tool"} if cfg.get("component_aliases", True) else {}
            hit = any(x in rs["untried_U_t"] or alias.get(x) in rs["untried_U_t"] for x in comps)
            A.add("reserved slot declares an untried component", "pass" if hit else "fail",
                  f"declared {comps}, U_t={rs['untried_U_t']}", t, c)
        if p.get("n_changes") and not (p.get("diff") or "").strip():
            A.add("claimed change has a non-empty diff", "fail", "n_changes > 0 but empty diff", t, c)
    for p in props:
        if str(p.get("outcome", "")).startswith("bounced"):
            A.add("done() bounce", "info", str(p.get("outcome"))[:300], t, c)
    gate = _first(evs, "gate", candidate=c)
    ev = _first(evs, "eval", candidate=c)
    crit = [e["data"] for e in evs if e["kind"] == "critic" and e["data"]["candidate"] == c]
    tag = _first(evs, "note", stage="tagging", candidate=c)
    for row in (tag or {}).get("edits") or []:
        if row.get("retagged"):
            A.add("declared component re-tagged from the diff", "info",
                  f"{row.get('id')}: declared {row.get('declared')!r} -> {row.get('normalized')!r} "
                  f"(files {tag.get('files_in_diff')})", t, c)
    gf = (gate or {}).get("math", {}).get("gate_failure")
    if gf is not None:
        A.add("dropped candidate was not evaluated", "pass" if ev is None else "fail", f"gate failure {gf}", t, c)
        if gf == "critic_reject" and crit:
            A.add("critic_reject follows a rejecting final verdict", "pass" if not crit[-1].get("accept") else "fail",
                  "", t, c)
        return
    if ev is None:
        A.add("evaluated candidate has an eval event", "unverifiable" if gate is None else "fail", "", t, c)
        return
    if sw.get("critic"):
        A.add("evaluated candidate passed the critic", "pass" if crit and crit[-1].get("accept") else "fail",
              f"{len(crit)} review(s)", t, c)
    S_c = ev["summary"]["S"]
    A.eq("S' = mean of raw trials", S_c, _mean_trials(ev.get("trials") or {}), t, c)
    if gate is None:
        A.add("gate event present", "fail", "", t, c)
        return
    m = gate["math"]
    A.eq("gate S' = eval S'", m.get("S_prime"), S_c, t, c, tol=1e-12)
    A.eq("gate S_t = incumbent S", m.get("S_t"), inc_S, t, c, tol=1e-12)
    A.eq("gate S* = running max", m.get("S_star"), S_star, t, c, tol=1e-12)
    A.eq("gate delta = round delta", m.get("delta"), delta, t, c, tol=1e-12)
    dS = S_c - inc_S
    C_c, C_i = ev["summary"].get("C"), m.get("C_t")
    dC = (C_c - C_i) / C_i if C_c and C_i else 0.0
    A.eq("dS = S' - S_t", m.get("dS"), dS, t, c, tol=1e-9)
    A.eq("dC = (C' - C_t) / C_t", m.get("dC"), dC, t, c, tol=1e-9)
    if not full_rules:
        A.add("admissible = Alg. 2 re-derived", "unverifiable",
              f"ablation switches / guards active ({sw.get('name')}); loop said {gate['accept']}", t, c)
        return
    floor_ok = S_c >= S_star - delta - eps
    if dS > delta + eps:
        lim = cfg["beta0"] + cfg["beta1"] * dS
        c_ok, rule = dC <= lim + eps, f"cost rule dC={dC:+.4f} <= {lim:.4f}"
    else:
        sh = cfg["w_s"] * dS - cfg["w_c"] * dC + cfg["w_n"] * (m.get("nu") or 0)
        c_ok, rule = sh > eps, f"shaped {cfg['w_s']}*{dS:+.4f} - {cfg['w_c']}*{dC:+.4f} + " \
                               f"{cfg['w_n']}*{m.get('nu')} = {sh:+.4f} > 0"
    want = bool(floor_ok and c_ok)
    A.add("admissible = Alg. 2 re-derived", "pass" if want == gate["accept"] else "fail",
          f"floor {S_c:.4f} >= {S_star - delta:.4f}: {floor_ok}; {rule}: {c_ok} -> {want}; "
          f"loop said {gate['accept']} ({gate.get('reason', '')[:160]})", t, c)


def _audit_split(A: _Audit, events: list[dict], domain) -> None:
    """Sealed holdout / ood task ids and questions never appear in what the search LLMs read."""
    sealed = [tk for s in ("holdout", "ood", "test") if s in domain.tasks.splits
              for tk in domain.tasks.split(s, allow_sealed=True)]
    needles = []
    for tk in sealed:
        needles.append(tk.id)
        if isinstance(tk.input, str) and len(tk.input) >= 16:
            needles.append(tk.input[:60])
    evolve_inputs = {str(tk.input)[:60] for tk in domain.tasks.split("evolve")}
    needles = [n for n in needles if n not in evolve_inputs]
    texts = []
    for e in events:
        d = e["data"]
        if e["kind"] == "proposal":
            texts.append(("proposer prompt", e.get("round"), d.get("candidate"), d.get("prompt") or ""))
        elif e["kind"] == "analysis":
            for call in d.get("llm_calls") or []:
                texts.append(("analyst prompt", e.get("round"), call.get("role"), call.get("prompt") or ""))
        elif e["kind"] == "critic" and d.get("llm"):
            texts.append(("critic payload", e.get("round"), d.get("candidate"), (d["llm"] or {}).get("payload") or ""))
    hits = [(w, r, c, n) for (w, r, c, txt) in texts for n in needles if n and n in txt]
    A.add("sealed holdout/ood tasks never appear in search-LLM inputs", "pass" if not hits else "fail",
          f"{len(texts)} prompts scanned for {len(needles)} sealed ids/questions; hits: {hits[:5]}")


def audit_events(events: list[dict], domain=None) -> dict:
    A = _Audit()
    start = next((e["data"] for e in events if e["kind"] == "run_start"), None)
    if start is None:
        A.add("run_start present", "fail", "no run_start event")
        return {"summary": _summ(A.rows), "checks": A.rows}
    cfg, sw = start["config"], start["switches"]
    K = start.get("K") or []
    # the run's Alg. 2 tie tolerance; traces older than Config.tie_eps used rsi.core's TIE_EPS
    eps = float(cfg["tie_eps"]) if "tie_eps" in cfg else EPS
    full_rules = sw.get("selection") == "rrsi" and sw.get("floor") == "S_star" and sw.get("cost_rule") \
        and sw.get("within_band") == "shaped" and not start.get("guards")
    if sw.get("budget_anneal"):
        want = [budget_formula(t, cfg["T"], cfg["b_min"], cfg["b_max"], cfg.get("budget_rounding", "ceil"))
                for t in range(cfg["T"])]
        A.eq("b_t schedule = Eq. (anneal)", start.get("b_t_schedule"), want)
    base = next((e["data"] for e in events if e["kind"] == "baseline"), None)
    if base is not None:
        A.eq("baseline S = mean of raw trials", base["summary"]["S"], _mean_trials(base.get("trials") or {}))
    delta_used = _audit_noise(A, base, next((e["data"] for e in events if e["kind"] == "noise"), None))

    by_round: dict[int, list[dict]] = {}
    for e in events:
        if e.get("round") is not None:
            by_round.setdefault(e["round"], []).append(e)
    tried: set = set()
    traj: list[float] = [base["summary"]["S"]] if base is not None else []
    inc_name, inc_S = "H0", (base["summary"]["S"] if base else None)
    S_star = inc_S
    truth_inc = (base or {}).get("ground_truth")
    seen_incumbents = {(base or {}).get("summary", {}).get("artifact"): "H0"} if base else {}
    monitored = {e["data"].get("artifact") for e in events if e["kind"] == "monitor"}
    for t in sorted(by_round):
        evs = by_round[t]
        rs = _first(evs, "round_start")
        if rs is None:
            A.add("round_start present", "fail", "no round_start event", t)
            continue
        delta = rs["delta"]
        if delta_used is not None:
            A.eq("delta constant across rounds", delta, delta_used, t)
        A.eq("incumbent at round start = last decision's incumbent", rs["incumbent"]["node"], inc_name, t)
        A.eq("S_t = measured S of the incumbent", rs["incumbent"]["S"], inc_S, t, tol=1e-9)
        A.eq("S* = running max of incumbent scores", rs["S_star"], S_star, t, tol=1e-9)
        A.eq("trajectory = incumbent score per settled round", [round(x, 9) for x in rs["trajectory_S"]],
             [round(x, 9) for x in traj], t)
        if sw.get("budget_anneal"):
            A.eq("b_t = Eq. (anneal)", rs["b_t"], budget_formula(t, cfg["T"], cfg["b_min"], cfg["b_max"],
                                                                 cfg.get("budget_rounding", "ceil")), t)
        if sw.get("stall_exploration"):
            w = cfg["w"]
            A.eq("sigma_t = 1[S_t - S_{t-w} <= delta]", rs["sigma_t"],
                 0 if t < w else int(traj[t] - traj[t - w] <= delta), t)
            A.eq("T_t = components of measured edits so far", sorted(rs["tried_T_t"]), sorted(tried), t)
            A.eq("U_t = K minus T_t", rs["untried_U_t"], [c for c in K if c not in tried], t)
            want_res = [chr(65 + v) for v in range(cfg["m"]) if rs["sigma_t"] and rs["untried_U_t"]
                        and v >= cfg["m"] - cfg["m_draft"]]
            A.eq("reserved variants = last m_draft when stalled", rs["reserved_variants"], want_res, t)
        for c in sorted({e["data"]["candidate"] for e in evs if e["kind"] in ("proposal", "gate")}):
            _audit_candidate(A, t, c, evs, rs, cfg, sw, inc_S, S_star, delta, eps, full_rules)

        dec = _first(evs, "decision")
        if dec is None:
            A.add("decision present", "fail", "", t)
            continue
        gates = [e["data"] for e in evs if e["kind"] == "gate"]
        adm = [(g["candidate"], g["math"]["S_prime"]) for g in gates if g.get("accept")]
        best = max((s for _, s in adm), default=None)
        want_w = next((n for n, s in adm if s == best), None)
        A.add("winner = argmax S' over admissible (first on ties)",
              "pass" if dec.get("kept") == want_w else "fail",
              f"recorded={dec.get('kept')!r} re-derived={want_w!r} from admissible {adm}", t)
        prev_star = S_star
        if want_w is not None:
            new_S = next(s for n, s in adm if n == want_w)
            evd = _first(evs, "eval", candidate=want_w)
            truth_new = (evd or {}).get("ground_truth")
            if truth_inc and truth_new and "E_S" in truth_inc and "E_S" in truth_new:
                d_ev = truth_new["E_S"]["evolve"] - truth_inc["E_S"]["evolve"]
                d_ood = truth_new["E_S"]["ood"] - truth_inc["E_S"]["ood"]
                leaks = [x["id"] for x in truth_new.get("mechanisms", []) if x.get("payload_live")]
                A.add("kept version: analytic ground truth", "info",
                      f"measured dS={new_S - inc_S:+.4f}; true dE[S] evolve {d_ev:+.4f}, holdout "
                      f"{truth_new['E_S']['holdout'] - truth_inc['E_S']['holdout']:+.4f}, ood {d_ood:+.4f}; "
                      f"live leak payloads {leaks}", t, want_w,
                      true_d_evolve=d_ev, true_d_ood=d_ood, measured_dS=new_S - inc_S)
                truth_inc = truth_new
            art = ((evd or {}).get("summary") or {}).get("artifact")
            if art in seen_incumbents:
                A.add("kept version is byte-identical to an earlier incumbent", "info",
                      f"{want_w} == {seen_incumbents[art]} (artifact {art}): re-measured dS={new_S - inc_S:+.4f} "
                      f"comes from evaluation noise alone; S* {S_star:.4f} -> {max(S_star, new_S):.4f}", t, want_w)
            seen_incumbents.setdefault(art, want_w)
            inc_name, inc_S = want_w, new_S
            S_star = max(S_star, new_S)
        A.eq("S* after = max(S*, S_{t+1})", dec.get("S_star_after"), S_star, t, tol=1e-12)
        A.eq("incumbent after", dec.get("incumbent_after"), inc_name, t)
        if prev_star is not None and S_star < prev_star - EPS:
            A.add("S* never decreases", "fail", f"{prev_star} -> {S_star}", t)
        traj.append(inc_S)
        for g in gates:                                  # T_t gains this round's measured components
            if "gate_failure" in (g.get("math") or {}):
                continue
            for row in (_first(evs, "note", stage="tagging", candidate=g["candidate"]) or {}).get("edits") or []:
                if row.get("normalized") in K:
                    tried.add(row["normalized"])
        st = _first(evs, "state")
        if st is not None:
            A.eq("state incumbent = decision incumbent", st["incumbent"]["node"], inc_name, t)
        mon = [e["data"] for e in evs if e["kind"] == "monitor"]
        if dec.get("kept") and start.get("shadow_monitor"):
            kept_art = (_first(evs, "eval", candidate=dec["kept"]) or {"summary": {}})["summary"].get("artifact")
            ok = bool(mon) or kept_art in monitored
            A.add("shadow monitor scored the new incumbent", "pass" if ok else "fail",
                  "; ".join(f"{s}: S={v.get('S'):.4f}" for m_ in mon for s, v in m_.get("sealed", {}).items())
                  or f"artifact {kept_art} already scored earlier (the monitor de-duplicates identical versions)", t)
    if domain is not None:
        _audit_split(A, events, domain)
    return {"summary": _summ(A.rows), "checks": A.rows}


def _summ(rows: list[dict]) -> dict:
    return {"pass": 0, "fail": 0, "unverifiable": 0, "info": 0, **Counter(r["status"] for r in rows)}


def audit_run(run_dir: str | Path, domain=None, write: bool = True) -> dict:
    """Audit ``run_dir/trace.jsonl``; writes ``run_dir/audit.json`` when ``write``."""
    rep = audit_events(load_trace(run_dir), domain=domain)
    if write:
        Path(run_dir, "audit.json").write_text(json.dumps(rep, indent=1, default=str))
    return rep
