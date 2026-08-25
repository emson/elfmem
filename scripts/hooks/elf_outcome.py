#!/usr/bin/env python
"""Stop hook — close the loop that has stayed open since elfmem was written.

`frame()` records what it *assembled* into a turn, for free. Nothing records
whether any of it was used. `ledger.record_assembly`'s own docstring names the
consequence: the voluntary feedback verb has been called nine times across
three real instances, so reinforcement counts retrievals and a block that is
retrieved constantly without ever being drawn on rises exactly like one doing
the work. The UserPromptSubmit hook makes that worse by retrieving on every
prompt, which is what makes this the next thing to build rather than a nicety.

The evidence needed was always there: the answer. This hook reads the turn's
assistant text, compares it against the blocks the prompt hook injected, and
records the ones whose content shows through.

What it deliberately does NOT do
--------------------------------
Touch confidence. Usage is evidence of relevance, never of truth -- a block
can be drawn on and be wrong. `outcome()` owns the Beta posterior, and folding
usage into it would redefine confidence from "has proven right" to "gets
talked about", silently, in a term carrying 15-30% of every frame's ranking.

Penalise. Attribution is lexical and under-detects paraphrase, so a block that
scores zero may still have been used. Rewarding detected use is safe in the
direction the measurement errs; decaying on non-detection is not.

Wiring (personal, `.claude/` is gitignored) -- in `.claude/settings.local.json`:

    "hooks": {"Stop": [{"hooks": [{"type": "command",
      "command": "<project>/.venv/bin/python",
      "args": ["<project>/scripts/hooks/elf_outcome.py"], "timeout": 15}]}]}
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from elf_context import _load_env, _log, is_addressed, pending_file  # noqa: E402

ELFMEM_TOOL_PREFIX = "mcp__elfmem__"


def _turn_rows(transcript_path: Path) -> list[dict]:
    """Transcript rows for the turn that just ended.

    A genuine user prompt is the only transcript entry whose message content
    is a bare string -- tool results arrive as lists, and so does injected
    skill text. That makes the last string-content `user` entry an exact turn
    boundary, with no need to interpret what any of the entries mean.
    """
    if not transcript_path.exists():
        return []
    rows: list[dict] = []
    for line in transcript_path.read_text().splitlines():
        if not line.strip():
            continue
        with contextlib.suppress(json.JSONDecodeError):
            rows.append(json.loads(line))

    start = 0
    for i, row in enumerate(rows):
        if row.get("type") == "user" and isinstance(
            row.get("message", {}).get("content"), str
        ):
            start = i
    return rows[start:]


def turn_response(rows: list[dict]) -> str:
    """The assistant's prose for the turn.

    Returns assistant `text` blocks only. Tool calls and their results are
    excluded on purpose: a block quoted back inside a grep result is not the
    same event as a block that shaped the answer.
    """
    parts: list[str] = []
    for row in rows:
        if row.get("type") != "assistant":
            continue
        content = row.get("message", {}).get("content")
        if not isinstance(content, list):
            continue
        parts.extend(
            block.get("text", "") for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return "\n".join(parts)


def turn_tool_names(rows: list[dict]) -> list[str]:
    """Tool names invoked during the turn.

    The counterpart `turn_response` deliberately can't see this: a block that
    shaped an active elfmem_recall call was engaged with even if the final
    prose never quotes it back. Attribution on the answer alone would score
    that turn as a miss; this is the other half of the evidence.
    """
    names: list[str] = []
    for row in rows:
        if row.get("type") != "assistant":
            continue
        content = row.get("message", {}).get("content")
        if not isinstance(content, list):
            continue
        names.extend(
            block.get("name", "") for block in content
            if isinstance(block, dict) and block.get("type") == "tool_use"
        )
    return names


async def _record(project_root: Path, used: list[str]) -> str:
    from elfmem.api import MemorySystem
    from elfmem.project import find_local_config, resolve_db

    config_path = find_local_config(project_root)
    db_path, _ = resolve_db(None, str(config_path) if config_path else None, project_root)
    mem = await MemorySystem.from_config(
        db_path, config=str(config_path) if config_path else None
    )
    try:
        result = await mem.record_use(used, source="claude-code")
        return result.summary
    finally:
        await mem.close()


def main() -> int:
    started = time.monotonic()
    raw = sys.stdin.read()
    payload = json.loads(raw) if raw.strip() else {}
    session_id = str(payload.get("session_id", ""))
    project_root = Path(payload.get("cwd") or os.getcwd())

    pending = pending_file(project_root, session_id)
    if not pending.exists():
        return 0  # nothing was injected this turn; nothing to judge

    injected: dict[str, str] = json.loads(pending.read_text())
    pending.unlink()  # one turn, one judgement -- never carried forward
    if not injected:
        return 0

    rows = _turn_rows(Path(payload.get("transcript_path", "")))
    response = turn_response(rows)
    if not response:
        _log(project_root, {"decision": "no-response", "injected": len(injected)})
        return 0

    _load_env(project_root)
    from elfmem.memory.attribution import attributed_ids

    used = attributed_ids(injected, response)
    active_elfmem = any(
        name.startswith(ELFMEM_TOOL_PREFIX) for name in turn_tool_names(rows)
    )

    # Passive injection already ran unconditionally -- that's elf_context.py's
    # job. What it can't guarantee is engagement: a session that named elf
    # directly got memory handed to it and drew on none of it, actively or in
    # the prose. One nudge back, not a hard loop -- `pending` is already
    # unlinked, so a second Stop event this turn finds nothing to re-check and
    # lets the turn end regardless of whether the retry actually engaged.
    if not used and not active_elfmem and is_addressed(project_root, session_id):
        _log(project_root, {"decision": "engagement-gap", "injected": len(injected)})
        print(json.dumps({
            "decision": "block",
            "reason": (
                "This session addressed elf directly, and this turn drew on "
                "none of the memory elf_context.py injected -- no active "
                "mcp__elfmem__* call, and the answer's prose didn't reflect it "
                "either. Call elfmem_recall or elfmem_status before finishing, "
                "and let the answer actually show what came back."
            ),
        }))
        return 0

    summary = asyncio.run(_record(project_root, used)) if used else "nothing drawn on"

    _log(project_root, {
        "decision": "attributed", "injected": len(injected), "used": len(used),
        "ids": used, "summary": summary,
        "ms": round((time.monotonic() - started) * 1000),
    })
    return 0


if __name__ == "__main__":
    # Same system-boundary contract as the prompt hook: a memory failure must
    # never cost the user a turn. Exit 0, record nothing, leave the reason in
    # the log. A Stop hook that raises is worse than one that learns nothing.
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 — see above
        with contextlib.suppress(Exception):
            _log(Path(os.getcwd()), {"decision": "failed", "error": repr(exc)[:300]})
        sys.exit(0)
