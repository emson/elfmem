"""Migration scanner and applier for elfmem config drift.

Provides two layers:

1. **Scan** — pure, read-only. Detect stale entries in Claude MCP configs.
   Used by ``elfmem doctor --migrate-mcp`` and ``elfmem migrate status``.

2. **Plan + Apply** — structured, agent-friendly. Build a typed plan, hash
   the source files, and apply changes atomically with backups. Used by
   ``elfmem migrate plan`` and ``elfmem migrate apply``.

Design properties:

- **Hash gate**: every step records the source file's SHA256 at plan time;
  apply refuses if the file changed in between (catches "stale plan").
- **Atomic write**: write to ``<file>.tmp`` and rename; the rename is the
  commit point.
- **Backup before write**: every apply writes a ``<file>.elfmem-bak-<ts>``
  file before touching the original. Backups are not auto-deleted.
- **Idempotent**: re-running ``apply`` after success is a no-op — ``scan()``
  returns nothing for files that already match the canonical pattern.
- **Per-step**: each step targets exactly one file and one server entry, so
  an agent can apply one at a time and recover from per-step failures.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Env var aliases that have been renamed. Map deprecated → canonical.
DEPRECATED_ENV_VARS: dict[str, str] = {
    "ELFMEM_CONFIG_PATH": "ELFMEM_CONFIG",
    "ELFMEM_DB_PATH": "ELFMEM_DB",
}

# Default Claude config locations to scan, in priority order.
DEFAULT_SCAN_PATHS: tuple[Path, ...] = (
    Path.home() / ".claude" / "claude_code_config.json",
    Path.cwd() / ".claude.json",
)


@dataclass(frozen=True)
class MigrationFinding:
    """One actionable finding for an elfmem MCP entry that needs updating."""

    file: Path
    server_name: str
    issues: list[str] = field(default_factory=list)
    current: dict[str, Any] = field(default_factory=dict)
    suggested: dict[str, Any] = field(default_factory=dict)

    @property
    def needs_migration(self) -> bool:
        return bool(self.issues)


def is_elfmem_entry(entry: dict[str, Any]) -> bool:
    """Return True if a Claude MCP server entry is an elfmem instance.

    Detection rule: any of args, command, or env contains "elfmem".
    """
    blob = json.dumps(entry).lower()
    return "elfmem" in blob


def _suggest_entry(entry: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Return (suggested_entry, issues) — what the entry should look like.

    Conservative: keeps user customisations (alwaysAllow, command shape) and
    only rewrites the parts that are demonstrably stale.
    """
    issues: list[str] = []
    suggested = json.loads(json.dumps(entry))  # deep copy

    env = suggested.get("env") or {}

    # 1. Rename deprecated env vars.
    for old, new in DEPRECATED_ENV_VARS.items():
        if old in env:
            value = env.pop(old)
            if new not in env:
                env[new] = value
            elif env[new] != value:
                issues.append(
                    f"both {old}={value!r} and {new}={env[new]!r} are set; "
                    "remove one (canonical is preferred)"
                )
            else:
                # Same value under both names — drop deprecated, keep canonical.
                pass
            issues.append(f"renamed env var {old} → {new}")

    if env:
        suggested["env"] = env
    elif "env" in suggested:
        suggested.pop("env")

    # 2. Suggest 'elfmem serve --config' over 'python -m elfmem.mcp'.
    args = suggested.get("args", [])
    has_module_invocation = any(
        a == "-m" or a == "elfmem.mcp" for a in args
    ) or any("elfmem.mcp" in str(a) for a in args)
    if has_module_invocation:
        cfg = env.get("ELFMEM_CONFIG") or env.get(
            list(DEPRECATED_ENV_VARS.keys())[0]
        )
        # Only suggest the rewrite when we know which config to point at —
        # otherwise the user has to do this by hand.
        if cfg:
            suggested["command"] = "elfmem"
            suggested["args"] = ["serve", "--config", cfg]
            # Config now lives on the command line, drop from env.
            new_env = {k: v for k, v in env.items() if k != "ELFMEM_CONFIG"}
            if new_env:
                suggested["env"] = new_env
            else:
                suggested.pop("env", None)
            issues.append(
                "launch pattern: 'python -m elfmem.mcp' → 'elfmem serve --config <path>'"
            )

    return suggested, issues


