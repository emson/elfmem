# Plan: MCP Server + CLI for elfmem

**Status:** Design v3 — implementation-ready

---

## 1. Design Intent

elfmem is a library. MCP and CLI are **skins** — thin transport layers that make
`MemorySystem` accessible to agents without Python code.

This means: zero new public types, zero changes to the public API. The existing
`LearnResult`, `FrameResult`, `SystemStatus` etc. already have the `__str__` and
`to_dict()` that tool outputs need. We reuse them entirely.

```
Agent sees:          Library underneath:
───────────────────  ──────────────────────────────────
elfmem_remember  →   MemorySystem.learn()
elfmem_recall    →   MemorySystem.frame()
elfmem_status    →   MemorySystem.status()
elfmem_outcome   →   MemorySystem.outcome()
elfmem_curate    →   MemorySystem.curate()
elfmem_guide     →   get_guide()  (no DB needed)
```

One internal class (`SmartMemory`) adds auto-session and auto-consolidation and is
shared by both MCP and CLI. It is never exported.

---

## 2. New Files (3 source, 3 test)

```
src/elfmem/smart.py          ~75 lines   SmartMemory + format helpers
src/elfmem/mcp.py            ~75 lines   FastMCP server (6 tools)
src/elfmem/cli.py            ~135 lines  Typer CLI (7 commands)

tests/test_smart.py          ~35 tests
tests/test_mcp.py            ~12 tests
tests/test_cli.py            ~12 tests
```

**One change to existing files:** `pyproject.toml` only.
`__init__.py` is **unchanged** — SmartMemory is not public.

---

## 3. `src/elfmem/smart.py`

### Key design decisions resolved here

**`query or None` in recall:**
`MemorySystem.frame()` treats `query=None` as "no vector search, use non-similarity
weights". An empty string `""` would trigger vector search with an empty embedding —
meaningless noise. `recall()` converts `""` to `None` with `query or None`.

**Local inbox counter vs DB call per learn:**
`status()` is a DB round-trip. Calling it after every `learn()` doubles cost in
batch workflows. Instead, `open()` seeds `_pending` once from DB and `remember()`
increments it locally. Reset to zero after consolidation. Worst case: counter drifts
if another process writes the same DB. This is acceptable — elfmem targets single-agent
ownership and the drift causes consolidation a few blocks early or late, never wrong.

**`managed()` classmethod + `@asynccontextmanager`:**
For short-lived CLI use. Guarantees `close()` even on exception. Python 3.11 supports
`@classmethod @asynccontextmanager` correctly — outer decorator is `@classmethod`,
inner is `@asynccontextmanager`.

**`_format_block()` as a separate function:**
Extracted per coding principles (≤50 lines, composable). Testable in isolation.

