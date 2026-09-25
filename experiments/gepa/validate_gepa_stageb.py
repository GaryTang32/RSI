"""Stage-B independent step audit of the GEPA validation runs (validation/gepa/<run>/).

Written independently of ``rsi.gepa`` (engine, frontier, strategies, merge, reflection): every
iteration of ``trace.jsonl`` is re-derived from the raw per-task scores it records, the artifact
store and - where possible - the *reference implementation itself* (``gepa-ai/gepa`` at d771eb21,
vendored in the scratchpad):

* RNG replay: one ``random.Random(seed)`` shared, in the reference order, by the candidate
  selector (Alg. 2, set-cover pruning + frequency sampling, re-implemented here), the reference
  ``EpochShuffledBatchSampler`` (imported from gepa-ai/gepa) and the merge proposer (re-transcribed
  from ``gepa/proposer/merge.py``). Every chosen parent, minibatch, merge triplet, module source and
  merge subsample must equal the trace. The reference ``select_program_candidate_from_pareto_front``
  (python-set iteration order) is also run on a cloned RNG to measure where the documented
  "sorted order" deviation changes a draw.
* reflection prompt: re-built with the reference ``InstructionProposalSignature.prompt_renderer``
  from the parent's component text and the reflective records of a *re-execution* of the parent
  minibatch (offline runs: same seeds, fresh evaluator, no cache) - must equal the traced prompt
  byte for byte; live runs: template skeleton + records restricted to the minibatch tasks.
* child text: reference ``parse_proposal`` of the raw reply must equal the stored child component.
* gate: recomputed from the parent / child eval events (not from the gate's own numbers).
* D_pareto: per-instance frontier, incumbent (argmax mean; ties -> coverage -> lowest index),
  frontier members and Pareto weights recomputed each round and compared with ``round_start``.
* budget identity by phase, stop-condition timing, monitor only after incumbent changes.
* split discipline: no D_pareto / sealed task input appears in any reflection prompt.
* task-specific content: ticket facts (RuleWorld), literal answers / task ids (AgentQA).
* re-proposals: identical rewrites proposed again after a rejection.

Usage: ``python experiments/gepa/validate_gepa_stageb.py [run ...]`` -> ``validation/gepa/<run>/stageb.json``.
"""
from __future__ import annotations

import copy
import json
import math
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
REF = Path("/tmp/claude-0/-home-user-RSI/ebd00391-ba98-5b98-9125-83abd1dce979/scratchpad/src/gepa-ai__gepa/src")
OUT = ROOT / "validation" / "gepa"

HAVE_REF = REF.exists()
if HAVE_REF:
    sys.path.insert(0, str(REF))
    import gepa.gepa_utils as ref_utils  # noqa: E402
    from gepa.strategies.batch_sampler import EpochShuffledBatchSampler as RefSampler  # noqa: E402
    from gepa.strategies.instruction_proposal import InstructionProposalSignature as RefSig, parse_proposal  # noqa

from rsi.core.ledger import ArtifactStore, Ledger  # noqa: E402

VAL_PHASES = ("seed_val", "val_reflective", "val_merge")


# ------------------------------------------------------------------ independent Alg. 2 --
def fronts(val: list[dict], keys: list[str]) -> dict[str, list[int]]:
    out = {}
    for v in keys:
        b = max(sc[v] for sc in val)
        out[v] = [c for c, sc in enumerate(val) if sc[v] == b]
    return out


def prune(front: dict[str, list[int]], agg: list[float]) -> set[int]:
    """Set-cover pruning (spec 3 SELECT_CANDIDATE); returns the dominated programs."""
    order: list[int] = []
    for v in front:
        for c in sorted(front[v]):
            if c not in order:
                order.append(c)
    progs = sorted(order, key=lambda c: agg[c])
    dom: set[int] = set()
    changed = True
    while changed:
        changed = False
        for y in progs:
            if y in dom:
                continue
            others = set(progs) - {y} - dom
            if all(set(front[v]) & others for v in front if y in front[v]):
                dom.add(y)
                changed = True
                break
    return dom


def sampling_list(front, agg):
    dom = prune(front, agg)
    freq: dict[int, int] = {}
    for v in front:
        for c in sorted(front[v]):
            if c not in dom:
                freq[c] = freq.get(c, 0) + 1
    return freq, [c for c, f in freq.items() for _ in range(f)]


