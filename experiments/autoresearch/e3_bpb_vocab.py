"""E3 - bits per byte does not depend on the vocabulary, so tokenizer changes are compared fairly.

Claim [doc/upstream README]: "val_bpb ... is vocab-size-independent, so architectural changes
[and tokenizer changes] are fairly compared". Per-token loss is not: a bigger vocabulary packs
more bytes into each token, so every token is harder to predict even when the model assigns
the text a higher probability.

Setup (CPU, deterministic, the tinylm corpus: Python stdlib docstrings, 1.3 MB train / 85 KB val):
tokenizers = bytes (vocab 256) or byte-pair encodings with 256 / 768 / 1792 merges fitted on
train (vocab 512 / 1024 / 2048); models = n-gram LMs of order 1-3 with additive smoothing toward
the next-lower order (Dirichlet concentration ALPHA * VOCAB), all properly normalised.
Both metrics are computed from the same per-token log-probabilities:
  per-token loss = mean nats per token;  bpb = sum of nats / (ln 2 * sum of TOKEN_BYTES[token]),
  with TOKEN_BYTES the locked byte-length table (the sum equals the val byte count exactly).

A. Metric battery: 4 vocabularies x 3 orders. Pairwise ranking disagreements between per-token
   loss and bpb; within each order, how the two metrics move with the vocabulary.
B. The loop: an autoresearch night on this problem, written as a new in-process ResearchTask
   (train.py = VOCAB, ORDER, ALPHA constants; the locked grader tokenizes, trains and scores),
   strict keep rule, 30 experiments x 5 agent seeds, judged either by val bpb or by val
   per-token loss. Measured: final vocabulary and the final version's bpb on val and on the
   hidden test_iid split.
Confirming outcome: per-token loss mis-ranks models across vocabularies (and steers the loop
away from larger vocabularies), bpb ranks them by how well they predict the byte stream.

With --llm claude:haiku (or the offline --llm scripted) only part B runs, with an LLM research
agent editing train.py (part A involves no agent).

Usage: python experiments/autoresearch/e3_bpb_vocab.py [--llm sim|claude:haiku] [--seeds N] [--quick]
"""
from __future__ import annotations

from _common import SCRATCH, ci, is_live, parser, plt, pool_map, research_agent, suffix, usage_of, write  # noqa: I001

import json
import math
from itertools import combinations
from typing import Optional

import numpy as np

from rsi.autoresearch import AutoresearchLoop, Config, ResearchTask, RunBudget, RunOutcome, knob_edit, parse_summary
from rsi.autoresearch.landscape import parse_knobs
from rsi.core import Artifact

VOCABS = (256, 512, 1024, 2048)
ORDERS = (1, 2, 3)
BPE_SAMPLE_BYTES = 400_000                 # merges are learnt on this prefix of train, applied to every split

TRAIN_PY = """\
\"\"\"train.py - the file the research agent edits (tokenizer + n-gram language model).

VOCAB: tokenizer vocabulary (256 = raw bytes; larger = byte-pair encoding with VOCAB - 256 merges
       learnt on the training text; one of 256, 512, 1024, 2048).
ORDER: n-gram order of the model (1 = unigram ... 3 = trigram).
ALPHA: smoothing toward the next-lower order (Dirichlet concentration ALPHA * VOCAB).
The locked prepare.py tokenizes the data, fits the counts and scores the validation text.
\"\"\"
VOCAB = 256
ORDER = 1
ALPHA = 0.1
"""

PREPARE_PY = """\
\"\"\"prepare.py - LOCKED. Data (tinylm corpus), the BPE tokenizer trainer, the n-gram counter and the
metric. Metrics: val_bpb = sum of token nats / (ln 2 * sum of the locked byte-length table over the
tokens) and val_nats_per_token = mean token nats. (Executed by the framework in-process.)\"\"\"
"""