```python
"""SmartMemory — auto-managed MemorySystem for MCP and CLI interfaces.

Internal to elfmem. Not part of the public API.
Session management and inbox consolidation are handled automatically.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from elfmem.api import MemorySystem
from elfmem.config import ElfmemConfig
from elfmem.types import (
    CurateResult,
    FrameResult,
    LearnResult,
    OutcomeResult,
    ScoredBlock,
    SystemStatus,
)


class SmartMemory:
    """MemorySystem with lazy session start and auto-consolidation.

    For tool interfaces only. Not for library users.
    """

    def __init__(
        self,
        system: MemorySystem,
        threshold: int,
        pending: int = 0,
    ) -> None:
        self._system = system
        self._threshold = threshold
        self._pending = pending

    @classmethod
    async def open(
        cls,
        db_path: str,
        config: ElfmemConfig | str | dict[str, Any] | None = None,
    ) -> SmartMemory:
        """Open a database and seed inbox count from current state."""
        system = await MemorySystem.from_config(db_path, config)
        status = await system.status()
        return cls(system, status.inbox_threshold, status.inbox_count)

    @classmethod
    @asynccontextmanager
    async def managed(
        cls,
        db_path: str,
        config: ElfmemConfig | str | dict[str, Any] | None = None,
    ) -> AsyncIterator[SmartMemory]:
        """Open → yield → close. For short-lived CLI invocations."""
        mem = await cls.open(db_path, config=config)
        try:
            yield mem
        finally:
            await mem.close()

    async def close(self) -> None:
        """End any active session and dispose the DB engine."""
        await self._system.end_session()
        await self._system.close()

    async def remember(
        self,
        content: str,
        tags: list[str] | None = None,
        category: str = "knowledge",
    ) -> LearnResult:
        """learn() + auto-session + auto-consolidate when inbox fills."""
        await self._system.begin_session()
        result = await self._system.learn(content, tags=tags, category=category)
        if result.status == "created":
            self._pending += 1
        if self._pending >= self._threshold:
            await self._system.consolidate()
            self._pending = 0
        return result

    async def recall(
        self,
        query: str,
        top_k: int = 5,
        frame: str = "attention",
    ) -> FrameResult:
        """frame() + auto-session. text field is ready for prompt injection."""
        await self._system.begin_session()
        return await self._system.frame(frame, query=query or None, top_k=top_k)

    async def status(self) -> SystemStatus:
        return await self._system.status()

    async def outcome(
        self,
        block_ids: list[str],
        signal: float,
        weight: float = 1.0,
        source: str = "",
    ) -> OutcomeResult:
        return await self._system.outcome(
            block_ids, signal, weight=weight, source=source
        )

    async def curate(self) -> CurateResult:
        return await self._system.curate()

    def guide(self, method: str | None = None) -> str:
        return self._system.guide(method)


# ── Formatting helpers ────────────────────────────────────────────────────────

def format_recall_response(result: FrameResult) -> dict[str, Any]:
    """Format FrameResult for agent tool responses.

    FrameResult.to_dict() is compact and omits per-block detail intentionally.
    Agents need block IDs to call outcome() — this function includes them.
    Used by both MCP and CLI --json output.
    """
    return {
        "text": result.text,
        "frame_name": result.frame_name,
        "cached": result.cached,
        "blocks": [_format_block(b) for b in result.blocks],
    }


def _format_block(block: ScoredBlock) -> dict[str, Any]:
    """Extract agent-relevant fields from a ScoredBlock."""
    return {
        "id": block.id,
        "content": block.content,
        "score": round(block.score, 3),
        "tags": block.tags,
    }
```

---

## 4. `src/elfmem/mcp.py`

### Key design decisions resolved here

**Module-level globals, not FastMCP state:**
FastMCP's state API (`ctx.fastmcp.state`, `server.state`) varies between versions and
requires `Context` in every tool signature, adding noise. Module-level `_memory` is
simpler, version-agnostic, and safe: MCP server is single-process, `_memory` is set
once at startup and cleared at shutdown.

**No `ctx: Context` in tools:**
Tools that don't need progress reporting, logging, or calling other tools don't need
the `Context` parameter. FastMCP 2.x makes it optional. Our tools only delegate to
`_memory` — no context needed.

**No per-tool try/except:**
`ElfmemError.__str__` already formats as `"message — Recovery: hint"`. FastMCP returns
unhandled exceptions as MCP error responses containing the exception string. The agent
receives the recovery hint automatically. Adding try/except in every tool would duplicate
this without adding value.

**`elfmem_guide` returns `str`, not `dict`:**
FastMCP serialises string returns as-is. Guide text is documentation, not data.
An agent using `elfmem_guide` wants readable text, not a wrapped dict.

