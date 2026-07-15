"""Pluggable scoring strategies.

A strategy is a Python module exposing:

    NAME        : str             — shown in the GUI dropdown
    DESCRIPTION : str             — one-line explanation
    OPTIONS     : dict[str, dict] — option schema (see below)
    score(selected: set[int], correct: set[int], weight: float,
          num_options: int, **opts) -> float

``OPTIONS`` maps option name to a small dict::

    {"type": float | int | bool, "default": <value>,
     "label": "Human label", "tooltip": "(optional)"}

The GUI uses ``OPTIONS`` to render a form on the Review tab.

Three strategies ship in this package; users may also load a custom .py
file via :func:`load_strategy_from_file`."""
import importlib
import importlib.util
from types import ModuleType
from typing import Iterable

BUILTIN_NAMES = ("all_or_nothing", "partial_credit", "only_correct_partial", "negative_marking")


def list_builtins() -> Iterable[ModuleType]:
    """Return the built-in strategy modules in display order."""
    return [importlib.import_module(f"bubblemarking.scoring.{n}") for n in BUILTIN_NAMES]


def load_strategy_from_file(path: str) -> ModuleType:
    """Load a strategy from an arbitrary .py file. Raises ValueError if the
    module does not expose a callable ``score``."""
    spec = importlib.util.spec_from_file_location("bubblemarking_custom_strategy", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Could not load module from {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not hasattr(mod, "score") or not callable(mod.score):
        raise ValueError("Strategy module must define a callable `score`.")
    if not hasattr(mod, "NAME"):
        mod.NAME = path
    if not hasattr(mod, "DESCRIPTION"):
        mod.DESCRIPTION = ""
    if not hasattr(mod, "OPTIONS"):
        mod.OPTIONS = {}
    return mod


def default_options(strategy: ModuleType) -> dict:
    """Return ``{name: default}`` for a strategy."""
    return {k: v.get("default") for k, v in getattr(strategy, "OPTIONS", {}).items()}


def coerce_options(strategy: ModuleType, raw: dict) -> dict:
    """Coerce option values to their declared types, falling back to defaults
    on bad input."""
    schema = getattr(strategy, "OPTIONS", {})
    out = {}
    for name, spec in schema.items():
        t = spec.get("type", str)
        if name not in raw:
            out[name] = spec.get("default")
            continue
        v = raw[name]
        try:
            if t is bool:
                out[name] = bool(v) if not isinstance(v, str) else v.strip().lower() in ("1", "true", "yes", "on")
            elif t is int:
                out[name] = int(v)
            elif t is float:
                out[name] = float(v)
            else:
                out[name] = v
        except (ValueError, TypeError):
            out[name] = spec.get("default")
    return out