@dataclass(frozen=True)
class ParseWarning:
    """A scanned file looked like a Claude config but couldn't be parsed.

    Surfaced separately from findings so users with comments / trailing commas
    in their Claude configs aren't silently invisible to migration tooling.
    """

    file: Path
    error: str

    def to_dict(self) -> dict[str, Any]:
        return {"file": str(self.file), "error": self.error}


def scan_file(path: Path) -> list[MigrationFinding]:
    """Read one Claude config file and return findings for each elfmem entry.

    Returns [] if the file does not exist, is unparseable, or contains no
    elfmem entries that need updating. Use ``scan_file_with_warnings`` to
    distinguish "no findings" from "couldn't parse".
    """
    findings, _ = scan_file_with_warnings(path)
    return findings


def scan_file_with_warnings(
    path: Path,
) -> tuple[list[MigrationFinding], ParseWarning | None]:
    """Like ``scan_file``, but also returns a ParseWarning if parsing failed.

    The warning carries the parser's error message so users can locate the
    offending line. Files that simply don't exist return (None warning).
    """
    if not path.exists():
        return [], None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        # Only warn for files that *could* be elfmem-relevant. A random
        # non-JSON file in the scan list (unlikely, but possible) shouldn't
        # produce noise.
        text = path.read_text(encoding="utf-8", errors="replace")
        if "elfmem" not in text.lower():
            return [], None
        return [], ParseWarning(
            file=path,
            error=f"{e.__class__.__name__}: {e}. "
                  "If your config uses comments or trailing commas, edit by "
                  "hand to plain JSON before running 'elfmem migrate apply'.",
        )
    except OSError as e:
        return [], ParseWarning(file=path, error=f"could not read: {e}")

    servers = data.get("mcpServers") or {}
    findings: list[MigrationFinding] = []
    for name, entry in servers.items():
        if not isinstance(entry, dict) or not is_elfmem_entry(entry):
            continue
        suggested, issues = _suggest_entry(entry)
        if issues:
            findings.append(
                MigrationFinding(
                    file=path,
                    server_name=name,
                    issues=issues,
                    current=entry,
                    suggested=suggested,
                )
            )
    return findings, None


def scan(paths: tuple[Path, ...] = DEFAULT_SCAN_PATHS) -> list[MigrationFinding]:
    """Scan multiple Claude config locations and aggregate findings."""
    out, _ = scan_with_warnings(paths)
    return out


def scan_with_warnings(
    paths: tuple[Path, ...] = DEFAULT_SCAN_PATHS,
) -> tuple[list[MigrationFinding], list[ParseWarning]]:
    """Scan with parse-warning aggregation. Used by 'migrate status' so users
    with hand-edited (JSON5-ish) configs see a clear diagnostic instead of
    silent emptiness."""
    findings: list[MigrationFinding] = []
    warnings: list[ParseWarning] = []
    seen: set[Path] = set()
    for p in paths:
        resolved = p.expanduser()
        if resolved in seen:
            continue
        seen.add(resolved)
        f, w = scan_file_with_warnings(resolved)
        findings.extend(f)
        if w is not None:
            warnings.append(w)
    return findings, warnings


# ── Plan + Apply ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MigrationStep:
    """One discrete, reversible migration unit.

    A step targets exactly one file and one server entry inside it. Agents
    can apply steps individually via ``elfmem migrate apply --id <step.id>``.
    """

    id: str
    kind: str
    summary: str
    file: Path
    file_sha256: str
    issues: list[str]
    before: dict[str, Any]
    after: dict[str, Any]
    json_pointer: str
    reversible: bool = True
    post_apply_step: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "summary": self.summary,
            "file": str(self.file),
            "file_sha256": self.file_sha256,
            "issues": list(self.issues),
            "before": self.before,
            "after": self.after,
            "json_pointer": self.json_pointer,
            "reversible": self.reversible,
            "post_apply_step": self.post_apply_step,
            "apply_command": f"elfmem migrate apply --id {self.id} --yes",
        }


