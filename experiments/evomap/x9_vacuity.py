"""X9: verifiable execution stops vacuous validation (VacuityDetector precision / recall).
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
DOMAINS = ("geneworld", "katas")
DETECTORS = ("evolver_local_runner", "naive_hub_empty_dir", "lint_only", "discriminative_only", "combined")
KATA_SHELL_KINDS = {"eval_flag": (["python -c \"print('ok')\""], {}), "shell_echo": (["echo ok"], {})}


def geneworld_items() -> list[tuple]:
    """(domain, item, kind, validation, workspace before, workspace after): one real and every vacuous kind."""
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


def katas_items(quick: bool) -> list[tuple]:
    from rsi.domains.katas import KATAS, KatasDomain
    from rsi.domains.katas.forge import VACUOUS
    dom = KatasDomain()
    items = []
    for k in (KATAS[::3] if quick else KATAS):
        before = dom.pre_workspace(dom.tasks.get(k.id))
        after = {**before, "solution.py": k.correct}
        items.append(("katas", k.id, "real", ["python smoke_test.py"], before, after))
        for kind in KINDS:
            val, extra = VACUOUS.get(kind) or KATA_SHELL_KINDS[kind]
            items.append(("katas", k.id, kind, list(val), before, {**after, **extra}))
    return items


def detectors(domain: str) -> dict:
    """Each detector maps (validation, before, after) to True when it flags or rejects the validation."""
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


def prf(pred, truth) -> dict:
    pred, truth = np.asarray(pred, bool), np.asarray(truth, bool)
    tp, fp, fn = int((pred & truth).sum()), int((pred & ~truth).sum()), int((~pred & truth).sum())
    real = ~truth
    r_ci = bootstrap_ci(pred[truth].astype(float), reps=2000) if truth.any() else (float("nan"),) * 3
    return {"precision": tp / (tp + fp) if tp + fp else float("nan"),
            "recall": tp / (tp + fn) if tp + fn else float("nan"), "recall_ci": [r_ci[1], r_ci[2]],
            "false_reject_rate": float(pred[real].mean()) if real.any() else float("nan"),
            "tp": tp, "fp": fp, "fn": fn, "n": int(len(pred))}


def live_genes(args) -> list[dict]:
    """Detector verdicts on validations a live model writes per kata class under the faithful and the safe prompt."""
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
            g = parse_gene(llm.complete(p, system=GENE_WRITER_SYSTEM, seed=0, role="proposer").text)
            val = g.validation if g else []
            det = {name: bool(f(val, before, after)) for name, f in detectors("katas").items()}
            out.append({"class": cls, "regime": regime, "validation": val, **det})
    return out


def main():
    args = parse_args(__doc__.split("\n")[0], default_seeds=1)
    items = geneworld_items() + katas_items(args.quick)
    res, rows = {}, []
    for dom in DOMAINS:
        det = detectors(dom)
        its = [i for i in items if i[0] == dom]
        truth = [kind != "real" for (_, _, kind, _, _, _) in its]
        for name, f in det.items():
            pred = [bool(f(v, b, a)) for (_, _, _, v, b, a) in its]
            res.setdefault(dom, {})[name] = prf(pred, truth)
            rows += [{"domain": d, "item": tid, "kind": kind, "detector": name, "flagged": p}
                     for (d, tid, kind, _, _, _), p in zip(its, pred)]

    def flag_rate(*key):
        return float(np.mean([r["flagged"] for r in rows if (r["domain"], r["kind"], r["detector"]) == key]))
    by_kind = {dom: {kind: {det: flag_rate(dom, kind, det) for det in DETECTORS} for kind in ("real",) + KINDS}
               for dom in DOMAINS}
    out = {"config": {"llm": args.llm, "kinds": KINDS, "quick": args.quick}, "summary": res,
           "flag_rate_by_kind": by_kind, "raw": rows}
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
