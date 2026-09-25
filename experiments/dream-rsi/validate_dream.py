"""From-scratch validation runs of Dream-RSI with the per-iteration audit trace.

Each run starts from the untouched seed artifact of its domain, in a fresh run directory
``validation/dream-rsi/<run>/`` (deleted first) and - for the live run - a fresh LLM cache
``validation/dream-rsi/.cache_<run>/`` (deleted first). The loop writes ``trace.jsonl`` (every live
cycle: loop state, each online decision round, each agent attempt with prompt / reply / actual
diff / locked evaluation, the best-program gate + decision, the manifest, the dreaming phase with
every policy version's proposal, screen, replay evaluation, the selection arithmetic and decision)
and, when the domain has sealed splits, the shadow monitor's scores of every new best program.
This script then

* renders ``TRACE.md`` with :func:`rsi.trace.inspect`;
* writes ``report.json``: seed vs final via :func:`rsi.core.transfer_report` (evolve, and holdout /
  ood when the domain has them), spend from the meters;
* writes ``audit.json`` + ``audit.md``: an INDEPENDENT re-derivation from the files on disk only
  (``trace.jsonl``, ``trace_pool/``, ``snapshots/``, ``history/``): re-grading of every attempt,
  diffs from the snapshot store, online-batch legality, live root = best so far, best-program
  rule, replay fidelity (re-replaying each world with the policy that recorded it), Eq.-1
  arithmetic, re-replaying every policy version, the argmax selection, the developer's diffs and
  claims, the no-leak screen, the call budget - and (offline only) a ground-truth check of each
  dreaming decision by fresh online searches of every version.

Runs::

    python experiments/dream-rsi/validate_dream.py sumdiff_offline    # mock agent + ParametricMutator
    python experiments/dream-rsi/validate_dream.py agentqa_offline    # AgentQA harness, SimModel (sealed splits)
    python experiments/dream-rsi/validate_dream.py sumdiff_live       # claude haiku: agent AND developer
    python experiments/dream-rsi/validate_dream.py <run> --audit-only # re-audit an existing run directory
"""
from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from rsi.core import CachedLLM, ClaudeCLI, paired_diff_ci, spearman, transfer_report  # noqa: E402
from rsi.core.artifact import Artifact  # noqa: E402
from rsi.core.sandbox import call_function  # noqa: E402
from rsi.dream import Config, run  # noqa: E402
from rsi.trace import inspect, load_trace  # noqa: E402

OUT = ROOT / "validation" / "dream-rsi"


# ------------------------------------------------------------------------------------ runs --
def setup(name: str, reuse_cache: bool = False) -> dict:
    from rsi.dream import EditorAgent, LLMPolicyDeveloper, ParametricMutator

    if name == "sumdiff_offline":
        from rsi.domains.discovery import SumDiffDomain
        dom = SumDiffDomain(sandboxed=True)
        cfg = Config(rounds=4, W=3, branch_count=3, refine_count=4, M=4, seed=0, sandbox="subprocess")
        return dict(domain=dom, task=dom, seed=dom.seed_artifact(), agent=dom.mock_agent(),
                    developer=ParametricMutator(), llm=None, llm_task=None, config=cfg, cache=None,
                    kind="sumdiff", ground_truth=True,
                    setup="Sum-difference (maximize Gamma(A)), untouched seed construct.py (INIT = 12-element set, "
                          "no stages; Gamma = 0.9105). Discovery agent = the domain's offline mock agent (each attempt "
                          "appends one search stage of the branch's direction to the parent's recipe); policy "
                          "developer = ParametricMutator (offline mock). pi_1 = parallel refine on a 3 x 5 grid "
                          "(branch_count 3, refine_count 4), W = 3, T = 4 live cycles, M = 4 versions per dreaming "
                          "phase (incumbent + 3 revisions), Eq. 1 with beta1 = 0.01, beta2 = 0.005, per-world "
                          "normalization, argmax selection, root_mode earliest, policies in the subprocess sandbox, "
                          "programs graded in the subprocess sandbox.")
    if name == "agentqa_offline":
        from rsi.domains.agentqa import AgentQADomain, SimModel, make_suite
        from rsi.domains.discovery.agentqa_agent import MECHANISMS, agentqa_mock_agent
        from rsi.dream import DomainTask
        suite = make_suite(n_evolve=8, n_val=4, n_holdout=8, n_ood_per_family=2, seed=0)
        dom = AgentQADomain(suite)
        sim = SimModel(suite)
        task = DomainTask(dom, dom.seed_artifact(), sim, directions=MECHANISMS, workers=2)
        cfg = Config(rounds=4, W=3, branch_count=3, refine_count=2, M=4, seed=0, sandbox="subprocess")
        return dict(domain=dom, task=task, seed=dom.seed_artifact(), agent=agentqa_mock_agent(),
                    developer=ParametricMutator(), llm=None, llm_task=sim, config=cfg, cache=None,
                    kind="agentqa", ground_truth=True,
                    setup="AgentQA (shared harness domain) through DomainTask: untouched AgentQADomain.seed_artifact() "
                          "(one direct model call, 'You are a helpful assistant.'); score = mean evolve S (8 numeric "
                          "tasks, k = 1) with SimModel; sealed holdout (8) + ood (8: 2 per unseen family) scored only "
                          "by the shadow monitor and the final transfer report. Agent = agentqa_mock_agent (one "
                          "mechanism knob per attempt), developer = ParametricMutator; pi_1 = parallel refine 3 x 3, "
                          "W = 3, T = 4, M = 4, Eq. 1 defaults, argmax.")
    if name == "sumdiff_live":
        from rsi.domains.discovery import SumDiffDomain
        dom = SumDiffDomain(sandboxed=True)
        cache = OUT / f".cache_{name}"
        if cache.exists() and not reuse_cache:
            shutil.rmtree(cache)          # from scratch: a fresh LLM cache
        llm = CachedLLM(ClaudeCLI("haiku", timeout_s=300), cache)
        cfg = Config(rounds=3, W=3, branch_count=3, refine_count=3, hard_max_branch=4, hard_max_refine=3, M=3,
                     m_semantics="revisions", seed=0, sandbox="subprocess", policy_timeout_s=60.0, max_calls=40)
        return dict(domain=dom, task=dom, seed=dom.seed_artifact(), llm=llm, llm_task=None,
                    agent=EditorAgent(llm, editable=[dom.program_file]), developer=LLMPolicyDeveloper(llm),
                    config=cfg, cache=cache, kind="sumdiff", ground_truth=False,
                    setup="Sum-difference, untouched seed construct.py (Gamma = 0.9105). Discovery agent = "
                          "EditorAgent (Listing-1 prompt, one completion rewrites construct.py) and policy developer "
                          "= LLMPolicyDeveloper (Listing-2 prompt, static check + leakage screen + 1 repair round), "
                          "BOTH claude haiku (ClaudeCLI('haiku') behind a fresh CachedLLM). pi_1 = parallel refine on "
                          "a 3 x 4 grid (depth <= 4: refine_count 3), hard caps 4 branches x 3 refinements, W = 3, "
                          "T = 3 live cycles, M = 3 dreaming revisions per phase (m_semantics='revisions': incumbent + "
                          "3 LLM revisions), Eq. 1 defaults, argmax, max_calls = 40 agent calls, candidate programs "
                          "and policies in subprocess sandboxes.")
    raise SystemExit(f"unknown run {name!r}")