# --------------------------------------------------------------------------- tokenizer + model
class Corpus:
    """Byte splits and BPE merges (learnt once, lazily, up to the largest vocabulary)."""

    def __init__(self) -> None:
        from rsi.domains.tinylm import build_corpus, default_data_root

        root = default_data_root()
        build_corpus(root)
        self.raw = {s: np.fromfile(root / "all" / f"{s}.bin", dtype=np.uint8).astype(np.int32)
                    for s in ("train", "val", "test_iid")}
        self.merges: list[tuple[int, int]] = []
        self.token_bytes = [1] * 256                       # locked byte-length table (bytes per token id)
        self._tok: dict[tuple[str, int], np.ndarray] = {}
        self._counts: dict[tuple[int, int], tuple] = {}
        self._fit_seq: Optional[np.ndarray] = None

    @staticmethod
    def _merge(seq: np.ndarray, a: int, b: int, new: int) -> np.ndarray:
        idx = np.flatnonzero((seq[:-1] == a) & (seq[1:] == b))
        if len(idx) == 0:
            return seq
        if a == b:                                         # "aaa" -> merge left to right without overlaps
            keep, last = np.ones(len(idx), bool), -2
            for j, i in enumerate(idx.tolist()):
                if i == last + 1:
                    keep[j] = False
                else:
                    last = i
            idx = idx[keep]
        out = seq.copy()
        out[idx] = new
        return np.delete(out, idx + 1)

    def fit(self, vocab: int) -> None:
        """Learn merges on the train prefix until the vocabulary has ``vocab`` tokens (incremental)."""
        if 256 + len(self.merges) >= vocab:
            return
        seq = self._fit_seq if self._fit_seq is not None else self.raw["train"][:BPE_SAMPLE_BYTES]
        while 256 + len(self.merges) < vocab:
            V = 256 + len(self.merges)
            codes, counts = np.unique(seq[:-1].astype(np.int64) * V + seq[1:], return_counts=True)
            a, b = divmod(int(codes[counts.argmax()]), V)
            self.merges.append((a, b))
            self.token_bytes.append(self.token_bytes[a] + self.token_bytes[b])
            seq = self._merge(seq, a, b, V)
        self._fit_seq = seq

    def tokens(self, split: str, vocab: int) -> np.ndarray:
        key = (split, vocab)
        if key not in self._tok:
            self.fit(vocab)
            prev = [v for (s, v) in self._tok if s == split and v < vocab]
            start = max(prev) if prev else 256
            seq = self._tok[(split, start)] if prev else self.raw[split]
            for k in range(start - 256, vocab - 256):
                a, b = self.merges[k]
                seq = self._merge(seq, a, b, 256 + k)
            self._tok[key] = seq
        return self._tok[key]

    def _ngram_counts(self, vocab: int, k: int):
        """Sorted (context+token) codes/counts and context codes/counts for order k on train."""
        key = (vocab, k)
        if key not in self._counts:
            seq = self.tokens("train", vocab)
            B = vocab + 1                                   # + a padding symbol for the first positions
            ctx = self._contexts(seq, k, vocab)
            full = ctx * B + seq
            self._counts[key] = (np.unique(full, return_counts=True), np.unique(ctx, return_counts=True))
        return self._counts[key]

    @staticmethod
    def _contexts(seq: np.ndarray, k: int, vocab: int) -> np.ndarray:
        """Integer code of the k-1 previous tokens at every position (padding id = vocab)."""
        B = vocab + 1
        pad = np.concatenate([np.full(k - 1, vocab, dtype=np.int64), seq.astype(np.int64)])
        ctx = np.zeros(len(seq), dtype=np.int64)
        for j in range(k - 1):
            ctx = ctx * B + pad[j:j + len(seq)]
        return ctx

    @staticmethod
    def _lookup(keys: np.ndarray, counts: np.ndarray, q: np.ndarray) -> np.ndarray:
        i = np.clip(np.searchsorted(keys, q), 0, len(keys) - 1)
        return np.where(keys[i] == q, counts[i], 0).astype(np.float64)

    def score(self, split: str, vocab: int, order: int, alpha: float) -> dict:
        """Per-token nats of ``split`` under the n-gram model, and both metrics."""
        seq = self.tokens(split, vocab)
        p = np.full(len(seq), 1.0 / vocab)                  # order 0: uniform over the vocabulary
        for k in range(1, order + 1):
            (fk, fc), (ck, cc) = self._ngram_counts(vocab, k)
            ctx = self._contexts(seq, k, vocab)
            c_hw = self._lookup(fk, fc, ctx * (vocab + 1) + seq)
            c_h = self._lookup(ck, cc, ctx)
            conc = alpha * vocab
            p = (c_hw + conc * p) / (c_h + conc)
        nats = -np.log(p)
        tb = np.asarray(self.token_bytes[:vocab])
        nbytes = tb[seq]
        assert int(nbytes.sum()) == len(self.raw[split])    # the locked table covers every byte exactly once
        return {"val_bpb": float(nats.sum() / (math.log(2) * nbytes.sum())),
                "val_nats_per_token": float(nats.mean()), "n_tokens": int(len(seq)),
                "bytes_per_token": float(nbytes.mean()), "nats_total": float(nats.sum())}


_CORPUS: Optional[Corpus] = None


