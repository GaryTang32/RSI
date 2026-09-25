"""ScalarRLBaseline: GRPO-style policy-gradient search over prompts with scalar reward
only (no textual feedback) - the E1 comparison arm (spec 9.2, Tier 1).

The policy is a set of independent Bernoulli inclusion probabilities, one per
(component, candidate line). A sampled prompt = the seed component text plus the
included lines. Each step draws ``instances_per_step`` training examples; for each it
samples a group of ``group_size`` prompts, runs them (one rollout each), and forms
group-relative advantages A = (r - mean) / std (GRPO). The update is the REINFORCE
gradient of Bernoulli logits, (a - p), weighted by A, with a KL-like pull ``beta``
towards the initial logits. Defaults follow the paper's GRPO setup where it maps
(group 12, 4 instances per step, beta 0.01, validation every 20 steps with early
stopping on the best validation checkpoint); the learning rate is swept by E1.

The line vocabulary comes from ``domain.rl_vocabulary(component)`` when the domain
provides one (RuleWorld: every rule the world's lexicon can express), otherwise it is
brainstormed once per component by ``llm_propose`` (role ``brainstorm``) from the
domain description - the prompt-space analogue of what the policy LM could say.
Rollout accounting is identical to GEPA's (one (prompt, example) run = one call).
"""
from __future__ import annotations

import hashlib
import math
import random
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional, Sequence

import numpy as np

from ..core.artifact import Artifact
from ..core.domain import Domain
from ..core.evaluate import Evaluator
from ..core.ledger import ArtifactStore, Ledger, Node
from ..core.llm import LLM, extract_code_blocks
from ..core.run import ImprovementResult
from .adapter import resolve_splits
from .engine import merge_usage
from .strategies import EpochShuffledBatchSampler

BRAINSTORM_TEMPLATE = """You are helping to optimize the instruction of one module of an AI system.

System description:
{description}

Current instruction of the module:
```
{current}
```

List {n} short, distinct, general guidelines (one sentence each) that could be added to this instruction to improve the system's accuracy on tasks of this kind. Output them one per line inside a single ``` block, with no numbering."""


@dataclass
class RLConfig:
    max_metric_calls: int = 24000
    group_size: int = 12
    instances_per_step: int = 4
    lr: float = 1.0
    beta: float = 0.01
    init_p: float = 0.1
    val_every: int = 20              # steps between validations of the greedy (p > 0.5) prompt
    vocab_size: int = 24             # brainstormed lines per component (when the domain has no vocabulary)
    train_split: str = "evolve"
    val_split: Optional[str] = "val"
    seed: int = 0
    val_seed: int = 0

    def to_json(self) -> dict:
        return asdict(self)


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