def execute(name: str, s: dict, out: Path) -> dict:
    if out.exists():
        shutil.rmtree(out)
    t0 = time.time()
    res = run(s["task"], s["seed"], llm_task=s["llm_task"], agent=s["agent"], developer=s["developer"],
              config=s["config"], out_dir=str(out))
    wall = time.time() - t0
    md = inspect(out)
    # ---- report: seed vs final on every available split (report-only unsealing)
    dom = s["domain"]
    splits = [sp for sp in ("evolve", "holdout", "ood") if sp in dom.tasks.splits and dom.tasks.splits[sp]]
    rep_llm = None
    if s["llm_task"] is not None:
        from rsi.dream.tracing import ShadowLLM
        rep_llm = ShadowLLM(s["llm_task"])            # metered apart from the loop
    tr = transfer_report(dom, rep_llm, {"seed": s["seed"], "final": res.best}, splits=splits, k=1, workers=2)
    usage = {k: v for k, v in res.usage.items() if not k.startswith("_")}
    loop_usd = float(sum(v.get("cost_usd", 0.0) for v in usage.values()))
    shadow = res.meta.get("shadow_usage") or {}
    report = {
        "run": name, "setup": s["setup"], "config": asdict(s["config"]), "wall_s": round(wall, 1),
        "seed_score": res.meta["seed_score"], "best_score": res.meta["best_score"], "stop_reason": res.stop_reason,
        "calls_per_cycle": [r["calls"] for r in res.trajectory],
        "best_per_cycle": [r["best"] for r in res.trajectory],
        "transfer": tr, "usage_by_role": usage, "cost_meter": res.usage.get("_cost"),
        "spend": {"loop_usd": round(loop_usd, 4),
                  "shadow_monitor_usd": round(float((shadow.get("_total") or {}).get("cost_usd", 0.0)), 4),
                  "report_usd": round(float(rep_llm.meter.total().cost_usd), 4) if rep_llm is not None else 0.0},
        "llm_cache": ({"hits": s["llm"].hits, "misses": s["llm"].misses} if s["llm"] is not None else None),
        "trace_md": str(md),
    }
    (out / "report.json").write_text(json.dumps(report, indent=1, default=str))
    return report


# ---------------------------------------------------------------------------------- audit --
def gamma_indep(A) -> float:
    """Gamma(A) = log(|A+A|/|A|) / log(|A-A|/|A|), written independently of the domain (pure sets)."""
    A = sorted(set(int(a) for a in A))
    n = len(A)
    if n < 2:
        return 0.0
    s = {a + b for a in A for b in A}
    d = {a - b for a in A for b in A}
    if len(d) <= n or len(s) <= n:
        return 0.0
    return math.log(len(s) / n) / math.log(len(d) / n)