```python
"""elfmem MCP server — adaptive memory as agent tools.

Start:  elfmem serve --db agent.db
        elfmem serve --db agent.db --config elfmem.yaml
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastmcp import FastMCP

from elfmem.smart import SmartMemory, format_recall_response

_memory: SmartMemory | None = None


@asynccontextmanager
async def _lifespan(server: FastMCP) -> AsyncIterator[None]:
    global _memory
    _memory = await SmartMemory.open(_db_path, config=_config_path)
    try:
        yield
    finally:
        await _memory.close()
        _memory = None


mcp = FastMCP("elfmem", lifespan=_lifespan)


def _mem() -> SmartMemory:
    """Return active SmartMemory. Fails fast if server not initialised."""
    if _memory is None:
        raise RuntimeError("elfmem MCP server not initialised.")
    return _memory


@mcp.tool()
async def elfmem_remember(
    content: str,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Store knowledge for future retrieval.

    Call when the agent discovers a fact, preference, decision, or observation
    worth keeping across sessions. Sessions and consolidation are automatic.
    Returns: block_id, status ("created"|"duplicate_rejected"|"near_duplicate_superseded").
    """
    result = await _mem().remember(content, tags=tags)
    return result.to_dict()


@mcp.tool()
async def elfmem_recall(
    query: str,
    top_k: int = 5,
    frame: str = "attention",
) -> dict[str, Any]:
    """Retrieve relevant knowledge, rendered for prompt injection.

    Use result.text directly in your LLM prompt. Block IDs in result.blocks
    can be passed to elfmem_outcome to record outcome feedback later.
    frame: "attention" (query-driven, default) | "self" (identity) | "task" (goals).
    """
    result = await _mem().recall(query, top_k=top_k, frame=frame)
    return format_recall_response(result)


@mcp.tool()
async def elfmem_status() -> dict[str, Any]:
    """Memory health snapshot. Check the suggestion field for recommended action."""
    result = await _mem().status()
    return result.to_dict()


@mcp.tool()
async def elfmem_outcome(
    block_ids: list[str],
    signal: float,
    weight: float = 1.0,
    source: str = "",
) -> dict[str, Any]:
    """Update block confidence from a domain outcome signal.

    signal: 0.0–1.0. Use block IDs from a previous elfmem_recall response.
    0.8–1.0 → reinforces (decay resets). 0.2–0.8 → neutral. 0.0–0.2 → penalises.
    """
    result = await _mem().outcome(block_ids, signal, weight=weight, source=source)
    return result.to_dict()


@mcp.tool()
async def elfmem_curate() -> dict[str, Any]:
    """Archive decayed blocks, prune weak edges, reinforce top knowledge.

    Runs automatically on schedule after consolidation.
    Call manually only if retrieval quality visibly degrades.
    """
    result = await _mem().curate()
    return result.to_dict()


@mcp.tool()
async def elfmem_guide(method: str | None = None) -> str:
    """Detailed documentation for a specific operation, or the full overview.

    method: e.g. "remember", "recall", "outcome". None returns overview table.
    """
    return _mem().guide(method)


# ── Entry point ───────────────────────────────────────────────────────────────

_db_path: str = ""
_config_path: str | None = None


def main(db_path: str, config_path: str | None = None) -> None:
    """Start the MCP server. Called by `elfmem serve`."""
    global _db_path, _config_path
    _db_path = db_path
    _config_path = config_path
    mcp.run()
```

---

## 5. `src/elfmem/cli.py`

### Key design decisions resolved here

**`recall` text output uses `result.text`, not `str(result)`:**
`FrameResult.__str__` returns `"attention frame: 5 blocks returned."` — the frame
summary, not the content. CLI recall text output must use `result.text` explicitly.

**`guide` command needs no database:**
`MemorySystem.guide()` delegates to `get_guide()` which reads a static dict — no DB
access. Importing `get_guide` directly avoids opening a DB connection for a docs
lookup. No `--db` flag, no async.

**`_run()` centralises the error boundary:**
All commands call `_run(coroutine)` instead of raw `asyncio.run()`. The boundary
is one place: `ElfmemError` is caught and formatted, all other exceptions surface
as stack traces per coding principles (errors must be visible, never hidden).

**`serve` defers MCP import:**
`fastmcp` is an optional extra. The `serve` command only imports `elfmem.mcp` when
invoked. Users who installed `elfmem[cli]` but not `elfmem[mcp]` get a clear error
message at the point of use.

**Async helpers are private and typed:**
Each command has a paired `_verb(...)` async function. This separation makes the
async logic independently testable (though per testing_principles, we test via CLI
rather than calling helpers directly). Complete type hints throughout.

