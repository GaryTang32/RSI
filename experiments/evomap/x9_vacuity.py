"""X9: verifiable execution stops vacuous validation (VacuityDetector precision / recall).

Claim [abs:BE]: >84% of approved assets pass validation with vacuous tests (e.g. console.log); validations are
not independently verified. Design claim (§9.2.2-9.2.3): a static lint plus a discriminative before/after run
(V(W1)=pass, V(W0)=fail, new tests on old code) catches the seeded vacuous classes with few false rejects.

Items: for every GeneWorld class (12) and every kata (25): one REAL validation (the public check / smoke test)
and eight VACUOUS kinds (--version, print-only script, constant assert, weak assert that ignores the change,
missing script, empty list, python -c, echo). Katas run in real sandboxed subprocesses.
Detectors (positive = "flag as vacuous / reject"): Evolver's faithful local runner (reject iff it fails),
the naive hub's empty-directory validator (reject iff it fails), lint only, discriminative only, combined.
With --llm claude:<model>, the model also writes one gene per kata class under the faithful prompt ("prefer
--version") and under the safe prompt; the detectors' verdicts on those real, model-written validations are
reported (no ground-truth labels).

Run: python experiments/evomap/x9_vacuity.py [--llm sim|claude:haiku] [--quick]
"""
from __future__ import annotations

from _common import FAITHFUL_HINT, SAFE_HINT, cached_llm, live, parse_args, save

import numpy as np

from rsi.core.stats import bootstrap_ci
from rsi.evomap import (CommandPolicy, InProcessExecutor, SubprocessExecutor, ValidationRunner, VacuityDetector,
                        parse_gene)
from rsi.evomap.prompts import GENE_WRITER_SYSTEM, gene_writer_prompt

KINDS = ("version", "print_only", "constant_assert", "weak_assert", "missing_script", "empty", "eval_flag",
         "shell_echo")


def geneworld_items():
    from rsi.domains.geneworld import make_domain
    w = make_domain().world
    items = []
    for cl in w.classes:
        before = {"mod.py": w.module(cl, "buggy"), "check.py": w.check_script(cl)}
        after = {**before, "mod.py": w.module(cl, "fixed")}
        items.append(("geneworld", cl, "real", ["python check.py"], before, after))
        for k in KINDS:
            val, extra = w.vacuous_validation(k, cl)
            items.append(("geneworld", cl, k, val, before, {**after, **extra}))
    return items


def katas_items(quick: bool):
    from rsi.domains.katas import KATAS, KatasDomain
    from rsi.domains.katas.forge import VACUOUS
    dom = KatasDomain()
    items = []
    extra_kinds = {"eval_flag": (["python -c \"print('ok')\""], {}), "shell_echo": (["echo ok"], {})}
    for k in (KATAS[::3] if quick else KATAS):
        t = dom.tasks.get(k.id)
        before = dom.pre_workspace(t)
        after = {**before, "solution.py": k.correct}
        items.append(("katas", k.id, "real", ["python smoke_test.py"], before, after))
        for kind in KINDS:
            val, extra = VACUOUS.get(kind) or extra_kinds[kind]
            items.append(("katas", k.id, kind, list(val), before, {**after, **extra}))
    return items


def detectors(domain: str):
    ex = InProcessExecutor() if domain == "geneworld" else SubprocessExecutor(timeout_s=20)
    faithful = ValidationRunner(CommandPolicy.faithful(), ex, mode="faithful")
    hub_validator = ValidationRunner(CommandPolicy.faithful(), ex, mode="safe", retries=0)
    vd = VacuityDetector(ValidationRunner(CommandPolicy.safe(internal=()), ex, mode="safe"))
    return {
        "evolver_local_runner": lambda v, b, a: not faithful.run(v, a).ok,
        "naive_hub_empty_dir": lambda v, b, a: not hub_validator.run(v, {}).ok,
        "lint_only": lambda v, b, a: vd.lint_vacuous(v, a)[0],
        "discriminative_only": lambda v, b, a: not vd.discriminate(v, b, a, mutants=False).discriminative,
        "combined": lambda v, b, a: vd.verdict(v, b, a).vacuous,
    }