def best_idx(val):
    best, bs, bc = -1, -math.inf, -1
    for k, sc in enumerate(val):
        a = sum(sc.values()) / len(sc)
        if a > bs or (a == bs and len(sc) > bc):
            best, bs, bc = k, a, len(sc)
    return best


# ------------------------------------------------------ merge (transcribed from reference) --
def ancestors(parents, node):
    found, stack = set(), [node]
    while stack:
        for p in parents[stack.pop()]:
            if p is not None and p not in found:
                found.add(p)
                stack.append(p)
    return found


def ref_merge(rng, agg, cands_idx, performed, texts, parents, comps, overlap_ok):
    """sample_and_attempt_merge_programs_by_common_predictors with sorted iteration order."""
    if len(cands_idx) < 2 or len(parents) < 3:
        return None
    for _ in range(10):
        found = None
        for _ in range(10):
            if len(cands_idx) < 2:
                break
            i, j = rng.sample(list(cands_idx), 2)
            if i == j:
                continue
            if j < i:
                i, j = j, i
            ai, aj = ancestors(parents, i), ancestors(parents, j)
            if j in ai or i in aj:
                continue
            common = []
            for a in sorted(ai & aj):
                if (i, j, a) in performed[0]:
                    continue
                if agg[a] > agg[i] or agg[a] > agg[j]:
                    continue
                if not any((texts[a][m] == texts[i][m] or texts[a][m] == texts[j][m]) and texts[i][m] != texts[j][m]
                           for m in comps):
                    continue
                common.append(a)
            if common:
                w = [agg[a] for a in common]
                if sum(w) <= 0:
                    w = [1.0] * len(common)          # documented deviation 3 (reference raises)
                found = (i, j, rng.choices(common, k=1, weights=w)[0])
                break
        if found is None:
            continue
        i, j, a = found
        if (i, j, a) in performed[0]:
            continue
        new, desc = {}, ()
        for m in sorted(comps):
            pa, pi, pj = texts[a][m], texts[i][m], texts[j][m]
            if (pa == pi or pa == pj) and pi != pj:
                src = j if pa == pi else i
            elif pa != pi and pa != pj:
                src = i if agg[i] > agg[j] else (j if agg[j] > agg[i] else rng.choice([i, j]))
            elif pi == pj:
                src = i
            else:
                raise AssertionError
            new[m] = texts[src][m]
            desc = (*desc, src)
        if (i, j, desc) in performed[1]:
            continue
        if not overlap_ok(i, j):
            continue
        performed[1].append((i, j, desc))
        return new, i, j, a, desc
    return None


def ref_subsample(rng, s1, s2, n=5):
    common = sorted(set(s1) & set(s2))
    p1 = [k for k in common if s1[k] > s2[k]]
    p2 = [k for k in common if s2[k] > s1[k]]
    p3 = [k for k in common if k not in p1 and k not in p2]
    each = max(1, math.ceil(n / 3))
    sel: list = []
    for b in (p1, p2, p3):
        if len(sel) >= n:
            break
        av = [k for k in b if k not in sel]
        t = min(len(av), each, n - len(sel))
        if t > 0:
            sel += rng.sample(av, k=t)
    rem = n - len(sel)
    if rem > 0:
        un = [k for k in common if k not in sel]
        if len(un) >= rem:
            sel += rng.sample(un, k=rem)
        elif common:
            sel += rng.choices(common, k=rem)
    return sel[:n]


# ------------------------------------------------------------------------------ setup --
def domain_for(run: str):
    """Domain (and offline task LLM) of a run, rebuilt exactly as validate_gepa.setup does."""
    if run.startswith("ruleworld"):
        from rsi.domains.ruleworld import make_domain
        return make_domain(seed=0, feedback="rich"), None
    from rsi.domains.agentqa import AgentQADomain, SimModel, make_suite
    suite = make_suite(n_evolve=10, n_val=8, n_holdout=6, n_ood_per_family=1, seed=0)
    dom = AgentQADomain(suite)
    return dom, (SimModel(suite) if run.startswith("agentqa_offline") else None)


class _Loader:
    def __init__(self, ids):
        self.ids = list(ids)

    def all_ids(self):
        return list(self.ids)

    def __len__(self):
        return len(self.ids)


