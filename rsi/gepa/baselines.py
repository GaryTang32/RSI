"""Baselines at equal rollout budget (spec 9.1): ScoreOnlyReflection, BestOfN,
FewShotDemoOptimizer (MIPRO-lite). ScalarRLBaseline lives in :mod:`rsi.gepa.rl`.

All count rollouts exactly as GEPA does (one (candidate, example) run = one metric
call) and return :class:`rsi.core.ImprovementResult` with ``result.meta["rollouts"]``.
"""
from __future__ import annotations

import hashlib
import math
import random
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Optional, Sequence

import numpy as np

from ..core.artifact import Artifact
from ..core.domain import Domain
from ..core.evaluate import Evaluator
from ..core.ledger import Ledger, Node
from ..core.llm import LLM
from ..core.run import ImprovementResult
from .adapter import DomainAdapter, resolve_splits
from .api import default_components, run
from .config import Config
from .engine import merge_usage
from .reflection import ReflectionProposer, parse_fenced, reflection_seed
from .strategies import EpochShuffledBatchSampler


def _usage(*llms) -> dict:
    return merge_usage(*[l.meter.snapshot() for l in {id(x): x for x in llms if x is not None}.values()])


def _splits(domain: Domain, train: str, val: Optional[str]) -> tuple[list[str], list[str]]:
    trn, vs = resolve_splits(domain, train, val)
    tr = [t.id for t in domain.tasks.split(trn)]
    if not tr:
        raise ValueError(f"train split {trn!r} is empty")
    return tr, ([t.id for t in domain.tasks.split(vs)] if vs else list(tr))


# ----------------------------------------------------------- score-only reflection --
def run_score_only(domain: Domain, seed_artifact: Artifact, *, llm_task: Optional[LLM] = None,
                   llm_propose: Optional[LLM] = None, config: Optional[Config] = None, **kw) -> ImprovementResult:
    """ScoreOnlyReflection: the same GEPA engine, but the reflective dataset keeps only
    inputs, outputs and the score - the grader's feedback text is removed (the text
    feedback / ASI ablation)."""
    cfg = replace(config or Config(), feedback="score_only")
    return run(domain, seed_artifact, llm_task=llm_task, llm_propose=llm_propose, config=cfg,
               method="score_only_reflection", **kw)


# ------------------------------------------------------------------------ best of N --
def run_best_of_n(domain: Domain, seed_artifact: Artifact, *, llm_task: Optional[LLM] = None,
                  llm_propose: LLM, config: Optional[Config] = None, components: Optional[Sequence[str]] = None,
                  out_dir: Optional[str | Path] = None) -> ImprovementResult:
    """BestOfN: N independent reflective rewrites of the seed (each from its own
    minibatch, all components at once), each scored on all of D_pareto; keep the best.
    N is set by the budget: |V| + N (b + |V|) <= B."""
    cfg = config or Config()
    comps = list(components or cfg.components or default_components(domain, seed_artifact))
    adapter = DomainAdapter(domain, llm_task, workers=cfg.workers, feedback=cfg.feedback)
    train, val = _splits(domain, cfg.train_split, cfg.val_split)
    rng = random.Random(cfg.seed)
    sampler = EpochShuffledBatchSampler(cfg.minibatch_size, rng)
    proposer = ReflectionProposer(llm_propose, template=cfg.reflection_template, system=cfg.reflection_system)
    ledger = Ledger(Path(out_dir) / "ledger.jsonl" if out_dir else None)
    rollouts = 0

    def val_score(a):
        nonlocal rollouts
        rollouts += len(val)
        eb = adapter.evaluate(val, a, False, [cfg.val_seed] * len(val))
        return float(np.mean(eb.scores))

    best, best_v = seed_artifact, val_score(seed_artifact)
    ledger.add(Node("c0", None, kind="baseline", status="keep", score=best_v, artifact_id=seed_artifact.id))
    traj, n = [{"i": 0, "rollouts": rollouts, "best_val": best_v, "best_id": best.id}], 0
    arts = {best.id: best}
    B = cfg.max_metric_calls or 0
    while rollouts + cfg.minibatch_size + len(val) <= B:
        n += 1
        ids = sampler.next_ids(train, n - 1)
        seeds = [1 + int(hashlib.sha256(f"{cfg.seed}|bon|{n}|{j}".encode()).hexdigest()[:8], 16) % (2 ** 31 - 2)
                 for j in range(len(ids))]
        rollouts += len(ids)
        eb = adapter.evaluate(ids, seed_artifact, True, seeds)
        refl = adapter.make_reflective_dataset(seed_artifact, eb, comps)
        res = proposer.propose(seed_artifact, refl, comps, seed_fn=lambda c: reflection_seed(cfg.seed, n, c))
        if not res.new_texts:
            continue
        child = seed_artifact.with_files(res.new_texts)
        v = val_score(child)
        ok = v > best_v
        ledger.add(Node(f"c{n}", "c0", round=n, kind="rewrite", status="accepted" if ok else "discard", score=v,
                        artifact_id=child.id))
        if ok:
            best, best_v = child, v
            arts[child.id] = child
        traj.append({"i": n, "rollouts": rollouts, "best_val": best_v, "best_id": best.id})
    out = ImprovementResult("best_of_n", seed_artifact, best, ledger, traj, _usage(llm_task, llm_propose),
                            "max_metric_calls", str(out_dir) if out_dir else None,
                            {"rollouts": rollouts, "n_rewrites": n, "best_val": best_v})
    out.artifacts = arts
    return out


