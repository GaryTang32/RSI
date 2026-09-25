"""tinylm: CPU byte-level language-model research task (karpathy/autoresearch analogue).

``prepare.py`` is the locked file (corpus, dataloader/budget clock, val_bpb);
``train.py`` is the editable baseline. :class:`TinyLMTask` wraps both as an
:class:`rsi.autoresearch.ScriptResearchTask`.
"""
from .task import DOMAIN_DIR, TinyLMTask, build_corpus, default_data_root, tinylm_edit_pool

__all__ = ["TinyLMTask", "build_corpus", "default_data_root", "tinylm_edit_pool", "DOMAIN_DIR"]
