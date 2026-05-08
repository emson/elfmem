"""Establishment-state detection: classify an elfmem instance for lifecycle ops.

Used by ``elfmem init`` (to decide between full-init / refresh-only / refuse),
``elfmem doctor`` (to choose the right recovery suggestion), and any future
command that needs to know "is this a fresh install or an established one?".

The principle this module enforces:

    Authoritative state is read, never inferred. When live state exists,
    config is truth; defaults are bootstrap.

The detector branches the lifecycle so each command can do exactly one job:

- ``"fresh"``        — no config / no DB / DB has no user data → init creates.
- ``"established"``  — config + DB + ≥1 content row → init refreshes only.
- ``"orphan"``       — populated DB at a neighbour path; configured DB is
                       empty. Init/refresh refuse; ``elfmem rescue`` is the
                       right tool.
- ``"unreadable"``   — DB exists but cannot be opened. Init/refresh refuse;
                       user must investigate before mutation.

Pure-read; never writes. Reuses the rescue module's neighbour detection so
both surfaces share one implementation of "where could the user's data be?".
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from elfmem.rescue import RescuePlan, build_rescue_plan, inspect


@dataclass(frozen=True)
class EstablishmentState:
    """Result of classifying an elfmem instance for lifecycle decisions.

    ``kind`` is the canonical branch label; ``reason`` is human-readable
    detail; ``rescue_plan`` is populated only for ``"orphan"`` so callers
    can surface the rebind plan without recomputing.
    """

    established: bool
    kind: str  # "fresh" | "established" | "orphan" | "unreadable"
    reason: str
    block_count: int = 0
    peer_count: int = 0
    rescue_plan: RescuePlan | None = None

    @property
    def suggested_command(self) -> str:
        """The command an operator/agent should run from this state.

        - ``"fresh"``       → ``elfmem init`` (creates).
        - ``"established"`` → ``elfmem init`` (idempotent refresh; same word,
                              state-aware behaviour, banner makes mode visible).
        - ``"orphan"``      → ``elfmem rescue`` (rebind the configured path).
        - ``"unreadable"``  → ``elfmem doctor`` (diagnose; no destructive op).
        """
        if self.kind == "orphan":
            return "elfmem rescue"
        if self.kind == "unreadable":
            return "elfmem doctor"
        return "elfmem init"

    def to_dict(self) -> dict[str, Any]:
        return {
            "established": self.established,
            "kind": self.kind,
            "reason": self.reason,
            "block_count": self.block_count,
            "peer_count": self.peer_count,
            "suggested_command": self.suggested_command,
            "rescue_plan": (
                self.rescue_plan.to_dict() if self.rescue_plan else None
            ),
        }


def is_established_instance(
    config_path: str | Path | None,
    db_path: str | Path | None,
) -> EstablishmentState:
    """Classify the lifecycle state of an elfmem instance.

    Order of checks (each rules out the next):
    1. Config absent → ``"fresh"`` — nothing to refresh, init must create.
    2. DB path missing/empty argument → ``"fresh"``.
    3. DB file missing → ``"fresh"`` — config exists but DB hasn't been created.
    4. Rescue plan suggests ``rebind`` or ``ambiguous`` → ``"orphan"`` — caller
       must run ``elfmem rescue`` first; init/refresh would orphan more data.
    5. DB file unreadable (corrupt / not SQLite / IO error) → ``"unreadable"``
       — refuse; never silently classify as fresh and overwrite.
    6. DB has no content rows → ``"fresh"`` — same as a brand-new DB even
       though the file exists; init's seed step is meaningful here.
    7. DB has ≥1 content row → ``"established"`` — init delegates to
       refresh-only mode.
    """
    if config_path is None or not Path(config_path).exists():
        return EstablishmentState(
            established=False, kind="fresh", reason="no config",
        )
    if not db_path:
        return EstablishmentState(
            established=False, kind="fresh", reason="no db path",
        )

    db = Path(db_path)
    if not db.exists():
        return EstablishmentState(
            established=False, kind="fresh",
            reason=f"db file does not exist: {db}",
        )

    plan = build_rescue_plan(db_path, config_path)
    if plan.action in ("rebind", "ambiguous"):
        return EstablishmentState(
            established=False, kind="orphan",
            reason=plan.summary, rescue_plan=plan,
        )

    candidate = inspect(db)
    if candidate.error:
        return EstablishmentState(
            established=False, kind="unreadable",
            reason=f"DB unreadable: {candidate.error}",
        )
    if not candidate.populated:
        return EstablishmentState(
            established=False, kind="fresh",
            reason=f"db file exists but has no content rows: {db}",
        )

    return EstablishmentState(
        established=True, kind="established",
        reason=f"{candidate.block_count} blocks, {candidate.peer_count} peers",
        block_count=candidate.block_count,
        peer_count=candidate.peer_count,
    )