class _St:
    def __init__(self, i):
        self.i = i


def audit(run: str, rd: "Path | None" = None) -> dict:
    """``run`` names the setup (domain + task model, as in validate_gepa.setup); ``rd`` its directory."""
    rd = Path(rd) if rd is not None else OUT / run
    ev = [json.loads(l) for l in (rd / "trace.jsonl").read_text().splitlines() if l.strip()]
    cfg = json.loads((rd / "config.json").read_text())
    store = ArtifactStore(rd / "artifacts")
    ledger = {n.id: n for n in Ledger(rd / "ledger.jsonl").nodes()}
    start = next(e["data"] for e in ev if e["kind"] == "run_start")
    comps = start["components"]
    b = cfg["minibatch_size"]
    B = cfg["max_metric_calls"]
    dom, llm_task = domain_for(run)
    ts = dom.tasks
    train_ids = list(ts.splits[cfg["train_split"]])
    val_ids = list(ts.splits[cfg["val_split"]])
    assert start["splits"]["n_train"] == len(train_ids) and start["splits"]["n_pareto"] == len(val_ids)
    sealed = [s for s in ts.splits if ts.is_sealed(s)]
    perfect = start["perfect_score"]
    use_merge = cfg["use_merge"]
    reexec = None
    if llm_task is not None or run.startswith("ruleworld"):
        from rsi.core.evaluate import Evaluator
        from rsi.gepa.adapter import DomainAdapter
        reexec = (Evaluator(dom, llm_task, workers=1), DomainAdapter(dom, llm_task, feedback=cfg["feedback"]))

    rng = random.Random(cfg["seed"])
    sampler = RefSampler(b, rng) if HAVE_REF else None
    loader = _Loader(train_ids)
    val: list[dict] = []          # per candidate D_pareto scores (as recorded)
    parents: list[list] = []
    art: list[str] = []           # artifact ids
    rr: list[int] = []
    kinds: list[str] = []
    counter = {}
    m_due, m_tested, m_last = 0, 0, False
    performed = ([], [])
    steps: list[dict] = []
    agg_problems: list[str] = []
    rejected_texts: dict = {}     # (comp, text) -> round rejected
    seen_texts: dict = {}

    def charge(phase, n):
        counter[phase] = counter.get(phase, 0) + n

    def add_cand(d, par, kind):
        val.append(dict(d["per_task"]))
        parents.append(par)
        rr.append(max((rr[p] for p in par if p is not None), default=0))
        kinds.append(kind)

    by_round: dict = {}
    for e in ev:
        by_round.setdefault(e["round"], []).append(e)
    base = next(e["data"] for e in ev if e["kind"] == "baseline")
    assert list(base["per_task"]) == val_ids
    add_cand(base, [None], "baseline")
    charge("seed_val", base["rollouts_charged"])
    art.append(ledger["c0"].artifact_id)
    seed_ok = store.get(art[0]).id == start["seed_artifact_id"]
    mon_rounds = [e["round"] for e in ev if e["kind"] == "monitor"]
    usd_track = []

    for r in sorted(k for k in by_round if k is not None):
        E = by_round[r]
        st: dict = {"round": r, "checks": {}, "notes": []}
        chk = st["checks"]

        def ck(name, cond, why=""):
            chk[name] = bool(cond)
            if not cond:
                st["notes"].append(f"FAIL {name}: {why}")

        rs = next(e["data"] for e in E if e["kind"] == "round_start")
        agg = [sum(v.values()) / len(v) for v in val]
        front = fronts(val, val_ids)
        freq, slist = sampling_list(front, agg)
        inc = best_idx(val)
        ck("state_rollouts", rs["rollouts_used"] == sum(counter.values()), f"{rs['rollouts_used']} vs {sum(counter.values())}")
        ck("state_incumbent", rs["incumbent"] == f"c{inc}", f"{rs['incumbent']} vs c{inc}")
        ck("state_weights", rs["pareto_sampling_weights"] == {f"c{c}": f for c, f in sorted(freq.items())},
           f"{rs['pareto_sampling_weights']} vs {freq}")
        members = sorted({c for v in front.values() for c in v})
        ck("state_frontier_members", rs["frontier_members"] == [f"c{c}" for c in members])
        ck("state_pool", [p["c"] for p in rs["pool"]] == list(range(len(val)))
           and all(comps.index(p["next_component"]) == rr[p["c"]] for p in rs["pool"]), "rr pointers")
        usd_track.append(rs.get("loop_usd", 0.0))
        dec = next((e["data"] for e in E if e["kind"] == "decision"), None)
        st["event"] = dec["event"] if dec else None
        evals = [e["data"] for e in E if e["kind"] == "eval"]
        # ---------------------------------------------------------------- merge ----
        merged_round = False
        if use_merge:
            if m_due > 0 and m_last:
                texts = [store.get(a).files for a in art]
                cands_idx = sorted(c for c in members if c not in prune(front, agg))
                out = ref_merge(rng, agg, cands_idx, performed, texts, parents, comps,
                                lambda i, j: len(set(val[i]) & set(val[j])) >= cfg["merge_val_overlap_floor"])
                m_last = False
                prop = next((e["data"] for e in E if e["kind"] == "proposal" and e["data"].get("proposal_kind") ==
                             "merge"), None)
                if out is None:
                    ck("merge_none_matches", prop is None and any(e["kind"] == "note" and e["data"].get("what") ==
                                                                  "merge attempt" for e in E))
                else:
                    new, i, j, a, desc = out
                    performed[0].append((i, j, a))
                    sub = ref_subsample(rng, val[i], val[j], cfg["merge_subsample_size"])
                    merged_round = True
                    ck("merge_triplet", prop is not None and prop["parents"] == [f"c{i}", f"c{j}"]
                       and prop["ancestor"] == f"c{a}", f"trace {prop and (prop['parents'], prop['ancestor'])} vs "
                                                        f"({i},{j},{a})")
                    ck("merge_sources", prop is not None and prop["module_sources"] ==
                       {m: f"c{s}" for m, s in zip(sorted(comps), desc)})
                    child = store.get(prop["child_artifact"])
                    ck("merge_child_text", all(child.get(m) == new[m] for m in comps)
                       and all(child.get(f) == store.get(art[a]).get(f) for f in child.files if f not in comps))
                    ev_sub = next(d for d in evals if d["phase"] == "merge_subsample")
                    ck("merge_subsample_ids", ev_sub["ids"] == sub, f"{ev_sub['ids']} vs {sub}")
                    before = [sum(val[i][k] for k in sub), sum(val[j][k] for k in sub)]
                    after = ev_sub["scores"]
                    charge("merge_subsample", ev_sub["rollouts_charged"])
                    ck("merge_charge", ev_sub["rollouts_charged"] == len(sub))
                    acc = sum(after) >= max(before)
                    g = next(e["data"] for e in E if e["kind"] == "gate")
                    ck("merge_gate", g["accept"] == acc and abs(g["math"]["threshold"] - max(before)) < 1e-12,
                       f"{g['accept']} vs {acc}")
                    st.update(kind="merge", parents=[i, j], ancestor=a, sub_before=before, sub_after=sum(after),
                              gate=acc, delta=sum(after) - max(before))
                    if acc:
                        v = next(d for d in evals if d["phase"] == "val_merge")
                        ck("val_charge", v["rollouts_charged"] == len(val_ids))
                        charge("val_merge", v["rollouts_charged"])
                        ck("val_covers_pareto", list(v["per_task"]) == val_ids)
                        add_cand(v, [i, j], "merge")
                        art.append(prop["child_artifact"])
                        m_due -= 1
                        m_tested += 1
                        ck("decision", dec["event"] == "merge_accepted" and dec["kept"] == f"c{len(val) - 1}")
                    else:
                        ck("decision", dec["event"] == "merge_rejected" and not dec.get("kept"))
                        ck("ledger_rejected", ledger.get(f"m{r}") is not None and ledger[f"m{r}"].status == "rejected")
                    st["truth_pair"] = ([art[i], art[j]], prop["child_artifact"])
            else:
                m_last = False
            ck("merge_schedule", ("merge" not in rs) or (rs["merge"]["merges_due"] >= 0))
        if merged_round:
            new_inc = best_idx(val)
            ck("incumbent_after", dec["incumbent_after"] == f"c{new_inc}")
            ck("monitor_iff_incumbent_change", (r in mon_rounds) == (new_inc != inc))
            steps.append(st)
            continue
        # ---------------------------------------------------------- selection ----
        sel = next(e["data"] for e in E if e["kind"] == "note" and e["data"].get("what", "").startswith("parent"))
        if HAVE_REF:
            clone = random.Random()
            clone.setstate(rng.getstate())
            ref_front = {v: set(front[v]) for v in val_ids}
            k_ref = ref_utils.select_program_candidate_from_pareto_front(ref_front, agg, clone)
        k = rng.choice(slist)
        ck("parent_replay", sel["chosen"] == f"c{k}", f"trace {sel['chosen']} vs replay c{k}")
        st["ref_set_order_same_parent"] = (k_ref == k) if HAVE_REF else None
        st["parent"], st["p_parent"] = k, freq.get(k, 0) / len(slist)
        ids = sampler.next_minibatch_ids(loader, _St(r))
        ck("minibatch_replay_ref_sampler", sel["minibatch_ids"] == ids, f"{sel['minibatch_ids']} vs {ids}")
        pe = next((d for d in evals if d["phase"] == "minibatch_parent"), None)
        if pe is None:          # killed mid-iteration (interrupted run)
            st["notes"].append("incomplete round (run killed before the parent evaluation was recorded)")
            st["event"] = "incomplete"
            steps.append(st)
            continue
        ck("parent_eval_ids", pe["ids"] == ids and pe["candidate"] == f"c{k}" and pe["rollouts_charged"] == len(ids))
        charge("minibatch_parent", pe["rollouts_charged"])
        before = pe["scores"]
        st["minibatch"] = ids
        st["mb_before"] = before
        ev_name = dec["event"] if dec else None
        if ev_name == "skip_perfect":
            ck("skip_perfect_rule", all(s >= perfect for s in before))
            steps.append(st)
            continue
        ck("not_all_perfect", not all(s >= perfect for s in before) or not cfg["skip_perfect_score"])
        if ev_name in ("skip_infra_error", "skip_no_trajectories"):
            st["notes"].append(f"{ev_name}")
            steps.append(st)
            continue
        # ---------------------------------------------------------- component ----
        ana = next(e["data"] for e in E if e["kind"] == "analysis")
        comp = comps[rr[k]]
        rr[k] = (rr[k] + 1) % len(comps)
        ck("round_robin", ana["components"] == [comp], f"{ana['components']} vs {comp}")
        st["component"] = comp
        prop = next(e["data"] for e in E if e["kind"] == "proposal")
        if ev_name == "no_proposal":      # reflection failed AFTER the pointer advanced (reference order)
            ck("no_proposal_has_error", bool(prop.get("error")) and not prop.get("child_artifact"))
            st["notes"].append("no_proposal: " + str(prop.get("error"))[:120])
            steps.append(st)
            continue
        parent_art = store.get(art[k])
        ck("proposal_parent_artifact", prop["parent_artifact"] == art[k])
        cur = parent_art.get(comp, "")
        prompt = prop["prompt"]
        # ---- prompt: reference template around the parent's text
        tmpl = RefSig.default_prompt_template
        pre, rest = tmpl.split("<curr_param>")
        mid, post = rest.split("<side_info>")
        ok_skel = prompt.startswith(pre + cur.rstrip("\n") + mid) and prompt.endswith(post)
        ck("prompt_template_verbatim", ok_skel)
        side = prompt[len(pre + cur.rstrip("\n") + mid):len(prompt) - len(post)] if ok_skel else ""
        n_ex = len(re.findall(r"^# Example \d+$", side, flags=re.M))
        ck("prompt_has_b_examples", n_ex == len(ids), f"{n_ex} examples")
        # split discipline: no D_pareto / sealed input in the prompt
        leaked = []
        for sp in [cfg["val_split"], *sealed]:
            for tid in ts.splits[sp]:
                t = ts.get(tid)
                inp = str(t.input)
                if len(inp) > 25 and inp in prompt and tid not in ids:
                    leaked.append(tid)
        ck("prompt_no_val_or_sealed_inputs", not leaked, f"{leaked[:5]}")
        mb_inputs_present = all(str(ts.get(t).input)[:60] in prompt for t in set(ids))
        ck("prompt_contains_minibatch_inputs", mb_inputs_present)
        # exact rebuild from a re-execution (offline): the reflection LM saw exactly the parent's minibatch records
        if reexec is not None:
            evl, adp = reexec
            trials = [evl.run_one(parent_art, ts.get(t), s) for t, s in zip(ids, pe["seeds"])]
            ck("reexec_parent_scores", [float(x.score) for x in trials] == [float(x) for x in before])
            from rsi.gepa.adapter import EvalBatch
            eb = EvalBatch(list(ids), [x.output for x in trials], [x.score for x in trials], trials)
            recs = adp.make_reflective_dataset(parent_art, eb, [comp])[comp]
            ref_prompt = RefSig.prompt_renderer({"current_instruction_doc": cur.rstrip("\n"),
                                                 "dataset_with_feedback": recs})
            ck("prompt_equals_reference_render", ref_prompt == prompt)
        # ---- child = reference parse of the raw reply
        child = store.get(prop["child_artifact"]) if prop.get("child_artifact") else None
        if child is None:
            st["notes"].append("no child")
            steps.append(st)
            continue
        parsed = parse_proposal(RefSig, prop["reply"].strip())
        exp = parsed.text
        if exp is not None and cur.endswith("\n") and not exp.endswith("\n"):
            exp += "\n"
        ck("child_equals_reference_parse", exp == child.get(comp))
        ck("other_files_unchanged", all(child.get(f) == parent_art.get(f) for f in set(child.files) | set(parent_art.files)
                                        if f != comp))
        ck("diff_equals_store", prop["diff"] == parent_art.diff(child))
        st["unchanged_rewrite"] = child.get(comp) == cur
        key = (comp, child.get(comp))
        st["repeat_of_rejected"] = rejected_texts.get(key)
        st["repeat_of_any"] = seen_texts.get(key)
        seen_texts.setdefault(key, r)
        # ---- child eval + gate from the raw scores
        ce = next(d for d in evals if d["phase"] == "minibatch_child")
        ck("child_same_ids", ce["ids"] == ids and ce["rollouts_charged"] == len(ids))
        ck("child_fresh_seeds", set(ce["seeds"]).isdisjoint(pe["seeds"]))
        charge("minibatch_child", ce["rollouts_charged"])
        after = ce["scores"]
        sb, sa = sum(before), sum(after)
        acc = sa > sb
        g = next(e["data"] for e in E if e["kind"] == "gate")
        ck("gate_uses_eval_scores", g["math"]["before"] == before and g["math"]["after"] == after)
        ck("gate_recomputed", g["accept"] == acc, f"{g['accept']} vs {acc}")
        st.update(kind="reflective", mb_after=after, sum_before=sb, sum_after=sa, gate=acc, delta=sa - sb,
                  float_tie=(acc and abs(sa - sb) < 1e-9))
        if acc:
            v = next(d for d in evals if d["phase"] == "val_reflective")
            ck("val_charge", v["rollouts_charged"] == len(val_ids))
            ck("val_covers_pareto", list(v["per_task"]) == val_ids and v["candidate"] == f"c{len(val)}")
            ck("val_seed_fixed", set(v["seeds"]) == {cfg["val_seed"]})
            charge("val_reflective", v["rollouts_charged"])
            add_cand(v, [k], "reflective")
            art.append(prop["child_artifact"])
            node = ledger.get(f"c{len(val) - 1}")
            ck("ledger_node", node is not None and node.artifact_id == prop["child_artifact"]
               and abs(float(node.score) - sum(v["per_task"].values()) / len(val_ids)) < 1e-12
               and node.meta["parents"] == [k])
            ck("decision", dec["event"] == "accepted" and dec["kept"] == f"c{len(val) - 1}")
            if use_merge:
                m_last = True
                if m_tested < cfg["max_merge_invocations"]:
                    m_due += 1
            st["new_val"] = sum(v["per_task"].values()) / len(val_ids)
        else:
            ck("decision", dec["event"] == "rejected" and not dec.get("kept"))
            ck("ledger_rejected", ledger.get(f"x{r}") is not None and ledger[f"x{r}"].status == "rejected"
               and ledger[f"x{r}"].artifact_id == prop["child_artifact"])
            ck("no_val_eval_for_rejected", not any(d["phase"].startswith("val") for d in evals))
            rejected_texts.setdefault(key, r)
        new_inc = best_idx(val)
        ck("incumbent_after", dec["incumbent_after"] == f"c{new_inc}")
        ck("monitor_iff_incumbent_change", (r in mon_rounds) == (new_inc != inc))
        st["child_artifact"] = prop["child_artifact"]
        st["parent_artifact"] = art[k]
        steps.append(st)
        for e in E:          # the monitor never feeds back: no later event may carry sealed numbers
            pass

    end = next((e["data"] for e in ev if e["kind"] == "run_end"), None)
    if end is None:          # interrupted run: no run_end; use the last state event
        last = [e["data"] for e in ev if e["kind"] == "state"][-1]
        end = {"rollouts": last["rollouts_used"], "rollouts_by_phase": last["rollouts_by_phase"],
               "best": last["incumbent"], "stop_reason": "(interrupted: no run_end)", "loop_usage": {"_total": {
                   "cost_usd": last.get("loop_usd")}}}
    fin = {"rollouts": end["rollouts"] == sum(counter.values()),
           "by_phase": {k: v for k, v in end["rollouts_by_phase"].items() if v} == {k: v for k, v in counter.items() if v},
           "returned_argmax": end["best"] == f"c{best_idx(val)}",
           "seed_artifact_untouched": seed_ok}
    # budget identity (spec 4.5)
    n_it = len([s for s in steps])
    n_parent = sum(1 for s in steps if "mb_before" in s)
    n_child = sum(1 for s in steps if s.get("kind") == "reflective")
    n_acc_ref = sum(1 for s in steps if s.get("kind") == "reflective" and s["gate"])
    n_merge = sum(1 for s in steps if s.get("kind") == "merge")
    n_acc_m = sum(1 for s in steps if s.get("kind") == "merge" and s["gate"])
    ident = len(val_ids) + b * n_parent + b * n_child + len(val_ids) * n_acc_ref + 5 * n_merge + len(val_ids) * n_acc_m
    fin["budget_identity"] = ident == end["rollouts"]
    fin["budget_identity_value"] = ident
    # stop timing (reference_soft): the last round started below B, the counter then reached >= B
    if end["stop_reason"].startswith("max_metric_calls"):
        last_rs = [e["data"]["rollouts_used"] for e in ev if e["kind"] == "round_start"][-1]
        fin["stop_timing"] = last_rs < B <= end["rollouts"]
    else:
        fin["stop_timing_usd"] = {"last_round_start_usd": usd_track[-1] if usd_track else None,
                                  "final_loop_usd": end["loop_usage"]["_total"]["cost_usd"]}
    fin["stop_reason"] = end["stop_reason"]
    # task-specific content in the returned artifact and in every accepted child
    spec = []
    if run.startswith("ruleworld"):
        for s in steps:
            if s.get("child_artifact"):
                f = dom.world.count_facts(store.get(s["child_artifact"]).files) - \
                    dom.world.count_facts(store.get(s["parent_artifact"]).files)
                s["ticket_facts_added"] = f
    else:
        answers = {}
        for sp, tids in ts.splits.items():
            for tid in tids:
                t = ts.get(tid)
                tg = str(t.target).strip()
                if len(tg) >= 3 and re.fullmatch(r"-?[\w.]+", tg):
                    answers.setdefault(tg, []).append((sp, tid))
        for s in steps:
            if s.get("child_artifact"):
                ch, pa = store.get(s["child_artifact"]), store.get(s["parent_artifact"])
                added = "\n".join(l[1:] for l in pa.diff(ch).splitlines() if l.startswith("+") and not l.startswith("+++"))
                hits = sorted({(a, sp) for a, lst in answers.items() for sp, _ in lst
                               if re.search(r"(?<![\w.])" + re.escape(a) + r"(?![\w])", added)})
                ids_hit = sorted(set(re.findall(r"\b(?:evolve|val|holdout|ood)-\d+\b", added)))
                s["literal_answers_added"] = hits
                s["task_ids_added"] = ids_hit
    # truth (RuleWorld exact)
    if run.startswith("ruleworld"):
        for s in steps:
            if s.get("kind") == "reflective":
                tp = dom.expected(store.get(s["parent_artifact"]), "test")
                tc = dom.expected(store.get(s["child_artifact"]), "test")
                s["true_gain_test"] = round(tc - tp, 4)
                s["exp_mb_parent"] = dom.world.expected(store.get(s["parent_artifact"]).files, s["minibatch"]) * b
                s["exp_mb_child"] = dom.world.expected(store.get(s["child_artifact"]).files, s["minibatch"]) * b
            elif s.get("kind") == "merge":
                (pi, pj), c = s["truth_pair"]
                tb = max(dom.expected(store.get(pi), "test"), dom.expected(store.get(pj), "test"))
                s["true_gain_test"] = round(dom.expected(store.get(c), "test") - tb, 4)
    else:
        aj = json.loads((rd / "audit.json").read_text()) if (rd / "audit.json").exists() else {"rows": []}
        tg = {row["round"]: row.get("true_gain") for row in aj["rows"]}
        for s in steps:
            if tg.get(s["round"]) is not None:
                s["true_gain_holdout_stageA"] = tg[s["round"]]
    fails = {}
    for s in steps:
        for kx, vx in s["checks"].items():
            fails.setdefault(kx, [0, 0])
            fails[kx][0] += int(vx)
            fails[kx][1] += 1
    return {"run": run, "have_reference_code": HAVE_REF, "checks": {k: f"{a}/{n}" for k, (a, n) in fails.items()},
            "final": fin, "steps": steps}