```python
"""elfmem CLI — adaptive memory as shell commands.

Commands:
    elfmem remember CONTENT [--tags t1,t2] [--category C] [--json]
    elfmem recall QUERY [--top-k N] [--frame F] [--json]
    elfmem status [--json]
    elfmem outcome BLOCK_IDS SIGNAL [--weight N] [--source LABEL] [--json]
    elfmem curate [--json]
    elfmem guide [METHOD]
    elfmem serve [--config PATH]

Database: --db PATH  or  ELFMEM_DB env var (all commands except guide and serve).
Config:   --config PATH  or  ELFMEM_CONFIG env var (optional).
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Annotated, Any

try:
    import typer
except ImportError:
    raise SystemExit(
        "elfmem CLI requires the 'cli' extra:\n"
        "  pip install 'elfmem[cli]'  or  uv add 'elfmem[cli]'"
    )

from elfmem.exceptions import ElfmemError
from elfmem.guide import get_guide
from elfmem.smart import SmartMemory, format_recall_response
from elfmem.types import CurateResult, FrameResult, LearnResult, OutcomeResult, SystemStatus

app = typer.Typer(
    name="elfmem",
    help="Adaptive memory for AI agents.",
    no_args_is_help=True,
)

# ── Shared helpers ────────────────────────────────────────────────────────────

def _resolve_db(db: str | None) -> str:
    """Resolve DB path from argument or ELFMEM_DB env var. Exits if missing."""
    resolved = db or os.getenv("ELFMEM_DB", "")
    if not resolved:
        typer.echo("Error: --db is required (or set ELFMEM_DB env var)", err=True)
        raise typer.Exit(1)
    return resolved


def _resolve_config(config: str | None) -> str | None:
    """Resolve config path from argument or ELFMEM_CONFIG env var."""
    return config or os.getenv("ELFMEM_CONFIG") or None


def _run(coro: Any) -> Any:
    """Execute an async operation. Catches ElfmemError at the CLI boundary."""
    try:
        return asyncio.run(coro)
    except ElfmemError as e:
        typer.echo(f"Error: {e.args[0]}\nRecovery: {e.recovery}", err=True)
        raise typer.Exit(1)


def _json(data: Any) -> None:
    """Print data as indented JSON."""
    typer.echo(json.dumps(data, indent=2))

# ── Commands ──────────────────────────────────────────────────────────────────

@app.command()
def remember(
    content: str,
    tags: Annotated[str | None, typer.Option("--tags", help="Comma-separated tags")] = None,
    category: Annotated[str, typer.Option("--category", help="Block category")] = "knowledge",
    db: Annotated[str | None, typer.Option("--db", envvar="ELFMEM_DB", help="Database path")] = None,
    config: Annotated[str | None, typer.Option("--config", envvar="ELFMEM_CONFIG", help="Config YAML")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Output JSON")] = False,
) -> None:
    """Store knowledge for future retrieval."""
    tag_list = [t.strip() for t in tags.split(",")] if tags else None
    result: LearnResult = _run(
        _remember(_resolve_db(db), _resolve_config(config), content, tag_list, category)
    )
    _json(result.to_dict()) if json_output else typer.echo(str(result))


@app.command()
def recall(
    query: str,
    top_k: Annotated[int, typer.Option("--top-k", help="Max results")] = 5,
    frame: Annotated[str, typer.Option("--frame", help="attention|self|task")] = "attention",
    db: Annotated[str | None, typer.Option("--db", envvar="ELFMEM_DB")] = None,
    config: Annotated[str | None, typer.Option("--config", envvar="ELFMEM_CONFIG")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Retrieve relevant knowledge, rendered for prompt injection."""
    result: FrameResult = _run(
        _recall(_resolve_db(db), _resolve_config(config), query, top_k, frame)
    )
    # NOTE: str(result) gives frame summary ("attention frame: 5 blocks returned.")
    # For text mode, output the rendered content agents inject into prompts.
    _json(format_recall_response(result)) if json_output else typer.echo(result.text)


@app.command()
def status(
    db: Annotated[str | None, typer.Option("--db", envvar="ELFMEM_DB")] = None,
    config: Annotated[str | None, typer.Option("--config", envvar="ELFMEM_CONFIG")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """System health and suggested next action."""
    result: SystemStatus = _run(_status(_resolve_db(db), _resolve_config(config)))
    _json(result.to_dict()) if json_output else typer.echo(str(result))


@app.command()
def outcome(
    block_ids: str,
    signal: float,
    weight: Annotated[float, typer.Option("--weight", help="Observation weight")] = 1.0,
    source: Annotated[str, typer.Option("--source", help="Audit label")] = "",
    db: Annotated[str | None, typer.Option("--db", envvar="ELFMEM_DB")] = None,
    config: Annotated[str | None, typer.Option("--config", envvar="ELFMEM_CONFIG")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Record domain outcome signal [0.0-1.0] to update block confidence."""
    ids = [bid.strip() for bid in block_ids.split(",")]
    result: OutcomeResult = _run(
        _outcome(_resolve_db(db), _resolve_config(config), ids, signal, weight, source)
    )
    _json(result.to_dict()) if json_output else typer.echo(str(result))


@app.command()
def curate(
    db: Annotated[str | None, typer.Option("--db", envvar="ELFMEM_DB")] = None,
    config: Annotated[str | None, typer.Option("--config", envvar="ELFMEM_CONFIG")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Archive decayed blocks, prune weak edges, reinforce top knowledge."""
    result: CurateResult = _run(_curate(_resolve_db(db), _resolve_config(config)))
    _json(result.to_dict()) if json_output else typer.echo(str(result))


@app.command()
def guide(
    method: Annotated[str | None, typer.Argument(help="Operation name, or blank for overview")] = None,
) -> None:
    """Show documentation for a specific operation, or the full overview.

    Does not require a database connection.
    """
    typer.echo(get_guide(method))


@app.command()
def serve(
    db: Annotated[str, typer.Option("--db", envvar="ELFMEM_DB", help="Database path")] = ...,
    config: Annotated[str | None, typer.Option("--config", envvar="ELFMEM_CONFIG")] = None,
) -> None:
    """Start the elfmem MCP server for agent tool integration."""
    try:
        from elfmem.mcp import main as mcp_main
    except ImportError:
        typer.echo(
            "MCP server requires the 'mcp' extra:\n"
            "  pip install 'elfmem[mcp]'  or  uv add 'elfmem[mcp]'",
            err=True,
        )
        raise typer.Exit(1)
    mcp_main(db_path=db, config_path=config)


def main() -> None:
    """Package entry point."""
    app()

# ── Async helpers ─────────────────────────────────────────────────────────────

async def _remember(
    db_path: str,
    config: str | None,
    content: str,
    tags: list[str] | None,
    category: str,
) -> LearnResult:
    async with SmartMemory.managed(db_path, config=config) as mem:
        return await mem.remember(content, tags=tags, category=category)


async def _recall(
    db_path: str,
    config: str | None,
    query: str,
    top_k: int,
    frame: str,
) -> FrameResult:
    async with SmartMemory.managed(db_path, config=config) as mem:
        return await mem.recall(query, top_k=top_k, frame=frame)


async def _status(db_path: str, config: str | None) -> SystemStatus:
    async with SmartMemory.managed(db_path, config=config) as mem:
        return await mem.status()


async def _outcome(
    db_path: str,
    config: str | None,
    block_ids: list[str],
    signal: float,
    weight: float,
    source: str,
) -> OutcomeResult:
    async with SmartMemory.managed(db_path, config=config) as mem:
        return await mem.outcome(block_ids, signal, weight=weight, source=source)


async def _curate(db_path: str, config: str | None) -> CurateResult:
    async with SmartMemory.managed(db_path, config=config) as mem:
        return await mem.curate()
```

