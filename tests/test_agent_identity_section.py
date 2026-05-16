"""Tests for the Agent Identity section in the rendered AGENT.md fragment.

Covers the four-change minimal contract:
- Empty ``agent_name`` ⇒ section omitted (legacy output preserved).
- Non-empty ``agent_name`` ⇒ section rendered with name interpolated.
- Different names ⇒ different fragment hashes (drift detection).
- ``read_agent_name_from_config`` returns "" on missing/unreadable config.
"""

from __future__ import annotations

from pathlib import Path

from elfmem.agent_docs import get_fragment_hash, render_agent_docs
from elfmem.project import read_agent_name_from_config


def test_no_agent_name_omits_identity_section() -> None:
    out = render_agent_docs()
    assert "Agent Identity:" not in out
    assert "## Quick Start" in out  # legacy content still present


def test_agent_name_renders_identity_section_with_name() -> None:
    out = render_agent_docs(agent_name="Nim")
    assert "## Agent Identity: Nim" in out
    assert '"Nim" by name' in out
    assert "elfmem recall --frame self" in out
    # Section sits before Quick Start so the host LLM reads identity first.
    assert out.index("Agent Identity: Nim") < out.index("Quick Start")


def test_different_names_produce_different_hashes() -> None:
    h_empty = get_fragment_hash(render_agent_docs())
    h_nim = get_fragment_hash(render_agent_docs(agent_name="Nim"))
    h_aria = get_fragment_hash(render_agent_docs(agent_name="Aria"))
    assert len({h_empty, h_nim, h_aria}) == 3


def test_read_agent_name_handles_missing_or_unreadable(tmp_path: Path) -> None:
    assert read_agent_name_from_config(None) == ""
    assert read_agent_name_from_config(tmp_path / "nope.yaml") == ""
    bad = tmp_path / "bad.yaml"
    bad.write_text("not: [valid: yaml", encoding="utf-8")
    assert read_agent_name_from_config(bad) == ""


def test_read_agent_name_reads_field(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "project:\n  name: demo\n  agent_name: Nim\n",
        encoding="utf-8",
    )
    assert read_agent_name_from_config(cfg) == "Nim"
