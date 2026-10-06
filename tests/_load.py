"""Import the plugin package under a valid Python identifier.

The package directory is ``comment-sicko`` and Hermes loads it through a
namespaced loader, so ``import comment-sicko`` is not available to tests. This
registers the directory under the alias ``cs`` and installs it in
``sys.modules``, so ``from cs import gate`` works and the relative imports
inside the package resolve against the real package.

Relative imports inside the plugin (``from . import config``) resolve through
``__package__``, which the alias preserves.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

ALIAS = "cs"
ROOT = Path(__file__).resolve().parent.parent


def load() -> object:
    """Import and return the plugin package."""
    if ALIAS in sys.modules:
        return sys.modules[ALIAS]

    init = ROOT / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        ALIAS, init, submodule_search_locations=[str(ROOT)]
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load plugin package from {init}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[ALIAS] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        del sys.modules[ALIAS]
        raise
    return module


def submodule(name: str):
    """Import and return one submodule, e.g. ``submodule('scanner.strip')``."""
    load()
    return importlib.import_module(f"{ALIAS}.{name}")