---

## 6. `pyproject.toml` Changes

```toml
[project.optional-dependencies]
mcp = ["fastmcp>=2.0"]
cli = ["typer>=0.12"]
tools = ["fastmcp>=2.0", "typer>=0.12"]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "mypy>=1.8",
    "ruff>=0.3",
    "types-PyYAML",
    "fastmcp>=2.0",    # needed to test mcp.py
    "typer>=0.12",     # needed to test cli.py
]

[project.scripts]
elfmem = "elfmem.cli:main"
```

---

## 7. Testing

### Approach (from testing_principles.md)

**Test behaviour, not implementation.**
`SmartMemory._pending` is private state — never assert on it.
Instead, observe effects: does `status().inbox_count` drop to zero after threshold?

**Construct SmartMemory directly in tests.**
`SmartMemory.open()` opens a real DB file. Tests use the existing `test_engine` and
mock services to build a `MemorySystem`, then pass it to `SmartMemory.__init__`
directly. This matches the existing pattern in `test_agent_api.py`.

**CLI: use CliRunner, not async helpers.**
Per testing_principles, test CLI at the command level. Test that running
`elfmem remember "x" --db test.db` produces correct output, not that `_remember()`
returns the right type.

---

### `tests/test_smart.py`

**Fixture:**

```python
@pytest.fixture
async def smart_memory(test_engine, mock_llm, mock_embedding) -> SmartMemory:
    """SmartMemory backed by in-memory test engine. threshold=3 for fast consolidation."""
    system = MemorySystem(
        engine=test_engine,
        llm_service=mock_llm,
        embedding_service=mock_embedding,
    )
    mem = SmartMemory(system, threshold=3, pending=0)
    yield mem
    await mem.close()
```

