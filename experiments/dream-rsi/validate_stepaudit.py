"""Stage-B independent step audit of the Dream-RSI validation runs.

Re-derives every step of a run from the files on disk ONLY (trace.jsonl, trace_pool/iter*/tree.json +
live_cycle_manifest.json, snapshots/, history/r####_*/method.py, the LLM cache) with code written
independently of the loop:

* object level: own re-grade (sum-difference: construct.py run in a fresh python process, Gamma from
  pure-Python sets; AgentQA: a fresh Evaluator + SimModel), own difflib diffs from snapshots, claim vs
  diff, context sizes (lineage / siblings / history), online batch legality with an own grid
  simulator, prefix_seen reconstruction, best-program rule, live root, manifest fields;
* dreaming: an OWN replay implementation (paper §3 semantics: root -> earliest-created unrevealed
  branch, leaf -> recorded child, plan ∩ support) that drives every archived policy version through
  the same public API, own Eq.-1 arithmetic from tree.json scores, argmax with the incumbent kept on
  ties, next-cycle deployment, mutator claims vs PARAMS, developer diffs, leakage grep, beta-sweep AUC;
* accounting: calls per cycle, per-attempt usage vs meter, cache-file spend (live run).

Usage: python experiments/dream-rsi/validate_stepaudit.py <run> [<run> ...]
Writes validation/dream-rsi/<run>/stepaudit.json and prints a summary.
"""
from __future__ import annotations

import ast
import difflib
import glob
import json
import math
import re
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from rsi.dream import policy_api  # noqa: E402  (the policy-side API data types only)

OUT = ROOT / "validation" / "dream-rsi"
META_FILES = ("proposal.md", "eval/score.json", "error.txt")
MECHS = ("hill", "anneal", "window", "fringe", "grow")
TOL = 1e-6


# ------------------------------------------------------------------------------ helpers
def load_events(run_dir: Path) -> list[dict]:
    return [json.loads(l) for l in (run_dir / "trace.jsonl").read_text().splitlines() if l.strip()]


def snapshot(run_dir: Path, aid: str) -> dict:
    d = json.loads((run_dir / "snapshots" / aid[:2] / f"{aid}.json").read_text())
    files = d.get("files", d)
    return {k: v for k, v in files.items() if k not in META_FILES and v is not None}


def my_diff(old: dict, new: dict) -> list[str]:
    """+/- content lines of a unified diff (headers dropped), per file."""
    out = []
    for name in sorted(set(old) | set(new)):
        a, b = old.get(name, ""), new.get(name, "")
        if a == b:
            continue
        for l in difflib.unified_diff(a.splitlines(), b.splitlines(), lineterm="", n=0):
            if l.startswith(("---", "+++", "@@")):
                continue
            out.append(l)
    return out


def traced_diff_lines(diff: str) -> tuple[list[str], bool]:
    trunc = "\n...[truncated " in (diff or "")
    body = (diff or "").split("\n...[truncated ", 1)[0]
    lines = [l for l in body.splitlines() if l[:1] in "+-" and not l.startswith(("---", "+++"))]
    if trunc and lines:
        lines = lines[:-1]            # the last line may be cut mid-way
    return lines, trunc


def gamma_sets(A) -> float:
    A = sorted(set(A))
    n = len(A)
    if n < 2:
        return 0.0
    s = {x + y for x in A for y in A}
    d = {x - y for x in A for y in A}
    if len(s) <= n or len(d) <= n:
        return 0.0
    return math.log(len(s) / n) / math.log(len(d) / n)


def regrade_sumdiff(code: str) -> tuple[str, float | None]:
    """Run construct.py in a fresh interpreter; own constraint checks and own Gamma."""
    with tempfile.TemporaryDirectory() as d:
        Path(d, "construct.py").write_text(code)
        try:
            p = subprocess.run([sys.executable, "-c", "import json, construct; print(json.dumps(construct.construct()))"],
                               cwd=d, capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired:
            return "timeout", None
        if p.returncode != 0:
            tail = (p.stderr.strip().splitlines() or ["?"])[-1]
            return f"error: {tail}", 0.0
        out = json.loads(p.stdout.strip().splitlines()[-1])
    if not isinstance(out, list) or not all(isinstance(v, (int, float)) and float(v).is_integer() for v in out):
        return "correctness", 0.0
    A = [int(v) for v in out]
    if len(set(A)) != len(A):
        return "constraint", 0.0
    if not 2 <= len(A) <= 400 or max(A) - min(A) > 20000:
        return "constraint", 0.0
    g = gamma_sets(A)
    return ("ok" if g > 0 else "constraint"), g


def parse_stages(code: str):
    try:
        for node in ast.walk(ast.parse(code)):
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "STAGES" for t in node.targets):
                return ast.literal_eval(node.value)
    except (SyntaxError, ValueError):
        pass
    m = re.search(r"^STAGES\s*=\s*(\[.*?\])\s*$", code, re.S | re.M)
    if m:
        try:
            return ast.literal_eval(m.group(1))
        except (SyntaxError, ValueError):
            return None
    return None


def params_of(code: str) -> dict:
    try:
        for node in ast.walk(ast.parse(code)):
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "PARAMS" for t in node.targets):
                return ast.literal_eval(node.value)
    except (SyntaxError, ValueError):
        pass
    return {}


def default_beta(code: str) -> float:
    p = params_of(code)
    if "default_beta" in p:
        return float(p["default_beta"])
    try:
        for node in ast.walk(ast.parse(code)):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get"
                    and len(node.args) == 2 and isinstance(node.args[0], ast.Constant) and node.args[0].value == "beta"
                    and isinstance(node.args[1], ast.Constant)):
                return float(node.args[1].value)
    except SyntaxError:
        pass
    return 0.6