@dataclass(frozen=True)
class MigrationPlan:
    """Aggregate of all pending migrations across the user's environment.

    ``warnings`` carries parse failures for files that looked like elfmem
    configs but couldn't be parsed (e.g. JSON5 with comments). The user must
    hand-fix those before migration tooling can act on them.
    """

    steps: list[MigrationStep] = field(default_factory=list)
    warnings: list[ParseWarning] = field(default_factory=list)

    @property
    def pending_count(self) -> int:
        return len(self.steps)

    @property
    def summary(self) -> str:
        bits = []
        if self.steps:
            bits.append(f"{self.pending_count} migration(s) pending")
        if self.warnings:
            bits.append(f"{len(self.warnings)} unparseable file(s)")
        return ", ".join(bits) if bits else "No migrations pending."

    def to_dict(self) -> dict[str, Any]:
        from importlib.metadata import version as _pkg_version
        try:
            elfmem_version = _pkg_version("elfmem")
        except Exception:
            elfmem_version = "unknown"
        return {
            "elfmem_version": elfmem_version,
            "pending_count": self.pending_count,
            "steps": [s.to_dict() for s in self.steps],
            "warnings": [w.to_dict() for w in self.warnings],
            "next_action": (
                "elfmem migrate apply --yes  # apply all"
                if self.steps
                else "no action needed"
            ),
        }


@dataclass(frozen=True)
class StepApplyResult:
    """Outcome of applying a single migration step."""

    step_id: str
    status: str  # "applied" | "skipped" | "failed" | "stale"
    detail: str
    backup: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "status": self.status,
            "detail": self.detail,
            "backup": str(self.backup) if self.backup else None,
        }


@dataclass(frozen=True)
class ApplyResult:
    """Aggregate result of applying one or more steps."""

    results: list[StepApplyResult] = field(default_factory=list)

    @property
    def applied(self) -> list[str]:
        return [r.step_id for r in self.results if r.status == "applied"]

    @property
    def failed(self) -> list[str]:
        return [r.step_id for r in self.results if r.status in ("failed", "stale")]

    @property
    def skipped(self) -> list[str]:
        return [r.step_id for r in self.results if r.status == "skipped"]

    @property
    def all_ok(self) -> bool:
        return not self.failed

    def to_dict(self) -> dict[str, Any]:
        return {
            "applied": self.applied,
            "skipped": self.skipped,
            "failed": self.failed,
            "results": [r.to_dict() for r in self.results],
            "all_ok": self.all_ok,
        }


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _step_id(file: Path, server_name: str) -> str:
    """Stable, human-readable identifier for one migration step.

    Format: ``mcp-{server_name}@{file_basename_stem}-{short_hash}``. The hash
    component disambiguates two files with the same basename in different
    locations (e.g. multiple project ``.claude.json`` files).
    """
    h = hashlib.sha256(str(file).encode()).hexdigest()[:8]
    stem = file.stem.replace(".", "-")
    return f"mcp-{server_name}@{stem}-{h}"


def _finding_to_step(finding: MigrationFinding) -> MigrationStep:
    issues_text = "; ".join(finding.issues)
    return MigrationStep(
        id=_step_id(finding.file, finding.server_name),
        kind="claude_mcp_config",
        summary=f"Update '{finding.server_name}' MCP entry: {issues_text}",
        file=finding.file,
        file_sha256=_sha256(finding.file),
        issues=list(finding.issues),
        before=finding.current,
        after=finding.suggested,
        json_pointer=f"/mcpServers/{finding.server_name}",
        reversible=True,
        post_apply_step="Restart Claude Code so MCP servers reload.",
    )


def build_plan(paths: tuple[Path, ...] = DEFAULT_SCAN_PATHS) -> MigrationPlan:
    """Build a structured migration plan from the current environment.

    Pure-read: never modifies any file. Includes a SHA256 of each source
    file so apply can refuse stale plans. Parse warnings (for files that
    look like elfmem configs but aren't valid JSON) are attached to the
    plan so callers can surface them to the user.
    """
    findings, warnings = scan_with_warnings(paths)
    steps = [_finding_to_step(f) for f in findings]
    return MigrationPlan(steps=steps, warnings=warnings)


def _resolve_target(path: Path) -> Path:
    """Resolve symlinks so writes commit to the real file, not the link.

    A naive ``os.replace(tmp, path)`` against a symlink replaces the link
    itself with a regular file, orphaning the original target. Resolving up
    front means we backup and rewrite the real file in place; the symlink
    stays a symlink, and dotfile managers (stow, chezmoi, yadm) keep working.
    """
    return path.resolve() if path.is_symlink() else path


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """Write JSON atomically via tmp file + rename on the same filesystem.

    The rename is the commit point — readers either see the old file or the
    new file, never a partial write. If *path* is a symlink, the real target
    is rewritten in place so the link survives.
    """
    target = _resolve_target(path)
    tmp = target.with_suffix(target.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, target)
    except Exception:
        if tmp.exists():
            with contextlib.suppress(OSError):
                tmp.unlink()
        raise