# --------------------------------------------------------------- few-shot / MIPRO-lite --
GROUNDED_TEMPLATE = """I am building an AI system and need a better instruction for one of its modules. Below are the module's current instruction and the correct outputs for several example inputs from the training data.

Current instruction:
```
{current}
```

Examples (input and the correct output of this module):
```
{examples}
```

System description: {description}
Tip: {tip}

Write a new, improved instruction for this module that would help it produce correct outputs for inputs like these. Provide the new instruction within ``` blocks."""

TIPS = ["Be concise.", "Be specific about the rules the outputs follow.", "Describe the task clearly.",
        "Include general rules you can infer from the examples.", "Keep what already works.",
        "Think about edge cases."]


@dataclass
class FewShotConfig:
    """MIPROv2-lite: bootstrapped + labeled demos, grounded instruction proposals,
    and a TPE-like categorical search over (instruction, demo set) per component,
    judged on validation minibatches with periodic full evaluations."""

    max_metric_calls: int = 1500
    n_instructions: int = 6          # per component (index 0 = the seed instruction)
    n_demo_sets: int = 6             # per component (index 0 = no demos)
    max_demos: int = 4
    labeled_demos: bool = True       # also use gold (input, target) pairs from the train split
    bootstrap: bool = True           # run the seed on train examples, keep perfect traces as demos
    max_bootstrap: int = 30
    n_proposal_examples: int = 5
    minibatch_size: int = 25
    full_eval_every: int = 5
    explore_trials: int = 6          # initial random trials before exploitation
    temperature: float = 0.1
    train_split: str = "evolve"
    val_split: Optional[str] = "val"
    seed: int = 0
    val_seed: int = 0

    def to_json(self) -> dict:
        return asdict(self)


def _gold(domain: Domain, task, comp: str) -> str:
    hook = getattr(domain, "gold_text", None)
    return hook(task, comp) if callable(hook) else str(task.target)


def _demo(domain: Domain, task, output, comp: str) -> str:
    hook = getattr(domain, "demo_text", None)
    if callable(hook):
        return hook(task, output, comp)
    return f"Example input: {task.input}\nExample output: {output if output is not None else task.target}"