# --------------------------------------------------------------------- own replay (paper §3)
class IndepReplay:
    """A second, independent implementation of the replay transition, exposing the Listing-2 API."""

    def __init__(self, tree: dict, W: int, plan: tuple[int, int], root_mode: str = "earliest") -> None:
        self.W = W
        self.root = tree["root"]
        self.nodes = {n["id"]: n for n in tree["nodes"]}
        self.tags = {int(k): v for k, v in (tree.get("branch_tags") or {}).items()}
        br = defaultdict(list)
        for n in tree["nodes"]:
            br[n["branch"]].append(n)
        rec_order = [b for b, ns in sorted(br.items(), key=lambda kv: min(x["seq"] for x in kv[1]))]
        mp = (tree.get("meta") or {}).get("plan")
        sup_b = max([b + 1 for b in br] + ([int(mp["branch_count"])] if mp else []) + [0])
        sup_r = max([n["attempt"] for n in tree["nodes"]] + ([int(mp["refine_count"])] if mp else []) + [0])
        self.support = (sup_b, sup_r)
        self.out_of_support = plan[0] > sup_b or plan[1] > sup_r
        self.eb, self.er = min(plan[0], sup_b), min(plan[1], sup_r)
        order = (rec_order if root_mode == "earliest" else sorted(br)) + [b for b in range(sup_b) if b not in br]
        self.slots = order[: self.eb]
        self.in_plan = {n["id"] for n in tree["nodes"] if n["branch"] in self.slots and n["attempt"] <= self.er}
        self.root_mode = root_mode
        self.reset()

    # ---- API
    def reset(self):
        self.rev, self.last, self.exh, self.k, self.done = {}, {}, set(), 0, False
        self.batches = []

    @property
    def baseline_score(self):
        return self.root["score"]

    @property
    def max_parallelism(self):
        return self.W

    def observed(self):
        return dict(self.rev)

    def legal_roots(self):
        if self.done:
            return []
        return [f"b{b}.a0" for b in self.slots if b not in self.last and b not in self.exh]

    def legal_frontiers(self):
        if self.done:
            return []
        return [f"b{b}.a{a + 1}" for b, a in sorted(self.last.items()) if b not in self.exh and a < self.er]

    def legal_actions(self):
        return self.legal_roots() + self.legal_frontiers()

    def opened_branches(self):
        return sorted(self.last)

    def is_done(self):
        return self.done

    def _scaffold_budget_done(self, budget):
        return self.done or (budget is not None and len(self.rev) >= budget)

    def meta(self, cid):
        b, a = (int(x[1:]) for x in cid.split("."))
        n = self.nodes.get(cid)
        parent = n["parent_id"] if n else ("root" if a == 0 else f"b{b}.a{a - 1}")
        show = self.root_mode == "addressable" or a > 0 or cid in self.rev
        return policy_api.CellMeta(cid, b, a, parent, None, dict(self.tags.get(b, {})) if show else {})

    def _obs(self, n):
        par = self.nodes.get(n["parent_id"]) if n["parent_id"] != "root" else self.root
        sc = n["score"]
        dp = sc - par["score"] if (sc is not None and par and par.get("score") is not None) else None
        db = sc - self.root["score"] if (sc is not None and self.root.get("score") is not None) else None
        return policy_api.Observation(n["id"], n["branch"], n["attempt"], sc, n["evaluated"], n["valid"],
                                      n["fail_class"], n["error"], db, dp, n.get("n_valid"), n.get("n_total"),
                                      n["parent_id"])

    def probe_batch(self, cells, on_reveal=None):
        cells = [str(c) for c in cells]
        if self.done:
            return []
        legal = set(self.legal_actions())
        brs = [int(c.split(".")[0][1:]) for c in cells]
        if not cells or len(cells) > self.W or len(set(cells)) != len(cells) or any(c not in legal for c in cells) \
                or len(set(brs)) != len(brs):
            raise policy_api.BatchError(f"illegal batch {cells}")
        pending = [b for b in self.slots if b not in self.last and b not in self.exh and f"b{b}.a0" in self.nodes]
        used, out, row = set(), [], {"batch": cells, "revealed": [], "empty": []}
        for c in cells:
            b, a = (int(x[1:]) for x in c.split("."))
            if a == 0 and self.root_mode == "earliest":
                nxt = next((x for x in pending if x not in used), None)
                if nxt is None:
                    self.exh.add(b)
                    row["empty"].append(c)
                    continue
                used.add(nxt)
                b = nxt
            n = self.nodes.get(f"b{b}.a{a}")
            if n is None:
                self.exh.add(b)
                row["empty"].append(c)
                continue
            o = self._obs(n)
            self.rev[o.cell_id] = o
            self.last[b] = max(self.last.get(b, -1), a)
            out.append(o)
            row["revealed"].append(o.cell_id)
        self.k += 1
        self.batches.append(row)
        if self.in_plan.issubset(self.rev) or not self.legal_actions():
            self.done = True
        if on_reveal:
            for o in out:
                on_reveal(o)
        return out

    def stats(self):
        succ = [o.score for o in self.rev.values() if o.success and o.score is not None]
        best = max(succ + [self.root["score"]])
        return {"N": len(self.rev), "k": self.k, "best": best, "batches": self.batches}


def load_policy(code: str):
    sys.modules.setdefault("policy_api", policy_api)
    import hashlib

    name = "indep_policy_" + hashlib.sha1(code.encode()).hexdigest()[:12]
    mod = type(sys)(name)
    sys.modules[name] = mod            # dataclasses in LLM-written policies look their module up
    exec(compile(code, "method.py", "exec"), mod.__dict__)  # noqa: S102 - archived run artifacts
    return mod.__dict__[mod.__dict__.get("NAME", "OptimalPolicy")]


def ceiling_of(tree: dict) -> float:
    vals = [tree["root"]["score"]] + [n["score"] for n in tree["nodes"]
                                      if n["score"] is not None and n["evaluated"] and n["error"] is None
                                      and n["fail_class"] == "ok"]
    return max(vals)


def eq1(best, root, ceil, N, k, b1=0.01, b2=0.005, normalize=True):
    if normalize:
        q = 0.0 if ceil - root <= 0 else min(1.0, max(0.0, (best - root) / (ceil - root)))
    else:
        q = best
    return q - b1 * N + b2 * N / max(1, k)


def indep_replay(code: str, tree: dict, W: int, manifests: list, cfg: dict, beta=None, plan_mode="replay"):
    cls = load_policy(code)
    rnd = int((tree.get("meta") or {}).get("round", 0))
    hist = [m for m in manifests if int(m["iteration"]) < rnd]
    trb = max([n["branch"] + 1 for n in tree["nodes"]] + [int(tree["meta"]["plan"]["branch_count"])])
    trr = max([n["attempt"] for n in tree["nodes"]] + [int(tree["meta"]["plan"]["refine_count"])])
    ctx = policy_api.GridPlanningContext(history=hist, fallback_branch_count=cfg["branch_count"],
                                         fallback_refine_count=cfg["refine_count"],
                                         hard_max_branch_count=cfg["hard_max_branch"],
                                         hard_max_refine_count=cfg["hard_max_refine"], worker_cap=W,
                                         trace_branch_count=trb, trace_refine_count=trr, mode=plan_mode)
    conf = {} if beta is None else {"beta": beta}
    try:
        plan = cls(dict(conf)).plan_grid(ctx)
    except Exception:  # noqa: BLE001
        plan = None
    if plan is None:
        pb, pr = cfg["branch_count"], cfg["refine_count"]
    else:
        pb = min(max(1, plan.branch_count), cfg["hard_max_branch"])
        pr = min(max(0, plan.refine_count), cfg["hard_max_refine"])
    q = IndepReplay(tree, W, (pb, pr))
    err = None
    try:
        cls(dict(conf)).solve(q, None)
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"
    st = q.stats()
    st.update(plan=(pb, pr), error=err, ceiling=ceiling_of(tree), root=tree["root"]["score"], size=len(tree["nodes"]))
    return st