def _backup_path(file: Path, step_id: str) -> Path:
    """Return the backup filename for *file* under *step_id*.

    Format: ``<file>.elfmem-bak-<step_id>-<unix_ns_timestamp>``. Nanosecond
    resolution avoids collisions when multiple steps target the same file
    in rapid succession. The backup lives next to the real file (resolving
    symlinks first) so dotfile-manager source trees are not polluted.
    """
    target = _resolve_target(file)
    ts = time.time_ns()
    return target.with_name(f"{target.name}.elfmem-bak-{step_id}-{ts}")


def _server_name_from_pointer(pointer: str) -> str:
    return pointer.rsplit("/", 1)[-1]


def _check_step_preconditions(
    step: MigrationStep,
    data: dict[str, Any],
) -> tuple[str, str] | None:
    """Return (status, detail) if *step* should not apply against *data*; else None.

    Pulled out so file-grouped apply can reuse the same idempotency and
    server-presence checks per step before mutating the in-memory state.
    """
    servers = data.get("mcpServers") or {}
    server_name = _server_name_from_pointer(step.json_pointer)
    if server_name not in servers:
        return "skipped", f"server '{server_name}' is no longer present in {step.file}"
    if servers[server_name] == step.after:
        return "skipped", f"'{server_name}' already matches the canonical pattern"
    return None


