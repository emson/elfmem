#!/usr/bin/env python
"""PreCompact / SessionEnd hook — distill what a per-turn nudge can't catch.

elf_context.py's capture gate (increment 1) only fires when a prompt itself
says "remember that" or "that's outdated" -- a narrow, deliberately
high-precision regex. Real capture-worthy content often shows up wrapped in
ordinary conversation with no trigger phrase at all: a fact stated in passing,
a decision that emerges across several turns, a preference implied rather
than declared. Catching that needs judgement a regex cannot do -- so this
hook, unlike the other two, makes its own LLM call rather than staying
zero-cost.

Fires at a natural pause rather than every turn, matching the "consolidation
through rest" rhythm the rest of elfmem is built on:
  - PreCompact: right before context gets compacted -- the moment detail
    would otherwise be silently lost, the actual threat this hook exists to
    prevent. Cannot inject content back into the conversation, but does not
    need to -- this hook writes to memory directly via remember(), the same
    way a person would, not through the conversation.
  - SessionEnd: backstop for a session that ends (including /clear) before
    ever hitting the auto-compact threshold.

Both share a payload shape with Stop/UserPromptSubmit (session_id, cwd,
transcript_path), so one script and one marker-file scheme covers both.

Per-session marker (`{session_id}.distilled`) tracks how many transcript
lines have already been distilled, so repeated firings in one long session
(several compactions, or a compaction followed by session end) only ever
send the NEW slice to the LLM -- never redundant, never silently dropped.
The offset only advances after a slice is successfully distilled (LLM call
returned, whether it found 0 or 5 candidates); a network failure leaves the
marker untouched so the same slice is retried next time rather than lost.

Wiring (personal, `.claude/` is gitignored) -- in `.claude/settings.local.json`:

    "hooks": {
      "PreCompact": [{"hooks": [{"type": "command", "command": "<venv>/bin/python",
        "args": ["<project>/scripts/hooks/elf_distill.py"], "timeout": 45}]}],
      "SessionEnd": [{"hooks": [{"type": "command", "command": "<venv>/bin/python",
        "args": ["<project>/scripts/hooks/elf_distill.py"], "timeout": 45}]}]
    }
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from elf_context import _load_env, _log  # noqa: E402

# Below this many new characters, an LLM call isn't worth its cost -- the
# marker does NOT advance on this path, so the thin slice accumulates with
# whatever comes next instead of being silently dropped.
MIN_NEW_CHARS = 200
# Local-model context budget. Keep the tail: the most recent material is
# likeliest to be undistilled, since anything trigger-worthy earlier was
# probably already caught by the per-turn capture gate.
MAX_TRANSCRIPT_CHARS = 8000
MAX_CANDIDATES = 5

DISTILL_PROMPT_TEMPLATE = """You are reflecting on part of a conversation to \
decide what belongs in your long-term memory.

Your identity:
{self_text}

Conversation since your last reflection:
{conversation_text}

Extract only durable facts, decisions, corrections, or stated preferences \
that would be worth recalling in a future, unrelated conversation. Skip \
small talk, routine tool output, and anything already obviously covered by \
your identity above. Return at most {max_candidates} items -- fewer is \
fine, and an empty list is a correct answer if nothing here is worth \
keeping.

