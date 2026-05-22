"""Safety guards — refuse to operate on the production DB.

The longitudinal harness must NEVER touch ~/.elfmem/databases/elfmem.db or any
real on-disk DB. Three checks:

1. Engine URL must contain ":memory:".
2. No path under ~/.elfmem/ may appear in any config seen by the harness.
3. ``from_config()`` is never called (enforced by code review; this module
   provides the runtime check the code can run before any operation).

Usage:
    from scripts.longitudinal_sim.safety import assert_in_memory_only
    assert_in_memory_only(engine)
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

FORBIDDEN_PATH_FRAGMENTS = (
    "/.elfmem/databases",
    "/.elfmem/elfmem.db",
    str(Path.home() / ".elfmem"),
)


class SafetyViolation(RuntimeError):
    """Raised when the harness would touch the production DB."""


def assert_in_memory_only(engine: Any) -> None:
    """Refuse to proceed if the engine is anything other than in-memory SQLite.

    Checks the engine.url string for ``:memory:``. Anything else — including
    tempfile paths or named in-memory shared caches — is rejected.
    """
    url = str(getattr(engine, "url", engine))
    if ":memory:" not in url:
        raise SafetyViolation(
            f"longitudinal harness requires in-memory engine; got url={url!r}"
        )
    for fragment in FORBIDDEN_PATH_FRAGMENTS:
        if fragment in url:
            raise SafetyViolation(
                f"engine URL contains forbidden path fragment {fragment!r}: {url!r}"
            )


def assert_no_elfmem_env() -> None:
    """Refuse to proceed if env vars point at a real elfmem DB.

    ELFMEM_DB_PATH or similar would override defaults; we want a clean slate.
    """
    for var in ("ELFMEM_DB_PATH", "ELFMEM_CONFIG_PATH", "ELFMEM_PROJECT_ROOT"):
        if var in os.environ:
            raise SafetyViolation(
                f"env var {var} is set ({os.environ[var]!r}); unset before running "
                f"the longitudinal harness to avoid auto-discovery of real DB"
            )