def apply_step(step: MigrationStep, *, dry_run: bool = False) -> StepApplyResult:
    """Apply a single migration step, returning a structured result.

    Single-step entry point. For multi-step plans, prefer ``apply_plan``,
    which groups steps by file and applies each file's mutations in one
    backup-and-write cycle. apply_step is correct for one-step-per-file
    cases and the per-step interactive flow.

    Steps are idempotent: re-application against an already-canonical file
    returns ``status="skipped"``. If the file's content has drifted from
    the plan's recorded hash, returns ``status="stale"`` — the caller
    should re-run ``build_plan`` and try again.
    """
    if not step.file.exists():
        return StepApplyResult(step.id, "failed", f"file no longer exists: {step.file}")

    current_hash = _sha256(step.file)
    if current_hash != step.file_sha256:
        return StepApplyResult(
            step.id, "stale",
            f"file changed since plan computed (hash {current_hash[:12]}… "
            f"vs expected {step.file_sha256[:12]}…). Re-run 'elfmem migrate plan'.",
        )

    try:
        data = json.loads(step.file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return StepApplyResult(step.id, "failed", f"file is not valid JSON: {e}")

    pre = _check_step_preconditions(step, data)
    if pre is not None:
        return StepApplyResult(step.id, pre[0], pre[1])

    server_name = _server_name_from_pointer(step.json_pointer)
    if dry_run:
        return StepApplyResult(
            step.id, "applied",
            f"[dry-run] would update '{server_name}' in {step.file}",
        )

    try:
        backup = _backup_path(step.file, step.id)
        backup.write_bytes(_resolve_target(step.file).read_bytes())
        servers = data["mcpServers"]
        servers[server_name] = step.after
        data["mcpServers"] = servers
        _atomic_write_json(step.file, data)
    except OSError as e:
        return StepApplyResult(
            step.id, "failed",
            f"OS error writing {step.file}: {e}. Check file permissions and "
            "available disk space.",
        )

    return StepApplyResult(
        step.id, "applied",
        f"updated '{server_name}' in {step.file}",
        backup=backup,
    )


def _apply_file_group(
    file: Path,
    steps: list[MigrationStep],
    *,
    dry_run: bool,
) -> list[StepApplyResult]:
    """Apply every step targeting *file* in one backup-and-write cycle.

    Why grouping matters: each successful write changes the file's hash, so
    sequential apply_step calls against multiple steps in the same file see
    only the first succeed and the rest return "stale". Grouping reads once,
    verifies the hash once, applies all mutations in memory, and writes
    once — preserving per-step result granularity while staying correct for
    files with multiple targeted server entries (the common case for users
    with several elfmem MCP instances in one Claude config).
    """
    results: list[StepApplyResult] = []

    if not file.exists():
        for step in steps:
            results.append(StepApplyResult(
                step.id, "failed", f"file no longer exists: {file}",
            ))
        return results

    expected_hashes = {s.file_sha256 for s in steps}
    if len(expected_hashes) > 1:
        # Plan integrity guard: if two steps in one plan disagree on the source
        # hash, the plan was corrupted. Fail every step in the group.
        for step in steps:
            results.append(StepApplyResult(
                step.id, "failed",
                f"plan hash mismatch across steps targeting {file}; "
                "re-run 'elfmem migrate plan'.",
            ))
        return results

    current_hash = _sha256(file)
    if current_hash != next(iter(expected_hashes)):
        for step in steps:
            results.append(StepApplyResult(
                step.id, "stale",
                f"file changed since plan computed (hash {current_hash[:12]}… "
                f"vs expected {next(iter(expected_hashes))[:12]}…). "
                "Re-run 'elfmem migrate plan'.",
            ))
        return results

    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        for step in steps:
            results.append(StepApplyResult(
                step.id, "failed", f"file is not valid JSON: {e}",
            ))
        return results

    # Pass 1: filter steps that are skippable (idempotent or server gone) AND
    # apply the rest to the in-memory data. Build per-step results so the
    # caller sees one outcome per step, not one per file.
    pending: list[MigrationStep] = []
    for step in steps:
        pre = _check_step_preconditions(step, data)
        if pre is not None:
            results.append(StepApplyResult(step.id, pre[0], pre[1]))
            continue
        # Mutate in-memory; commit happens once after the loop.
        server_name = _server_name_from_pointer(step.json_pointer)
        data["mcpServers"][server_name] = step.after
        pending.append(step)

    if not pending:
        return results

    if dry_run:
        for step in pending:
            server_name = _server_name_from_pointer(step.json_pointer)
            results.append(StepApplyResult(
                step.id, "applied",
                f"[dry-run] would update '{server_name}' in {file}",
            ))
        return results

    # One backup + one write per file group. Backup id is the FIRST step's id
    # (more readable than concatenating); the per-step results all reference
    # the same backup so users can see which steps share a rollback point.
    try:
        backup = _backup_path(file, pending[0].id)
        backup.write_bytes(_resolve_target(file).read_bytes())
        _atomic_write_json(file, data)
    except OSError as e:
        for step in pending:
            results.append(StepApplyResult(
                step.id, "failed",
                f"OS error writing {file}: {e}. Check file permissions and "
                "available disk space.",
            ))
        return results

    for step in pending:
        server_name = _server_name_from_pointer(step.json_pointer)
        results.append(StepApplyResult(
            step.id, "applied",
            f"updated '{server_name}' in {file}",
            backup=backup,
        ))
    return results


def apply_plan(
    plan: MigrationPlan,
    *,
    only: tuple[str, ...] | None = None,
    dry_run: bool = False,
) -> ApplyResult:
    """Apply every step in *plan* (or only those whose id is in *only*).

    Steps targeting the same file are grouped and applied in a single
    backup-and-write cycle. Per-step results are still returned so callers
    see one outcome per step.
    """
    target_ids = set(only) if only else None
    targeted = [
        s for s in plan.steps if target_ids is None or s.id in target_ids
    ]

    # Group by file (preserve plan order within each group).
    groups: dict[Path, list[MigrationStep]] = {}
    for step in targeted:
        groups.setdefault(step.file, []).append(step)

    results: list[StepApplyResult] = []
    for file, file_steps in groups.items():
        results.extend(_apply_file_group(file, file_steps, dry_run=dry_run))

    # Report missing target ids that didn't match any step.
    if target_ids is not None:
        executed = {r.step_id for r in results}
        for missing in sorted(target_ids - executed):
            results.append(StepApplyResult(
                missing, "failed", f"no such migration in current plan: {missing}",
            ))

    return ApplyResult(results=results)


# ── Formatting ────────────────────────────────────────────────────────────────


def format_finding(finding: MigrationFinding) -> str:
    """Render one finding as a human-readable diff for terminal display."""
    lines = [
        f"  File: {finding.file}",
        f"  Server: {finding.server_name}",
        "  Issues:",
    ]
    for issue in finding.issues:
        lines.append(f"    - {issue}")
    lines.append("")
    lines.append("  Current:")
    for ln in json.dumps(finding.current, indent=2).splitlines():
        lines.append(f"    {ln}")
    lines.append("")
    lines.append("  Suggested:")
    for ln in json.dumps(finding.suggested, indent=2).splitlines():
        lines.append(f"    {ln}")
    return "\n".join(lines)
