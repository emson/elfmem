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
    apply_agent_name_step,
    apply_cutover_step,
    build_full_plan,
    cutover_preflight,
    cutover_step_id,
    scan_agent_name,
    scan_cutover,
    undo_cutover_step,
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


def _project_with_identity(tmp_path: Path) -> Path:
    """A config shaped like a real `elfmem init` output: `identity:` present.

    `_project()`'s bare `project:\\n  name: t\\n` has no `agent_name:` OR
    `identity:` line -- `set_agent_name_in_config` needs one as an insertion
    anchor when `agent_name:` is absent, and every config `init` actually
    writes has `identity:` (it's a required ProjectConfig field, always
    rendered). Applying against the bare fixture is testing a config shape
    that doesn't occur in practice.
    """
    _, _, config = _project(tmp_path)
    config.write_text(config.read_text() + '  identity: ""\n')
    return config


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
        _marker(memory)  # git checks only run once an export exists to cut over to
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
        _marker(memory)
        _git(tmp_path, "init")
        (tmp_path / ".gitignore").write_text(".elfmem/\n")
        (memory / "notes").mkdir(parents=True, exist_ok=True)
        (memory / "notes" / "knowledge.md").write_text("# x\n")

        checks = await cutover_preflight(db, memory, config)
        tracked = [c for c in checks if c.name == "git tracks memory/"][0]
        assert not tracked.ok
        assert "gitignored" in tracked.detail

    async def test_no_git_subprocess_without_an_export(self, tmp_path: Path):
        """The bug this whole class exists to prevent: git checks running
        unconditionally would mean every `migrate status` on a fresh, never-
        exported project shells out to git for no reason — the common case,
        not a rare one, since `scan_cutover` runs whenever `scan_substrate`
        returns None."""
        db, memory, config = _project(tmp_path)
        checks = await cutover_preflight(db, memory, config)
        assert not any(c.name.startswith("git") for c in checks)


# ── Agent naming: make the identity explicit, don't just detect a bug ────────
#
# Unlike the two steps above, an unset project.agent_name is not wrong -- "elf"
# is a perfectly good default identity. This step exists because the fallback
# to "elf" is silent (context/rendering.py, elfmem_index @ c19dcc5), and this
# project has spent both migration steps above closing exactly that failure
# mode elsewhere: an unset value producing behaviour nobody chose. Applying
# writes SOME value -- "elf" if nothing else is offered -- so the identity
# becomes a fact recorded in the project's own config, not an implicit default
# a future reader has to already know about to find.


class TestScanAgentName:
    def test_offered_when_unset(self, tmp_path: Path):
        _, _, config = _project(tmp_path)
        step = scan_agent_name(config)
        assert step is not None
        assert step.kind == "agent_name"
        assert step.after == {"agent_name": "elf"}

    def test_not_offered_once_set(self, tmp_path: Path):
        _, _, config = _project(tmp_path)
        config.write_text(config.read_text() + '  agent_name: "Theo"\n')
        assert scan_agent_name(config) is None

    def test_not_offered_when_no_config(self, tmp_path: Path):
        assert scan_agent_name(tmp_path / "nowhere" / "config.yaml") is None

    def test_step_id_names_the_project(self, tmp_path: Path):
        _, _, config = _project(tmp_path)
        step = scan_agent_name(config)
        assert step.id.endswith(tmp_path.name)
        assert ".elfmem" not in step.id


class TestApplyAgentName:
    def test_writes_the_chosen_name(self, tmp_path: Path):
        config = _project_with_identity(tmp_path)
        step = scan_agent_name(config)
        result = apply_agent_name_step(step, config_path=config, name="Theo")
        assert result.status == "applied"
        assert yaml.safe_load(config.read_text())["project"]["agent_name"] == "Theo"

    def test_defaults_to_elf_when_name_is_blank(self, tmp_path: Path):
        """The exact case a non-interactive run hits: nothing typed, no
        --yes-supplied override -- must still write something, not skip."""
        config = _project_with_identity(tmp_path)
        step = scan_agent_name(config)
        result = apply_agent_name_step(step, config_path=config, name="")
        assert result.status == "applied"
        assert yaml.safe_load(config.read_text())["project"]["agent_name"] == "elf"

    def test_defaults_to_elf_when_name_is_whitespace_only(self, tmp_path: Path):
        """A prompt answered with just spaces must not write an
        effectively-empty identity back."""
        config = _project_with_identity(tmp_path)
        step = scan_agent_name(config)
        apply_agent_name_step(step, config_path=config, name="   ")
        assert yaml.safe_load(config.read_text())["project"]["agent_name"] == "elf"

    def test_dry_run_writes_nothing(self, tmp_path: Path):
        config = _project_with_identity(tmp_path)
        step = scan_agent_name(config)
        before = config.read_text()
        result = apply_agent_name_step(
            step, config_path=config, name="Theo", dry_run=True,
        )
        assert result.status == "skipped"
        assert config.read_text() == before

    def test_becomes_idempotent_after_applying(self, tmp_path: Path):
        config = _project_with_identity(tmp_path)
        step = scan_agent_name(config)
        apply_agent_name_step(step, config_path=config, name="Theo")
        assert scan_agent_name(config) is None

    def test_reapplying_an_already_set_name_is_a_noop(self, tmp_path: Path):
        config = _project_with_identity(tmp_path)
        step = scan_agent_name(config)
        apply_agent_name_step(step, config_path=config, name="Theo")
        result = apply_agent_name_step(step, config_path=config, name="Theo")
        assert result.status == "skipped"

    def test_missing_config_fails_without_raising(self, tmp_path: Path):
        """set_agent_name_in_config raises ConfigError on a missing file --
        the step must catch it and report failed, not crash migrate apply."""
        missing = tmp_path / "gone" / "config.yaml"
        step = MigrationStep(
            id="agent-name@x", kind="agent_name", summary="", file=missing,
            file_sha256="", issues=[], before={}, after={}, json_pointer="",
        )
        result = apply_agent_name_step(step, config_path=missing, name="Theo")
        assert result.status == "failed"


