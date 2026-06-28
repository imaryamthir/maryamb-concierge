"""Layla concierge agent package.

ADK looks for `root_agent` when running `adk web` / `adk run` from the project
root, so we re-export it here for convenience.

The import is wrapped defensively: the security guardrails in `guardrails.py`
are pure Python and must remain importable (and unit-testable) even in an
environment where `google-adk` is not installed.
"""

try:
    from .agent import root_agent  # noqa: F401
    __all__ = ["root_agent"]
except ModuleNotFoundError:  # google-adk not present (e.g. running unit tests)
    __all__ = []
