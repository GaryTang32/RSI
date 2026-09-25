"""tabular: gradient-boosted trees against a held-out metric (the non-LLM autoresearch adaptation).

``prepare.py`` is locked (synthetic drifting data, CV-AUC protocol, per-row check);
``train.py`` is the editable baseline. :class:`TabularTask` wraps both.
"""
from .task import DOMAIN_DIR, TabularTask, build_data, default_data_root, tabular_edit_pool

__all__ = ["TabularTask", "build_data", "default_data_root", "tabular_edit_pool", "DOMAIN_DIR"]