def regrade_sumdiff(prog: Artifact, max_size: int = 400, max_span: int = 20000) -> tuple:
    code = prog.get("construct.py")
    if code is None:
        return None, "construct.py missing"
    out, rr = call_function(code, "construct", {}, timeout_s=60)
    if out is None:
        return 0.0, "no output (" + ("timeout" if rr.timed_out else "error") + ")"
    try:
        A = [int(v) for v in out]
        if len(set(A)) != len(A) or not 2 <= len(A) <= max_size or max(A) - min(A) > max_span:
            return 0.0, "constraint"
        if any(float(v) != int(v) for v in out):
            return 0.0, "non-integer"
    except Exception as e:  # noqa: BLE001
        return 0.0, f"bad output {type(e).__name__}"
    return gamma_indep(A), "ok"


def _load_run(out: Path) -> dict:
    from rsi.dream.tree import DiscoveryTree, SnapshotStore

    ev = load_trace(out)
    worlds, mans = [], []
    t = 1
    while (out / f"trace_pool/iter{t:02d}/tree.json").exists():
        worlds.append(DiscoveryTree.load(out / f"trace_pool/iter{t:02d}/tree.json"))
        mans.append(json.loads((out / f"trace_pool/iter{t:02d}/live_cycle_manifest.json").read_text()))
        t += 1
    hist = {}
    for d in sorted((out / "history").glob("r*_*")):
        hist[int(d.name[1:5])] = {"label": d.name[6:], "code": (d / "method.py").read_text(),
                                  "report": json.loads((d / "report.json").read_text())
                                  if (d / "report.json").exists() else None}
    return {"ev": ev, "worlds": worlds, "manifests": mans, "history": hist, "store": SnapshotStore(out / "snapshots"),
            "summary": json.loads((out / "summary.json").read_text()), "trajectory":
            json.loads((out / "trajectory.json").read_text())}


def _by(ev, kind, rnd=None, **match):
    out = []
    for e in ev:
        if e["kind"] != kind or (rnd is not None and e["round"] != rnd):
            continue
        if all(e["data"].get(k) == v for k, v in match.items()):
            out.append(e["data"])
    return out


def same_text(traced: str, full: str) -> bool:
    """The trace clips long text (rsi.trace.MAX_TEXT): a clipped value must be a prefix of the full one."""
    mark = "\n...[truncated "
    if mark in (traced or ""):
        return full.startswith(traced.split(mark, 1)[0])
    return traced == full


def _check(rows: list, name: str, ok, detail: str = "") -> None:
    rows.append({"check": name, "result": ("PASS" if ok is True else "FAIL" if ok is False else str(ok)),
                 "detail": detail})


