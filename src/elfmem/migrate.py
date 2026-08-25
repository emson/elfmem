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

from elfmem.config import ElfmemConfig
from elfmem.project import get_project_info

# Env var aliases that have been renamed. Map deprecated → canonical.
DEPRECATED_ENV_VARS: dict[str, str] = {
    "ELFMEM_CONFIG_PATH": "ELFMEM_CONFIG",
    "ELFMEM_DB_PATH": "ELFMEM_DB",
}

# Default Claude config locations to scan, in priority order.
# ~/.claude.json is Claude Code's real global config — each project's
# mcpServers entry lives nested under projects[<project path>], a distinct
# shape from the flat mcpServers used by .mcp.json / claude_code_config.json
# (see scan_file_with_warnings and ADR 0008).
DEFAULT_SCAN_PATHS: tuple[Path, ...] = (
    Path.home() / ".claude" / "claude_code_config.json",
    Path.cwd() / ".claude.json",
    Path.home() / ".claude.json",
)


@dataclass(frozen=True)
class MigrationFinding:
    """One actionable finding for an elfmem MCP entry that needs updating.

    ``project_path`` is set when the entry was found nested under
    ``projects[<project_path>].mcpServers`` in a global ``~/.claude.json``
    (None for the flat top-level ``mcpServers`` shape used by ``.mcp.json``).
    """

    file: Path
    server_name: str
    issues: list[str] = field(default_factory=list)
    current: dict[str, Any] = field(default_factory=dict)
    suggested: dict[str, Any] = field(default_factory=dict)
    project_path: str | None = None

    @property
    def needs_migration(self) -> bool:
        return bool(self.issues)


def is_elfmem_entry(entry: dict[str, Any]) -> bool:
    """Return True if a Claude MCP server entry is an elfmem instance.

    Detection rule: any of args, command, or env contains "elfmem".
    """
    blob = json.dumps(entry).lower()
    return "elfmem" in blob


def _extract_config_arg(args: list[Any]) -> str | None:
    """Return the value following '--config' in a serve invocation's args."""
    for i, a in enumerate(args):
        if a == "--config" and i + 1 < len(args):
            return str(args[i + 1])
    return None


