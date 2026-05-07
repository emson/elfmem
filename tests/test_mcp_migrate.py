"""Tests for migrate.py — Claude MCP config migration scanner."""

from __future__ import annotations

import json
from pathlib import Path

from elfmem.migrate import (
    DEPRECATED_ENV_VARS,
    ApplyResult,
    MigrationFinding,
    MigrationPlan,
    MigrationStep,
    apply_plan,
    apply_step,
    build_plan,
    format_finding,
    is_elfmem_entry,
    scan,
    scan_file,
)


def _write_config(path: Path, servers: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")


class TestIsElfmemEntry:
    def test_command_match(self):
        assert is_elfmem_entry({"command": "elfmem", "args": ["serve"]})

    def test_module_match(self):
        assert is_elfmem_entry({
            "command": "uv",
            "args": ["run", "python", "-m", "elfmem.mcp"],
        })

    def test_env_match(self):
        assert is_elfmem_entry({
            "command": "uv",
            "env": {"ELFMEM_DB_PATH": "/x"},
        })

    def test_unrelated_server(self):
        assert not is_elfmem_entry({"command": "node", "args": ["other.js"]})


class TestScanFile:
    def test_missing_file(self, tmp_path: Path):
        assert scan_file(tmp_path / "missing.json") == []

    def test_malformed_json(self, tmp_path: Path):
        path = tmp_path / "broken.json"
        path.write_text("not json", encoding="utf-8")
        assert scan_file(path) == []

    def test_no_elfmem_servers(self, tmp_path: Path):
        path = tmp_path / "claude.json"
        _write_config(path, {"other": {"command": "node"}})
        assert scan_file(path) == []

    def test_clean_entry_no_findings(self, tmp_path: Path):
        path = tmp_path / "claude.json"
        _write_config(path, {
            "elfmem": {
                "command": "elfmem",
                "args": ["serve", "--config", "/x/.elfmem/config.yaml"],
            }
        })
        assert scan_file(path) == []

    def test_deprecated_env_only(self, tmp_path: Path):
        path = tmp_path / "claude.json"
        _write_config(path, {
            "elfmem": {
                "command": "elfmem",
                "args": ["serve"],
                "env": {"ELFMEM_DB_PATH": "/x/.elfmem/db.db"},
            }
        })
        findings = scan_file(path)
        assert len(findings) == 1
        f = findings[0]
        assert f.server_name == "elfmem"
        assert any("ELFMEM_DB_PATH" in i for i in f.issues)
        assert f.suggested["env"] == {"ELFMEM_DB": "/x/.elfmem/db.db"}

    def test_legacy_module_invocation(self, tmp_path: Path):
        path = tmp_path / "claude.json"
        _write_config(path, {
            "elfmem": {
                "command": "uv",
                "args": ["run", "python", "-m", "elfmem.mcp"],
                "env": {"ELFMEM_CONFIG_PATH": "~/.elfmem/config.yaml"},
            }
        })
        findings = scan_file(path)
        assert len(findings) == 1
        f = findings[0]
        assert any("ELFMEM_CONFIG_PATH" in i for i in f.issues)
        assert any("elfmem serve" in i for i in f.issues)
        assert f.suggested["command"] == "elfmem"
        assert f.suggested["args"] == ["serve", "--config", "~/.elfmem/config.yaml"]
        assert "env" not in f.suggested

    def test_legacy_invocation_without_config_left_alone(self, tmp_path: Path):
        path = tmp_path / "claude.json"
        _write_config(path, {
            "elfmem": {
                "command": "uv",
                "args": ["run", "python", "-m", "elfmem.mcp"],
                "env": {"ELFMEM_DB_PATH": "/x"},
            }
        })
        findings = scan_file(path)
        assert len(findings) == 1
        f = findings[0]
        assert any("ELFMEM_DB_PATH" in i for i in f.issues)
        assert f.suggested["command"] == "uv"

    def test_preserves_alwaysallow(self, tmp_path: Path):
        path = tmp_path / "claude.json"
        _write_config(path, {
            "elfmem": {
                "command": "uv",
                "args": ["run", "python", "-m", "elfmem.mcp"],
                "env": {"ELFMEM_CONFIG_PATH": "/x.yaml"},
                "alwaysAllow": ["elfmem_recall", "elfmem_remember"],
            }
        })
        findings = scan_file(path)
        assert findings[0].suggested["alwaysAllow"] == ["elfmem_recall", "elfmem_remember"]

    def test_split_db_and_config(self, tmp_path: Path):
        path = tmp_path / "claude.json"
        _write_config(path, {
            "elfmem": {
                "command": "uv",
                "args": ["run", "python", "-m", "elfmem.mcp"],
                "env": {
                    "ELFMEM_DB_PATH": "/d.db",
                    "ELFMEM_CONFIG_PATH": "/c.yaml",
                },
            }
        })
        findings = scan_file(path)
        f = findings[0]
        assert f.suggested["args"] == ["serve", "--config", "/c.yaml"]
        assert f.suggested["env"] == {"ELFMEM_DB": "/d.db"}


class TestScan:
    def test_aggregates_multiple_files(self, tmp_path: Path):
        a = tmp_path / "a.json"
        b = tmp_path / "b.json"
        _write_config(a, {"elfmem": {
            "command": "elfmem", "args": ["serve"],
            "env": {"ELFMEM_DB_PATH": "/x"},
        }})
        _write_config(b, {"other": {
            "command": "elfmem", "args": ["serve"],
            "env": {"ELFMEM_CONFIG_PATH": "/y"},
        }})
        results = scan(paths=(a, b))
        assert len(results) == 2

    def test_dedups_same_path(self, tmp_path: Path):
        a = tmp_path / "a.json"
        _write_config(a, {"elfmem": {
            "command": "elfmem", "args": ["serve"],
            "env": {"ELFMEM_DB_PATH": "/x"},
        }})
        results = scan(paths=(a, a))
        assert len(results) == 1


class TestFormatFinding:
    def test_includes_file_and_diff(self):
        finding = MigrationFinding(
            file=Path("/tmp/x.json"),
            server_name="elfmem",
            issues=["renamed env var ELFMEM_DB_PATH → ELFMEM_DB"],
            current={"command": "elfmem", "env": {"ELFMEM_DB_PATH": "/x"}},
            suggested={"command": "elfmem", "env": {"ELFMEM_DB": "/x"}},
        )
        out = format_finding(finding)
        assert "/tmp/x.json" in out
        assert "elfmem" in out
        assert "ELFMEM_DB_PATH" in out
        assert "ELFMEM_DB" in out


class TestDeprecatedMap:
    def test_known_aliases(self):
        assert DEPRECATED_ENV_VARS["ELFMEM_CONFIG_PATH"] == "ELFMEM_CONFIG"
        assert DEPRECATED_ENV_VARS["ELFMEM_DB_PATH"] == "ELFMEM_DB"


# ── Plan + Apply tests ───────────────────────────────────────────────────────


def _legacy_entry() -> dict:
    return {
        "command": "uv",
        "args": ["run", "python", "-m", "elfmem.mcp"],
        "env": {"ELFMEM_CONFIG_PATH": "/some/.elfmem/config.yaml"},
        "alwaysAllow": ["elfmem_recall"],
    }


class TestBuildPlan:
    def test_empty_when_no_files(self, tmp_path: Path):
        plan = build_plan(paths=(tmp_path / "missing.json",))
        assert plan.pending_count == 0
        assert plan.steps == []
        assert "No migrations" in plan.summary

    def test_step_per_finding(self, tmp_path: Path):
        path = tmp_path / "claude.json"
        _write_config(path, {"elfmem": _legacy_entry()})
        plan = build_plan(paths=(path,))
        assert plan.pending_count == 1
        step = plan.steps[0]
        assert step.kind == "claude_mcp_config"
        assert step.file == path
        assert len(step.file_sha256) == 64  # sha256 hex
        assert "elfmem" in step.id
        assert step.json_pointer == "/mcpServers/elfmem"
        assert step.reversible is True

    def test_to_dict_includes_apply_command(self, tmp_path: Path):
        path = tmp_path / "claude.json"
        _write_config(path, {"elfmem": _legacy_entry()})
        plan = build_plan(paths=(path,))
        d = plan.to_dict()
        assert d["pending_count"] == 1
        assert d["steps"][0]["apply_command"].startswith("elfmem migrate apply --id ")
        assert "elfmem_version" in d
        assert "next_action" in d


class TestApplyStep:
    def test_dry_run_does_not_write(self, tmp_path: Path):
        path = tmp_path / "claude.json"
        _write_config(path, {"elfmem": _legacy_entry()})
        before = path.read_bytes()
        plan = build_plan(paths=(path,))
        result = apply_step(plan.steps[0], dry_run=True)
        assert result.status == "applied"
        assert "[dry-run]" in result.detail
        assert path.read_bytes() == before
        assert result.backup is None

    def test_apply_creates_backup_and_updates(self, tmp_path: Path):
        path = tmp_path / "claude.json"
        _write_config(path, {"elfmem": _legacy_entry()})
        plan = build_plan(paths=(path,))
        result = apply_step(plan.steps[0])
        assert result.status == "applied"
        # Backup exists and matches original.
        assert result.backup is not None
        assert result.backup.exists()
        backup_data = json.loads(result.backup.read_text())
        assert backup_data["mcpServers"]["elfmem"]["env"] == {
            "ELFMEM_CONFIG_PATH": "/some/.elfmem/config.yaml"
        }
        # File updated to canonical form.
        new_data = json.loads(path.read_text())
        elfmem = new_data["mcpServers"]["elfmem"]
        assert elfmem["command"] == "elfmem"
        assert elfmem["args"] == ["serve", "--config", "/some/.elfmem/config.yaml"]
        assert "env" not in elfmem

    def test_idempotent_skip_after_apply(self, tmp_path: Path):
        path = tmp_path / "claude.json"
        _write_config(path, {"elfmem": _legacy_entry()})
        plan = build_plan(paths=(path,))
        first = apply_step(plan.steps[0])
        assert first.status == "applied"
        # Re-applying the same step against the now-modernised file: scan finds
        # nothing, so build_plan returns no steps. Calling apply_step on a stale
        # step object hits the hash gate.
        replan = build_plan(paths=(path,))
        assert replan.pending_count == 0
        retry = apply_step(plan.steps[0])
        assert retry.status == "stale"

    def test_stale_when_file_modified(self, tmp_path: Path):
        path = tmp_path / "claude.json"
        _write_config(path, {"elfmem": _legacy_entry()})
        plan = build_plan(paths=(path,))
        # User edits the file between plan and apply.
        _write_config(path, {"elfmem": _legacy_entry(), "other": {"command": "x"}})
        result = apply_step(plan.steps[0])
        assert result.status == "stale"
        assert "Re-run" in result.detail

    def test_failed_when_file_deleted(self, tmp_path: Path):
        path = tmp_path / "claude.json"
        _write_config(path, {"elfmem": _legacy_entry()})
        plan = build_plan(paths=(path,))
        path.unlink()
        result = apply_step(plan.steps[0])
        assert result.status == "failed"
        assert "no longer exists" in result.detail

    def test_skipped_when_server_removed(self, tmp_path: Path):
        path = tmp_path / "claude.json"
        _write_config(path, {"elfmem": _legacy_entry()})
        plan = build_plan(paths=(path,))
        # Build the plan, but THEN remove the server entirely. Because removing
        # the server changes file content, we need to re-stamp the plan's hash
        # to isolate this case from the stale-hash case.
        _write_config(path, {"other": {"command": "x"}})
        from elfmem.migrate import _sha256
        modified_hash = _sha256(path)
        step = plan.steps[0]
        # Replace the step's file_sha256 with the new hash.
        modified_step = MigrationStep(
            id=step.id, kind=step.kind, summary=step.summary, file=step.file,
            file_sha256=modified_hash, issues=step.issues,
            before=step.before, after=step.after,
            json_pointer=step.json_pointer, reversible=step.reversible,
            post_apply_step=step.post_apply_step,
        )
        result = apply_step(modified_step)
        assert result.status == "skipped"
        assert "no longer present" in result.detail

    def test_skipped_when_already_canonical(self, tmp_path: Path):
        path = tmp_path / "claude.json"
        # A canonical file produces no findings, so build_plan returns nothing.
        # This tests apply_step's defence: if scan missed something but the
        # entry equals the proposed shape, treat it as already-applied.
        canonical = {
            "command": "elfmem",
            "args": ["serve", "--config", "/x/.elfmem/config.yaml"],
        }
        _write_config(path, {"elfmem": canonical})
        # Hand-craft a step whose 'after' equals the current state.
        from elfmem.migrate import _sha256, _step_id
        step = MigrationStep(
            id=_step_id(path, "elfmem"),
            kind="claude_mcp_config",
            summary="hand-crafted",
            file=path,
            file_sha256=_sha256(path),
            issues=[],
            before=canonical,
            after=canonical,
            json_pointer="/mcpServers/elfmem",
        )
        result = apply_step(step)
        assert result.status == "skipped"
        assert "already matches" in result.detail


class TestApplyPlan:
    def test_apply_all(self, tmp_path: Path):
        a = tmp_path / "a.json"
        b = tmp_path / "b.json"
        _write_config(a, {"elfmem": _legacy_entry()})
        _write_config(b, {"elfmem": _legacy_entry()})
        plan = build_plan(paths=(a, b))
        result = apply_plan(plan)
        assert result.all_ok
        assert len(result.applied) == 2
        # Each file has its own backup with a unique step id.
        backups = [r.backup for r in result.results if r.backup]
        assert len(backups) == 2
        assert backups[0] != backups[1]

    def test_apply_only_one(self, tmp_path: Path):
        a = tmp_path / "a.json"
        b = tmp_path / "b.json"
        _write_config(a, {"elfmem": _legacy_entry()})
        _write_config(b, {"elfmem": _legacy_entry()})
        plan = build_plan(paths=(a, b))
        target = plan.steps[0].id
        result = apply_plan(plan, only=(target,))
        assert result.all_ok
        assert result.applied == [target]
        # The non-targeted file is untouched.
        assert "ELFMEM_CONFIG_PATH" in b.read_text()

    def test_apply_unknown_id_records_failure(self, tmp_path: Path):
        a = tmp_path / "a.json"
        _write_config(a, {"elfmem": _legacy_entry()})
        plan = build_plan(paths=(a,))
        result = apply_plan(plan, only=("does-not-exist",))
        assert not result.all_ok
        assert "does-not-exist" in result.failed

    def test_apply_continues_past_failure(self, tmp_path: Path):
        a = tmp_path / "a.json"
        b = tmp_path / "b.json"
        _write_config(a, {"elfmem": _legacy_entry()})
        _write_config(b, {"elfmem": _legacy_entry()})
        plan = build_plan(paths=(a, b))
        # Corrupt one source file before applying — that step will fail (stale),
        # but the other should still succeed.
        a.write_text("{}", encoding="utf-8")
        result = apply_plan(plan)
        assert not result.all_ok
        assert len(result.applied) == 1
        assert len(result.failed) == 1


class TestMultiStepPerFile:
    """Two MCP servers in one Claude config — the real-world case for users
    with multiple elfmem instances. Both must apply in one plan."""

    def test_both_servers_in_one_file_apply_together(self, tmp_path: Path):
        path = tmp_path / "claude.json"
        _write_config(path, {
            "elfmem": _legacy_entry(),
            "movemyth_elfmem": _legacy_entry(),
        })
        plan = build_plan(paths=(path,))
        assert plan.pending_count == 2
        result = apply_plan(plan)
        assert result.all_ok
        assert len(result.applied) == 2
        # Both servers updated in the file.
        data = json.loads(path.read_text())
        assert data["mcpServers"]["elfmem"]["command"] == "elfmem"
        assert data["mcpServers"]["movemyth_elfmem"]["command"] == "elfmem"

    def test_one_backup_per_file_group(self, tmp_path: Path):
        path = tmp_path / "claude.json"
        _write_config(path, {
            "elfmem": _legacy_entry(),
            "movemyth_elfmem": _legacy_entry(),
        })
        plan = build_plan(paths=(path,))
        result = apply_plan(plan)
        # Both per-step results reference the same backup — one rollback point
        # per file write, even though there are two logical steps.
        backups = {r.backup for r in result.results}
        assert len(backups) == 1


class TestSymlinkHandling:
    def test_symlink_preserved_real_target_updated(self, tmp_path: Path):
        import os
        real = tmp_path / "real.json"
        link = tmp_path / "claude.json"
        _write_config(real, {"elfmem": _legacy_entry()})
        os.symlink(real, link)
        plan = build_plan(paths=(link,))
        result = apply_plan(plan)
        assert result.all_ok
        # Symlink is still a symlink, pointing at the same target.
        assert link.is_symlink()
        assert os.readlink(link) == str(real)
        # Real target has the updated content.
        data = json.loads(real.read_text())
        assert data["mcpServers"]["elfmem"]["command"] == "elfmem"
        # Backup lives next to the resolved target, not the link.
        backup = result.results[0].backup
        assert backup is not None
        assert backup.parent == real.parent

    def test_symlink_dry_run_no_change(self, tmp_path: Path):
        import os
        real = tmp_path / "real.json"
        link = tmp_path / "claude.json"
        _write_config(real, {"elfmem": _legacy_entry()})
        os.symlink(real, link)
        before = real.read_bytes()
        plan = build_plan(paths=(link,))
        result = apply_plan(plan, dry_run=True)
        assert result.all_ok
        assert link.is_symlink()
        assert real.read_bytes() == before


class TestParseWarnings:
    """Files that look like elfmem configs but won't parse must not be silent."""

    def test_json5_with_comments_surfaces_warning(self, tmp_path: Path):
        path = tmp_path / "claude.json"
        path.write_text(
            '{\n  // a comment\n  "mcpServers": {"elfmem": {"command": "x"}},\n}\n',
            encoding="utf-8",
        )
        plan = build_plan(paths=(path,))
        assert plan.pending_count == 0
        assert len(plan.warnings) == 1
        assert plan.warnings[0].file == path
        assert "JSONDecodeError" in plan.warnings[0].error

    def test_unrelated_unparseable_file_silent(self, tmp_path: Path):
        # A non-JSON file with no elfmem markers shouldn't produce noise.
        path = tmp_path / "claude.json"
        path.write_text("this is not json at all", encoding="utf-8")
        plan = build_plan(paths=(path,))
        assert plan.pending_count == 0
        assert plan.warnings == []

    def test_warning_in_to_dict(self, tmp_path: Path):
        path = tmp_path / "claude.json"
        path.write_text(
            '{ "mcpServers": {"elfmem": {} } ,}',  # trailing comma
            encoding="utf-8",
        )
        plan = build_plan(paths=(path,))
        d = plan.to_dict()
        assert d["warnings"]
        assert d["warnings"][0]["file"] == str(path)
        assert "error" in d["warnings"][0]

    def test_summary_mentions_warnings(self, tmp_path: Path):
        path = tmp_path / "claude.json"
        path.write_text(
            '{ "mcpServers": {"elfmem": {} } ,}',
            encoding="utf-8",
        )
        plan = build_plan(paths=(path,))
        assert "unparseable" in plan.summary


class TestPlanIntegrityGuard:
    def test_mismatched_hashes_within_group_fail(self, tmp_path: Path):
        # Construct two steps targeting the same file but with different
        # recorded hashes (artificial — should never happen in practice, but
        # we want a clear failure mode if it ever does).
        from elfmem.migrate import _sha256, _step_id
        path = tmp_path / "claude.json"
        _write_config(path, {"elfmem": _legacy_entry(), "other_elfmem": _legacy_entry()})
        h = _sha256(path)
        step1 = MigrationStep(
            id=_step_id(path, "elfmem"),
            kind="claude_mcp_config", summary="x", file=path,
            file_sha256=h, issues=[], before={}, after={},
            json_pointer="/mcpServers/elfmem",
        )
        step2 = MigrationStep(
            id=_step_id(path, "other"),
            kind="claude_mcp_config", summary="x", file=path,
            file_sha256="0" * 64, issues=[], before={}, after={},
            json_pointer="/mcpServers/other_elfmem",
        )
        plan = MigrationPlan(steps=[step1, step2])
        result = apply_plan(plan)
        assert not result.all_ok
        # All steps in the corrupted group fail with a clear message.
        for r in result.results:
            assert "plan hash mismatch" in r.detail


class TestApplyResultSurface:
    def test_to_dict_shape(self):
        from elfmem.migrate import StepApplyResult
        result = ApplyResult(results=[
            StepApplyResult("s1", "applied", "ok", backup=Path("/tmp/b")),
            StepApplyResult("s2", "skipped", "already done"),
            StepApplyResult("s3", "failed", "boom"),
        ])
        d = result.to_dict()
        assert d["applied"] == ["s1"]
        assert d["skipped"] == ["s2"]
        assert d["failed"] == ["s3"]
        assert not d["all_ok"]
        assert d["results"][0]["backup"] == "/tmp/b"
        assert d["results"][2]["backup"] is None
