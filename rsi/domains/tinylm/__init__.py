"""tinylm: CPU byte-level language-model research task (locked ``prepare.py``, editable ``train.py``)."""
from .task import DOMAIN_DIR, TinyLMTask, build_corpus, default_data_root, tinylm_edit_pool

__all__ = ["TinyLMTask", "build_corpus", "default_data_root", "tinylm_edit_pool", "DOMAIN_DIR"]
