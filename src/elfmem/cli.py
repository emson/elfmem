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
    tags: Annotated[
        str | None, typer.Option("--tags", help="Comma-separated tags")
    ] = None,
    category: Annotated[
        str, typer.Option("--category", help="Block category")
    ] = "knowledge",
    db: Annotated[
        str | None, typer.Option("--db", envvar="ELFMEM_DB", help="Database path")
    ] = None,
    config: Annotated[
        str | None, typer.Option("--config", envvar="ELFMEM_CONFIG", help="Config YAML")
    ] = None,
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
    frame: Annotated[
        str, typer.Option("--frame", help="attention|self|task")
    ] = "attention",
    db: Annotated[str | None, typer.Option("--db", envvar="ELFMEM_DB")] = None,
    config: Annotated[
        str | None, typer.Option("--config", envvar="ELFMEM_CONFIG")
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Retrieve relevant knowledge, rendered for prompt injection."""
    result: FrameResult = _run(
        _recall(_resolve_db(db), _resolve_config(config), query, top_k, frame)
    )
    # NOTE: str(result) returns frame summary ("attention frame: 5 blocks returned.")
    # For text mode, output the rendered content that agents inject into prompts.
    _json(format_recall_response(result)) if json_output else typer.echo(result.text)


@app.command()
def status(
    db: Annotated[str | None, typer.Option("--db", envvar="ELFMEM_DB")] = None,
    config: Annotated[
        str | None, typer.Option("--config", envvar="ELFMEM_CONFIG")
    ] = None,
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
    config: Annotated[
        str | None, typer.Option("--config", envvar="ELFMEM_CONFIG")
    ] = None,
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
    config: Annotated[
        str | None, typer.Option("--config", envvar="ELFMEM_CONFIG")
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Archive decayed blocks, prune weak edges, reinforce top knowledge."""
    result: CurateResult = _run(_curate(_resolve_db(db), _resolve_config(config)))
    _json(result.to_dict()) if json_output else typer.echo(str(result))


@app.command()
def guide(
    method: Annotated[
        str | None,
        typer.Argument(help="Operation name, or blank for overview"),
    ] = None,
) -> None:
    """Show documentation for a specific operation, or the full overview.

    Does not require a database connection.
    """
    typer.echo(get_guide(method))


@app.command()
def serve(
    db: Annotated[str, typer.Option("--db", envvar="ELFMEM_DB", help="Database path")],
    config: Annotated[
        str | None, typer.Option("--config", envvar="ELFMEM_CONFIG")
    ] = None,
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