class TestAgentNameInThePlan:
    """Wired into the same plan the other two steps use, independently of
    both -- agent_name has nothing to do with the file substrate."""

    async def test_appears_alongside_a_pending_export(self, tmp_path: Path):
        db, memory, config = _project(tmp_path)
        kinds = {s.kind for s in (await build_full_plan(
            db_path=db, memory_dir=memory,
        )).steps}
        assert "agent_name" in kinds

    async def test_appears_alongside_a_pending_cutover(self, tmp_path: Path):
        db, memory, config = _project(tmp_path)
        _marker(memory)
        kinds = {s.kind for s in (await build_full_plan(
            db_path=db, memory_dir=memory,
        )).steps}
        assert "agent_name" in kinds
        assert "substrate_cutover" in kinds

    async def test_absent_once_a_name_is_set(self, tmp_path: Path):
        db, memory, config = _project(tmp_path)
        config.write_text(config.read_text() + '  agent_name: "Theo"\n')
        kinds = {s.kind for s in (await build_full_plan(
            db_path=db, memory_dir=memory,
        )).steps}
        assert "agent_name" not in kinds


class TestApplyAgentNameSafety:
    """Every other apply function in this module backs up before writing and
    verifies the write took (apply_cutover_step's shutil.copy2 + re-parse,
    apply_substrate_step's VACUUM INTO, config-drift's per-step .elfmem-bak).
    apply_agent_name_step must make the same guarantee -- a regex-based
    surgical edit is exactly the risk profile that pattern exists to cover,
    and the README documents "each apply writes a backup" as a blanket claim,
    not one with an unstated exception for this step kind.
    """

    def test_writes_a_backup_before_editing(self, tmp_path: Path):
        config = _project_with_identity(tmp_path)
        original = config.read_text()
        step = scan_agent_name(config)

        result = apply_agent_name_step(step, config_path=config, name="Theo")

        assert result.backup is not None
        assert result.backup.exists()
        assert result.backup.read_text() == original

    def test_no_backup_on_dry_run(self, tmp_path: Path):
        config = _project_with_identity(tmp_path)
        step = scan_agent_name(config)
        result = apply_agent_name_step(
            step, config_path=config, name="Theo", dry_run=True,
        )
        assert result.backup is None
        assert not any(config.parent.glob("*.elfmem-bak-agent-name-*"))

    def test_no_backup_when_config_is_missing(self, tmp_path: Path):
        missing = tmp_path / "gone" / "config.yaml"
        step = MigrationStep(
            id="agent-name@x", kind="agent_name", summary="", file=missing,
            file_sha256="", issues=[], before={}, after={}, json_pointer="",
        )
        result = apply_agent_name_step(step, config_path=missing, name="Theo")
        assert result.status == "failed"
        assert result.backup is None


class TestUndoCutoverSafety:
    """The forward path (apply_cutover_step) backs up before writing. Undo
    must make the same guarantee -- it is the "something's wrong, get back
    to safety" path, and that is the one place a backup matters most."""

    async def test_writes_a_backup_before_reverting(self, tmp_path: Path):
        config = _project_with_identity(tmp_path)
        _set_files_authoritative(config, value=True)
        before = config.read_text()

        result = await undo_cutover_step(
            _step(config), config_path=config,
        )

        assert result.status == "applied"
        assert result.backup is not None
        assert result.backup.exists()
        assert result.backup.read_text() == before

    async def test_no_backup_when_nothing_to_revert(self, tmp_path: Path):
        config = _project_with_identity(tmp_path)  # files_authoritative already false
        result = await undo_cutover_step(_step(config), config_path=config)
        assert result.status == "skipped"
        assert result.backup is None
