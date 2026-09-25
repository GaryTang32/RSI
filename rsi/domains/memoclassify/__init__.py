"""MemoClassify: online text classification where the harness is a memory system.

The CPU-only analogue of Meta-Harness's text-classification setting (spec A9.2):
synthetic datasets with many confusable labels, a deterministic frozen model
(:class:`MemoLM`, variants ``A`` and ``B``) that learns only in context and
degrades on long prompts, the ``MemorySystem`` interface, the ``no_memory`` and
``fewshot_all`` seed baselines, and exact context-cost accounting.

    from rsi.domains.memoclassify import make_domain
    dom = make_domain(seed=0)
    seed = dom.seed_artifact("fewshot_all")      # Artifact({"memory.py": ...})
    model = dom.make_model("A")                   # frozen base model
"""
from .data import (LEAKY_SPEC, OOD_SPECS, SEARCH_SPECS, ClassDataset, DatasetSpec, Example, make_dataset,
                   make_datasets)
from .domain import MemoClassifyDomain, load_memory_class, make_domain, normalize_label, validate_memory
from .memory import COMPARATOR_PROGRAMS, SEED_PROGRAMS, MemorySystem, extract_json_field, fewshot_program
from .model import MemoLM
from . import programs

__all__ = ["MemoClassifyDomain", "make_domain", "MemoLM", "MemorySystem", "extract_json_field", "SEED_PROGRAMS",
           "COMPARATOR_PROGRAMS", "fewshot_program",
           "ClassDataset", "DatasetSpec", "Example", "make_dataset", "make_datasets", "SEARCH_SPECS", "OOD_SPECS",
           "LEAKY_SPEC", "load_memory_class", "normalize_label", "validate_memory", "programs"]