class ScalarRLBaseline:
    """GRPO-style Bernoulli prompt policy trained on scalar rewards."""

    def __init__(self, domain: Domain, seed_artifact: Artifact, *, llm_task: Optional[LLM] = None,
                 llm_propose: Optional[LLM] = None, config: Optional[RLConfig] = None,
                 components: Optional[Sequence[str]] = None, vocabulary: Optional[dict[str, list[str]]] = None,
                 out_dir: Optional[str | Path] = None) -> None:
        from .api import default_components
        self.cfg = cfg = config or RLConfig()
        self.domain, self.seed = domain, seed_artifact
        self.llm_task, self.llm_propose = llm_task, llm_propose
        self.components = list(components or default_components(domain, seed_artifact))
        self.evaluator = Evaluator(domain, llm_task, workers=1)
        self.rng = random.Random(cfg.seed)
        self.np_rng = np.random.default_rng(cfg.seed)
        tr, vs = resolve_splits(domain, cfg.train_split, cfg.val_split)
        self.train_ids = [t.id for t in domain.tasks.split(tr)]
        self.val_ids = [t.id for t in domain.tasks.split(vs)] if vs else list(self.train_ids)
        if not self.train_ids:
            raise ValueError(f"train split {tr!r} is empty")
        self.vocab = vocabulary or self._vocabulary()
        self.theta0 = {c: np.full(len(self.vocab[c]), math.log(cfg.init_p / (1 - cfg.init_p))) for c in self.components}
        self.theta = {c: v.copy() for c, v in self.theta0.items()}
        self.rollouts = 0
        self.out_dir = Path(out_dir) if out_dir else None
        self.ledger = Ledger(self.out_dir / "ledger.jsonl" if self.out_dir else None)
        self.store = ArtifactStore(self.out_dir / "artifacts") if self.out_dir else None
        self.sampler = EpochShuffledBatchSampler(cfg.instances_per_step, self.rng)

    # -------------------------------------------------------------- vocabulary --
    def _vocabulary(self) -> dict[str, list[str]]:
        hook = getattr(self.domain, "rl_vocabulary", None)
        out = {}
        for c in self.components:
            lines = list(hook(c)) if callable(hook) else []
            if not lines:
                if self.llm_propose is None:
                    raise ValueError("domain has no rl_vocabulary(); pass llm_propose to brainstorm one")
                prompt = BRAINSTORM_TEMPLATE.format(description=self.domain.describe(),
                                                    current=self.seed.get(c, "").rstrip("\n"), n=self.cfg.vocab_size)
                resp = self.llm_propose.complete(prompt, seed=self.cfg.seed, role="brainstorm")
                blocks = extract_code_blocks(resp.text) or [resp.text]
                lines = [l.strip(" -*\t") for l in blocks[0].splitlines() if l.strip(" -*\t")]
                lines = list(dict.fromkeys(lines))[: self.cfg.vocab_size]
            out[c] = lines
        return out

    # ---------------------------------------------------------------- policy --
    def artifact_for(self, actions: dict[str, np.ndarray]) -> Artifact:
        upd = {}
        for c in self.components:
            base = self.seed.get(c, "").rstrip("\n")
            lines = [l for l, a in zip(self.vocab[c], actions[c]) if a]
            upd[c] = base + ("\n" + "\n".join(lines) if lines else "") + "\n"
        return self.seed.with_files(upd)

    def greedy(self) -> Artifact:
        return self.artifact_for({c: _sigmoid(self.theta[c]) > 0.5 for c in self.components})

    def _seed(self, *parts) -> int:
        return 1 + int(hashlib.sha256("|".join(map(str, (self.cfg.seed, "rl") + parts)).encode()).hexdigest()[:8],
                       16) % (2 ** 31 - 2)

    def _rollout(self, art: Artifact, tid: str, seed: int) -> float:
        self.rollouts += 1
        return float(self.evaluator.run_one(art, self.domain.tasks.get(tid), seed).score)

    def validate(self, art: Artifact) -> float:
        scores = [self._rollout(art, t, self.cfg.val_seed) for t in self.val_ids]
        return float(np.mean(scores)) if scores else 0.0

    # ------------------------------------------------------------------- run --
    def run(self) -> ImprovementResult:
        cfg = self.cfg
        traj: list[dict] = []
        best_art, best_val = self.seed, self.validate(self.seed)
        self.best_arts = {self.seed.id: self.seed}
        self.ledger.add(Node(id="c0", parent=None, kind="baseline", status="keep", score=best_val,
                             artifact_id=self.seed.id, metrics={"rollouts": self.rollouts}))
        traj.append({"step": 0, "rollouts": self.rollouts, "val_greedy": best_val, "best_val": best_val,
                     "best_id": best_art.id})
        best_node, step, stop = "c0", 0, "max_metric_calls"
        while self.rollouts < cfg.max_metric_calls:
            step += 1
            ids = self.sampler.next_ids(self.train_ids, step - 1)
            grads = {c: np.zeros_like(self.theta[c]) for c in self.components}
            probs = {c: _sigmoid(self.theta[c]) for c in self.components}
            n_groups = 0
            for tid in ids:
                acts, rewards = [], []
                for g in range(cfg.group_size):
                    a = {c: self.np_rng.random(len(probs[c])) < probs[c] for c in self.components}
                    acts.append(a)
                    rewards.append(self._rollout(self.artifact_for(a), tid, self._seed(step, tid, g)))
                r = np.asarray(rewards)
                sd = r.std()
                if sd < 1e-9:
                    continue
                adv = (r - r.mean()) / (sd + 1e-6)
                n_groups += 1
                for a, A in zip(acts, adv):
                    for c in self.components:
                        grads[c] += A * (a[c].astype(float) - probs[c])
            for c in self.components:
                if n_groups:
                    grads[c] /= (n_groups * cfg.group_size)
                self.theta[c] += cfg.lr * grads[c] - cfg.lr * cfg.beta * (self.theta[c] - self.theta0[c])
            if step % cfg.val_every == 0 or self.rollouts >= cfg.max_metric_calls:
                g_art = self.greedy()
                v = self.validate(g_art)
                nid = f"s{step}"
                self.ledger.add(Node(id=nid, parent=best_node, round=step, kind="policy_checkpoint",
                                     status="accepted" if v > best_val else "discard", score=v, artifact_id=g_art.id,
                                     metrics={"rollouts": self.rollouts,
                                              "mean_p": float(np.mean(np.concatenate(list(probs.values()))))}))
                if self.store is not None:
                    self.store.put(g_art)
                if v > best_val:
                    best_art, best_val, best_node = g_art, v, nid
                    self.best_arts[g_art.id] = g_art
                traj.append({"step": step, "rollouts": self.rollouts, "val_greedy": v, "best_val": best_val,
                             "best_id": best_art.id})
        usage = merge_usage(*[l.meter.snapshot() for l in {id(x): x for x in (self.llm_task, self.llm_propose)
                                                              if x is not None}.values()])
        res = ImprovementResult(method="scalar_rl", baseline=self.seed, best=best_art, ledger=self.ledger,
                                trajectory=traj, usage=usage, stop_reason=stop,
                                out_dir=str(self.out_dir) if self.out_dir else None,
                                meta={"config": cfg.to_json(), "rollouts": self.rollouts, "steps": step,
                                      "best_val": best_val, "vocab_sizes": {c: len(v) for c, v in self.vocab.items()},
                                      "final_p": {c: _sigmoid(self.theta[c]).round(3).tolist()
                                                  for c in self.components}})
        res.artifacts = dict(self.best_arts)    # artifact id -> artifact for every trajectory "best_id"
        return res


def run_scalar_rl(domain: Domain, seed_artifact: Artifact, *, llm_task: Optional[LLM] = None,
                  llm_propose: Optional[LLM] = None, config: Optional[RLConfig] = None,
                  components: Optional[Sequence[str]] = None, out_dir: Optional[str | Path] = None,
                  vocabulary: Optional[dict[str, list[str]]] = None) -> ImprovementResult:
    """Run the scalar-reward RL baseline; ``result.trajectory`` rows carry
    ``rollouts`` and ``best_id`` (the artifact that would be returned at that point)."""
    rl = ScalarRLBaseline(domain, seed_artifact, llm_task=llm_task, llm_propose=llm_propose, config=config,
                          components=components, vocabulary=vocabulary, out_dir=out_dir)
    return rl.run()
