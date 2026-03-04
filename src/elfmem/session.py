"""Session management — active-hours clock, begin/end session."""

from __future__ import annotations

import time
import uuid

from sqlalchemy.ext.asyncio import AsyncConnection

from elfmem.db.queries import (
    end_session as _db_end_session,
)
from elfmem.db.queries import (
    get_total_active_hours,
    set_total_active_hours,
    start_session,
)

# Wall-clock start time for the current in-memory session (seconds since epoch).
# Set by begin_session(), used by compute_current_active_hours().
_session_wall_start: float | None = None
_session_base_hours: float = 0.0


def _new_session_id() -> str:
    return uuid.uuid4().hex[:16]


async def begin_session(
    conn: AsyncConnection,
    *,
    task_type: str = "general",
) -> str:
    """Start a new session. Returns session_id.

    Snapshots total_active_hours from the DB, records session start,
    and initialises the in-memory wall-clock.
    """
    global _session_wall_start, _session_base_hours

    session_id = _new_session_id()
    total_hours = await get_total_active_hours(conn)

    await start_session(
        conn,
        session_id=session_id,
        task_type=task_type,
        start_active_hours=total_hours,
    )

    _session_wall_start = time.monotonic()
    _session_base_hours = total_hours

    return session_id


async def end_session(
    conn: AsyncConnection,
    session_id: str,
) -> float:
    """End a session. Returns session duration in active hours.

    Updates total_active_hours in the DB.
    """
    global _session_wall_start, _session_base_hours

    duration_hours = _elapsed_hours()
    new_total = _session_base_hours + duration_hours

    await set_total_active_hours(conn, new_total)
    await _db_end_session(conn, session_id)

    _session_wall_start = None
    return duration_hours


def compute_current_active_hours() -> float:
    """Return total active hours including the current in-progress session."""
    return _session_base_hours + _elapsed_hours()


def _elapsed_hours() -> float:
    """Seconds elapsed since session start, converted to hours."""
    if _session_wall_start is None:
        return 0.0
    elapsed_sec = time.monotonic() - _session_wall_start
    return elapsed_sec / 3600.0