**Why threshold=3?**
Default is 10. Tests would need 10 `remember()` calls to trigger auto-consolidation.
Threshold=3 tests the same behaviour with 3 calls.

**Tests:**

```python
class TestSmartMemoryLifecycle:
    async def test_managed_yields_smart_memory(tmp_path):
        # Arrange/Act
        async with SmartMemory.managed(str(tmp_path / "test.db")) as mem:
            # Assert
            assert isinstance(mem, SmartMemory)

    async def test_close_safe_without_session(smart_memory):
        # close() on a never-used instance must not raise
        await smart_memory.close()  # should not raise

    async def test_close_safe_to_call_twice(smart_memory):
        await smart_memory.close()
        await smart_memory.close()  # idempotent via end_session() returning 0.0


class TestRemember:
    async def test_remember_returns_learn_result(smart_memory):
        result = await smart_memory.remember("User prefers dark mode")
        assert result.status == "created"

    async def test_remember_duplicate_returns_rejected_status(smart_memory):
        await smart_memory.remember("same content")
        result = await smart_memory.remember("same content")
        assert result.status == "duplicate_rejected"

    async def test_remember_consolidates_when_threshold_reached(smart_memory):
        # Arrange: threshold is 3. Learn 3 unique blocks.
        for i in range(3):
            await smart_memory.remember(f"fact number {i}")
        # Act: check memory state
        result = await smart_memory.status()
        # Assert: inbox drained (consolidation ran)
        assert result.inbox_count == 0

    async def test_remember_does_not_consolidate_below_threshold(smart_memory):
        # Learn 2 blocks (threshold is 3)
        for i in range(2):
            await smart_memory.remember(f"fact below threshold {i}")
        result = await smart_memory.status()
        assert result.inbox_count == 2


class TestRecall:
    async def test_recall_returns_frame_result(smart_memory):
        result = await smart_memory.recall("preferences")
        assert hasattr(result, "text")
        assert hasattr(result, "blocks")

    async def test_recall_empty_db_returns_empty_text(smart_memory):
        result = await smart_memory.recall("anything")
        assert result.text == "" or isinstance(result.text, str)

    async def test_recall_text_non_empty_after_blocks_consolidated(smart_memory):
        # Learn + consolidate (threshold=3)
        for i in range(3):
            await smart_memory.remember(f"user prefers option {i}")
        # Now recall
        result = await smart_memory.recall("user preferences")
        assert isinstance(result.text, str)

    async def test_recall_empty_query_treated_as_none(smart_memory):
        # Empty string query should not raise — treated as queryless recall
        result = await smart_memory.recall("")
        assert result is not None


class TestDelegation:
    async def test_status_returns_system_status(smart_memory):
        from elfmem.types import SystemStatus
        result = await smart_memory.status()
        assert isinstance(result, SystemStatus)

    async def test_guide_returns_string(smart_memory):
        result = smart_memory.guide()
        assert isinstance(result, str)
        assert len(result) > 0

    async def test_guide_method_returns_method_doc(smart_memory):
        result = smart_memory.guide("learn")
        assert "learn" in result


class TestFormatRecallResponse:
    """Tests for the format_recall_response() pure function."""

    def test_includes_text(self):
        result = _make_frame_result(text="## Context\nsome content")
        assert format_recall_response(result)["text"] == "## Context\nsome content"

    def test_includes_block_ids(self):
        result = _make_frame_result(block_id="abc123")
        blocks = format_recall_response(result)["blocks"]
        assert blocks[0]["id"] == "abc123"

    def test_scores_rounded_to_3dp(self):
        result = _make_frame_result(score=0.123456789)
        blocks = format_recall_response(result)["blocks"]
        assert blocks[0]["score"] == 0.123

    def test_empty_blocks_returns_empty_list(self):
        result = _make_frame_result(blocks=[])
        assert format_recall_response(result)["blocks"] == []

    def test_includes_frame_name(self):
        result = _make_frame_result(frame_name="attention")
        assert format_recall_response(result)["frame_name"] == "attention"
```

Where `_make_frame_result()` is a test helper that constructs minimal `FrameResult` /
`ScoredBlock` instances.

---

### `tests/test_mcp.py`