# ------------------------------------------------------------------------------ audit
class Audit:
    def __init__(self, run: str):
        self.run = run
        self.d = OUT / run
        self.ev = load_events(self.d)
        self.steps: list[dict] = []
        self.cfg = next(e for e in self.ev if e["kind"] == "run_start")["data"]["config"]
        self.kind = "agentqa" if "agentqa" in run else "sumdiff"
        self.trees = {int(p.parent.name[4:]): json.loads(p.read_text())
                      for p in sorted(self.d.glob("trace_pool/iter*/tree.json"))}
        self.mans = [json.loads(p.read_text()) for p in sorted(self.d.glob("trace_pool/iter*/live_cycle_manifest.json"))]
        self.hist = {}
        for p in sorted(self.d.glob("history/r*/method.py")):
            self.hist[p.parent.name.split("_")[0]] = {"dir": p.parent.name, "code": p.read_text()}
        self._aq = None

    def step(self, rnd, kind, name, verdict, evidence):
        self.steps.append({"round": rnd, "kind": kind, "step": name, "verdict": verdict, "evidence": evidence})

    # ---- object level ------------------------------------------------------------
    def regrade(self, prog: dict):
        if self.kind == "sumdiff":
            return regrade_sumdiff(prog["construct.py"])
        if self._aq is None:
            from rsi.core.artifact import Artifact
            from rsi.core.evaluate import Evaluator
            from rsi.domains.agentqa import AgentQADomain, SimModel, make_suite
            suite = make_suite(n_evolve=8, n_val=4, n_holdout=8, n_ood_per_family=2, seed=0)
            self._aq = (Artifact, Evaluator(AgentQADomain(suite), SimModel(suite), workers=1), suite)
        Artifact, ev, _ = self._aq
        r = ev.evaluate(Artifact(prog), "evolve", 1)
        return "ok", r.score

    def audit_baseline(self):
        b = next(e for e in self.ev if e["kind"] == "baseline")["data"]
        t1 = self.trees[1]
        seed_prog = snapshot(self.d, t1["root"]["artifact_id"])
        fc, g = self.regrade(seed_prog)
        ok = abs(g - b["summary"]["S"]) < TOL and abs(t1["root"]["score"] - b["summary"]["S"]) < TOL
        if self.kind == "agentqa":
            from rsi.domains.agentqa import AgentQADomain, make_suite
            dom = AgentQADomain(make_suite(n_evolve=8, n_val=4, n_holdout=8, n_ood_per_family=2, seed=0))
            untouched = dom.seed_artifact().files == seed_prog if hasattr(dom.seed_artifact(), "files") else \
                {k: dom.seed_artifact()[k] for k in dom.seed_artifact()} == seed_prog
        else:
            from rsi.domains.discovery import SumDiffDomain
            sa = SumDiffDomain().seed_artifact()
            untouched = {k: sa[k] for k in sa} == seed_prog
        per = b.get("trials") or {}
        mean_trials = sum(v[0] for v in per.values()) / max(1, len(per)) if per else None
        ok2 = mean_trials is None or abs(mean_trials - b["summary"]["S"]) < TOL
        self.step(0, "baseline", "seed evaluation", "correct" if (ok and ok2 and untouched) else "wrong",
                  f"trace S={b['summary']['S']:.6f}, own re-grade={g:.6f} ({fc}), mean of raw trials={mean_trials}, "
                  f"seed program == untouched domain seed: {untouched}")

    def audit_cycle(self, t: int):
        tr = self.trees[t]
        nodes = {n["id"]: n for n in tr["nodes"]}
        evs = [e for e in self.ev if e["round"] == t]
        rs = next(e for e in evs if e["kind"] == "round_start")["data"]
        plan = rs["plan_used"]
        W = rs["W"]
        # --- round_start: plan requested = deployed policy's plan_grid on the manifests of cycles < t
        dep = rs["deployed_policy"]["rev"]
        code = self.hist[dep]["code"]
        cls = load_policy(code)
        ctx = policy_api.GridPlanningContext(history=[m for m in self.mans if m["iteration"] < t],
                                             fallback_branch_count=self.cfg["branch_count"],
                                             fallback_refine_count=self.cfg["refine_count"],
                                             hard_max_branch_count=self.cfg["hard_max_branch"],
                                             hard_max_refine_count=self.cfg["hard_max_refine"], worker_cap=W,
                                             mode="live")
        p = cls({}).plan_grid(ctx)
        req = rs["plan_requested"]
        okp = p is not None and (p.branch_count, p.refine_count) == (req["branch_count"], req["refine_count"]) and \
            (plan["branch_count"], plan["refine_count"]) == (min(max(1, p.branch_count), self.cfg["hard_max_branch"]),
                                                             min(max(0, p.refine_count), self.cfg["hard_max_refine"]))
        self.step(t, "plan", f"cycle {t} plan_grid ({dep})", "correct" if okp else "wrong",
                  f"own plan_grid on manifests of cycles < {t}: {None if p is None else (p.branch_count, p.refine_count)}"
                  f" ('{'' if p is None else p.reason}'); trace requested {(req['branch_count'], req['refine_count'])},"
                  f" used {(plan['branch_count'], plan['refine_count'])}")
        # live root = best so far
        best_before = rs["best_so_far"]
        okr = abs(tr["root"]["score"] - best_before) < 1e-5
        self.step(t, "root", f"cycle {t} live root", "correct" if okr else "wrong",
                  f"tree root score {tr['root']['score']:.6f} vs best so far {best_before}")
        # --- online rounds: own grid simulation
        onl = [e["data"] for e in evs if e["kind"] == "note" and e["data"].get("what") == "online_round"]
        last, revealed = {}, {}
        bad = []
        for r in onl:
            legal_roots = {f"b{b}.a0" for b in range(plan["branch_count"]) if b not in last}
            legal_front = {f"b{b}.a{a + 1}" for b, a in last.items() if a < plan["refine_count"]}
            legal = legal_roots | legal_front
            batch = r["batch"]
            brs = [int(c.split(".")[0][1:]) for c in batch]
            prefix = {}
            for cid, sc in sorted(revealed.items(), key=lambda kv: (nodes[kv[0]]["branch"], nodes[kv[0]]["attempt"])):
                n = nodes[cid]
                v = round(n["score"], 6) if n["fail_class"] == "ok" and n["error"] is None and n["score"] is not None \
                    else n["fail_class"]
                prefix.setdefault(str(n["branch"]), []).append(v)
            prefix = {k: v[-4:] for k, v in prefix.items()}
            probs = []
            if len(batch) > W:
                probs.append("> W")
            if len(set(brs)) != len(brs):
                probs.append("two cells of one branch")
            if not set(batch) <= legal:
                probs.append(f"illegal {sorted(set(batch) - legal)}")
            if r["legal_n"] != len(legal):
                probs.append(f"legal_n {r['legal_n']} != {len(legal)}")
            if r["prefix_seen"]["branches"] != prefix or r["prefix_seen"]["n"] != len(revealed):
                probs.append("prefix_seen differs from own reconstruction")
            for x in r["revealed"]:
                n = nodes.get(x["cell"])
                if n is None or n["round"] != r["online_round"] or \
                        (x["score"] is not None and abs(x["score"] - n["score"]) > 1e-5):
                    probs.append(f"revealed {x['cell']} != tree")
            for c in batch:
                b, a = (int(x[1:]) for x in c.split("."))
                last[b] = a
                revealed[c] = nodes[c]["score"]
            if probs:
                bad.append((r["online_round"], probs))
            self.step(t, "online_round", f"cycle {t} online round {r['online_round']} batch {batch}",
                      "wrong" if probs else "correct",
                      "; ".join(probs) if probs else f"<= W={W}, one cell per branch, legal before the call "
                      f"({len(legal)} legal), prefix_seen = own reconstruction, reveals = tree.json")
        okn = set(revealed) == set(nodes)
        self.step(t, "online_round", f"cycle {t} online batches cover the recorded tree", "correct" if okn else "wrong",
                  f"{len(revealed)} probed cells vs {len(nodes)} tree nodes")
        # --- attempts
        P = {e["data"]["candidate"]: e["data"] for e in evs if e["kind"] == "proposal" and e["data"]["candidate"].startswith("t")}
        E = {e["data"]["candidate"]: e["data"] for e in evs if e["kind"] == "eval" and e["data"]["candidate"].startswith("t")}
        prev_worlds = [self.trees[i] for i in range(1, t)]
        hist_n = min(200, sum(len(w["nodes"]) for w in prev_worlds[-8:]))
        seen_recipes = {}
        for i in range(1, t):
            for n in self.trees[i]["nodes"]:
                pr = snapshot(self.d, n["artifact_id"])
                if n["fail_class"] == "ok" and n["error"] is None:
                    seen_recipes.setdefault(_norm(pr), f"t{i}/{n['id']}")
        order = sorted(tr["nodes"], key=lambda n: n["seq"])
        for n in order:
            cid = f"t{t}/{n['id']}"
            pr, ev = P.get(cid), E.get(cid)
            notes, verdict = [], "correct"
            parent = tr["root"] if n["parent_id"] == "root" else nodes[n["parent_id"]]
            exp_parent = "root" if n["attempt"] == 0 else f"b{n['branch']}.a{n['attempt'] - 1}"
            if n["parent_id"] != exp_parent:
                verdict = "wrong"
                notes.append(f"parent {n['parent_id']} != {exp_parent}")
            par_prog = snapshot(self.d, parent["artifact_id"])
            prog = snapshot(self.d, n["artifact_id"])
            mine = my_diff(par_prog, prog)
            tl, trunc = traced_diff_lines(pr["diff"] if pr else "")
            if pr is None or (tl != mine[: len(tl)] if trunc else tl != mine):
                verdict = "wrong"
                notes.append("trace diff != own snapshot diff")
            else:
                notes.append(f"diff = own snapshot diff ({len(mine)} +/- lines{', trace clipped' if trunc else ''})")
            # regrade
            fc, g = self.regrade(prog)
            rec_ok = n["fail_class"] == "ok" and n["error"] is None
            if rec_ok:
                if g is None or abs(g - n["score"]) > TOL or abs(ev["summary"]["S"] - n["score"]) > TOL:
                    verdict = "wrong"
                    notes.append(f"score {n['score']} vs own re-grade {g} ({fc})")
                else:
                    notes.append(f"own re-grade {g:.6f} = recorded")
                tri = ev.get("trials") or {}
                if tri and abs(sum(v[0] for v in tri.values()) / len(tri) - n["score"]) > TOL:
                    verdict = "wrong"
                    notes.append("mean of raw trials != S")
                # delta vs parent
                ps = parent["score"] if (parent is tr["root"] or (parent["fail_class"] == "ok" and parent["error"] is None)) else None
                exp_d = None if ps is None else n["score"] - ps
                if (exp_d is None) != (ev.get("delta_vs_parent") is None) or \
                        (exp_d is not None and abs(exp_d - ev["delta_vs_parent"]) > 1e-5):
                    verdict = "wrong"
                    notes.append(f"delta_vs_parent {ev.get('delta_vs_parent')} != {exp_d}")
            else:
                if fc == "ok":
                    verdict = "wrong"
                    notes.append(f"recorded failure ({n['fail_class']}) but own re-grade succeeds: {g}")
                else:
                    notes.append(f"failure reproduced ({fc})")
                    if self.kind == "sumdiff" and "SyntaxError" in str(n["error"]):
                        from rsi.core.artifact import Artifact
                        from rsi.dream.agent import strip_reply_terminators
                        fixed, cl = strip_reply_terminators(Artifact(prog), ["construct.py"])
                        if cl:
                            fc2, g2 = regrade_sumdiff(fixed["construct.py"])
                            verdict = "wrong"
                            notes.append(f"HARNESS: SyntaxError is a reply-parser leftover (trailing fence/===); "
                                         f"without it the program scores {g2:.6f} ({fc2})")
            # context the agent saw
            ctx = (pr or {}).get("context") or {}
            sib_exp = sum(1 for m in tr["nodes"] if m["branch"] != n["branch"] and m["round"] < n["round"])
            lin_exp = n["attempt"]
            if ctx and (ctx.get("lineage") != lin_exp or ctx.get("siblings") != sib_exp or ctx.get("history") != hist_n):
                verdict = "wrong"
                notes.append(f"context {ctx} != expected lineage {lin_exp}, siblings {sib_exp}, history {hist_n}")
            elif ctx:
                notes.append(f"context lineage={lin_exp}, siblings={sib_exp}, history={hist_n} (H_(t-1))")
            # claim vs diff
            if self.kind == "sumdiff":
                st0, st1 = parse_stages(par_prog["construct.py"]), parse_stages(prog["construct.py"])
                ch = (pr or {}).get("change", "")
                if ch.startswith("# apply "):
                    m = re.match(r"# apply (\w+) (\{.*\})", ch)
                    exp = [m.group(1), ast.literal_eval(m.group(2))] if m else None
                    okc = st0 is not None and st1 is not None and st1[: len(st0)] == st0 and len(st1) == len(st0) + 1 \
                        and exp is not None and st1[-1][:2] == exp
                    if not okc:
                        verdict = "wrong"
                    notes.append(f"claim '{ch}' {'=' if okc else '!='} appended stage")
                elif st1 is not None:
                    mechs_new = [s[0] for s in st1]
                    txt = (ch + " " + (pr or {}).get("hypothesis", "")).lower()
                    named = [m for m in MECHS if m in txt]
                    missing = [m for m in dict.fromkeys(mechs_new) if m not in txt]
                    notes.append(f"STAGES {[s[0] for s in (st0 or [])]} -> {mechs_new}; claim names {named}"
                                 + (f"; claim omits {missing}" if missing else ""))
                    key = _norm(prog)
                    if key in seen_recipes:
                        notes.append(f"REPEAT: identical program already evaluated successfully at {seen_recipes[key]}")
                        if verdict == "correct":
                            verdict = "questionable"
                    fn_changed = [l for l in mine if not re.match(r"^[+-](STAGES|INIT|\s*\[|\]|\s*\"|\s*$)", l)]
                    if fn_changed:
                        notes.append(f"also edits {len(fn_changed)} code lines outside INIT/STAGES")
                    if parent is not tr["root"] and parent["fail_class"] != "ok" and "SyntaxError" in str(parent["error"]):
                        hyp = (pr or {}).get("hypothesis", "") + " " + ch
                        blame = re.findall(r"(compil\w*|syntax\w*|fail\w*|markdown|format\w*|stray)", hyp, re.I)
                        notes.append(f"parent failed on a parser leftover; its diagnosis mentions {sorted(set(b.lower() for b in blame))}")
                if rec_ok:
                    seen_recipes.setdefault(_norm(prog), cid)
            else:
                ch = (pr or {}).get("change", "")
                k0, k1 = _knobs(par_prog), _knobs(prog)
                mech = ch.lstrip("# ").strip()
                if mech == "simplify":
                    okc = sum(1 for k in set(k0) | set(k1) if bool(k0.get(k)) and not bool(k1.get(k))) <= 1 and \
                        not any(bool(k1.get(k)) and not bool(k0.get(k)) for k in k1)
                else:
                    okc = bool(k1.get(mech)) and all(bool(k1.get(k)) == bool(k0.get(k)) for k in set(k0) | set(k1) if k != mech)
                if not okc:
                    verdict = "wrong"
                notes.append(f"claim '{mech}': knobs {k0} -> {k1} ({'consistent' if okc else 'INCONSISTENT'})")
                lk = self._leak(prog)
                if lk:
                    verdict = "wrong"
                    notes.append(f"LEAK: task-specific content {lk}")
                else:
                    notes.append("no task id / question / target string of any split in the program")
            self.step(t, "attempt", cid, verdict, "; ".join(notes))
        # --- object gate + decision
        gate = next(e["data"] for e in evs if e["kind"] == "gate" and e["data"].get("level") == "object (best program)")
        dec = next(e["data"] for e in evs if e["kind"] == "decision" and e["data"].get("level") == "object (best program)")
        succ = [n for n in tr["nodes"] if n["fail_class"] == "ok" and n["error"] is None and n["score"] is not None]
        top = max(succ, key=lambda n: (n["score"], -n["seq"])) if succ else None
        acc = top is not None and top["score"] > best_before + 0.0
        exp_after = top["score"] if acc else best_before
        okg = (gate["accept"] == acc) and abs(dec["best_after"] - exp_after) < 1e-5 and \
            (not acc or dec["kept"] == f"t{t}/{top['id']}")
        self.step(t, "gate", f"cycle {t} best-program gate", "correct" if okg else "wrong",
                  f"own: round best {None if top is None else round(top['score'], 6)} at "
                  f"{None if top is None else top['id']} vs best before {best_before} -> accept={acc}; "
                  f"trace accept={gate['accept']}, best_after={dec['best_after']}, kept={dec['kept']}")
        # next root equals this best (checked at t+1 via root step); the kept program is that node's program
        # --- manifest
        man = next(e["data"]["manifest"] for e in evs if e["kind"] == "note" and e["data"].get("what") == "live_cycle_manifest")
        root_s = tr["root"]["score"]
        rb = max([n["score"] for n in succ] + [root_s])
        gain = rb - root_s
        early = max([n["score"] for n in succ if n["attempt"] <= 1] + [root_s]) - root_s
        fails = [n for n in tr["nodes"] if not (n["fail_class"] == "ok" and n["error"] is None and n["evaluated"])]
        exp = {"probe_work": len(tr["nodes"]), "decision_rounds": len(onl), "round_best": rb,
               "gain_early": early / gain if gain > 0 else 0.0, "fail_frac": len(fails) / max(1, len(tr["nodes"])),
               "beta": default_beta(code), "opened_width": len({n["branch"] for n in tr["nodes"]}),
               "max_depth": max((n["attempt"] for n in tr["nodes"]), default=-1), "final_best": exp_after,
               "agent_calls": len(tr["nodes"])}
        diffs = {k: (man.get(k), v) for k, v in exp.items() if not (isinstance(v, float) and abs((man.get(k) or 0) - v) < 1e-6)
                 and man.get(k) != v}
        self.step(t, "manifest", f"cycle {t} live_cycle_manifest", "wrong" if diffs else "correct",
                  f"mismatches {diffs}" if diffs else f"all fields re-derived from tree.json: {exp}")
        # --- dreaming
        self.audit_dream(t, evs)

    def _leak(self, prog: dict) -> list[str]:
        suite = self._aq[2] if self._aq else None
        if suite is None:
            self.regrade(prog)
            suite = self._aq[2]
        text = "\n".join(prog.values())
        hits = []
        for sp in ("evolve", "val", "holdout", "ood"):
            for tid in suite.splits.get(sp, []):
                task = suite.get(tid)
                for s in (tid, task.input[:40], str(task.target)):
                    if len(s) >= 4 and s in text:
                        hits.append(f"{sp}:{tid}:{s[:30]}")
        return hits

    def audit_dream(self, t: int, evs: list[dict]):
        reps = [e["data"] for e in evs if e["kind"] == "eval" and e["data"]["candidate"].startswith("r")]
        if not reps:
            return
        worlds = [self.trees[i] for i in range(1, t + 1)]
        b1, b2, norm = self.cfg["beta1"], self.cfg["beta2"], self.cfg["normalize"]
        vals = []
        for rp in reps:
            rev = rp["candidate"].split("_")[0]
            code = self.hist[rev]["code"]
            notes, verdict = [], "correct"
            Vs = []
            for w, tw in zip(rp["worlds"], worlds):
                # (1) Eq.1 from the traced reveal batches with scores looked up in tree.json
                nodes = {n["id"]: n for n in tw["nodes"]}
                cells = [x.split("=")[0].split(":")[0] for b in w["reveal_batches"] for x in b["revealed"]]
                succ = [nodes[c]["score"] for c in cells if nodes[c]["fail_class"] == "ok" and nodes[c]["error"] is None]
                best = max(succ + [tw["root"]["score"]])
                ceil = ceiling_of(tw)
                N, k = len(cells), len(w["reveal_batches"])
                v = eq1(best, tw["root"]["score"], ceil, N, k, b1, b2, norm)
                if w.get("disqualified"):
                    notes.append(f"{w['world']} disqualified")
                    v = w["V_i"]
                elif abs(v - w["V_i"]) > 1e-5 or N != w["N"] or k != w["k"]:
                    verdict = "wrong"
                    notes.append(f"{w['world']}: own Eq.1 {v:.6f} (N={N},k={k}) != trace {w['V_i']}")
                # (2) own independent replay of the archived code
                ir = indep_replay(code, tw, self.cfg["W"], self.mans, self.cfg)
                iv = eq1(ir["best"], ir["root"], ir["ceiling"], ir["N"], ir["k"], b1, b2, norm)
                same = [r["batch"] for r in ir["batches"]] == [b["batch"] for b in w["reveal_batches"]] and \
                    [sorted(r["revealed"]) for r in ir["batches"]] == \
                    [sorted(x.split("=")[0].split(":")[0] for x in b["revealed"]) for b in w["reveal_batches"]]
                if ir["error"] or not same or abs(iv - w["V_i"]) > 1e-5:
                    verdict = "wrong"
                    notes.append(f"{w['world']}: OWN replay differs (err={ir['error']}, V={iv:.6f}, batches "
                                 f"{[r['batch'] for r in ir['batches']][:3]}...)")
                # (3) no peeking: every revealed cell was the recorded child (root pick -> earliest branch)
                Vs.append(v)
            V = sum(Vs) / len(Vs)
            if abs(V - rp["summary"]["S"]) > 1e-5:
                verdict = "wrong"
                notes.append(f"mean V {V:.6f} != trace {rp['summary']['S']}")
            vals.append(V)
            notes.insert(0, f"V = {V:.6f} over {len(Vs)} world(s) re-derived (own Eq.1 on tree.json scores) and "
                            f"re-replayed by an independent replay implementation: per world {[round(x, 4) for x in Vs]}")
            self.step(t, "replay_eval", rp["candidate"], verdict, "; ".join(notes))
        # revisions
        props = [e["data"] for e in evs if e["kind"] == "proposal" and e["data"]["candidate"].startswith("r")]
        phase_vals = {reps[0]["candidate"].split("_")[0]: vals[0]}
        rep_by = {rp["candidate"]: v for rp, v in zip(reps, vals)}
        for pr in props:
            rev = pr["candidate"].split("_")[0]
            notes, verdict = [], "correct"
            parent = pr["parent"]
            base = self.hist.get(parent, {}).get("code", "")
            new = self.hist[rev]["code"]
            mine = my_diff({"method.py": base}, {"method.py": new})
            tl, trunc = traced_diff_lines(pr["diff"])
            if tl != (mine[: len(tl)] if trunc else mine):
                verdict = "wrong"
                notes.append("trace diff != own diff of history code")
            else:
                notes.append(f"diff = own diff of history/{parent} -> history/{rev} ({len(mine)} lines)")
            # 'start from a strong recent policy': parent = argmax (V, index) of this phase's versions so far
            cand = {k: v for k, v in phase_vals.items()}
            exp_parent = max(cand, key=lambda r: (round(cand[r], 9), int(r[1:])))
            if parent != exp_parent:
                verdict = "wrong"
                notes.append(f"base {parent} != strongest-so-far {exp_parent} ({cand})")
            else:
                notes.append(f"base {parent} = strongest so far (value, recency)")
            if pr.get("moves") is not None:
                p0, p1 = params_of(base), params_of(new)
                ch = {k: [p0.get(k), p1.get(k)] for k in set(p0) | set(p1) if p0.get(k) != p1.get(k)}
                if p0 and (pr.get("params_changed") or {}) != ch:
                    verdict = "wrong"
                    notes.append(f"params_changed {pr.get('params_changed')} != own PARAMS diff {ch}")
                bad = []
                for mv in pr["moves"]:
                    m = re.match(r"perturb (\w+) -> ([\d.\-]+)$", mv)
                    if m and (m.group(1) not in p1 or abs(float(p1[m.group(1)]) - float(m.group(2))) > 1e-9
                              or p0.get(m.group(1)) == p1.get(m.group(1))):
                        bad.append(mv)
                if bad:
                    verdict = "wrong"
                    notes.append(f"claimed moves not in code: {bad}")
                noop = [mv for mv in pr["moves"] if "unchanged" in mv]
                notes.append(f"mutator moves {pr['moves']}" + (f" ({len(noop)} declared no-op)" if noop else ""))
            else:
                notes.append(f"LLM claim: {pr['change'][:200]}")
                if pr.get("repairs"):
                    notes.append(f"{pr['repairs']} repair round(s)")
            # leakage + forbidden API in the new code
            forb = self._forbidden(t)
            hits = [f for f in forb if f in new]
            api = [a for a in ("best_so_far", "budget_spent", "open(", "import os", "__import__", "eval(", "exec(")
                   if a in new]
            if hits or api:
                verdict = "wrong" if api else "questionable"
                notes.append(f"leak/forbidden terms in code: {hits} {api}")
            phase_vals[rev] = rep_by.get(next((c for c in rep_by if c.startswith(rev)), ""), float("-inf"))
            self.step(t, "revision", pr["candidate"], verdict, "; ".join(notes))
        # selection
        dec = next(e["data"] for e in evs if e["kind"] == "decision" and e["data"].get("level") == "policy (replay selection)")
        best = max(vals)
        winners = [i for i, v in enumerate(vals) if v >= best - 1e-12]
        idx = 0 if 0 in winners else winners[0]
        chosen = reps[idx]["candidate"]
        nxt = next((e["data"] for e in self.ev if e["kind"] == "round_start" and e["round"] == t + 1), None)
        dep_ok = nxt is None or nxt["deployed_policy"]["rev"] == chosen.split("_")[0]
        ok = dec["incumbent_after"] == chosen and dep_ok and vals[idx] >= vals[0] - 1e-12
        self.step(t, "selection", f"cycle {t} policy selection", "correct" if ok else "wrong",
                  f"own V {[round(v, 6) for v in vals]} -> argmax (incumbent kept on ties) {chosen}; trace "
                  f"{dec['incumbent_after']}; next cycle deploys {None if nxt is None else nxt['deployed_policy']['rev']}; "
                  f"V(m*) - V(0) = {vals[idx] - vals[0]:+.6f} >= 0")
        # beta sweep: recompute the Pareto numbers from the traced points
        sw = next((e["data"] for e in evs if e["kind"] == "note" and e["data"].get("what") == "beta_sweep"), None)
        if sw:
            pts = sorted(sw["points"], key=lambda p: (p["probes_frac"], -p["attainment"]))
            fr, bst = [], -1.0
            for p in pts:
                if p["attainment"] > bst:
                    bst = p["attainment"]
                    fr.append((min(1.0, p["probes_frac"]), bst))
            auc = sum(a * max(0.0, (fr[i + 1][0] if i + 1 < len(fr) else 1.0) - x) for i, (x, a) in enumerate(fr))
            pen = sum(p["parallel_penalty"] for p in sw["points"]) / len(sw["points"])
            att = [p["attainment"] for p in sw["points"]]
            wk = [p["probes_frac"] for p in sw["points"]]
            deg = max(att) - min(att) < 1e-9 and max(wk) - min(wk) < 1e-9
            okb = abs(auc - sw["auc"]) < 2e-3 and abs(pen - sw["parallel_penalty"]) < 2e-3 and deg == sw["degenerate"]
            self.step(t, "beta_sweep", f"cycle {t} beta sweep of {sw['policy']}", "correct" if okb else "wrong",
                      f"own AUC {auc:.4f} / penalty {pen:.4f} / degenerate {deg} vs trace {sw['auc']} / "
                      f"{sw['parallel_penalty']} / {sw['degenerate']} (feedback only; not a selection input under Eq.1)")

    def _forbidden(self, t: int) -> list[str]:
        out = []
        for i in range(1, t + 1):
            tw = self.trees[i]
            succ = [n for n in tw["nodes"] if n["fail_class"] == "ok" and n["error"] is None and n["score"] is not None]
            if succ:
                b = max(succ, key=lambda n: (n["score"], -n["seq"]))
                if b["score"] >= tw["root"]["score"]:
                    out += [f"{b['score']:.4f}"]
        return sorted(set(out))

    # ---- what the live agent saw: rebuild every Listing-1 prompt from disk, match the cache key
    def audit_agent_prompts(self):
        cache = OUT / f".cache_{self.run}"
        if not cache.exists():
            return
        import hashlib

        from rsi.core.artifact import Artifact
        from rsi.core.llm import ClaudeCLI
        from rsi.domains.discovery import SumDiffDomain
        from rsi.dream.agent import AttemptContext, EditorAgent, history_records, record_of
        from rsi.dream.question import _seed_of
        from rsi.dream.tree import DiscoveryTree

        dom = SumDiffDomain()
        agent = EditorAgent(ClaudeCLI("haiku"), editable=["construct.py"])
        name = ClaudeCLI("haiku").name
        seed_score = self.trees[1]["root"]["score"]
        worlds = {t: DiscoveryTree.from_json(self.trees[t]) for t in self.trees}
        ok = bad = 0
        misses = []
        from rsi.dream import agent as agent_mod

        legacy = self.run == "sumdiff_live"      # recorded BEFORE the tail-of-error fix: head-only errors

        def old_render(r, max_chars: int = 600) -> str:
            head = f"[round {r.round} {r.cell}{' dir=' + r.direction if r.direction else ''}] "
            res = f"score={r.score:.6g}" if r.score is not None and r.fail_class == "ok" else \
                f"FAILED ({r.fail_class}): {str(r.error)[:200]}"
            prop = (r.proposal or "").strip().replace("\n", " ")[:max_chars]
            return f"{head}{res}\n  proposal: {prop}"

        cur_render = agent_mod.AttemptRecord.render
        if legacy:
            agent_mod.AttemptRecord.render = old_render
        for t in sorted(self.trees):
            tr = worlds[t]
            hist = history_records([worlds[i] for i in sorted(worlds) if i < t][-8:])
            for n in tr.non_root():
                parent = tr.node(n.parent_id)
                d = json.loads((self.d / "snapshots" / parent.artifact_id[:2] / f"{parent.artifact_id}.json").read_text())
                ws = Artifact({k: v for k, v in d["files"].items() if v is not None})
                lineage = [record_of(m) for m in tr.nodes() if m.branch == n.branch and m.attempt < n.attempt]
                sibs = [record_of(m) for m in tr.non_root() if m.branch != n.branch and m.round < n.round]
                dirn = dict(tr.branch_tags.get(n.branch, {}))
                ctx = AttemptContext(dom.describe(), parent.id, ws, parent.score, n.branch, n.attempt, t, lineage, sibs,
                                     hist, seed_score, dirn,
                                     f"Direction assigned to this branch: {dirn['direction']}." if dirn.get("direction")
                                     else "", ["construct.py"])
                prompt = agent.editor.build_prompt(ws, agent.build_instructions(ctx), None, ["construct.py"])
                seed = _seed_of(self.cfg["seed"], t, n.branch, n.attempt)
                h = hashlib.sha256(json.dumps([name, agent.system, prompt, None, seed]).encode()).hexdigest()
                if (cache / h[:2] / f"{h}.json").exists():
                    ok += 1
                else:
                    bad += 1
                    misses.append(f"t{t}/{n.id}")
        agent_mod.AttemptRecord.render = cur_render
        self.step(None, "prompts", "every Listing-1 agent prompt rebuilt from disk state",
                  "correct" if not bad else "wrong",
                  f"{ok}/{ok + bad} prompts rebuilt from tree.json + snapshots (problem, parent workspace, lineage, "
                  f"siblings of earlier rounds, H_(t-1), seed baseline, direction) hash to an entry of the fresh LLM "
                  f"cache: the agent saw exactly that and nothing else (no sealed data exists for this domain)"
                  + (" [rebuilt with the pre-fix head-only error rendering the run used]" if legacy else "")
                  + (f"; not reproduced: {misses[:6]}" if misses else ""))

    def audit_developer_prompts(self):
        cache = OUT / f".cache_{self.run}"
        dp = self.d / "dream_prompts"
        if not cache.exists() or not dp.exists():
            return
        import hashlib

        from rsi.core.llm import ClaudeCLI
        from rsi.dream.developer import DEVELOPER_SYSTEM

        name = ClaudeCLI("haiku").name
        ok, bad, leak = 0, [], []
        for f in sorted(dp.glob("*_prompt.txt")):
            m = re.match(r"r(\d{4})_t(\d+)m(\d+)_a(\d+)_prompt", f.name)
            t, mm, att = int(m.group(2)), int(m.group(3)) - 1, int(m.group(4))
            seed = (self.cfg["seed"] * 100003 + t * 101 + mm) * 10 + att
            prompt = f.read_text()
            h = hashlib.sha256(json.dumps([name, DEVELOPER_SYSTEM, prompt, None, seed]).encode()).hexdigest()
            (ok := ok + 1) if (cache / h[:2] / f"{h}.json").exists() else bad.append(f.name)
            # the developer may see trace data (between-round feedback) but never data of a LATER world
            later = [f"iter{i:02d}" for i in self.trees if i > t]
            if any(f"trace_pool/{w}" in prompt for w in later):
                leak.append(f.name)
        self.step(None, "prompts", "every Listing-2 developer prompt (dream_prompts/) is the prompt the model got",
                  "correct" if not bad and not leak else "wrong",
                  f"{ok}/{ok + len(bad)} saved developer prompts hash to an entry of the fresh LLM cache"
                  + (f"; unmatched {bad}" if bad else "") + (f"; later-world data in {leak}" if leak else
                                                             "; no manifest of a later live cycle in any prompt"))

    # ---- accounting / monitor ---------------------------------------------------------------
    def audit_costs(self):
        end = next(e["data"] for e in self.ev if e["kind"] == "run_end")
        sizes = [len(self.trees[t]["nodes"]) for t in sorted(self.trees)]
        props = [e["data"] for e in self.ev if e["kind"] == "proposal" and e["data"]["candidate"].startswith("t")]
        usd = sum((p.get("usage") or {}).get("usd", 0.0) for p in props)
        ok = sizes == end["calls_per_cycle"] and sum(sizes) == end["cost"]["agent_calls"] and \
            abs(usd - end["cost"]["agent_usd"]) < 1e-6
        ev = f"calls per cycle own {sizes} vs trace {end['calls_per_cycle']}; meter {end['cost']['agent_calls']}; " \
             f"sum of per-attempt usd {usd:.4f} vs meter {end['cost']['agent_usd']:.4f}; developer usd " \
             f"{end['cost']['developer_usd']:.4f} ({end['cost']['developer_calls']} calls)"
        cache = OUT / f".cache_{self.run}"
        if cache.exists():
            tot, n = 0.0, 0
            for f in glob.glob(str(cache / "*" / "*.json")):
                d = json.loads(Path(f).read_text())
                tot += float((d.get("usage") or {}).get("cost_usd", 0.0))
                n += 1
            loop = end["cost"]["agent_usd"] + end["cost"]["developer_usd"]
            ok = ok and abs(tot - loop) < 1e-3 and n == end["cost"]["agent_calls"] + end["cost"]["developer_calls"]
            ev += f"; fresh cache: {n} entries, ${tot:.4f} (= loop ${loop:.4f})"
        rp = next((e["data"] for e in self.ev if e["kind"] == "note" and False), None)  # noqa: F841
        self.step(None, "cost", "cost accounting", "correct" if ok else "wrong", ev)
        mons = [e for e in self.ev if e["kind"] == "monitor"]
        if mons:
            kept = [e["data"]["kept"] for e in self.ev if e["kind"] == "decision"
                    and e["data"].get("level") == "object (best program)" and e["data"].get("kept")]
            names = [m["data"]["version"] for m in mons]
            okm = names == ["seed"] + kept
            self.step(None, "monitor", "shadow monitor events", "correct" if okm else "wrong",
                      f"monitored {names} = seed + every new best {kept}; sealed numbers never read by the loop "
                      f"(static check in AUDIT.md); shadow usage {end.get('shadow_usage')}")

    def run_all(self):
        self.audit_baseline()
        for t in sorted(self.trees):
            self.audit_cycle(t)
        self.audit_agent_prompts()
        self.audit_developer_prompts()
        self.audit_costs()
        # auditor's judgement (read from the traces by hand; see AUDIT.md): steps that pass every
        # mechanical check but do not make sense / were misled. Only ever downgrades a verdict.
        rank = {"correct": 0, "unverifiable": 1, "questionable": 2, "wrong": 3}
        for s in self.steps:
            j = JUDGEMENT.get((self.run, s["kind"], s["step"]))
            if j and rank[j[0]] > rank[s["verdict"]]:
                s["verdict"] = j[0]
                s["evidence"] = f"JUDGEMENT: {j[1]} || mechanical: {s['evidence']}"
        c = Counter(s["verdict"] for s in self.steps)
        res = {"run": self.run, "counts": dict(c), "steps": self.steps}
        (self.d / "stepaudit.json").write_text(json.dumps(res, indent=1, default=str))
        return res


