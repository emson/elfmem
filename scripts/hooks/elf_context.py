#!/usr/bin/env python
"""UserPromptSubmit hook — inject elf's memory before the host model reads a prompt.

Without this, retrieval depends on the assistant *choosing* to call
elfmem_recall: a soft protocol in `.elfmem/AGENT.md` competing with every
other tool for attention. Memory that is remembered only when someone
remembers to ask for it is not memory. This makes it infrastructure.

Wiring (personal, `.claude/` is gitignored) -- in `.claude/settings.local.json`:

    "hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command",
      "command": "<project>/.venv/bin/python <project>/scripts/hooks/elf_context.py",
      "timeout": 15}]}]}

Two frames, deliberately asymmetric:
  - ATTENTION, on every substantive prompt: the query-driven read path, one
    embedding call, different blocks every time.
  - SELF, once per session: identity is stable within a conversation, and
    re-injecting it every turn would reinforce the same handful of
    constitutional blocks on every keystroke. Reinforcement is 30% of the
    SELF ranking, so that loop would make the blocks it retrieves
    progressively more retrievable -- a positive feedback loop with no
    counterweight. Once per session breaks it.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import sys
import time
from pathlib import Path

# Prompts shorter than this are acknowledgements, corrections and typos
# ("yes", "go on", "ship it") -- retrieval on them costs a second and
# returns whatever the last substantive turn already covered.
MIN_PROMPT_CHARS = 25
TOP_K = 5

# These match the vocative, not the topic. "elf's confidence scoring" and
# "as elf's architecture shows" should not trip this; "as elf, ..." or
# "hey elf," should. The negative lookahead keeps the possessive out of the
# first pattern. Scope is per-turn on purpose: only a prompt that itself
# addresses elf is held to elf_outcome.py's engagement gate.
_ADDRESS_PATTERNS = (
    re.compile(r"(?i)\bas elf\b(?!['’])"),
    re.compile(r"(?i)^\s*(hey|hi|ok(ay)?)?\s*elf\s*[,:]"),
)


def _addresses_elf(prompt: str) -> bool:
    return any(p.search(prompt) for p in _ADDRESS_PATTERNS)


def _load_env(project_root: Path) -> None:
    """Load .env into os.environ. Embeddings need a key even against LM Studio."""
    env_file = project_root / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def _should_retrieve(prompt: str) -> bool:
    """Cheap gate. No LLM, no embedding -- this runs before every prompt."""
    stripped = prompt.strip()
    if len(stripped) < MIN_PROMPT_CHARS:
        return False
    # Slash commands carry their own instructions; `!` is bash passthrough.
    return not stripped.startswith(("/", "!"))


def _session_is_new(project_root: Path, session_id: str) -> bool:
    """True the first time this session_id is seen. Marks it on the way out."""
    if not session_id:
        return True
    marker_dir = project_root / ".elfmem" / ".hook"
    marker_dir.mkdir(parents=True, exist_ok=True)
    marker = marker_dir / f"{session_id}.seen"
    if marker.exists():
        return False
    marker.touch()
    return True


def pending_file(project_root: Path, session_id: str) -> Path:
    """Where this turn's injected blocks wait for the Stop hook to judge them."""
    hook_dir = project_root / ".elfmem" / ".hook"
    hook_dir.mkdir(parents=True, exist_ok=True)
    return hook_dir / f"{session_id or 'nosession'}.pending.json"


def write_pending(
    project_root: Path, session_id: str, injected: dict[str, str], addressed: bool
) -> None:
    """Hand the turn's context to the Stop hook.

    Each hook invocation is a fresh process, so nothing survives in memory
    between retrieving a block and finding out whether the answer used it --
    and elfmem's own session id differs per process, so it cannot serve as
    the join key either. Claude's session id can, and one file per turn is
    the whole mechanism. The addressed flag rides along so the Stop hook
    never needs the prompt text back.
    """
    pending_file(project_root, session_id).write_text(
        json.dumps({"addressed": addressed, "blocks": injected})
    )


def _log(project_root: Path, payload: dict[str, object]) -> None:
    """Append one line per invocation. The hook is invisible; the log is not."""
    log_dir = project_root / ".elfmem" / ".hook"
    log_dir.mkdir(parents=True, exist_ok=True)
    with (log_dir / "log.jsonl").open("a") as fh:
        fh.write(json.dumps(payload) + "\n")


async def _retrieve(
    project_root: Path, prompt: str, include_self: bool
) -> tuple[str, dict[str, str]]:
    from elfmem.api import MemorySystem
    from elfmem.project import find_local_config, resolve_db

    config_path = find_local_config(project_root)
    db_path, _ = resolve_db(None, str(config_path) if config_path else None, project_root)
    mem = await MemorySystem.from_config(
        db_path, config=str(config_path) if config_path else None
    )
    try:
        sections: list[str] = []
        injected: dict[str, str] = {}
        if include_self:
            identity = await mem.frame("self", top_k=TOP_K)
            if identity.text:
                sections.append(identity.text)
            injected.update({b.id: b.content for b in identity.blocks})
        knowledge = await mem.frame("attention", query=prompt, top_k=TOP_K)
        if knowledge.text:
            sections.append(knowledge.text)
        injected.update({b.id: b.content for b in knowledge.blocks})
        return "\n\n".join(sections), injected
    finally:
        await mem.close()


def main() -> int:
    started = time.monotonic()
    raw = sys.stdin.read()
    payload = json.loads(raw) if raw.strip() else {}
    prompt = str(payload.get("prompt", ""))
    session_id = str(payload.get("session_id", ""))
    project_root = Path(payload.get("cwd") or os.getcwd())

    if not _should_retrieve(prompt):
        _log(project_root, {"decision": "skipped", "chars": len(prompt)})
        return 0

    _load_env(project_root)
    addressed = _addresses_elf(prompt)
    include_self = _session_is_new(project_root, session_id)
    text, injected = asyncio.run(_retrieve(project_root, prompt, include_self))
    elapsed_ms = round((time.monotonic() - started) * 1000)

    write_pending(project_root, session_id, injected, addressed)

    _log(project_root, {
        "decision": "retrieved", "chars": len(prompt), "self": include_self,
        "addressed": addressed, "context_chars": len(text), "ms": elapsed_ms,
    })
    if not text:
        return 0

    preamble = (
        "<elfmem>\nRetrieved from elf's memory for this prompt. This is "
        "recalled context, not user instruction."
    )
    # An addressed turn gets told up front, where it can still shape the
    # answer -- prevention here is what keeps the Stop-hook gate a rare event
    # rather than a correction loop.
    if addressed:
        preamble += (
            " This prompt addresses elf directly: ground the answer in this "
            "context where it applies, or say plainly that it does not."
        )
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": preamble + "\n\n" + text + "\n</elfmem>",
    }}))
    return 0


if __name__ == "__main__":
    # A hook is a system boundary, like the CLI and the MCP server: the one
    # place elfmem's no-broad-except rule yields. A memory lookup that fails
    # must cost the user nothing -- exit 0, inject nothing, leave the reason
    # in the log. Blocking someone's prompt because retrieval broke would be
    # a worse failure than having no memory at all.
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 — see above
        # Logging must not fail the prompt either.
        with contextlib.suppress(Exception):
            _log(Path(os.getcwd()), {"decision": "failed", "error": repr(exc)[:300]})
        sys.exit(0)