**Philosophy:**
Tests call the tool functions directly using `monkeypatch` to inject a mock
`SmartMemory`. No MCP transport is invoked. This follows "don't test third-party
library internals" (FastMCP is not under test here).

```python
@pytest.fixture
def mock_mem(monkeypatch):
    """Inject a mock SmartMemory into the mcp module."""
    import elfmem.mcp as mcp_module
    from unittest.mock import AsyncMock, MagicMock
    mem = AsyncMock(spec=SmartMemory)
    mem.guide = MagicMock(return_value="guide text")
    monkeypatch.setattr(mcp_module, "_memory", mem)
    return mem


class TestMcpTools:
    async def test_remember_returns_dict_with_block_id(mock_mem):
        from elfmem.mcp import elfmem_remember
        mock_mem.remember.return_value = LearnResult(block_id="abc123", status="created")
        result = await elfmem_remember(content="test fact")
        assert result["block_id"] == "abc123"
        assert result["status"] == "created"

    async def test_recall_returns_dict_with_text(mock_mem):
        from elfmem.mcp import elfmem_recall
        mock_mem.recall.return_value = FrameResult(text="context", blocks=[], frame_name="attention")
        result = await elfmem_recall(query="test")
        assert result["text"] == "context"
        assert "blocks" in result

    async def test_recall_includes_block_ids(mock_mem):
        from elfmem.mcp import elfmem_recall
        block = _make_scored_block(id="xyz789")
        mock_mem.recall.return_value = FrameResult(text="text", blocks=[block], frame_name="attention")
        result = await elfmem_recall(query="test")
        assert result["blocks"][0]["id"] == "xyz789"

    async def test_status_returns_dict_with_health(mock_mem):
        from elfmem.mcp import elfmem_status
        mock_mem.status.return_value = _make_system_status(health="good")
        result = await elfmem_status()
        assert result["health"] == "good"

    async def test_outcome_returns_dict_with_blocks_updated(mock_mem):
        from elfmem.mcp import elfmem_outcome
        mock_mem.outcome.return_value = OutcomeResult(
            blocks_updated=2, mean_confidence_delta=0.05,
            edges_reinforced=1, blocks_penalized=0,
        )
        result = await elfmem_outcome(block_ids=["a", "b"], signal=0.9)
        assert result["blocks_updated"] == 2

    async def test_guide_returns_string(mock_mem):
        from elfmem.mcp import elfmem_guide
        result = await elfmem_guide(method=None)
        assert isinstance(result, str)
```

---

### `tests/test_cli.py`

**Philosophy:**
Use `typer.testing.CliRunner`. Test that commands produce correct output and exit
codes. Mock `SmartMemory.managed()` to avoid real DB I/O in unit tests.

