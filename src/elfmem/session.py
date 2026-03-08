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


def _new_session_id() -> str:
    return uuid.uuid4().hex[:16]


async def begin_session(
    conn: AsyncConnection,
    *,
    task_type: str = "general",
) -> str:
    """Start a new session. Returns session_id.

    Records session start in the database. Timing state (wall_start, base_hours)
    is returned implicitly through the session_id — callers that need active-hours
    tracking should record time.monotonic() and call get_total_active_hours()
    themselves (MemorySystem stores these as instance fields).
    """
    session_id = _new_session_id()
    total_hours = await get_total_active_hours(conn)

    await start_session(
        conn,
        session_id=session_id,
        task_type=task_type,
        start_active_hours=total_hours,
    )

    return session_id


async def end_session(
    conn: AsyncConnection,
    session_id: str,
    *,
    wall_start: float | None = None,
    base_hours: float = 0.0,
) -> float:
    """End a session. Returns session duration in active hours.

    Updates total_active_hours in the DB.

    Args:
        wall_start: time.monotonic() value from session start. None → duration=0.0.
        base_hours: total_active_hours snapshot taken at session start.
    """
    duration_hours = _elapsed_hours(wall_start)
    new_total = base_hours + duration_hours

    await set_total_active_hours(conn, new_total)
    await _db_end_session(conn, session_id)

    return duration_hours


def _elapsed_hours(wall_start: float | None) -> float:
    """Seconds elapsed since wall_start, converted to hours."""
    if wall_start is None:
        return 0.0
    return (time.monotonic() - wall_start) / 3600.0
