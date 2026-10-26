"""rsi: recursive self-improvement loops (autoresearch, RRSI, Dream-RSI, EvoMap, GEPA, Meta-Harness, SoL-Pi, Harness²)."""
from .core import *  # noqa: F401,F403
from .api import METHODS, describe_methods, improve, make_config, method_module, recommend  # noqa: F401

__version__ = "0.1.0"