```python
from typer.testing import CliRunner
from elfmem.cli import app

runner = CliRunner()


@pytest.fixture
def mock_managed(monkeypatch):
    """Mock SmartMemory.managed() to return a fake SmartMemory."""
    from unittest.mock import AsyncMock, patch
    mem = AsyncMock(spec=SmartMemory)
    mem.remember.return_value = LearnResult(block_id="abc12345", status="created")
    mem.recall.return_value = FrameResult(text="recalled context", blocks=[], frame_name="attention")
    mem.status.return_value = _make_system_status(health="good")
    mem.curate.return_value = CurateResult(archived=0, edges_pruned=0, reinforced=0)
    mem.outcome.return_value = OutcomeResult(blocks_updated=1, mean_confidence_delta=0.0,
                                              edges_reinforced=0, blocks_penalized=0)
    # Patch managed() to yield the mock
    from contextlib import asynccontextmanager
    @asynccontextmanager
    async def _managed(*args, **kwargs):
        yield mem
    monkeypatch.setattr(SmartMemory, "managed", _managed)
    return mem


class TestRememberCommand:
    def test_text_output_shows_stored(mock_managed):
        result = runner.invoke(app, ["remember", "test fact", "--db", "test.db"])
        assert result.exit_code == 0
        assert "Stored" in result.output

    def test_json_output_has_block_id(mock_managed):
        result = runner.invoke(app, ["remember", "fact", "--db", "test.db", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "block_id" in data

    def test_missing_db_exits_nonzero():
        result = runner.invoke(app, ["remember", "fact"])  # no --db
        assert result.exit_code != 0
        assert "ELFMEM_DB" in result.output


class TestRecallCommand:
    def test_text_output_is_rendered_content(mock_managed):
        result = runner.invoke(app, ["recall", "query", "--db", "test.db"])
        assert result.exit_code == 0
        assert "recalled context" in result.output  # result.text, not frame summary

    def test_json_output_has_blocks_key(mock_managed):
        result = runner.invoke(app, ["recall", "query", "--db", "test.db", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "blocks" in data
        assert "text" in data


class TestStatusCommand:
    def test_status_exits_zero(mock_managed):
        result = runner.invoke(app, ["status", "--db", "test.db"])
        assert result.exit_code == 0

    def test_status_json_has_health_key(mock_managed):
        result = runner.invoke(app, ["status", "--db", "test.db", "--json"])
        data = json.loads(result.output)
        assert "health" in data


class TestGuideCommand:
    def test_guide_no_db_required():
        # guide works without --db
        result = runner.invoke(app, ["guide"])
        assert result.exit_code == 0
        assert len(result.output) > 0

    def test_guide_method_shows_doc():
        result = runner.invoke(app, ["guide", "learn"])
        assert result.exit_code == 0
        assert "learn" in result.output.lower()


class TestErrorHandling:
    def test_elfmem_error_shows_recovery(monkeypatch):
        from elfmem.exceptions import ElfmemError
        from contextlib import asynccontextmanager
        @asynccontextmanager
        async def _bad_managed(*args, **kwargs):
            mem = AsyncMock(spec=SmartMemory)
            mem.remember.side_effect = ElfmemError("bad frame", recovery="try again")
            yield mem
        monkeypatch.setattr(SmartMemory, "managed", _bad_managed)
        result = runner.invoke(app, ["remember", "x", "--db", "test.db"])
        assert result.exit_code != 0
        assert "Recovery:" in result.output


class TestHelp:
    def test_help_lists_all_commands():
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        for cmd in ("remember", "recall", "status", "outcome", "curate", "guide", "serve"):
            assert cmd in result.output
```

---

## 8. Implementation Checklist

Per coding_principles.md, verify each file before it is considered complete:

**`smart.py`**
- [ ] All functions ≤50 lines
- [ ] Complete type hints (including `_format_block`)
- [ ] No try/except (errors bubble up)
- [ ] `query or None` in `recall()` — handles empty string
- [ ] `managed()` uses `@classmethod @asynccontextmanager` (outer → inner order)
- [ ] `close()` safe to call without active session

**`mcp.py`**
- [ ] Global `_db_path`, `_config_path` set before `mcp.run()`
- [ ] `_mem()` fails fast (raises `RuntimeError` if `_memory is None`)
- [ ] `elfmem_guide` returns `str` (not `dict`)
- [ ] No `ctx: Context` in tool signatures (not needed)
- [ ] No per-tool try/except (ElfmemError.__str__ includes recovery)

**`cli.py`**
- [ ] `guide` command has no `--db` parameter
- [ ] `recall` text output uses `result.text` (not `str(result)`)
- [ ] `_run()` catches only `ElfmemError`, lets all others surface
- [ ] `serve` command defers MCP import with user-friendly ImportError message
- [ ] All async helpers have complete type hints
- [ ] `Annotated[...]` style for all Typer options

**All three files**
- [ ] `uv run pytest tests/ -x` — all tests pass, no regressions
- [ ] `uv run mypy src/` — clean under strict mode
- [ ] `uv run ruff check src/` — clean

---

## 9. Implementation Order

```
Step 0  pyproject.toml: add optional extras + dev deps + entry point
        uv sync

Step 1  src/elfmem/smart.py
        tests/test_smart.py
        Verify: uv run pytest tests/test_smart.py -v

Step 2  src/elfmem/mcp.py
        tests/test_mcp.py
        Verify: uv run pytest tests/test_mcp.py -v

Step 3  src/elfmem/cli.py
        tests/test_cli.py
        Verify: uv run pytest tests/test_cli.py -v
        Verify: elfmem --help (all 7 commands listed)

Step 4  Full regression + smoke test
        Verify: uv run pytest tests/ -x (no regressions)
        Verify: ELFMEM_DB=/tmp/smoke.db elfmem remember "hello world"
        Verify: ELFMEM_DB=/tmp/smoke.db elfmem recall "hello"
        Verify: ELFMEM_DB=/tmp/smoke.db elfmem status --json
        Verify: elfmem guide recall
```