def _suggest_entry(
    entry: dict[str, Any], *, project_root: Path | None = None
) -> tuple[dict[str, Any], list[str]]:
    """Return (suggested_entry, issues) — what the entry should look like.

    Conservative: keeps user customisations (alwaysAllow, command shape) and
    only rewrites the parts that are demonstrably stale.

    *project_root* is the project this entry is nested under (only known for
    entries found via the ``~/.claude.json`` ``projects[path]`` shape — see
    ADR 0008). When given, checks whether ``--config`` actually points at
    that project's own config; a mismatch means the entry was copied from,
    or drifted to, an unrelated project.
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

    # 3. Detect an entry nested under the wrong project — its --config
    # doesn't match the project it's actually filed under.
    if project_root is not None:
        cfg_arg = _extract_config_arg(suggested.get("args", []))
        if cfg_arg:
            expected = get_project_info(project_root)
            if expected is not None:
                # A relative --config is what elfmem serve would resolve
                # against ITS cwd (the project root Claude Code spawns it
                # in) — resolve it the same way here, not against whatever
                # cwd 'elfmem migrate'/'doctor' itself happens to run from.
                cfg_path = Path(cfg_arg).expanduser()
                if not cfg_path.is_absolute():
                    cfg_path = project_root / cfg_path
                actual_resolved = cfg_path.resolve()
                expected_resolved = expected.config.resolve()
                if actual_resolved != expected_resolved:
                    issues.append(
                        f"--config points at {actual_resolved}, but the "
                        f"project at {project_root} resolves to "
                        f"{expected_resolved} — entry looks stale or copied "
                        "from another project"
                    )
                    new_args = list(suggested["args"])
                    new_args[new_args.index("--config") + 1] = str(expected_resolved)
                    suggested["args"] = new_args

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

    # ~/.claude.json's real shape: each project's mcpServers nests under
    # projects[<project path>], not a flat top-level key (see ADR 0008).
    # project_root is known exactly here — it's the dict key itself — so the
    # wrong-project drift check in _suggest_entry can run.
    projects = data.get("projects") or {}
    if isinstance(projects, dict):
        for project_path, project_data in projects.items():
            if not isinstance(project_data, dict):
                continue
            nested_servers = project_data.get("mcpServers") or {}
            if not isinstance(nested_servers, dict):
                continue
            for name, entry in nested_servers.items():
                if not isinstance(entry, dict) or not is_elfmem_entry(entry):
                    continue
                suggested, issues = _suggest_entry(
                    entry, project_root=Path(project_path)
                )
                if issues:
                    findings.append(
                        MigrationFinding(
                            file=path,
                            server_name=name,
                            issues=issues,
                            current=entry,
                            suggested=suggested,
                            project_path=project_path,
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
    project_path: str | None = None
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
            "project_path": self.project_path,
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


def _step_id(file: Path, server_name: str, project_path: str | None = None) -> str:
    """Stable, human-readable identifier for one migration step.

    Format: ``mcp-{server_name}@{file_basename_stem}-{short_hash}``. The hash
    component disambiguates two files with the same basename in different
    locations (e.g. multiple project ``.claude.json`` files), and — when
    *project_path* is given — two entries with the same server name nested
    under different projects within one ``~/.claude.json`` (the common case:
    every project's elfmem entry is conventionally named "elfmem").
    """
    key = str(file) if project_path is None else f"{file}::{project_path}"
    h = hashlib.sha256(key.encode()).hexdigest()[:8]
    stem = file.stem.replace(".", "-")
    return f"mcp-{server_name}@{stem}-{h}"


def _json_pointer_escape(segment: str) -> str:
    """Escape a literal path segment for embedding in a JSON pointer (RFC 6901)."""
    return segment.replace("~", "~0").replace("/", "~1")


def _finding_to_step(finding: MigrationFinding) -> MigrationStep:
    issues_text = "; ".join(finding.issues)
    if finding.project_path is None:
        pointer = f"/mcpServers/{finding.server_name}"
    else:
        pointer = (
            f"/projects/{_json_pointer_escape(finding.project_path)}"
            f"/mcpServers/{finding.server_name}"
        )
    return MigrationStep(
        id=_step_id(finding.file, finding.server_name, finding.project_path),
        kind="claude_mcp_config",
        summary=f"Update '{finding.server_name}' MCP entry: {issues_text}",
        file=finding.file,
        file_sha256=_sha256(finding.file),
        issues=list(finding.issues),
        before=finding.current,
        after=finding.suggested,
        json_pointer=pointer,
        project_path=finding.project_path,
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


def _servers_container(
    data: dict[str, Any], project_path: str | None
) -> dict[str, Any] | None:
    """Return the mcpServers dict a step targets, or None if it's gone.

    Flat shape (project_path=None): data["mcpServers"] — used by .mcp.json.
    Nested shape: data["projects"][project_path]["mcpServers"] — the real
    ~/.claude.json layout (see ADR 0008). The returned dict is the live
    object nested inside *data*; mutating it in place mutates *data*.
    """
    if project_path is None:
        servers = data.get("mcpServers")
        return servers if isinstance(servers, dict) else None
    projects = data.get("projects") or {}
    project = projects.get(project_path)
    if not isinstance(project, dict):
        return None
    servers = project.get("mcpServers")
    return servers if isinstance(servers, dict) else None


def _check_step_preconditions(
    step: MigrationStep,
    data: dict[str, Any],
) -> tuple[str, str] | None:
    """Return (status, detail) if *step* should not apply against *data*; else None.

    Pulled out so file-grouped apply can reuse the same idempotency and
    server-presence checks per step before mutating the in-memory state.
    """
    servers = _servers_container(data, step.project_path)
    server_name = _server_name_from_pointer(step.json_pointer)
    if servers is None or server_name not in servers:
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
        servers = _servers_container(data, step.project_path)
        assert servers is not None  # guaranteed by the precondition check above
        servers[server_name] = step.after
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
        servers = _servers_container(data, step.project_path)
        assert servers is not None  # guaranteed by the precondition check above
        servers[server_name] = step.after
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


# ── Substrate migration (v2 file substrate) ────────────────────────────────
#
# One more step `kind` on the same MigrationStep/status/plan/apply model
# used above for Claude MCP config drift — not a parallel command surface.
# `elfmem migrate status/plan/apply` already knows how to run this; users
# don't learn anything new.
#
# The whole thing is additive and read-only against the live database: it
# is only ever read from, never written to or deleted. Every write goes to
# a *new* file (a timestamped backup, `.elfmem/memory/**.md`, a fresh
# `.elfmem/index.db`). That is also what makes rollback (`undo_substrate_step`)
# safe by construction — nothing destructive ever happened to undo.
#
# Deliberately NOT built here: switching a live MemorySystem over to read
# from the file substrate ("cutover" / "flip authority", plan doc Phases
# 5-6). That needs real re-wiring of learn()/edit()/forget()/etc. at the
# API level, not a migration step — see docs/plans/v2_substrate/plan/
# build-plan.md units U-006/U-007. This step stops at "exported and
# verified," clearly labelled as such.

SUBSTRATE_MARKER_NAME = ".substrate-migration.json"


@dataclass(frozen=True)
class SubstrateMarker:
    """Recorded state of the last successful substrate export/rebuild/verify
    cycle for one project's `.elfmem/` directory.

    Read by ``scan_substrate`` to decide whether a new step is pending (the
    database's current fingerprint no longer matches ``fingerprint``), and
    by ``undo_substrate_step`` to refuse removing files that were hand-edited
    since export (``files_fingerprint`` no longer matches what's on disk).
    """

    fingerprint: str
    files_fingerprint: str
    applied_at: str
    backup_path: str
    memory_dir: str
    index_db_path: str
    blocks_exported: int
    blocks_written: int
    parity_passed: bool
    diverging_query_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "fingerprint": self.fingerprint,
            "files_fingerprint": self.files_fingerprint,
            "applied_at": self.applied_at,
            "backup_path": self.backup_path,
            "memory_dir": self.memory_dir,
            "index_db_path": self.index_db_path,
            "blocks_exported": self.blocks_exported,
            "blocks_written": self.blocks_written,
            "parity_passed": self.parity_passed,
            "diverging_query_count": self.diverging_query_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SubstrateMarker:
        fields = (
            "fingerprint", "files_fingerprint", "applied_at", "backup_path",
            "memory_dir", "index_db_path", "blocks_exported", "blocks_written",
            "parity_passed", "diverging_query_count",
        )
        return cls(**{k: data[k] for k in fields})


def _substrate_paths(memory_dir: Path) -> tuple[Path, Path]:
    """(index_db_path, marker_dir) derived from --memory-dir, so a custom
    --memory-dir override moves the derived index and marker with it."""
    return memory_dir.parent / "index.db", memory_dir.parent


def _marker_path(marker_dir: Path) -> Path:
    return marker_dir / SUBSTRATE_MARKER_NAME


def _read_marker(marker_dir: Path) -> SubstrateMarker | None:
    path = _marker_path(marker_dir)
    if not path.exists():
        return None
    try:
        return SubstrateMarker.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        # A corrupted marker is treated as "no record" — the next scan
        # re-detects the migration as pending rather than raising, since
        # nothing about this file being unreadable makes the DB unsafe.
        return None


def _write_marker_atomic(marker_dir: Path, marker: SubstrateMarker) -> None:
    marker_dir.mkdir(parents=True, exist_ok=True)
    target = _marker_path(marker_dir)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(marker.to_dict(), indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, target)


def _remove_marker(marker_dir: Path) -> None:
    with contextlib.suppress(OSError):
        _marker_path(marker_dir).unlink()


async def _corpus_snapshot(conn: Any) -> tuple[dict[str, int], str]:
    """Read-only. Returns (block counts by status, content fingerprint).

    Deliberately mirrors exactly what ``export_to_markdown`` reads (same
    fetchers, same tag batching) so the fingerprint changes precisely when
    a re-export would actually produce different files — not a raw file
    hash, which would false-positive on SQLite's own WAL/page-cache churn
    the way a JSON config file never does.
    """
    from elfmem.db.queries import (
        get_active_blocks,
        get_archived_blocks,
        get_inbox_blocks,
        get_tags_batch,
    )

    counts: dict[str, int] = {}
    fp_parts: list[str] = []
    fetchers = {
        "active": get_active_blocks,
        "inbox": get_inbox_blocks,
        "archived": get_archived_blocks,
    }
    for status, fetcher in fetchers.items():
        rows = await fetcher(conn)
        counts[status] = len(rows)
        if not rows:
            continue
        tags_by_id = await get_tags_batch(conn, [r["id"] for r in rows])
        for row in sorted(rows, key=lambda r: r["id"]):
            tags = ",".join(sorted(tags_by_id.get(row["id"], [])))
            digest = hashlib.sha256(row["content"].encode("utf-8")).hexdigest()[:16]
            fp_parts.append(f"{row['id']}|{status}|{row['category']}|{tags}|{digest}")

    fingerprint = hashlib.sha256("\n".join(fp_parts).encode("utf-8")).hexdigest()
    return counts, fingerprint


def _compute_files_fingerprint(memory_dir: Path) -> str:
    """Hash of every exported .md file's contents, used solely to detect
    hand-edits between apply and undo — not a staleness gate against the DB."""
    parts: list[str] = []
    if memory_dir.is_dir():
        for path in sorted(memory_dir.glob("**/*.md")):
            digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
            parts.append(f"{path.relative_to(memory_dir)}|{digest}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def substrate_step_id(db_path: Path) -> str:
    h = hashlib.sha256(str(db_path).encode()).hexdigest()[:8]
    stem = db_path.stem.replace(".", "-")
    return f"substrate-export@{stem}-{h}"


async def scan_substrate(db_path: Path, memory_dir: Path) -> MigrationStep | None:
    """Read-only. Detect whether this project's database has content not
    yet reflected in a verified `.elfmem/memory/` export.

    Pending when the database has any blocks and either no export has ever
    been recorded, or the database's content fingerprint has changed since
    the last recorded export. Returns None — nothing pending — otherwise.
    """
    from elfmem.db.engine import create_engine

    if not db_path.exists():
        return None

    engine = await create_engine(str(db_path))
    try:
        async with engine.connect() as conn:
            counts, fingerprint = await _corpus_snapshot(conn)
    finally:
        await engine.dispose()

    total = sum(counts.values())
    if total == 0:
        return None

    index_db_path, marker_dir = _substrate_paths(memory_dir)
    marker = _read_marker(marker_dir)
    if marker is not None and marker.fingerprint == fingerprint:
        return None

    issues = [
        f"{total} block(s) ({', '.join(f'{v} {k}' for k, v in counts.items() if v)}) "
        "not yet reflected in a verified .elfmem/memory/ export",
        "graph edges and reinforcement count/recency do not carry through "
        "export/rebuild — a known, disclosed limitation, not a defect",
    ]
    if marker is not None:
        issues.insert(0, "database content changed since the last export")

    return MigrationStep(
        id=substrate_step_id(db_path),
        kind="substrate_export",
        summary=(
            f"Export {total} block(s) to the .elfmem/memory/ file substrate "
            "and build a verified derived index (does not change how your "
            "agent operates today)"
        ),
        file=db_path,
        file_sha256=fingerprint,
        issues=issues,
        before=counts,
        after={
            "description": (
                f"All blocks written to {memory_dir}/**.md; a derived index "
                f"built at {index_db_path}; retrieval parity checked against "
                "4 frame-level queries. Your live database and agent "
                "behaviour are unchanged by this step."
            ),
        },
        json_pointer="",
        reversible=True,
        post_apply_step=(
            "Nothing to restart — your agent keeps reading the original "
            "database. Check the parity result in the apply output (or "
            "re-run 'elfmem index parity'); a failed gate is informational, "
            "not a rollback trigger."
        ),
    )


async def apply_substrate_step(
    step: MigrationStep,
    *,
    memory_dir: Path,
    cfg: ElfmemConfig,
    dry_run: bool = False,
) -> StepApplyResult:
    """Apply a `substrate_export` step: backup, export, rebuild, verify.

    Never modifies, deletes, or overwrites the live database — it is only
    ever read from. Every write lands in a new file: the backup, the
    `.elfmem/memory/` export, and a fresh derived `index.db`. Re-running
    after a prior successful apply fully re-derives the index from
    whatever's currently on disk (safe to re-run; not incremental).
    """
    import tempfile
    from datetime import UTC, datetime

    from sqlalchemy import text as sa_text

    from elfmem.adapters.factory import make_embedding_adapter
    from elfmem.context.frames import ATTENTION_FRAME, SELF_FRAME, SIMULATE_FRAME, TASK_FRAME
    from elfmem.db.engine import create_engine
    from elfmem.db.migrate import vacuum_backup
    from elfmem.db.models import metadata
    from elfmem.memory.index_rebuild import rebuild_index
    from elfmem.memory.ledger import ledger_dir_for
    from elfmem.migration.export import export_to_markdown
    from elfmem.migration.parity import check_retrieval_parity
    from elfmem.token_counter import TokenCounter

    db_path = step.file
    index_db_path, marker_dir = _substrate_paths(memory_dir)
    queries: list[tuple[str | None, Any]] = [
        (None, ATTENTION_FRAME), (None, SELF_FRAME),
        (None, TASK_FRAME), (None, SIMULATE_FRAME),
    ]

    live_engine = await create_engine(str(db_path))
    try:
        async with live_engine.connect() as conn:
            counts, current_fp = await _corpus_snapshot(conn)
        if current_fp != step.file_sha256:
            return StepApplyResult(
                step.id, "stale",
                "database content changed since 'elfmem migrate plan' was "
                "run. Re-run 'elfmem migrate plan' and try again.",
            )

        embedding_svc = make_embedding_adapter(cfg, TokenCounter())

        if dry_run:
            with tempfile.TemporaryDirectory() as tmp:
                scratch_memory = Path(tmp) / "memory"
                async with live_engine.connect() as conn:
                    export_result = await export_to_markdown(
                        conn, scratch_memory,
                        ledger_dir=ledger_dir_for(scratch_memory),
                    )
                rebuild_engine = await create_engine(str(Path(tmp) / "index.db"))
                try:
                    async with rebuild_engine.begin() as conn:
                        await conn.run_sync(metadata.create_all)
                        rebuild_result = await rebuild_index(
                            conn, scratch_memory, embedding_svc, cfg.embeddings.model,
                            ledger_dir=ledger_dir_for(scratch_memory),
                        )
                    async with (
                        live_engine.connect() as conn_before,
                        rebuild_engine.connect() as conn_after,
                    ):
                        parity = await check_retrieval_parity(
                            conn_before, conn_after, embedding_svc, queries,
                        )
                finally:
                    await rebuild_engine.dispose()
            gate = (
                "PASS" if parity.passed
                else f"FAIL ({len(parity.diverging_queries())} quer(ies) diverge)"
            )
            return StepApplyResult(
                step.id, "applied",
                f"[dry-run] would export {export_result.blocks_exported} "
                f"block(s), rebuild {rebuild_result.blocks_written} block(s), "
                f"parity gate: {gate}. Nothing written.",
            )

        # ── Real run ──────────────────────────────────────────────────
        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        backup_path = db_path.with_suffix(f".before-substrate.{timestamp}.bak")
        async with live_engine.begin() as conn:
            await vacuum_backup(conn, str(backup_path))

        backup_engine = await create_engine(str(backup_path))
        try:
            async with backup_engine.connect() as conn:
                backup_counts, _ = await _corpus_snapshot(conn)
        finally:
            await backup_engine.dispose()
        if backup_counts != counts:
            return StepApplyResult(
                step.id, "failed",
                f"backup validation failed — counts diverge (live={counts}, "
                f"backup={backup_counts}). No export was written; your "
                f"database is untouched. Backup left at {backup_path} for "
                "inspection.",
            )

        async with live_engine.connect() as conn:
            export_result = await export_to_markdown(
                conn, memory_dir, ledger_dir=ledger_dir_for(memory_dir)
            )

        rebuild_engine = await create_engine(str(index_db_path))
        try:
            async with rebuild_engine.begin() as conn:
                await conn.run_sync(metadata.create_all)
                existing = (
                    await conn.execute(sa_text("SELECT COUNT(*) FROM blocks"))
                ).scalar_one()
                if existing:
                    await conn.execute(sa_text("DELETE FROM edges"))
                    await conn.execute(sa_text("DELETE FROM block_tags"))
                    await conn.execute(sa_text("DELETE FROM blocks"))
                rebuild_result = await rebuild_index(
                    conn, memory_dir, embedding_svc, cfg.embeddings.model,
                    ledger_dir=ledger_dir_for(memory_dir),
                )
            async with (
                live_engine.connect() as conn_before,
                rebuild_engine.connect() as conn_after,
            ):
                parity = await check_retrieval_parity(
                    conn_before, conn_after, embedding_svc, queries,
                )
        finally:
            await rebuild_engine.dispose()

        marker = SubstrateMarker(
            fingerprint=current_fp,
            files_fingerprint=_compute_files_fingerprint(memory_dir),
            applied_at=datetime.now(UTC).isoformat(),
            backup_path=str(backup_path),
            memory_dir=str(memory_dir),
            index_db_path=str(index_db_path),
            blocks_exported=export_result.blocks_exported,
            blocks_written=rebuild_result.blocks_written,
            parity_passed=parity.passed,
            diverging_query_count=len(parity.diverging_queries()),
        )
        _write_marker_atomic(marker_dir, marker)

        gate = (
            "PASSED" if parity.passed
            else f"FAILED — {len(parity.diverging_queries())} quer(ies) "
                 "diverge, see 'elfmem index parity' for detail"
        )
        detail = (
            f"Exported {export_result.blocks_exported} block(s) to "
            f"{memory_dir}; verified index built at {index_db_path} "
            f"({rebuild_result.blocks_written} block(s)); parity gate {gate}. "
            f"Your agent is unchanged — still reading {db_path}."
        )
        return StepApplyResult(step.id, "applied", detail, backup=backup_path)
    finally:
        await live_engine.dispose()


async def undo_substrate_step(
    step: MigrationStep,
    *,
    memory_dir: Path,
    force: bool = False,
) -> StepApplyResult:
    """Remove the artifacts a prior ``apply_substrate_step`` created.

    Never touches the live database — nothing here can lose data that
    isn't already a byproduct of this migration step. Refuses (unless
    ``force=True``) if ``.elfmem/memory/`` has changed since it was
    written, protecting hand-edits made to the exported files.
    """
    import shutil

    _, marker_dir = _substrate_paths(memory_dir)
    marker = _read_marker(marker_dir)
    if marker is None:
        return StepApplyResult(
            step.id, "skipped",
            "no recorded substrate migration to undo (already clean, or never applied)",
        )

    if not force:
        current_files_fp = _compute_files_fingerprint(memory_dir)
        if current_files_fp != marker.files_fingerprint:
            return StepApplyResult(
                step.id, "failed",
                f"{memory_dir} has changed since this migration was applied "
                "(hand-edited?) — refusing to delete possibly-unsaved work. "
                "Pass --force to remove anyway, or back up your edits first.",
            )

    if memory_dir.exists():
        shutil.rmtree(memory_dir)
    with contextlib.suppress(OSError):
        Path(marker.index_db_path).unlink()
    _remove_marker(marker_dir)

    return StepApplyResult(
        step.id, "applied",
        f"Removed {memory_dir} and {marker.index_db_path}. Your original "
        f"database was never modified; its backup remains at "
        f"{marker.backup_path}.",
    )


async def build_full_plan(
    *,
    db_path: Path | None = None,
    memory_dir: Path | None = None,
    scan_paths: tuple[Path, ...] = DEFAULT_SCAN_PATHS,
) -> MigrationPlan:
    """``build_plan()`` (Claude MCP config drift) plus ``scan_substrate()``
    (the v2 file-substrate export), combined into the one plan
    ``elfmem migrate status/plan/apply`` operate on.

    ``db_path``/``memory_dir`` are None when the caller has no resolved
    project (global mode, before ``elfmem init``) — the substrate check is
    skipped in that case, not an error.
    """
    plan = build_plan(scan_paths)
    if db_path is None or memory_dir is None:
        return plan
    substrate_step = await scan_substrate(db_path, memory_dir)
    if substrate_step is None:
        return plan
    return MigrationPlan(steps=[*plan.steps, substrate_step], warnings=plan.warnings)


# ── Formatting ────────────────────────────────────────────────────────────────


def format_finding(finding: MigrationFinding) -> str:
    """Render one finding as a human-readable diff for terminal display."""
    lines = [
        f"  File: {finding.file}",
        *([f"  Project: {finding.project_path}"] if finding.project_path else []),
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
