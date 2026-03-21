"""Tests for render_dashboard() — Jinja2 → HTML string → file on disk."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine as sync_create_engine

from elfmem.db.models import metadata
from elfmem.viz.data import DashboardData
from elfmem.viz.renderer import render_dashboard

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def empty_db_path(tmp_path: pytest.fixture) -> str:  # type: ignore[assignment]
    """An empty but fully-schemaed database."""
    path = str(tmp_path / "render_test.db")
    engine = sync_create_engine(f"sqlite:///{path}")
    with engine.begin() as conn:
        metadata.create_all(conn)
    engine.dispose()
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO system_config (key, value) VALUES ('total_active_hours', '0.0')"
        )
        conn.commit()
    return path


@pytest.fixture
def minimal_data(empty_db_path: str) -> DashboardData:
    """DashboardData from an empty database — always succeeds."""
    return DashboardData.from_db(empty_db_path)


@pytest.fixture
def xss_db_path(tmp_path: pytest.fixture) -> str:  # type: ignore[assignment]
    """Database with an active block containing XSS payload."""
    path = str(tmp_path / "xss_test.db")
    engine = sync_create_engine(f"sqlite:///{path}")
    with engine.begin() as conn:
        metadata.create_all(conn)
    engine.dispose()
    block_id = uuid.uuid4().hex[:16]
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO system_config (key, value) VALUES ('total_active_hours', '0.0')"
        )
        conn.execute(
            """
            INSERT INTO blocks
              (id, content, category, source, created_at, status,
               confidence, reinforcement_count, decay_lambda,
               last_reinforced_at, outcome_evidence)
            VALUES (?, ?, 'general', 'api', ?, 'active', 0.5, 0, 0.010, 0.0, 0.0)
            """,
            (block_id, "<script>alert('xss')</script>", datetime.now(UTC).isoformat()),
        )
        conn.commit()
    return path


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestRenderProducesFile:
    def test_render_produces_html_file(self, minimal_data: DashboardData, tmp_path: Path) -> None:
        output = render_dashboard(minimal_data, open_browser=False)
        assert Path(output).exists()
        assert output.endswith(".html")

    def test_render_custom_path(self, minimal_data: DashboardData, tmp_path: Path) -> None:
        custom = str(tmp_path / "custom_dashboard.html")
        output = render_dashboard(minimal_data, path=custom, open_browser=False)
        assert output == str(Path(custom).resolve())
        assert Path(custom).exists()

    def test_render_returns_absolute_path(self, minimal_data: DashboardData) -> None:
        output = render_dashboard(minimal_data, open_browser=False)
        assert Path(output).is_absolute()


class TestRenderContent:
    def test_render_html_contains_elfmem_data(self, minimal_data: DashboardData) -> None:
        output = render_dashboard(minimal_data, open_browser=False)
        content = Path(output).read_text(encoding="utf-8")
        assert "ELFMEM_DATA" in content

    def test_render_online_uses_cdn_url(self, minimal_data: DashboardData) -> None:
        output = render_dashboard(minimal_data, open_browser=False, offline=False)
        content = Path(output).read_text(encoding="utf-8")
        assert "cdnjs.cloudflare.com" in content

    def test_render_offline_inlines_vis_network(self, minimal_data: DashboardData) -> None:
        output = render_dashboard(minimal_data, open_browser=False, offline=True)
        content = Path(output).read_text(encoding="utf-8")
        # The stub vis-network.min.js contains "vis" — confirms it was inlined
        assert "vis-network stub" in content

    def test_render_offline_no_cdn_url(self, minimal_data: DashboardData) -> None:
        output = render_dashboard(minimal_data, open_browser=False, offline=True)
        content = Path(output).read_text(encoding="utf-8")
        assert "cdnjs.cloudflare.com" not in content

    def test_render_no_browser_open_does_not_raise(self, minimal_data: DashboardData) -> None:
        # Should complete without error even if browser is unavailable
        output = render_dashboard(minimal_data, open_browser=False)
        assert Path(output).exists()


class TestXSSProtection:
    def test_xss_content_not_unescaped_in_output(self, xss_db_path: str) -> None:
        data = DashboardData.from_db(xss_db_path)
        output = render_dashboard(data, open_browser=False)
        content = Path(output).read_text(encoding="utf-8")
        # Raw script tag must not appear literally
        assert "<script>alert('xss')</script>" not in content

    def test_xss_json_uses_unicode_escapes(self, xss_db_path: str) -> None:
        data = DashboardData.from_db(xss_db_path)
        json_str = data.to_json()
        # to_json() replaces < with \u003c for safe HTML embedding
        assert r"\u003c" in json_str
        assert "<script>" not in json_str


class TestApiGuard:
    def test_visualise_raises_elfmem_error_without_viz(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """visualise() raises ElfmemError when elfmem.viz cannot be imported."""
        import builtins

        from elfmem.exceptions import ElfmemError

        real_import = builtins.__import__

        def patched_import(name: str, *args: object, **kwargs: object) -> object:
            if name == "elfmem.viz":
                raise ImportError("No module named 'elfmem.viz'")
            return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(builtins, "__import__", patched_import)

        # Build a minimal MemorySystem-like object with the method we can call
        # by importing the method directly from a real MemorySystem
        # and calling it with a patched _db_path.
        from unittest.mock import MagicMock, PropertyMock

        from elfmem.api import MemorySystem

        ms = MagicMock(spec=MemorySystem)
        type(ms)._db_path = PropertyMock(return_value=str(tmp_path / "fake.db"))
        # Create the fake db so the ElfmemError path is reached (not FileNotFoundError)
        (tmp_path / "fake.db").touch()

        with pytest.raises(ElfmemError) as exc_info:
            MemorySystem.visualise(ms)

        assert "uv add elfmem[viz]" in exc_info.value.recovery