def audit(name: str, s: dict, out: Path) -> dict:
    from rsi.dream.developer import default_beta_of
    from rsi.dream.evaluator import ReplayEvaluator
    from rsi.dream.guard import get_runner, static_check
    from rsi.dream.objectives import Eq1Objective
    from rsi.dream.policy_api import GridPlan
    from rsi.dream.question import ReplayQuestion, program_only
    from rsi.dream.tree import ROOT_ID

    R = _load_run(out)
    ev, worlds, mans, hist, store = R["ev"], R["worlds"], R["manifests"], R["history"], R["store"]
    cfg = s["config"]
    kind = s["kind"]
    global_rows: list = []
    cycles: list = []

    # ---- independent grader for attempts
    if kind == "sumdiff":
        def regrade(prog):
            return regrade_sumdiff(prog)
    else:
        from rsi.core.evaluate import Evaluator
        from rsi.domains.agentqa import SimModel
        evr = Evaluator(s["domain"], SimModel(s["domain"].tasks), workers=2)

        def regrade(prog):
            r = evr.evaluate(prog, "evolve", 1)
            return r.score, "ok"

    base = _by(ev, "baseline")[0]
    sg, why = regrade(program_only(s["seed"]))
    _check(global_rows, "seed program re-graded independently", abs(sg - base["summary"]["S"]) < 1e-9,
           f"trace S={base['summary']['S']:.6f}, independent={sg:.6f} ({why})")
    run_start = _by(ev, "run_start")[0]
    _check(global_rows, "run starts from the untouched seed artifact",
           run_start["seed_artifact_id"] == program_only(s["seed"]).id, run_start["seed"])

    obj = Eq1Objective(cfg.beta1, cfg.beta2, cfg.normalize, cfg.support)
    best = base["summary"]["S"]
    best_label = "seed"
    deployed_code = {1: hist[0]["code"]}         # policy deployed for cycle t
    gt_rows: list = []
    runner = get_runner("subprocess", timeout_s=60)

    for t, w in enumerate(worlds, 1):
        rows: list = []
        rs = _by(ev, "round_start", t)[0]
        rev_idx = int(rs["deployed_policy"]["rev"][1:])
        code_t = hist[rev_idx]["code"]
        deployed_code[t] = code_t
        # --- plan
        pr, pu = rs["plan_requested"], rs["plan_used"]
        in_caps = 1 <= pu["branch_count"] <= cfg.hard_max_branch and 0 <= pu["refine_count"] <= cfg.hard_max_refine
        _check(rows, "plan used within hard caps (and = requested clamped)",
               in_caps and (pr is None or (min(max(1, pr["branch_count"]), cfg.hard_max_branch) == pu["branch_count"]
                                           and min(max(0, pr["refine_count"]), cfg.hard_max_refine) == pu["refine_count"])),
               f"requested {pr and (pr['branch_count'], pr['refine_count'])} used {(pu['branch_count'], pu['refine_count'])}: "
               f"{pu['reason'][:120]}")
        # --- live root = best so far
        _check(rows, "live root = best program so far (root='best')", abs(w.root_score - best) < 1e-12,
               f"root {w.root_score:.6f} vs best so far {best:.6f}")
        # --- online rounds: legality and consistency with the recorded tree
        orounds = [d for d in _by(ev, "note", t) if d["what"] == "online_round"]
        by_round: dict = {}
        for n in w.non_root():
            by_round.setdefault(n.round, []).append(n)
        legal_ok, problems = True, []
        revealed: set = set()
        for d in orounds:
            cells = d["batch"]
            br = [int(c.split(".")[0][1:]) for c in cells]
            at = [int(c.split(".")[1][1:]) for c in cells]
            if len(cells) > cfg.W or len(set(br)) != len(br):
                legal_ok = False
                problems.append(f"round {d['online_round']}: {cells} (size / one cell per branch)")
            for b, a in zip(br, at):
                if b >= pu["branch_count"] or a > pu["refine_count"]:
                    legal_ok = False
                    problems.append(f"b{b}.a{a} outside plan")
                if a > 0 and f"b{b}.a{a - 1}" not in revealed:
                    legal_ok = False
                    problems.append(f"b{b}.a{a} probed before its parent")
            if sorted(cells) != sorted(n.id for n in by_round.get(d["online_round"], [])):
                legal_ok = False
                problems.append(f"round {d['online_round']}: trace batch != recorded nodes")
            revealed |= set(cells)
        _check(rows, "online batches legal (<= W, one cell per branch, inside plan, parent first) and = recorded tree",
               legal_ok, "; ".join(problems[:4]) or f"{len(orounds)} rounds, batch sizes "
               f"{[len(d['batch']) for d in orounds]}")
        # legal cells left when the policy stopped (portfolio stop)
        depth = {n.branch: max(x.attempt for x in w.non_root() if x.branch == n.branch) for n in w.non_root()}
        left = sum(1 for b in range(pu["branch_count"]) if b not in depth) + \
            sum(1 for b, a in depth.items() if a < pu["refine_count"])
        summ = [d for d in _by(ev, "note", t) if d["what"] == "online_summary"][0]
        # --- attempts: diff from the snapshot store, independent re-grade, claims
        n_ok_diff = n_ok_grade = n_nondet = 0
        diff_bad, grade_bad, claims = [], [], {"checked": 0, "match": 0, "switch": 0, "mismatch": []}
        parse_art: list = []
        props = {d["candidate"]: d for d in _by(ev, "proposal", t) if d["candidate"].startswith(f"t{t}/")}
        evals = {d["candidate"]: d for d in _by(ev, "eval", t) if d["candidate"].startswith(f"t{t}/")}
        for n in w.non_root():
            key = f"t{t}/{n.id}"
            p, e = props.get(key), evals.get(key)
            parent = w.node(n.parent_id)
            pa = program_only(store.get(parent.artifact_id))
            ch = program_only(store.get(n.artifact_id))
            if p is not None and same_text(p["diff"], pa.diff(ch)):
                n_ok_diff += 1
            else:
                diff_bad.append(n.id)
            if e is None or e["summary"]["S"] != n.score:
                grade_bad.append(f"{n.id}: trace/eval mismatch")
            if n.success:
                g, why = regrade(ch)
                if g is not None and abs(g - n.score) < 1e-9:
                    n_ok_grade += 1
                else:
                    g2, _ = regrade(ch)
                    if g2 is not None and g is not None and abs(g2 - g) > 1e-12:
                        n_nondet += 1
                    grade_bad.append(f"{n.id}: recorded {n.score:.6f} vs independent {g} ({why})")
            elif kind == "sumdiff":
                g, why = regrade(ch)
                if g not in (0.0, None):
                    grade_bad.append(f"{n.id}: recorded failure ({n.fail_class}) but independent grade {g:.6f}")
                else:
                    n_ok_grade += 1
                # was the failure the idea's, or a reply-format line the parser left in the file?
                if "SyntaxError" in str(n.error):
                    from rsi.dream.agent import strip_reply_terminators
                    fixed, which = strip_reply_terminators(ch, ["construct.py"])
                    if which:
                        g2, _ = regrade(fixed)
                        parse_art.append({"cell": n.id, "score_if_parsed": g2})
            # claimed change vs actual diff (mock sum-difference agent: "apply <mech> <params>")
            if kind == "sumdiff" and p is not None and p["change"].startswith("# apply "):
                claims["checked"] += 1
                mech = p["change"].split()[2]
                added = [l for l in p["diff"].splitlines() if l.startswith("+STAGES")]
                last = json.loads(added[0][len("+STAGES = "):])[-1] if added else None
                if last and last[0] == mech:
                    claims["match"] += 1
                    if n.tags.get("direction") and mech != n.tags["direction"]:
                        claims["switch"] += 1
                else:
                    claims["mismatch"].append(n.id)
        _check(rows, "every attempt's trace diff = parent -> child program diff from the snapshot store",
               not diff_bad, f"{n_ok_diff}/{w.size} match" + (f"; bad {diff_bad[:4]}" if diff_bad else ""))
        _check(rows, "every attempt re-graded independently (own grader, fresh process) = recorded score",
               not grade_bad, f"{n_ok_grade}/{w.size} match" + (f"; {grade_bad[:3]}" if grade_bad else "")
               + (f"; {n_nondet} program(s) non-deterministic on re-run" if n_nondet else ""))
        n_fail = sum(1 for n in w.non_root() if not n.success)
        if n_fail:
            _check(rows, "failed attempts: genuine failures (not reply-parsing artifacts)", not parse_art,
                   f"{n_fail} failed; {len(parse_art)} were a trailing '===' / fence line left by the reply parser - "
                   f"re-graded without it: " + ", ".join(f"{x['cell']}={x['score_if_parsed']:.4f}" for x in parse_art)
                   if parse_art else f"{n_fail} failed, none is a parse artifact")
        if claims["checked"]:
            _check(rows, "mock agent's claimed change = the stage its diff actually appends",
                   not claims["mismatch"], f"{claims['match']}/{claims['checked']} match; {claims['switch']} attempts "
                   "switched away from the branch direction (documented 25% mock behaviour)")
        # --- best program rule
        succ = [n.score for n in w.non_root() if n.success and n.score is not None]
        new_best = max([best] + succ)
        dec = [d for d in _by(ev, "decision", t) if d["level"].startswith("object")][0]
        _check(rows, "best program = max(best so far, best successful attempt), strict improvement",
               abs(dec["best_after"] - new_best) < 1e-6 and ((new_best > best) == (dec["kept"] is not None)),
               f"{best:.6f} -> {new_best:.6f} (trace: {dec['best_before']} -> {dec['best_after']}, kept {dec['kept']})")
        prev_best, best = best, new_best
        if dec["kept"]:
            best_label = dec["kept"]
        # --- replay fidelity: re-replay this world with the policy that recorded it, in the online plan
        plan = GridPlan(pu["branch_count"], pu["refine_count"], "online plan")
        truncated = summ.get("truncated_batch")
        with runner.session(code_t) as sess:
            q = ReplayQuestion(w, cfg.W, plan, K=cfg.K2, root_mode=cfg.root_mode, hide_missing=cfg.hide_missing)
            o = sess.solve({}, q)
        rep_rounds = [sorted(x["cell"] for x in r["revealed"]) for r in q.round_log]
        onl_rounds = [sorted(n.id for n in by_round[k]) for k in sorted(by_round)]
        fid = rep_rounds == onl_rounds
        _check(rows, "replay fidelity: the recording policy replayed on its own world reveals exactly the online "
               "batches", fid if not truncated else (fid or "N/A (call budget truncated a batch)"),
               f"online {[len(x) for x in onl_rounds]} vs replay {[len(x) for x in rep_rounds]}"
               + (f"; replay error {o.error}" if o.error else ""))
        row = {"cycle": t, "policy": rs["deployed_policy"], "plan": (pu["branch_count"], pu["refine_count"]),
               "plan_reason": pu["reason"], "N": summ["N"], "k": summ["k"], "batch_sizes": summ["batch_sizes"],
               "legal_left_at_stop": left, "round_best": max(succ) if succ else None, "best_before": prev_best,
               "parse_artifacts": parse_art,
               "best_after": best, "fail_classes": {fc: sum(1 for n in w.non_root() if n.fail_class == fc)
                                                    for fc in sorted({n.fail_class for n in w.non_root()})},
               "checks": rows}
        # --- manifest beta = the deployed code's baked-in default beta
        _check(rows, "manifest beta = baked-in default beta of the deployed code",
               abs(mans[t - 1]["beta"] - default_beta_of(code_t)) < 1e-12, f"beta {mans[t - 1]['beta']}")
        # --- dreaming phase
        dd = [d for d in _by(ev, "decision", t) if d["level"].startswith("policy")]
        if dd:
            row["dream"] = _audit_dream(t, ev, worlds[:t], mans, hist, cfg, obj, rows, s)
            if s.get("ground_truth") and len(row["dream"]["versions"]) > 1:
                gt = ground_truth(t, s, row["dream"], hist, best_prog=_best_prog(store, worlds, best), manifests=mans[:t])
                row["dream"]["ground_truth"] = gt
                gt_rows.append(gt)
        cycles.append(row)
    # ---- run-level checks
    tot = sum(len(w.non_root()) for w in worlds)
    meter = R["summary"]["usage"].get("_cost", {})
    _check(global_rows, "agent calls: sum of attempts over worlds = cost meter = trajectory",
           tot == meter.get("agent_calls") == sum(r["calls"] for r in R["trajectory"]),
           f"{tot} attempts, meter {meter.get('agent_calls')}")
    if cfg.max_calls is not None:
        _check(global_rows, "call budget respected", tot <= cfg.max_calls, f"{tot} <= {cfg.max_calls}")
    fin = R["summary"]["meta"]["best_score"]
    _check(global_rows, "returned best = best of every recorded successful attempt and the seed",
           abs(fin - max([base["summary"]["S"]] + [n.score for w in worlds for n in w.non_root() if n.success])) < 1e-12,
           f"{fin:.6f}")
    mon = [{"round": e["round"], **e["data"]} for e in ev if e["kind"] == "monitor"]
    return {"run": name, "global_checks": global_rows, "cycles": cycles, "monitor": mon,
            "ground_truth": gt_rows}