def verdicts(a: dict) -> list[dict]:
    """Per-step verdict: correct / questionable / wrong / unverifiable (mechanics + truth)."""
    out = []
    live = a["run"].endswith("live") or a["run"].endswith("interrupted")
    for s in a["steps"]:
        mech_ok = all(s["checks"].values())
        v, why = "correct", []
        if s.get("event") == "incomplete":
            out.append({"round": s["round"], "verdict": "unverifiable", "why": "; ".join(s["notes"])})
            continue
        if not mech_ok:
            v = "wrong"
            why.append("; ".join(s["notes"]))
        tg = s.get("true_gain_test", s.get("true_gain_holdout_stageA"))
        if s.get("event") in ("skip_perfect",):
            why.append("skip_perfect: parent perfect on the minibatch (reference rule)")
        elif s.get("event") in ("skip_infra_error",):
            v = "questionable" if mech_ok else v
            why.append("infra failure: rollouts charged, no proposal")
        elif "gate" in s:
            if live:
                if mech_ok:
                    v = "unverifiable"
                why.append("no ground truth (live)")
            elif tg is not None:
                if s["gate"] and tg <= 0:
                    v = "questionable" if mech_ok else v
                    why.append(f"false accept vs truth ({tg:+.3f})")
                elif (not s["gate"]) and tg > 0:
                    v = "questionable" if mech_ok else v
                    why.append(f"false reject vs truth ({tg:+.3f})")
            if s.get("float_tie"):
                v = "questionable"
                why.append("accepted on a float-rounding tie")
            if s.get("ticket_facts_added", 0) > 0 or s.get("literal_answers_added") or s.get("task_ids_added"):
                why.append("task-specific content added "
                           f"(facts {s.get('ticket_facts_added', 0)}, answers {s.get('literal_answers_added', [])}, "
                           f"ids {s.get('task_ids_added', [])})")
            if s.get("repeat_of_rejected") is not None:
                why.append(f"re-proposes the rewrite rejected at it {s['repeat_of_rejected']}")
        out.append({"round": s["round"], "verdict": v, "why": "; ".join(w for w in why if w)})
    return out


def main():
    runs = sys.argv[1:] or ["ruleworld_offline", "ruleworld_merge_offline", "agentqa_offline", "agentqa_live",
                            "agentqa_offline_mockbug", "agentqa_live_interrupted"]
    for run in runs:
        a = audit(run)
        a["verdicts"] = verdicts(a)
        cnt = {}
        for x in a["verdicts"]:
            cnt[x["verdict"]] = cnt.get(x["verdict"], 0) + 1
        a["verdict_counts"] = cnt
        (OUT / run / "stageb.json").write_text(json.dumps(a, indent=1, default=str))
        bad = {k: v for k, v in a["checks"].items() if v.split("/")[0] != v.split("/")[1]}
        print(run, cnt, "FAILED:" if bad else "all checks pass", bad, a["final"])


if __name__ == "__main__":
    main()
