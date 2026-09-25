"""Synthetic online-classification datasets for MemoClassify.

Each dataset is a templated-text classification problem with the properties the
Meta-Harness reproduction blueprint asks for (spec section A9.2):

* many labels, grouped into *confusable clusters* that share vocabulary;
* label-specific keywords (so retrieval of similar examples helps);
* hard examples that carry no label-specific keyword (so priors/label lists help);
* an optional in-input option list ("Options: a; b; ...") for datasets whose
  label space is given with the question;
* optional memorisable reference ids ("[ref K4821]") - used by the leakage
  experiment (M5) to show that a proposer which reads search-set traces can
  hard-code answers.

Vocabularies are pseudo-words drawn from a dataset-specific RNG, so the OOD
datasets use genuinely different words and label counts from the search datasets.
Everything is deterministic given the seed.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional

SYLLABLES = ["ka", "lo", "mi", "ne", "ru", "ta", "vo", "zi", "pe", "sa", "do", "fi", "gu", "ha", "ji", "ko", "le",
             "mo", "nu", "pi", "ra", "se", "ti", "vu", "we", "xo", "ya", "ze", "bra", "dri", "fle", "gro", "kli",
             "pra", "sto", "tru", "vin", "mar", "sol", "ten", "dor", "kel", "lun", "pas", "rim", "tor"]


@dataclass(frozen=True)
class Example:
    id: str
    text: str
    label: str


@dataclass
class ClassDataset:
    name: str
    labels: list[str]
    clusters: list[list[str]]
    keywords: dict[str, list[str]]          # label -> label-specific words
    cluster_words: dict[str, list[str]]     # label -> words shared with its cluster
    common: list[str]
    labels_in_input: bool
    train: list[Example] = field(default_factory=list)
    val: list[Example] = field(default_factory=list)
    test: list[Example] = field(default_factory=list)

    def part(self, name: str) -> list[Example]:
        return {"train": self.train, "val": self.val, "test": self.test}[name]

    def cluster_of(self, label: str) -> list[str]:
        for c in self.clusters:
            if label in c:
                return c
        return [label]

    def summary(self) -> dict:
        return {"name": self.name, "labels": len(self.labels), "clusters": len(self.clusters),
                "train": len(self.train), "val": len(self.val), "test": len(self.test),
                "labels_in_input": self.labels_in_input}


@dataclass(frozen=True)
class DatasetSpec:
    name: str
    n_labels: int
    cluster_size: tuple[int, int] = (2, 3)
    n_train: int = 120
    n_val: int = 40
    n_test: int = 60
    labels_in_input: bool = False
    hard_frac: float = 0.2        # examples with no label-specific keyword
    confuse_p: float = 0.35       # prob. of a sibling-label keyword (confusion)
    with_ids: bool = False        # memorisable reference ids
    n_common: int = 40
    words_min: int = 7
    words_max: int = 11


def _word(rng: random.Random, lo: int = 2, hi: int = 3) -> str:
    return "".join(rng.choice(SYLLABLES) for _ in range(rng.randint(lo, hi)))


def _unique_words(rng: random.Random, n: int, taken: set[str], lo: int = 2, hi: int = 3) -> list[str]:
    out: list[str] = []
    while len(out) < n:
        w = _word(rng, lo, hi)
        if len(w) >= 4 and w not in taken:
            taken.add(w)
            out.append(w)
    return out


def make_dataset(spec: DatasetSpec, seed: int = 0) -> ClassDataset:
    rng = random.Random(f"memoclassify|{spec.name}|{seed}")
    taken: set[str] = set()
    # labels: two pseudo-words joined by '-'
    labels = [f"{a}-{b}" for a, b in zip(_unique_words(rng, spec.n_labels, taken, 2, 2),
                                         _unique_words(rng, spec.n_labels, taken, 2, 2))]
    order = list(labels)
    rng.shuffle(order)
    clusters: list[list[str]] = []
    i = 0
    while i < len(order):
        size = rng.randint(*spec.cluster_size)
        clusters.append(order[i:i + size])
        i += size
    keywords = {l: _unique_words(rng, 4, taken) for l in labels}
    cluster_words: dict[str, list[str]] = {}
    for c in clusters:
        shared = _unique_words(rng, 4, taken)
        for l in c:
            cluster_words[l] = shared
    common = _unique_words(rng, spec.n_common, taken, 2, 3)
    intros = _unique_words(rng, 6, taken, 2, 2)
    ds = ClassDataset(spec.name, labels, clusters, keywords, cluster_words, common, spec.labels_in_input)

    def cluster_of(l: str) -> list[str]:
        return next(c for c in clusters if l in c)

    def gen(part: str, n: int) -> list[Example]:
        prng = random.Random(f"memoclassify|{spec.name}|{seed}|{part}")
        out = []
        for j in range(n):
            label = labels[j % len(labels)] if j < len(labels) and part == "train" else prng.choice(labels)
            words: list[str] = []
            if prng.random() >= spec.hard_frac:
                words += prng.sample(keywords[label], prng.randint(1, 2))
            words += prng.sample(cluster_words[label], 2)
            sib = [s for s in cluster_of(label) if s != label]
            if sib and prng.random() < spec.confuse_p:
                words.append(prng.choice(keywords[prng.choice(sib)]))
            words += prng.sample(common, prng.randint(spec.words_min - 4, spec.words_max - 4))
            prng.shuffle(words)
            text = f"{prng.choice(intros)}: " + " ".join(words) + "."
            if spec.with_ids:
                text = f"[ref {prng.choice('BCDFGHKMPRSTVXZ')}{prng.randint(1000, 9999)}] " + text
            if spec.labels_in_input:
                text += " Options: " + "; ".join(labels)
            out.append(Example(f"{spec.name}-{part}-{j:04d}", text, label))
        prng.shuffle(out) if part != "train" else None
        return out

    ds.train = gen("train", spec.n_train)
    # interleave train order so the online stream is not sorted by label
    random.Random(f"order|{spec.name}|{seed}").shuffle(ds.train)
    ds.val = gen("val", spec.n_val)
    ds.test = gen("test", spec.n_test)
    return ds


#: Search datasets: analogues of the paper's three (open label space / label list
#: in input / many confusable labels).
SEARCH_SPECS = (
    DatasetSpec("ds_alpha", 28, (2, 3), n_train=84, n_val=48, n_test=72, labels_in_input=False, hard_frac=0.25),
    DatasetSpec("ds_beta", 14, (2, 3), n_train=150, n_val=48, n_test=72, labels_in_input=True, hard_frac=0.3),
    DatasetSpec("ds_gamma", 22, (3, 4), n_train=180, n_val=48, n_test=72, labels_in_input=False, hard_frac=0.2,
                confuse_p=0.55),
)

#: Out-of-distribution datasets: other vocabularies, label counts and sizes.
OOD_SPECS = (
    DatasetSpec("ood_delta", 8, (2, 2), n_train=64, n_val=0, n_test=60, labels_in_input=True, hard_frac=0.3),
    DatasetSpec("ood_epsilon", 36, (2, 4), n_train=144, n_val=0, n_test=60, labels_in_input=False, hard_frac=0.2),
    DatasetSpec("ood_zeta", 12, (3, 3), n_train=200, n_val=0, n_test=60, labels_in_input=False, hard_frac=0.25,
                confuse_p=0.6),
    DatasetSpec("ood_eta", 20, (2, 3), n_train=100, n_val=0, n_test=60, labels_in_input=True, hard_frac=0.2),
)

#: A dataset whose inputs carry memorisable reference ids (M5 leakage experiment).
LEAKY_SPEC = DatasetSpec("ds_leaky", 18, (2, 3), n_train=90, n_val=48, n_test=72, labels_in_input=False,
                         hard_frac=0.35, with_ids=True)


def make_datasets(seed: int = 0, *, search: Optional[tuple] = None, ood: Optional[tuple] = None,
                  scale: float = 1.0) -> tuple[list[ClassDataset], list[ClassDataset]]:
    """Build (search datasets, ood datasets). ``scale`` shrinks every split (tests)."""
    def sc(s: DatasetSpec) -> DatasetSpec:
        if scale == 1.0:
            return s
        import dataclasses
        return dataclasses.replace(s, n_train=max(s.n_labels, int(s.n_train * scale)),
                                   n_val=int(s.n_val * scale), n_test=max(4, int(s.n_test * scale)))
    srch = [make_dataset(sc(s), seed) for s in (search if search is not None else SEARCH_SPECS)]
    oo = [make_dataset(sc(s), seed) for s in (ood if ood is not None else OOD_SPECS)]
    return srch, oo
