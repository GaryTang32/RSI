"""MemoClassify: CPU-only online text classification where the harness is a memory system around a frozen model."""
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