def _best_prog(store, worlds, best):
    from rsi.dream.question import program_only
    for w in worlds:
        for n in w.non_root():
            if n.success and n.score == best:
                return program_only(store.get(n.artifact_id))
    return None


def _audit_dream(t, ev, worlds, mans, hist, cfg, obj, rows, s) -> dict:
    from rsi.dream.developer import default_beta_of
    from rsi.dream.evaluator import ReplayEvaluator
    from rsi.dream.guard import static_check
    from rsi.dream.objectives import Eq1Objective

    evals = [d for d in _by(ev, "eval", t) if d["candidate"].startswith("r")]
    gates = [d for d in _by(ev, "gate", t) if d.get("level", "").startswith("policy")]
    dec = [d for d in _by(ev, "decision", t) if d["level"].startswith("policy")][0]
    props = {d["candidate"]: d for d in _by(ev, "proposal", t) if d["candidate"].startswith("r")}
    # 1. Eq. 1 arithmetic from the recorded episode statistics, with the world's root / ceiling
    #    recomputed from tree.json and every revealed cell's score looked up in the world
    arith_bad, look_bad = [], []
    wmap = {w.world_id: w for w in worlds}
    for e in evals:
        vals = []
        for wd in e["worlds"]:
            w = wmap[wd["world"]]
            succ = [n.score for n in w.non_root() if n.success and n.score is not None]
            ceil = max(succ + [w.root_score])
            if abs(ceil - wd["ceiling"]) > 1e-6 or abs(w.root_score - wd["root"]) > 1e-6:
                look_bad.append(f"{e['candidate']}/{wd['world']}: root/ceiling")
            rev_scores = []
            for r in wd["reveal_batches"]:
                for x in r["revealed"]:
                    if "=" in x:
                        cid, sc = x.split("=")
                        node = w.get(cid)
                        if node is None or abs(node.score - float(sc)) > 1e-4:
                            look_bad.append(f"{e['candidate']}/{wd['world']}: {x}")
                        rev_scores.append(node.score if node else float(sc))
            bst = max(rev_scores + [w.root_score])
            den = ceil - w.root_score
            att = 0.0 if den <= 0 else min(1.0, max(0.0, (bst - w.root_score) / den))
            q = att if cfg.normalize else bst
            v = q - cfg.beta1 * wd["N"] + cfg.beta2 * wd["N"] / max(1, wd["k"])
            if wd["disqualified"]:
                v = wd["V_i"]
            if abs(v - wd["V_i"]) > 2e-6:
                arith_bad.append(f"{e['candidate']}/{wd['world']}: {v:.6f} vs {wd['V_i']}")
            vals.append(v)
        if abs(float(np.mean(vals)) - e["summary"]["S"]) > 2e-6:
            arith_bad.append(f"{e['candidate']}: mean {np.mean(vals):.6f} vs {e['summary']['S']}")
    _check(rows, f"dream t={t}: Eq.1 V_i and V recomputed from revealed cells (scores looked up in tree.json)",
           not arith_bad and not look_bad, "; ".join((arith_bad + look_bad)[:4]) or
           f"{len(evals)} versions x {len(worlds)} worlds")
    # 2. re-replay every version from history/ on the same worlds with a fresh evaluator
    ev2 = ReplayEvaluator(Eq1Objective(cfg.beta1, cfg.beta2, cfg.normalize, cfg.support), W=cfg.W, K2=cfg.K2,
                          root_mode=cfg.root_mode, hide_missing=cfg.hide_missing, runner="subprocess",
                          fallback=(cfg.branch_count, cfg.refine_count), hard_max=(cfg.hard_max_branch,
                                                                                   cfg.hard_max_refine))
    rr_bad, versions = [], []
    for e in evals:
        idx = int(e["candidate"][1:5])
        rep = ev2.evaluate(hist[idx]["code"], worlds, manifests=mans)
        versions.append({"rev": idx, "label": hist[idx]["label"], "V": e["summary"]["S"], "V_rereplay": rep.value,
                         "diag": {k: e["diagnostics"].get(k) for k in ("attainment", "N", "k", "mean_batch",
                                                                          "wasted_probe_frac", "missed_ceiling_rate")}})
        if abs(rep.value - e["summary"]["S"]) > 2e-6:
            rr_bad.append(f"r{idx}: {rep.value:.6f} vs {e['summary']['S']}")
    _check(rows, f"dream t={t}: every version re-replayed from history/ gives the recorded V (deterministic replay)",
           not rr_bad, "; ".join(rr_bad[:3]) or ", ".join(f"r{v['rev']:04d}={v['V']:.4f}" for v in versions))
    # 3. selection = argmax (incumbent preferred on ties), and it is what the next cycle deploys
    V = [g["math"]["V_m"] for g in gates]
    finite = [i for i, v in enumerate(V) if v is not None and math.isfinite(v)]
    bestv = max(V[i] for i in finite)
    exp = 0 if abs(V[0] - bestv) <= 1e-12 else min(i for i in finite if abs(V[i] - bestv) <= 1e-12)
    sel = [i for i, g in enumerate(gates) if g["accept"]]
    nxt = _by(ev, "round_start", t + 1)
    deployed_next = nxt[0]["deployed_policy"]["rev"] if nxt else dec["deployed_for_next_cycle"]["rev"]
    chosen_rev = dec["incumbent_after"].split("_")[0]
    _check(rows, f"dream t={t}: selected = argmax V (incumbent kept on ties) and deployed next cycle",
           sel == [exp] and deployed_next == chosen_rev, f"V={V}, selected {sel}, expected {exp}, next cycle "
           f"deploys {deployed_next}")
    # 4. developer revisions: diff = base -> revision code; screens; claims
    rev_rows = []
    for cand, p in props.items():
        idx = int(cand[1:5])
        parent = int(p["parent"][1:]) if p.get("parent") else None
        code = hist[idx]["code"] if idx in hist else ""
        exp_diff = Artifact({"method.py": hist[parent]["code"]}).diff(Artifact({"method.py": code})) \
            if (parent in hist and code) else ""
        crit = [c for c in _by(ev, "critic", t) if c["candidate"] == cand]
        r = {"rev": cand, "parent": p.get("parent"), "change": p["change"][:300], "diff_ok": same_text(p["diff"], exp_diff),
             "diff_lines": sum(1 for l in p["diff"].splitlines() if l[:1] in "+-" and not l.startswith(("+++", "---"))),
             "screen": [c["accept"] for c in crit], "error": p.get("error"),
             "static_ok": static_check(code).ok if code else None}
        if p.get("moves"):                     # ParametricMutator: do the claimed perturbations match the code?
            noop = []
            for m in p["moves"]:
                mm = re.match(r"perturb (\w+) -> ([-\d.]+)", m)
                if mm and mm.group(1) not in (p.get("params_changed") or {}):
                    noop.append(mm.group(1))
            r["noop_claimed_moves"] = noop
        rev_rows.append(r)
    _check(rows, f"dream t={t}: each revision's trace diff = history code diff (base -> revision)",
           all(r["diff_ok"] for r in rev_rows if r["error"] is None), f"{len(rev_rows)} revisions")
    # 5. no-leak: the deployed code contains no trace-specific best cell id / score of any world
    chosen = int(chosen_rev[1:])
    code = hist[chosen]["code"]
    terms = []
    for w in worlds:
        b = w.best_node()
        terms += [b.id, f"{b.score:.4f}"] if b.id != "root" else []
    leaks = [x for x in terms if x in code and len(x) >= 4]
    _check(rows, f"dream t={t}: deployed policy passes the static check and copies no trace-specific id/score",
           static_check(code).ok and not leaks, f"leaks {leaks}" if leaks else f"r{chosen:04d} clean")
    return {"t": t, "V": V, "selected": sel[0] if sel else None, "reason": dec["why"][:300], "versions": versions,
            "revisions": rev_rows, "chosen": f"r{chosen:04d}", "default_beta_chosen": default_beta_of(code)}