def prf(pred, truth):
    pred, truth = np.asarray(pred, bool), np.asarray(truth, bool)
    tp = int((pred & truth).sum())
    fp = int((pred & ~truth).sum())
    fn = int((~pred & truth).sum())
    prec = tp / (tp + fp) if tp + fp else float("nan")
    rec = tp / (tp + fn) if tp + fn else float("nan")
    real = ~truth
    fr = float(pred[real].mean()) if real.any() else float("nan")
    # bootstrap CI of recall over vacuous items
    r_ci = bootstrap_ci(pred[truth].astype(float), reps=2000) if truth.any() else (float("nan"),) * 3
    return {"precision": prec, "recall": rec, "recall_ci": [r_ci[1], r_ci[2]], "false_reject_rate": fr,
            "tp": tp, "fp": fp, "fn": fn, "n": int(len(pred))}


def live_genes(args):
    from rsi.domains.katas import CLASSES, KATAS, KatasDomain
    llm = cached_llm(args.llm)
    dom = KatasDomain()
    out = []
    for cls in CLASSES:
        k = next(k for k in KATAS if k.cls == cls)
        t = dom.tasks.get(k.id)
        before = dom.pre_workspace(t)
        after = {**before, "solution.py": k.correct}
        for regime, hint in (("faithful", FAITHFUL_HINT), ("safe", SAFE_HINT)):
            p = gene_writer_prompt([f"task:{cls}"], dom.public_text(t), "public tests: 2/2 passed; hidden: FAILED",
                                   validation_hint=hint + " The workspace contains solution.py and smoke_test.py.")
            r = llm.complete(p, system=GENE_WRITER_SYSTEM, seed=0, role="proposer")
            g = parse_gene(r.text)
            val = g.validation if g else []
            det = {name: bool(f(val, before, after)) for name, f in detectors("katas").items()}
            out.append({"class": cls, "regime": regime, "validation": val, **det})
    return out


def main():
    args = parse_args(__doc__.split("\n")[0], default_seeds=1)
    items = geneworld_items() + katas_items(args.quick)
    res: dict = {}
    rows = []
    for dom in ("geneworld", "katas"):
        det = detectors(dom)
        its = [i for i in items if i[0] == dom]
        for name, f in det.items():
            pred = [bool(f(v, b, a)) for (_, _, _, v, b, a) in its]
            truth = [kind != "real" for (_, _, kind, _, _, _) in its]
            res.setdefault(dom, {})[name] = prf(pred, truth)
            for (d, tid, kind, v, _, _), p in zip(its, pred):
                rows.append({"domain": d, "item": tid, "kind": kind, "detector": name, "flagged": p})
    by_kind = {}
    for dom in ("geneworld", "katas"):
        for kind in ("real",) + KINDS:
            by_kind.setdefault(dom, {})[kind] = {det: float(np.mean([r["flagged"] for r in rows if r["domain"] == dom
                                                                     and r["kind"] == kind and r["detector"] == det]))
                                                 for det in ("evolver_local_runner", "naive_hub_empty_dir", "lint_only",
                                                             "discriminative_only", "combined")}
    out = {"config": {"llm": args.llm, "kinds": KINDS, "quick": args.quick}, "summary": res, "flag_rate_by_kind": by_kind,
           "raw": rows}
    comb = [res[d]["combined"] for d in res]
    out["verdict"] = {
        "combined_recall_ge_0.99": all(c["recall"] >= 0.99 for c in comb),
        "combined_false_reject_le_0.05": all(c["false_reject_rate"] <= 0.05 for c in comb),
        "evolver_runner_accepts_most_vacuous": all(res[d]["evolver_local_runner"]["recall"] < 0.5 for d in res),
        "empty_dir_validator_accepts_version_and_rejects_real": all(
            by_kind[d]["version"]["naive_hub_empty_dir"] == 0 and by_kind[d]["real"]["naive_hub_empty_dir"] == 1
            for d in by_kind),
        "lint_alone_misses_weak_assert": all(by_kind[d]["weak_assert"]["lint_only"] == 0 for d in by_kind),
    }
    if live(args):
        out["live_genes"] = live_genes(args)
    save("x9_vacuity", out, args.out)
    for d in res:
        print(f"== {d}")
        for name, m in res[d].items():
            print(f"  {name:22s} precision {m['precision']:.3f} recall {m['recall']:.3f} false-reject "
                  f"{m['false_reject_rate']:.3f} (n={m['n']})")
    print("verdict:", out["verdict"])


if __name__ == "__main__":
    main()