def corpus() -> Corpus:
    global _CORPUS
    if _CORPUS is None:
        _CORPUS = Corpus()
    return _CORPUS


# --------------------------------------------------------------------------- the research task
class TokenizerTask(ResearchTask):
    """A new in-process ResearchTask: train.py picks VOCAB / ORDER / ALPHA, the locked grader
    tokenizes, counts and scores (deterministic, no seed noise). ``metric`` selects what the
    loop is judged by: ``val_bpb`` or ``val_nats_per_token`` (both lower is better)."""

    def __init__(self, metric: str = "val_bpb") -> None:
        self.name = f"tokenizer-ngram[{metric}]"
        self.metric = metric
        self.direction = "min"
        self.editable_paths = ("train.py",)
        self.locked_paths = ("prepare.py",)
        self.budget = RunBudget(kind="none", amount=0.0, kill_after=600.0, mem_mb=None)
        self.run_command = "python train.py"
        self.audit_splits = ("test_iid",)

    def seed_artifact(self) -> Artifact:
        return Artifact({"prepare.py": PREPARE_PY, "train.py": TRAIN_PY})

    def sealed_files(self) -> dict:
        return {"prepare.py": PREPARE_PY}

    def describe(self) -> str:
        return ("Language modelling of English technical text with a tokenizer + n-gram model; train.py sets VOCAB "
                f"(256 bytes, or 512/1024/2048 BPE tokens), ORDER (1-3) and ALPHA; the metric is {self.metric} "
                "(lower is better).")

    def contract(self, mode: str) -> str:
        return "- train.py must stay a block of `NAME = value` constants: VOCAB in {256, 512, 1024, 2048}, ORDER in {1, 2, 3}, ALPHA > 0.\n"

    def _config(self, art: Artifact) -> tuple[int, int, float]:
        k = parse_knobs(art["train.py"])
        vocab, order, alpha = int(k.get("VOCAB", 256)), int(k.get("ORDER", 1)), float(k.get("ALPHA", 0.1))
        if vocab not in VOCABS or order not in ORDERS or not alpha > 0:
            raise ValueError(f"unsupported config VOCAB={vocab} ORDER={order} ALPHA={alpha}")
        return vocab, order, alpha

    def _run(self, art: Artifact, split: str) -> RunOutcome:
        try:
            r = corpus().score(split, *self._config(art))
        except Exception as e:  # noqa: BLE001 - a broken train.py is a crashed run
            return RunOutcome(None, log=f"Traceback (most recent call last):\n{type(e).__name__}: {e}\n",
                              returncode=1, crash_reason=f"{type(e).__name__}: {e}")
        log = "---\n" + "".join(f"{k}: {v}\n" for k, v in r.items())
        return RunOutcome(r[self.metric], log=log, summary=parse_summary(log), meta=r)

    def run(self, artifact, *, seed=0, mode="hardened", log_path=None, val_epoch=0) -> RunOutcome:
        out = self._run(artifact, "val")
        if log_path:
            from pathlib import Path

            Path(log_path).parent.mkdir(parents=True, exist_ok=True)
            Path(log_path).write_text(out.log)
            out.log_path = log_path
        return out

    def audit(self, artifact, *, seed=0) -> dict:
        o = self._run(artifact, "test_iid")
        return {"test_iid_bpb": o.meta["val_bpb"], "test_iid_nats_per_token": o.meta["val_nats_per_token"]} \
            if o.metric is not None else {"audit_error": o.crash_reason}

    def mock_edit_pool(self):
        return [knob_edit("VOCAB", lambda v: v * 2, lo=256, hi=2048, name="vocab_up"),
                knob_edit("VOCAB", lambda v: v // 2, lo=256, hi=2048, name="vocab_down"),
                knob_edit("ORDER", lambda v: v + 1, lo=1, hi=3, name="order_up"),
                knob_edit("ORDER", lambda v: v - 1, lo=1, hi=3, name="order_down"),
                knob_edit("ALPHA", lambda v: round(v * 3, 6), lo=1e-4, hi=10.0, name="alpha_up"),
                knob_edit("ALPHA", lambda v: round(v / 3, 6), lo=1e-4, hi=10.0, name="alpha_down")]


# --------------------------------------------------------------------------- A: battery
def battery(alpha: float = 0.1) -> dict:
    rows = {}
    for v in VOCABS:
        for o in ORDERS:
            rows[f"V{v}/n{o}"] = {"vocab": v, "order": o, **corpus().score("val", v, o, alpha)}
    names = list(rows)
    pairs = list(combinations(names, 2))
    disagree = [(a, b) for a, b in pairs if (rows[a]["val_nats_per_token"] < rows[b]["val_nats_per_token"])
                != (rows[a]["val_bpb"] < rows[b]["val_bpb"])]
    cross = [(a, b) for a, b in pairs if rows[a]["vocab"] != rows[b]["vocab"]]
    same = [(a, b) for a, b in pairs if rows[a]["vocab"] == rows[b]["vocab"]]
    by_order = {o: {"vocab": list(VOCABS), "bpb": [rows[f"V{v}/n{o}"]["val_bpb"] for v in VOCABS],
                    "nats_per_token": [rows[f"V{v}/n{o}"]["val_nats_per_token"] for v in VOCABS]} for o in ORDERS}
    best_bpb = min(names, key=lambda n: rows[n]["val_bpb"])
    best_tok = min(names, key=lambda n: rows[n]["val_nats_per_token"])
    return {"alpha": alpha, "rows": rows, "by_order": by_order,
            "pairwise_disagreement": len(disagree) / len(pairs),
            "pairwise_disagreement_across_vocabularies": sum(p in disagree for p in cross) / len(cross),
            "pairwise_disagreement_same_vocabulary": sum(p in disagree for p in same) / len(same),
            "best_by_bpb": best_bpb, "best_by_nats_per_token": best_tok,
            "bpb_equals_byte_stream_likelihood_rank": bool(
                sorted(names, key=lambda n: rows[n]["val_bpb"]) == sorted(names, key=lambda n: rows[n]["nats_total"]))}


# --------------------------------------------------------------------------- B: the loop
def night(args) -> dict:
    metric, seed, n, llm_spec = args
    task = TokenizerTask(metric)
    agent, llms = research_agent(llm_spec, task.mock_edit_pool(), seed=seed)
    res = AutoresearchLoop(task, agent, Config(max_experiments=n, seed=seed, plot=False, overwrite=True,
                                               persist=is_live(llm_spec), tag=f"e3-{metric}-{seed}"),
                           out_dir=SCRATCH / "e3" / f"{metric}_{seed}_{llm_spec.replace(':', '_')}", llms=llms).run()
    vocab, order, alpha = task._config(res.best)
    final = corpus().score("val", vocab, order, alpha)
    aud = res.meta["audit"][-1]
    return {"metric": metric, "seed": seed, "final_vocab": vocab, "final_order": order, "final_alpha": alpha,
            "final_val_bpb": final["val_bpb"], "final_val_nats_per_token": final["val_nats_per_token"],
            "final_test_iid_bpb": aud.get("test_iid_bpb"), "n_keep": res.meta["analysis"]["n_keep"] - 1,
            "kept": [n_.change for n_ in res.ledger.nodes() if n_.status == "keep"][1:],
            "vocab_keeps": sum(1 for n_ in res.ledger.nodes() if n_.status == "keep" and "VOCAB" in n_.change),
            "vocab_tried": sum(1 for n_ in res.ledger.nodes() if n_.kind == "candidate" and "VOCAB" in (n_.change or "")),
            "usage": usage_of(llms)}


def loop_part(seeds, n, llm_spec, workers) -> dict:
    corpus().fit(max(VOCABS))                             # learn the merges once before forking workers
    for v in VOCABS:
        for s in ("train", "val", "test_iid"):
            corpus().tokens(s, v)
    runs = pool_map(night, [(m, s, n, llm_spec) for m in ("val_bpb", "val_nats_per_token") for s in seeds],
                    1 if is_live(llm_spec) else workers)
    out = {}
    for m in ("val_bpb", "val_nats_per_token"):
        rs = [r for r in runs if r["metric"] == m]
        out[m] = {"final_val_bpb": ci([r["final_val_bpb"] for r in rs]),
                  "final_test_iid_bpb": ci([r["final_test_iid_bpb"] for r in rs]),
                  "final_vocab": ci([r["final_vocab"] for r in rs]),
                  "share_final_vocab_above_256": float(np.mean([r["final_vocab"] > 256 for r in rs])),
                  "vocab_keeps": ci([r["vocab_keeps"] for r in rs]), "runs": rs}
    return out


def main():
    ap = parser(__doc__.splitlines()[0], seeds=5)
    a = ap.parse_args()
    live = is_live(a.llm)
    n = (4 if a.quick else 10) if live else (12 if a.quick else 30)
    seeds = list(range(min(a.seeds, 1) if (live and a.quick) else 2 if a.quick else a.seeds))
    out = {"config": {"vocabs": VOCABS, "orders": ORDERS, "bpe_sample_bytes": BPE_SAMPLE_BYTES,
                      "loop_experiments": n, "seeds": seeds, "llm": a.llm, "keep_rule": "strict"}}
    if not live:
        out["battery"] = battery()
    out["loop"] = loop_part(seeds, n, a.llm, a.workers)
    L = out["loop"]
    verdict = {"loop_final_val_bpb_judged_by_bpb_vs_per_token": [L["val_bpb"]["final_val_bpb"]["mean"],
                                                                 L["val_nats_per_token"]["final_val_bpb"]["mean"]],
               "loop_final_vocab_judged_by_bpb_vs_per_token": [L["val_bpb"]["final_vocab"]["mean"],
                                                               L["val_nats_per_token"]["final_vocab"]["mean"]],
               "loop_hidden_test_bpb_judged_by_bpb_vs_per_token": [L["val_bpb"]["final_test_iid_bpb"]["mean"],
                                                                   L["val_nats_per_token"]["final_test_iid_bpb"]["mean"]]}
    if live:
        verdict["usage"] = [r["usage"] for m in L for r in L[m]["runs"]]
    else:
        B = out["battery"]
        verdict.update({"pairwise_disagreement_across_vocabularies": B["pairwise_disagreement_across_vocabularies"],
                        "pairwise_disagreement_same_vocabulary": B["pairwise_disagreement_same_vocabulary"],
                        "best_by_bpb": B["best_by_bpb"], "best_by_nats_per_token": B["best_by_nats_per_token"],
                        "bpb_rank_equals_byte_stream_likelihood_rank": B["bpb_equals_byte_stream_likelihood_rank"],
                        "nats_per_token_rises_with_vocab_every_order": all(
                            all(x < y for x, y in zip(B["by_order"][o]["nats_per_token"],
                                                      B["by_order"][o]["nats_per_token"][1:])) for o in ORDERS)})
        bpb_loop, tok_loop = L["val_bpb"], L["val_nats_per_token"]
        verdict["claim_reproduced"] = bool(
            B["pairwise_disagreement_across_vocabularies"] > 0 and B["pairwise_disagreement_same_vocabulary"] == 0
            and B["bpb_equals_byte_stream_likelihood_rank"]
            and bpb_loop["final_val_bpb"]["mean"] < tok_loop["final_val_bpb"]["mean"])
    out["verdict"] = verdict
    name = "e3_bpb_vocab" + suffix(a.llm, a.quick)
    out["figure"] = str(figure(out, name))
    write(name, out)
    print(json.dumps(verdict, indent=1))


def figure(out, name):
    from _common import RESULTS

    p = plt()
    fig, axes = p.subplots(1, 3 if "battery" in out else 1, figsize=(16 if "battery" in out else 6, 4.3))
    axes = np.atleast_1d(axes)
    k = 0
    if "battery" in out:
        B = out["battery"]
        for key, ax, lab in (("nats_per_token", axes[0], "val loss per token (nats)"),
                             ("bpb", axes[1], "val bits per byte")):
            for o in ORDERS:
                ax.plot(VOCABS, B["by_order"][o][key], marker="o", label=f"{o}-gram")
            ax.set_xscale("log", base=2)
            ax.set_xticks(VOCABS)
            ax.set_xticklabels([str(v) for v in VOCABS])
            ax.set_xlabel("vocabulary (256 = bytes, larger = BPE)")
            ax.set_ylabel(lab)
            ax.legend(fontsize=8)
        axes[0].set_title("per-token loss rises with the vocabulary", fontsize=9)
        axes[1].set_title("bits per byte: comparable across vocabularies", fontsize=9)
        k = 2
    ax = axes[k]
    L = out["loop"]
    for i, m in enumerate(("val_bpb", "val_nats_per_token")):
        vals = [r["final_val_bpb"] for r in L[m]["runs"]]
        ax.scatter([i] * len(vals), vals, color=["#1e8e3e", "#d93025"][i], zorder=3)
        ax.bar(i, float(np.mean(vals)), color=["#1e8e3e", "#d93025"][i], alpha=0.3)
    ax.set_xticks([0, 1])
    ax.set_xticklabels(["loop judged by bpb", "loop judged by\nper-token loss"])
    lo = min(r["final_val_bpb"] for m in L for r in L[m]["runs"])
    ax.set_ylim(lo - 0.1, None)
    ax.set_ylabel("val bpb of the final kept version")
    ax.set_title("autoresearch night on the tokenizer task", fontsize=9)
    fig.tight_layout()
    path = RESULTS / f"{name}.png"
    RESULTS.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=110)
    return path


if __name__ == "__main__":
    main()
