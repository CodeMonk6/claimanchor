"""Make ``agent_skeleton`` importable in tests without an editable install.

The import package is ``agent_skeleton`` but the checkout folder has a different
name, so ``import agent_skeleton`` only resolves after ``pip install -e .``. This
shim registers the package pointing at the repo root so the offline test suite
runs on the standard library alone (no install, no a2a-sdk/anthropic needed).
Individual test files also self-bootstrap the same way so they run under plain
``python tests/test_*.py`` as well as ``pytest``.
"""
from __future__ import annotations

import pathlib
import sys
import types

_ROOT = pathlib.Path(__file__).resolve().parent.parent

if "agent_skeleton" not in sys.modules:
    try:
        import agent_skeleton  # noqa: F401  (real editable install present)
    except Exception:
        _pkg = types.ModuleType("agent_skeleton")
        _pkg.__path__ = [str(_ROOT)]
        sys.modules["agent_skeleton"] = _pkg
