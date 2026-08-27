"""The final migration step: flipping authority to the file substrate.

`substrate_export` moves the data and verifies it; `substrate_cutover` is what
makes the agent actually read it. The export was always safe by construction
(it only ever reads the live database), so all of this step's risk is in
*when* it is allowed to run — hence preflight, which is most of the surface.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from elfmem.migrate import (
    MigrationStep,
    SubstrateMarker,
    _set_files_authoritative,
    _write_marker_atomic,
    apply_cutover_step,
    cutover_preflight,
    cutover_step_id,
    scan_cutover,
)


def _marker(memory_dir: Path, *, fingerprint: str = "fp", parity: bool = True) -> None:
    """Record a completed export so cutover becomes reachable."""
    _write_marker_atomic(memory_dir.parent, SubstrateMarker(
        fingerprint=fingerprint, files_fingerprint="ff", applied_at="2026-08-27T00:00:00Z",
        backup_path="/tmp/b.bak", memory_dir=str(memory_dir),
        index_db_path=str(memory_dir.parent / "index.db"),
        blocks_exported=3, blocks_written=3,
        parity_passed=parity, diverging_query_count=0,
    ))


def _step(config: Path) -> MigrationStep:
    return MigrationStep(
        id=cutover_step_id(config), kind="substrate_cutover", summary="",
        file=config, file_sha256="", issues=[], before={}, after={},
        json_pointer="/substrate/files_authoritative",
    )


# ── The config edit ──────────────────────────────────────────────────────────


class TestSetFilesAuthoritative:
    """A surgical line edit, not a yaml round-trip: the generated config is
    mostly comments explaining why each value is what it is, and pyyaml would
    silently discard every one of them."""

    @pytest.mark.parametrize(
        "before",
        [
            "project:\n  name: x\n\nsubstrate:\n  files_authoritative: false\n",
            "substrate:\n  files_authoritative: true\n",
            "project:\n  name: x\n\nsubstrate:\n",
            "project:\n  name: x\n  db: /tmp/a.db\n",
            "project:\n  name: x",  # no trailing newline
        ],
        ids=["key-false", "key-true", "block-no-key", "no-block", "no-newline"],
    )
    def test_sets_true_from_every_shape(self, tmp_path: Path, before: str):
        config = tmp_path / "config.yaml"
        config.write_text(before)
        _set_files_authoritative(config, value=True)
        parsed = yaml.safe_load(config.read_text())
        assert parsed["substrate"]["files_authoritative"] is True

    def test_round_trips_back_to_false(self, tmp_path: Path):
        config = tmp_path / "config.yaml"
        config.write_text("substrate:\n  files_authoritative: true\n")
        _set_files_authoritative(config, value=False)
        assert yaml.safe_load(config.read_text())["substrate"]["files_authoritative"] is False

    def test_comments_survive(self, tmp_path: Path):
        config = tmp_path / "config.yaml"
        config.write_text(
            "# why this model\nllm:\n  model: x  # inline note\n"
            "substrate:\n  files_authoritative: false\n# trailing note\n"
        )
        _set_files_authoritative(config, value=True)
        text = config.read_text()
        assert "# why this model" in text
        assert "# inline note" in text
        assert "# trailing note" in text

    def test_commented_out_key_is_not_mistaken_for_the_setting(self, tmp_path: Path):
        """A config documenting the flag in a comment must not have the
        comment rewritten in place of the real setting."""
        config = tmp_path / "config.yaml"
        config.write_text("# files_authoritative: false  <- docs only\nproject:\n  name: x\n")
        _set_files_authoritative(config, value=True)
        text = config.read_text()
        assert "# files_authoritative: false  <- docs only" in text
        assert yaml.safe_load(text)["substrate"]["files_authoritative"] is True


# ── Preflight ────────────────────────────────────────────────────────────────


def _git(path: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=path, capture_output=True, check=False)


def _project(tmp_path: Path) -> tuple[Path, Path, Path]:
    """(db_path, memory_dir, config_path) for a bare, un-exported project."""
    elfmem = tmp_path / ".elfmem"
    (elfmem / "memory").mkdir(parents=True)
    (elfmem / "ledger").mkdir(parents=True)
    config = elfmem / "config.yaml"
    config.write_text("project:\n  name: t\n")
    return tmp_path / "agent.db", elfmem / "memory", config


class TestPreflight:
    async def test_blocks_when_no_export_recorded(self, tmp_path: Path):
        db, memory, config = _project(tmp_path)
        checks = await cutover_preflight(db, memory, config)
        assert not checks[0].ok
        assert checks[0].name == "export applied"
        assert "migrate apply" in checks[0].recovery

    async def test_scan_offers_nothing_without_an_export(self, tmp_path: Path):
        """Cutover must never be the first thing a user is offered — there
        would be no verified files to cut over to."""
        db, memory, config = _project(tmp_path)
        assert await scan_cutover(db, memory, config) is None

    async def test_scan_offers_nothing_once_already_flipped(self, tmp_path: Path):
        """With a completed export present, so the already-flipped guard is
        what is actually being exercised rather than the missing-export one."""
        db, memory, config = _project(tmp_path)
        _marker(memory)
        assert await scan_cutover(db, memory, config) is not None, (
            "precondition: with an export recorded, cutover should be offered"
        )
        _set_files_authoritative(config, value=True)
        assert await scan_cutover(db, memory, config) is None


class TestApplyRefusesOnFailedPreflight:
    """The safety guarantee itself. Preflight reporting a blocker is useless
    if apply proceeds anyway."""

    async def test_apply_fails_and_leaves_config_untouched(self, tmp_path: Path):
        db, memory, config = _project(tmp_path)
        _marker(memory)  # export done, but no git repo -> git checks block
        before = config.read_text()

        result = await apply_cutover_step(
            _step(config), db_path=db, memory_dir=memory,
            config_path=config, dry_run=False, force=False,
        )

        assert result.status == "failed"
        assert "preflight failed" in result.detail
        assert config.read_text() == before, "a refused cutover must not edit the config"

    async def test_dry_run_never_writes(self, tmp_path: Path):
        db, memory, config = _project(tmp_path)
        _marker(memory)
        before = config.read_text()
        result = await apply_cutover_step(
            _step(config), db_path=db, memory_dir=memory,
            config_path=config, dry_run=True, force=True,
        )
        assert result.status == "skipped"
        assert config.read_text() == before

    async def test_force_proceeds_past_blockers(self, tmp_path: Path):
        """The escape hatch has to actually work, or users will edit the
        config by hand and skip every check instead."""
        db, memory, config = _project(tmp_path)
        _marker(memory)
        result = await apply_cutover_step(
            _step(config), db_path=db, memory_dir=memory,
            config_path=config, dry_run=False, force=True,
        )
        assert result.status == "applied"
        assert "forced" in result.detail
        assert yaml.safe_load(config.read_text())["substrate"]["files_authoritative"] is True


class TestStepId:
    def test_names_the_project_not_the_config_dir(self, tmp_path: Path):
        """`config_path.parent` is always the literal `.elfmem`, so naming the
        step after it would give every project on a machine the same id."""
        config = tmp_path / ".elfmem" / "config.yaml"
        config.parent.mkdir(parents=True)
        assert cutover_step_id(config).endswith(tmp_path.name)
        assert ".elfmem" not in cutover_step_id(config)


class TestGitPreflight:
    """Git is not a nicety here: under file authority git history is the only
    undo for forget() and edit(), so an untracked or uncommitted substrate
    means the pre-cutover state was never captured."""

    async def test_untracked_substrate_is_reported(self, tmp_path: Path):
        db, memory, config = _project(tmp_path)
        checks = await cutover_preflight(db, memory, config)
        git_checks = [c for c in checks if c.name.startswith("git tracks")]
        assert git_checks, "git trackability must be checked"
        assert all(not c.ok for c in git_checks), "no repo here, so none can pass"
        assert all("git init" in c.recovery for c in git_checks)

    async def test_gitignored_substrate_is_caught(self, tmp_path: Path):
        """The subtle one. `elfmem init` writes an .elfmem/.gitignore that
        deliberately does not ignore memory/ or ledger/, but a repo-root
        `.gitignore` with a blanket `.elfmem/` rule silences those negations
        — git never descends into an excluded directory to read them — and
        the undo path fails silently."""
        db, memory, config = _project(tmp_path)
        _git(tmp_path, "init")
        (tmp_path / ".gitignore").write_text(".elfmem/\n")
        (memory / "notes").mkdir(parents=True, exist_ok=True)
        (memory / "notes" / "knowledge.md").write_text("# x\n")

        checks = await cutover_preflight(db, memory, config)
        tracked = [c for c in checks if c.name == "git tracks memory/"][0]
        assert not tracked.ok
        assert "gitignored" in tracked.detail
