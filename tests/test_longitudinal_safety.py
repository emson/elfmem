"""Safety guard tests for the longitudinal harness.

Verifies the harness CANNOT silently touch the production DB:
- assert_in_memory_only rejects file URLs
- assert_in_memory_only rejects ~/.elfmem paths
- assert_no_elfmem_env catches env-var DB pointers
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from scripts.longitudinal_sim.safety import (
    SafetyViolation,
    assert_in_memory_only,
    assert_no_elfmem_env,
)


class _FakeEngine:
    def __init__(self, url: str) -> None:
        self.url = url


def test_rejects_file_url() -> None:
    with pytest.raises(SafetyViolation, match="in-memory"):
        assert_in_memory_only(_FakeEngine("sqlite+aiosqlite:///tmp/x.db"))


def test_rejects_elfmem_path_in_url() -> None:
    forbidden = (
        f"sqlite+aiosqlite:///{Path.home()}/.elfmem/databases/elfmem.db:memory:"
    )
    with pytest.raises(SafetyViolation, match="forbidden path fragment"):
        assert_in_memory_only(_FakeEngine(forbidden))


def test_accepts_in_memory_url() -> None:
    assert_in_memory_only(_FakeEngine("sqlite+aiosqlite:///:memory:"))


def test_rejects_elfmem_env_var(monkeypatch) -> None:
    monkeypatch.setenv("ELFMEM_DB_PATH", "/Users/x/.elfmem/databases/elfmem.db")
    with pytest.raises(SafetyViolation, match="ELFMEM_DB_PATH"):
        assert_no_elfmem_env()


def test_no_env_passes() -> None:
    for var in ("ELFMEM_DB_PATH", "ELFMEM_CONFIG_PATH", "ELFMEM_PROJECT_ROOT"):
        os.environ.pop(var, None)
    assert_no_elfmem_env()