def ground_truth(t, s, dream, hist, best_prog, manifests, n_seeds: int = 8) -> dict:
    """Offline only: estimate each version's TRUE online value by fresh live searches.

    For every version of phase t, run ``n_seeds`` fresh one-cycle live searches (new agent seeds,
    no dreaming) from the program the next cycle starts from, with the manifests the policy
    would see; score each search with Eq. 1 on a common scale (quality = gain over the root /
    the largest gain any probe of this phase reached). The question: did replay rank the versions
    the way their online behaviour ranks them, and was the deployed version really no worse?"""
    from rsi.dream.loop import DreamRSILoop
    from rsi.dream.agent import DomainTask

    cfg = s["config"]
    task = s["task"] if isinstance(s["task"], DomainTask) else s["task"].as_task()
    per = {}
    for v in dream["versions"]:
        code = hist[v["rev"]]["code"]
        rows = []
        for k in range(n_seeds):
            c = replace(cfg, rounds=1, dream=False, trace=False, seed=10_000 + 97 * t + k, max_calls=None)
            agent = s["agent"]
            lp = DreamRSILoop(task, agent, config=c, initial_policy=code, seed_artifact=best_prog)
            lp.manifests = [dict(m) for m in manifests]
            r = lp.run().trajectory[0]
            rows.append({"gain": r["round_best"] - r["root"], "N": r["N"], "k": r["k"]})
        per[v["rev"]] = rows
    scale = max([x["gain"] for rs in per.values() for x in rs] + [1e-12])
    vals = {rev: [x["gain"] / scale - cfg.beta1 * x["N"] + cfg.beta2 * x["N"] / max(1, x["k"]) for x in rs]
            for rev, rs in per.items()}
    revs = [v["rev"] for v in dream["versions"]]
    online = [float(np.mean(vals[r])) for r in revs]
    replay = [v["V"] for v in dream["versions"]]
    sel = revs[dream["selected"]]
    inc = revs[0]
    cmp = paired_diff_ci(vals[inc], vals[sel]) if sel != inc else None
    gains = {r: [x["gain"] for x in per[r]] for r in revs}
    cmp_gain = paired_diff_ci(gains[inc], gains[sel]) if sel != inc else None
    return {"t": t, "n_seeds": n_seeds, "scale": scale, "versions": revs, "replay_V": replay,
            "online_V": [round(x, 4) for x in online],
            "online_gain": [round(float(np.mean([x["gain"] for x in per[r]])), 5) for r in revs],
            "online_calls": [round(float(np.mean([x["N"] for x in per[r]])), 2) for r in revs],
            "spearman_replay_vs_online": spearman(replay, online),
            "selected": sel, "online_best_version": revs[int(np.argmax(online))],
            "selected_minus_incumbent_online": cmp, "selected_minus_incumbent_gain": cmp_gain}