Respond with ONLY a JSON array, no other text. Each item: {{"content": \
"the fact, self-contained", "cue": "one line: the situation where a \
future you should recall this", "tags": ["short-tag", ...]}}. Return [] \
if nothing qualifies."""


def marker_file(project_root: Path, session_id: str) -> Path:
    hook_dir = project_root / ".elfmem" / ".hook"
    hook_dir.mkdir(parents=True, exist_ok=True)
    return hook_dir / f"{session_id or 'nosession'}.distilled"


def read_processed(marker: Path) -> int:
    if not marker.exists():
        return 0
    with contextlib.suppress(ValueError):
        return int(marker.read_text().strip())
    return 0


def write_processed(marker: Path, n: int) -> None:
    marker.write_text(str(n))


def _all_rows(transcript_path: Path) -> list[dict]:
    if not transcript_path.exists():
        return []
    rows: list[dict] = []
    for line in transcript_path.read_text().splitlines():
        if not line.strip():
            continue
        with contextlib.suppress(json.JSONDecodeError):
            rows.append(json.loads(line))
    return rows


def new_rows(transcript_path: Path, processed: int) -> list[dict]:
    return _all_rows(transcript_path)[processed:]


def extract_conversation_text(rows: list[dict]) -> str:
    """Both speakers' text, tool calls and results excluded.

    A user's own correction or preference statement is exactly what
    distillation exists to catch, so -- unlike turn_response in
    elf_outcome.py, which deliberately keeps only assistant prose -- this
    keeps both sides of the conversation.
    """
    parts: list[str] = []
    for row in rows:
        content = row.get("message", {}).get("content")
        if row.get("type") == "user" and isinstance(content, str):
            parts.append(f"User: {content}")
        elif row.get("type") == "assistant" and isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(f"Assistant: {block.get('text', '')}")
    return "\n".join(parts)


def build_prompt(self_text: str, conversation_text: str) -> str:
    return DISTILL_PROMPT_TEMPLATE.format(
        self_text=self_text or "(no constitutional identity loaded)",
        conversation_text=conversation_text,
        max_candidates=MAX_CANDIDATES,
    )


def parse_candidates(raw: str) -> list[dict]:
    """Validate and cap the LLM's JSON output.

    A malformed item is skipped, not fatal -- one bad item in the array must
    not discard four good ones. Markdown-fence stripping reuses the adapter's
    own helper: local Gemma via LM Studio wraps JSON in fences even when
    asked not to, a known quirk already solved once in this codebase.
    """
    from elfmem.adapters.openai import _extract_json

    with contextlib.suppress(json.JSONDecodeError):
        data = json.loads(_extract_json(raw))
        if not isinstance(data, list):
            return []
        candidates: list[dict] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            cue = item.get("cue")
            if not isinstance(content, str) or not content.strip():
                continue
            if not isinstance(cue, str) or not cue.strip():
                continue
            tags = item.get("tags")
            candidates.append({
                "content": content.strip(),
                "cue": cue.strip(),
                "tags": [t for t in tags if isinstance(t, str)] if isinstance(tags, list) else [],
            })
            if len(candidates) >= MAX_CANDIDATES:
                break
        return candidates
    return []


async def _call_llm(
    *, base_url: str, model: str, temperature: float, max_tokens: int,
    timeout: int, api_key: str | None, prompt: str,
) -> str:
    import openai

    client = openai.AsyncOpenAI(base_url=base_url, api_key=api_key or "not-needed", timeout=timeout)
    response = await client.chat.completions.create(
        model=model, temperature=temperature, max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content or ""


async def _distill(project_root: Path, conversation_text: str) -> dict:
    from elfmem.api import MemorySystem
    from elfmem.config import ElfmemConfig
    from elfmem.project import find_local_config, resolve_db

    config_path = find_local_config(project_root)
    cfg = ElfmemConfig.from_yaml(str(config_path)) if config_path else ElfmemConfig()
    db_path, _ = resolve_db(None, str(config_path) if config_path else None, project_root)
    mem = await MemorySystem.from_config(db_path, config=str(config_path) if config_path else None)
    try:
        identity = await mem.frame("self", top_k=10)
        prompt = build_prompt(identity.text, conversation_text)
        raw = await _call_llm(
            base_url=cfg.llm.base_url, model=cfg.llm.model, temperature=cfg.llm.temperature,
            max_tokens=cfg.llm.max_tokens, timeout=cfg.llm.timeout,
            api_key=os.environ.get("OPENAI_API_KEY"), prompt=prompt,
        )
        candidates = parse_candidates(raw)
        stored: list[str] = []
        for c in candidates:
            result = await mem.remember(c["content"], cue=c["cue"], tags=c["tags"] or None)
            stored.append(result.block_id)
        return {"candidates": len(candidates), "stored": stored}
    finally:
        await mem.close()


def main() -> int:
    import asyncio

    started = time.monotonic()
    raw = sys.stdin.read()
    payload = json.loads(raw) if raw.strip() else {}
    session_id = str(payload.get("session_id", ""))
    project_root = Path(payload.get("cwd") or os.getcwd())
    transcript_path = Path(payload.get("transcript_path", ""))

    marker = marker_file(project_root, session_id)
    processed = read_processed(marker)
    rows = new_rows(transcript_path, processed)
    if not rows:
        return 0  # nothing new since the last distillation pass

    text = extract_conversation_text(rows)
    if len(text) < MIN_NEW_CHARS:
        _log(project_root, {
            "decision": "skipped", "hook": "distill",
            "reason": "too little new content", "chars": len(text),
        })
        return 0  # marker intentionally NOT advanced -- accumulate, don't drop

    text = text[-MAX_TRANSCRIPT_CHARS:]
    _load_env(project_root)
    result = asyncio.run(_distill(project_root, text))
    write_processed(marker, processed + len(rows))  # only on success

    _log(project_root, {
        "decision": "distilled", "candidates": result["candidates"], "stored": result["stored"],
        "ms": round((time.monotonic() - started) * 1000),
    })
    return 0


if __name__ == "__main__":
    # Same system-boundary contract as the other two hooks: a failure here
    # must never cost the user anything, and here specifically must never
    # advance the marker -- a network hiccup should be retried next time,
    # not treated as "distilled, nothing found."
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 — see above
        with contextlib.suppress(Exception):
            _log(Path(os.getcwd()), {
                "decision": "failed", "hook": "distill", "error": repr(exc)[:300],
            })
        sys.exit(0)