class FewShotDemoOptimizer:
    """MIPRO-lite baseline (instruction + few-shot demo optimization)."""

    def __init__(self, domain: Domain, seed_artifact: Artifact, *, llm_task: Optional[LLM] = None,
                 llm_propose: LLM, config: Optional[FewShotConfig] = None,
                 components: Optional[Sequence[str]] = None, out_dir: Optional[str | Path] = None) -> None:
        self.cfg = cfg = config or FewShotConfig()
        self.domain, self.seed = domain, seed_artifact
        self.llm_task, self.llm_propose = llm_task, llm_propose
        self.components = list(components or default_components(domain, seed_artifact))
        self.evaluator = Evaluator(domain, llm_task, workers=1)
        self.rng = random.Random(cfg.seed)
        self.train, self.val = _splits(domain, cfg.train_split, cfg.val_split)
        self.rollouts = 0
        self.ledger = Ledger(Path(out_dir) / "ledger.jsonl" if out_dir else None)
        self.out_dir = out_dir

    def _run(self, art: Artifact, tid: str, seed: int):
        self.rollouts += 1
        return self.evaluator.run_one(art, self.domain.tasks.get(tid), seed)

    def build(self, choice: dict[str, tuple[int, int]]) -> Artifact:
        upd = {}
        for c, (ii, di) in choice.items():
            text = self.instructions[c][ii].rstrip("\n")
            demos = self.demo_sets[c][di]
            if demos:
                text += "\n\n" + "\n".join(demos)
            upd[c] = text + "\n"
        return self.seed.with_files(upd)

    def run(self) -> ImprovementResult:
        cfg, dom = self.cfg, self.domain
        # (1) bootstrap demos: run the seed on training examples, keep perfect traces
        pool: list[tuple] = []           # (task, output or None)
        perfect = float(getattr(dom, "score_range", (0.0, 1.0))[1])
        if cfg.bootstrap:
            for tid in self.train[: cfg.max_bootstrap]:
                tr = self._run(self.seed, tid, 7919)
                if tr.score >= perfect:
                    pool.append((dom.tasks.get(tid), tr.output))
        if cfg.labeled_demos:
            pool += [(dom.tasks.get(t), None) for t in self.train]
        # (2) demo sets per component (index 0 = no demos)
        self.demo_sets = {}
        for c in self.components:
            sets = [[]]
            for _ in range(cfg.n_demo_sets - 1):
                pick = self.rng.sample(pool, min(cfg.max_demos, len(pool))) if pool else []
                sets.append([_demo(dom, t, o, c) for t, o in pick])
            self.demo_sets[c] = sets
        # (3) grounded instruction proposals (index 0 = seed instruction)
        self.instructions = {}
        for c in self.components:
            cands = [self.seed.get(c, "")]
            for k in range(cfg.n_instructions - 1):
                shown = self.rng.sample(self.train, min(cfg.n_proposal_examples, len(self.train)))
                ex = "\n\n".join(f"Input: {dom.tasks.get(t).input}\nCorrect output: {_gold(dom, dom.tasks.get(t), c)}"
                                 for t in shown)
                prompt = GROUNDED_TEMPLATE.format(current=self.seed.get(c, "").rstrip("\n"), examples=ex,
                                                  description=dom.describe(), tip=TIPS[k % len(TIPS)])
                resp = self.llm_propose.complete(prompt, seed=reflection_seed(cfg.seed, k, c), role="proposer")
                text, _ = parse_fenced(resp.text) if resp.ok else (None, None)
                if text:
                    cands.append(text)
            self.instructions[c] = cands
        # (4) TPE-lite search on validation minibatches
        trials: list[tuple[dict, float]] = []
        full: dict[str, float] = {}
        arts: dict[str, Artifact] = {}

        def full_eval(art: Artifact) -> float:
            if art.id not in full:
                full[art.id] = float(np.mean([self._run(art, t, cfg.val_seed).score for t in self.val]))
                arts[art.id] = art
            return full[art.id]

        base = {c: (0, 0) for c in self.components}
        best_art = self.build(base)
        best_v = full_eval(best_art)
        self.ledger.add(Node("c0", None, kind="baseline", status="keep", score=best_v, artifact_id=best_art.id))
        traj = [{"trial": 0, "rollouts": self.rollouts, "best_val": best_v, "best_id": best_art.id}]
        mb = min(cfg.minibatch_size, len(self.val))
        t = 0
        while self.rollouts + mb <= cfg.max_metric_calls:
            t += 1
            choice = {}
            for c in self.components:
                if t <= cfg.explore_trials or not trials:
                    choice[c] = (self.rng.randrange(len(self.instructions[c])), self.rng.randrange(cfg.n_demo_sets))
                else:
                    choice[c] = (self._pick(trials, c, 0, len(self.instructions[c])),
                                 self._pick(trials, c, 1, cfg.n_demo_sets))
            art = self.build(choice)
            ids = self.rng.sample(self.val, mb)
            s = float(np.mean([self._run(art, i, cfg.val_seed).score for i in ids]))
            trials.append((choice, s))
            arts[art.id] = art
            self.ledger.add(Node(f"t{t}", "c0", round=t, kind="config_trial", status="minibatch", score=s,
                                 artifact_id=art.id, meta={"choice": {c: list(v) for c, v in choice.items()}}))
            if t % cfg.full_eval_every == 0:
                agg: dict[str, list] = {}
                for ch, sc in trials:
                    agg.setdefault(self.build(ch).id, []).append(sc)
                cand_id = max(agg, key=lambda k: np.mean(agg[k]))
                if cand_id not in full and self.rollouts + len(self.val) <= cfg.max_metric_calls:
                    v = full_eval(arts[cand_id])
                    if v > best_v:
                        best_art, best_v = arts[cand_id], v
                traj.append({"trial": t, "rollouts": self.rollouts, "best_val": best_v, "best_id": best_art.id})
        usage = _usage(self.llm_task, self.llm_propose)
        res = ImprovementResult("fewshot_mipro_lite", self.seed, best_art, self.ledger, traj, usage,
                                "max_metric_calls", str(self.out_dir) if self.out_dir else None,
                                {"config": cfg.to_json(), "rollouts": self.rollouts, "trials": t, "best_val": best_v,
                                 "n_instructions": {c: len(v) for c, v in self.instructions.items()},
                                 "demo_pool": len(pool)})
        res.artifacts = {k: v for k, v in arts.items() if k in full}
        return res

    def _pick(self, trials, comp: str, slot: int, n: int) -> int:
        """Categorical sampling proportional to exp(mean minibatch score / T) per option."""
        means = []
        for k in range(n):
            vals = [s for ch, s in trials if ch[comp][slot] == k]
            means.append(np.mean(vals) if vals else max(s for _, s in trials))   # optimistic for untried
        w = np.exp((np.asarray(means) - max(means)) / max(self.cfg.temperature, 1e-6))
        return int(self.rng.choices(range(n), weights=w.tolist())[0])


def run_fewshot(domain: Domain, seed_artifact: Artifact, *, llm_task: Optional[LLM] = None, llm_propose: LLM,
                config: Optional[FewShotConfig] = None, components: Optional[Sequence[str]] = None,
                out_dir: Optional[str | Path] = None) -> ImprovementResult:
    """Run the MIPRO-lite few-shot + instruction baseline."""
    return FewShotDemoOptimizer(domain, seed_artifact, llm_task=llm_task, llm_propose=llm_propose, config=config,
                                components=components, out_dir=out_dir).run()