# --------------------------------------------------------------------------------- render --
def audit_md(a: dict, report: dict) -> str:
    L = [f"# Audit of `{a['run']}`", "", "Every check below is re-derived from the files on disk (trace.jsonl, "
         "trace_pool/, snapshots/, history/) with code independent of the loop where possible.", "",
         "## Run-level checks", "", "| check | result | detail |", "|---|---|---|"]
    for r in a["global_checks"]:
        L.append(f"| {r['check']} | {r['result']} | {r['detail']} |")
    for c in a["cycles"]:
        L += ["", f"## Cycle {c['cycle']}", "",
              f"Deployed policy `{c['policy']['rev']}` ({c['policy']['label']}, default beta "
              f"{c['policy']['default_beta']}); plan {c['plan'][0]} x {c['plan'][1] + 1} ({c['plan_reason'][:140]}); "
              f"N = {c['N']} attempts in k = {c['k']} rounds, batches {c['batch_sizes']}; "
              f"{c['legal_left_at_stop']} planned cells left unprobed at stop; outcomes {c['fail_classes']}; "
              f"best {c['best_before']:.6f} -> {c['best_after']:.6f}.", "",
              "| check | result | detail |", "|---|---|---|"]
        for r in c["checks"]:
            L.append(f"| {r['check']} | {r['result']} | {str(r['detail'])[:400]} |")
        d = c.get("dream")
        if d:
            L += ["", f"Dreaming after cycle {c['cycle']}: V = {d['V']}, selected index {d['selected']} "
                      f"({d['chosen']}); {d['reason']}", ""]
            L += ["| version | change | diff lines | screens | re-replay V |", "|---|---|---|---|---|"]
            vmap = {f"r{v['rev']:04d}": v for v in d["versions"]}
            for r in d["revisions"]:
                rid = r["rev"][:5]
                vv = vmap.get(rid, {})
                extra = f" (no-op claimed moves: {r['noop_claimed_moves']})" if r.get("noop_claimed_moves") else ""
                L.append(f"| {r['rev']} | {r['change'][:160].replace('|', '/')}{extra} | {r['diff_lines']} | "
                         f"{r['screen']} | {vv.get('V_rereplay', 'n/a')} |")
            gt = d.get("ground_truth")
            if gt:
                cmp = gt["selected_minus_incumbent_online"]
                L += ["", f"Ground truth (fresh online searches, {gt['n_seeds']} seeds per version): versions "
                          f"{gt['versions']}, replay V {[round(x, 4) for x in gt['replay_V']]}, online V "
                          f"{gt['online_V']}, online mean gain {gt['online_gain']}, online mean calls "
                          f"{gt['online_calls']}; Spearman(replay, online) = {gt['spearman_replay_vs_online']}; "
                          f"selected r{gt['selected']:04d}, best online r{gt['online_best_version']:04d}"
                          + (f"; selected - incumbent online V = {cmp['mean_diff']:+.4f} "
                             f"[{cmp['lo']:+.4f}, {cmp['hi']:+.4f}]" if cmp else "; incumbent kept")
                          + (f"; selected - incumbent online gain = {gt['selected_minus_incumbent_gain']['mean_diff']:+.5f} "
                             f"[{gt['selected_minus_incumbent_gain']['lo']:+.5f}, "
                             f"{gt['selected_minus_incumbent_gain']['hi']:+.5f}]"
                             if gt.get('selected_minus_incumbent_gain') else "")]
    if a["monitor"]:
        L += ["", "## Shadow monitor (sealed splits; never shown to the loop)", "",
              "| cycle | version | decision score | sealed |", "|---|---|---|---|"]
        for m in a["monitor"]:
            L.append(f"| {m['round']} | {m['version']} | {m['decision_score']} | "
                     + "; ".join(f"{k}: S={v['S']:.3f}" for k, v in m["sealed"].items()) + " |")
    L += ["", "## Report", "", "```json", json.dumps({k: report[k] for k in ("seed_score", "best_score",
                                                                              "calls_per_cycle", "spend")},
                                                    default=str, indent=1), "```"]
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--audit-only", action="store_true")
    ap.add_argument("--reuse-cache", action="store_true")
    a = ap.parse_args()
    out = OUT / a.run
    s = setup(a.run, reuse_cache=a.reuse_cache or a.audit_only)
    if a.audit_only:
        report = json.loads((out / "report.json").read_text())
    else:
        report = execute(a.run, s, out)
        print(json.dumps({k: report[k] for k in ("seed_score", "best_score", "calls_per_cycle", "spend", "wall_s")},
                         default=str), flush=True)
    t0 = time.time()
    au = audit(a.run, s, out)
    au["audit_wall_s"] = round(time.time() - t0, 1)
    (out / "audit.json").write_text(json.dumps(au, indent=1, default=str))
    (out / "audit.md").write_text(audit_md(au, report))
    fails = [r for r in au["global_checks"] if r["result"] == "FAIL"] + \
        [r for c in au["cycles"] for r in c["checks"] if r["result"] == "FAIL"]
    print(f"audit: {len(fails)} FAIL", [f["check"] for f in fails][:6])


if __name__ == "__main__":
    main()