_ONE_MANIFEST = ("the adaptive template calls a single live manifest 'live best still improving' "
                 "(improving = len(history) < 2 or ...); spec §4.5 treats insufficient history as a conservative "
                 "bootstrap. Outcome identical here (hold = the fallback grid); the reason text is wrong")
_MISLED = ("the parent's SyntaxError was a reply-parser leftover, but the agent only saw the HEAD of the "
           "traceback (fixed after the run) and blamed the idea/ordering: ")
_REPAIR = ("developer first attempt rejected on a reply-parser leftover (trailing fence) reported only as "
           "'invalid syntax, line N'; the repair round invented a cause (")
JUDGEMENT = {
    ("sumdiff_offline", "selection", "cycle 1 policy selection"): (
        "questionable", "rule-correct argmax (V 0.910 vs 0.865 on ONE world), but a replay artefact: on 40 fresh "
        "one-cycle online searches from the cycle-2 root (stage-A's 8 seeds + 32 new ones, re-run by stage B) r0001 "
        "never found more than pi_1 and found less on 18/40: mean gain -0.0028 (95% bootstrap CI [-0.0046, -0.0012]) "
        "at 8.2 vs 15 calls (stage A's 8 seeds alone: -0.0060). Frugality bias of the replay ceiling - spec §8.2/§8.4"),
    ("sumdiff_live_b", "selection", "cycle 1 policy selection"): (
        "unverifiable", "rule-correct and prefix-justified (r0001 closes branch b2 after two flat results "
        "1.0, 1.0; the unrevealed b2.a2 = 1.016 was not the ceiling), V 0.9333 vs 0.925 on ONE world; whether "
        "r0001 is better ONLINE is not measured (a live ground truth would cost as much as the run)"),
    ("sumdiff_offline", "plan", "cycle 2 plan_grid (r0001)"): ("questionable", _ONE_MANIFEST),
    ("agentqa_offline", "plan", "cycle 2 plan_grid (r0003)"): ("questionable", _ONE_MANIFEST),
    ("sumdiff_live", "attempt", "t1/b0.a2"): ("wrong", _MISLED + "'avoids the fringe-related compilation issue'"),
    ("sumdiff_live", "attempt", "t1/b0.a3"): ("questionable", _MISLED + "'reversed ... order, causing compilation failure'"),
    ("sumdiff_live", "attempt", "t1/b2.a3"): ("questionable", _MISLED + "'the reversed grow->anneal order is suboptimal'"),
    ("sumdiff_live", "revision", "r0002_t1m2"): ("wrong", _REPAIR + "'indentation error on line 170'); change text = repair only"),
    ("sumdiff_live", "revision", "r0005_t2m2"): ("wrong", _REPAIR + "'indentation ... line 230'); change text = repair only"),
    ("sumdiff_live", "revision", "r0006_t2m3"): ("wrong", _REPAIR + "'recovery_candidates sorting'); change text = repair only"),
}


def _norm(prog: dict) -> str:
    return json.dumps({k: re.sub(r"\s+", " ", v).strip() for k, v in sorted(prog.items())}, sort_keys=True)


def _knobs(prog: dict) -> dict:
    src = prog.get("harness.py", "")
    if "\nKNOBS = " not in src:
        return {}
    return dict(ast.literal_eval(src.split("\nKNOBS = ", 1)[1].split("\n", 1)[0]))


if __name__ == "__main__":
    for run in sys.argv[1:]:
        r = Audit(run).run_all()
        print(run, r["counts"])
        for s in r["steps"]:
            if s["verdict"] != "correct":
                print(f"  [{s['verdict']}] r{s['round']} {s['kind']} {s['step']}: {s['evidence'][:400]}")
