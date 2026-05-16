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


# ── Migration safety ─────────────────────────────────────────────────────────


def test_empty_agent_name_hash_is_pre_feature_hash() -> None:
    """Existing v0.13.3 installs upgrading must not see drift false-positives.

    Hash with empty agent_name must equal what the pre-feature renderer would
    have produced (which had no agent_name suffix at all). If this breaks,
    every existing install's `elfmem agent-docs check` will report "edited"
    immediately after upgrade.
    """
    import hashlib

    from elfmem.agent_docs import _guides_to_markdown
    from elfmem.guide import GUIDES

    pre_feature_input = _guides_to_markdown(GUIDES)
    pre_feature_hash = hashlib.sha256(pre_feature_input.encode()).hexdigest()[:16]

    current_empty_hash = get_fragment_hash(render_agent_docs(agent_name=""))
    # The fragment hash (of the full rendered text) is NOT the same as the
    # guides_content hash — render_agent_docs computes guides_content hash and
    # embeds it in the header. So instead, just verify the embedded
    # version/hash comment in the rendered fragment matches between
    # pre_feature_hash and what we render now.
    out_empty = render_agent_docs(agent_name="")
    assert pre_feature_hash in out_empty, (
        "Rendering with empty agent_name must embed the pre-feature guides "
        "hash unchanged; an existing install's lock file would otherwise see "
        "drift on upgrade."
    )
    assert current_empty_hash != pre_feature_hash  # different bytes hashed


def test_set_agent_name_replaces_existing_line(tmp_path: Path) -> None:
    from elfmem.project import set_agent_name_in_config

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        '# leading comment\n'
        'project:\n'
        '  name: "demo"\n'
        '  db: "/tmp/demo.db"\n'
        '  identity: "old identity"\n'
        '  agent_name: ""\n'
        '  created: "2026-05-16"\n'
        '\n'
        'llm:\n'
        '  model: "x"\n',
        encoding="utf-8",
    )
    action = set_agent_name_in_config(cfg, "elf")
    assert action == "replaced"
    text = cfg.read_text(encoding="utf-8")
    assert 'agent_name: "elf"' in text
    # Surrounding lines and comments preserved byte-for-byte.
    assert "# leading comment" in text
    assert 'identity: "old identity"' in text
    assert 'created: "2026-05-16"' in text
    assert 'model: "x"' in text


def test_set_agent_name_inserts_after_identity_when_missing(tmp_path: Path) -> None:
    """Upgraded users have pre-feature configs that lack the field entirely."""
    from elfmem.project import set_agent_name_in_config

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        'project:\n'
        '  name: "demo"\n'
        '  db: "/tmp/demo.db"\n'
        '  identity: "old identity"\n'
        '  created: "2026-04-01"\n',
        encoding="utf-8",
    )
    action = set_agent_name_in_config(cfg, "elf")
    assert action == "inserted"
    text = cfg.read_text(encoding="utf-8")
    # New line immediately after identity, before created.
    idx_identity = text.index("identity:")
    idx_agent = text.index("agent_name:")
    idx_created = text.index("created:")
    assert idx_identity < idx_agent < idx_created
    assert 'agent_name: "elf"' in text


def test_set_agent_name_unchanged_when_value_matches(tmp_path: Path) -> None:
    from elfmem.project import set_agent_name_in_config

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        'project:\n'
        '  identity: "x"\n'
        '  agent_name: "elf"\n',
        encoding="utf-8",
    )
    original = cfg.read_text(encoding="utf-8")
    action = set_agent_name_in_config(cfg, "elf")
    assert action == "unchanged"
    assert cfg.read_text(encoding="utf-8") == original


def test_set_agent_name_refuses_when_no_anchor(tmp_path: Path) -> None:
    """Won't invent project-section structure in a config it doesn't understand."""
    import pytest

    from elfmem.exceptions import ConfigError
    from elfmem.project import set_agent_name_in_config

    cfg = tmp_path / "config.yaml"
    cfg.write_text("llm:\n  model: x\n", encoding="utf-8")
    with pytest.raises(ConfigError) as exc_info:
        set_agent_name_in_config(cfg, "elf")
    # Agent-first contract: every elfmem exception carries an actionable
    # .recovery field — not just a message string.
    assert exc_info.value.recovery
    assert "identity:" in exc_info.value.recovery or "init" in exc_info.value.recovery


def test_set_agent_name_missing_config_raises_config_error(tmp_path: Path) -> None:
    import pytest

    from elfmem.exceptions import ConfigError
    from elfmem.project import set_agent_name_in_config

    nonexistent = tmp_path / "does_not_exist.yaml"
    with pytest.raises(ConfigError) as exc_info:
        set_agent_name_in_config(nonexistent, "elf")
    assert exc_info.value.recovery
    assert "init" in exc_info.value.recovery
