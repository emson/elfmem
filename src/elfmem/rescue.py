"""Detect orphaned populated DBs and propose rebind plans.

The 0.13.0 path-resolution change silently relocated existing users' DBs:
``project.db: <bare-relative>`` resolved against the config dir instead of
cwd. Affected users opened ``elfmem doctor``, saw "DB not accessible", ran
``elfmem init``, and ended up with a fresh empty DB at the new path while
their real data sat orphaned at the cwd-relative path.

This module is the recovery surface. Pure-read; never mutates.

Three layers:

- ``find_neighbour_dbs(configured_path, config_path)`` — given the resolved
  DB path that the configured value points at AND the config file's path,
  enumerate other plausible DB locations and report any that look populated.

- ``RescuePlan`` — structured suggestion: which DB looks like the real one,
  what action to take (rebind config, leave alone, etc.).

- ``build_rescue_plan(...)`` — agent-friendly entry point. Returns a typed
  RescuePlan with per-candidate detail and an apply hint.

The CLI wires this into ``elfmem rescue`` and ``elfmem doctor``.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Names we treat as plausible DB locations relative to a config file.
# Order matters: the first match wins ties.
_NEIGHBOUR_PATTERNS: tuple[str, ...] = (
    # config-dir-relative (0.13.0 buggy semantics — checked so users with
    # data accidentally written here can find it):
    "{db_basename}",
    # parent-of-config-dir (cwd-relative when run from project root —
    # the 0.12.x semantics, where most existing data lives):
    "../{db_basename}",
    # global location:
    "~/.elfmem/databases/{db_basename}",
)


_CONTENT_TABLES: tuple[str, ...] = ("blocks", "peer_roster", "block_tags", "edges")


@dataclass(frozen=True)
class DbCandidate:
    """One on-disk DB file evaluated for rescue purposes."""

    path: Path
    exists: bool
    size_bytes: int = 0
    block_count: int = 0
    peer_count: int = 0
    edge_count: int = 0
    tag_count: int = 0
    error: str = ""

    @property
    def populated(self) -> bool:
        """Does this candidate contain user data?

        Heuristic: any non-zero count in the four canonical content tables.
        Excludes the system_config / frames tables which always have rows
        even on a fresh install.
        """
        return any((
            self.block_count, self.peer_count,
            self.edge_count, self.tag_count,
        ))

    @property
    def total_rows(self) -> int:
        return (
            self.block_count + self.peer_count
            + self.edge_count + self.tag_count
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "exists": self.exists,
            "size_bytes": self.size_bytes,
            "block_count": self.block_count,
            "peer_count": self.peer_count,
            "edge_count": self.edge_count,
            "tag_count": self.tag_count,
            "populated": self.populated,
            "error": self.error,
        }


@dataclass(frozen=True)
class RescuePlan:
    """A structured rescue suggestion built from candidate analysis.

    ``action`` values:
    - ``"none"``           — configured DB exists and is populated (or no
                             populated alternative found).
    - ``"rebind"``         — configured DB is empty/missing; exactly one
                             populated alternative found. Suggest absolute
                             path rewrite of ``project.db``.
    - ``"ambiguous"``      — multiple populated alternatives found. Refuse
                             to suggest one; surface for human/agent choice.
    - ``"first_install"``  — no populated DB anywhere. Safe to ``elfmem init``.
    """

    configured: DbCandidate
    candidates: list[DbCandidate] = field(default_factory=list)
    action: str = "none"
    suggested_target: Path | None = None

    @property
    def populated_alternatives(self) -> list[DbCandidate]:
        return [
            c for c in self.candidates
            if c.populated and c.path.resolve() != self.configured.path.resolve()
        ]

    @property
    def summary(self) -> str:
        if self.action == "none":
            if self.configured.populated:
                return (
                    f"Configured DB is populated "
                    f"({self.configured.block_count} blocks, "
                    f"{self.configured.peer_count} peers). No rescue needed."
                )
            return "No populated DB found anywhere. Safe to run 'elfmem init'."
        if self.action == "rebind":
            assert self.suggested_target is not None
            t = self.suggested_target
            return (
                f"Configured DB is empty; populated DB found at {t} "
                f"({self._target_candidate.block_count} blocks). "
                f"Suggested: rewrite project.db to '{t}' (absolute)."
            )
        if self.action == "ambiguous":
            return (
                f"Found {len(self.populated_alternatives)} populated DBs at "
                "different paths. Manual choice required — see candidates."
            )
        if self.action == "first_install":
            return "No populated DB. Safe to run 'elfmem init'."
        return self.action

    @property
    def _target_candidate(self) -> DbCandidate:
        assert self.suggested_target is not None
        for c in self.candidates:
            if c.path.resolve() == self.suggested_target.resolve():
                return c
        raise AssertionError("suggested_target not in candidates")

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "summary": self.summary,
            "configured": self.configured.to_dict(),
            "candidates": [c.to_dict() for c in self.candidates],
            "suggested_target": (
                str(self.suggested_target) if self.suggested_target else None
            ),
            "populated_alternatives": [
                c.to_dict() for c in self.populated_alternatives
            ],
        }


def inspect(path: Path) -> DbCandidate:
    """Inspect one DB file by row counts. Read-only."""
    if not path.exists():
        return DbCandidate(path=path, exists=False)
    try:
        size = path.stat().st_size
    except OSError as e:
        return DbCandidate(path=path, exists=False, error=str(e))

    counts: dict[str, int] = dict.fromkeys(_CONTENT_TABLES, 0)
    error = ""
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            cur = conn.cursor()
            for table in _CONTENT_TABLES:
                try:
                    cur.execute(f"SELECT COUNT(*) FROM {table}")
                    counts[table] = int(cur.fetchone()[0])
                except sqlite3.OperationalError:
                    counts[table] = 0
        finally:
            conn.close()
    except sqlite3.DatabaseError as e:
        error = str(e)

    return DbCandidate(
        path=path,
        exists=True,
        size_bytes=size,
        block_count=counts["blocks"],
        peer_count=counts["peer_roster"],
        edge_count=counts["edges"],
        tag_count=counts["block_tags"],
        error=error,
    )


def find_neighbour_dbs(
    configured_path: str | Path,
    config_path: str | Path | None,
) -> list[Path]:
    """Return plausible alternate DB locations for the given configured path.

    Looks at: the configured path's basename in (a) the config dir, (b) the
    config dir's parent, (c) the global ``~/.elfmem/databases/`` directory.
    Excludes the configured path itself. Does not check existence — the
    caller filters via ``inspect()``.
    """
    configured = Path(configured_path).expanduser()
    basename = configured.name
    if not basename:
        return []

    candidates: list[Path] = []

    if config_path is not None:
        config_dir = Path(config_path).expanduser().resolve().parent
        config_dir_path = config_dir / basename
        candidates.append(config_dir_path)
        candidates.append(config_dir.parent / basename)

    candidates.append(
        Path(f"~/.elfmem/databases/{basename}").expanduser()
    )

    # Deduplicate while preserving order; exclude the configured path itself.
    seen: set[Path] = set()
    out: list[Path] = []
    configured_resolved = configured.resolve() if configured.exists() else configured
    for c in candidates:
        c_resolved = c.resolve() if c.exists() else c
        if c_resolved == configured_resolved:
            continue
        if c_resolved in seen:
            continue
        seen.add(c_resolved)
        out.append(c)
    return out


def build_rescue_plan(
    configured_path: str | Path,
    config_path: str | Path | None,
) -> RescuePlan:
    """Build a structured rescue plan for the given configuration.

    Pure-read entry point. The CLI calls this from both ``elfmem rescue``
    and ``elfmem doctor``; ``elfmem init`` calls it as a pre-flight to
    refuse on ambiguity.
    """
    configured = inspect(Path(configured_path).expanduser())
    candidates = [configured]
    for nb in find_neighbour_dbs(configured_path, config_path):
        candidates.append(inspect(nb))

    populated = [c for c in candidates if c.populated]

    if configured.populated:
        # Configured DB is fine; surface other populated DBs as candidates
        # but no rescue action recommended.
        return RescuePlan(
            configured=configured,
            candidates=candidates,
            action="none",
        )

    populated_others = [c for c in populated if c is not configured]
    if not populated_others:
        if configured.exists:
            # Empty DB at configured path, nothing elsewhere — fresh install state.
            return RescuePlan(
                configured=configured,
                candidates=candidates,
                action="first_install",
            )
        return RescuePlan(
            configured=configured,
            candidates=candidates,
            action="first_install",
        )

    if len(populated_others) == 1:
        return RescuePlan(
            configured=configured,
            candidates=candidates,
            action="rebind",
            suggested_target=populated_others[0].path,
        )

    return RescuePlan(
        configured=configured,
        candidates=candidates,
        action="ambiguous",
    )
