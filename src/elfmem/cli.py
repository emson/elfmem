"""elfmem CLI — adaptive memory as shell commands.

Commands:
    elfmem init [--self TEXT] [--db PATH] [--config PATH] [--force] [--json]
    elfmem doctor [--db PATH] [--config PATH] [--json]
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
import sys
from pathlib import Path
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
def init(
    self_description: Annotated[
        str | None,
        typer.Option("--self", help="Seed SELF frame with this identity description"),
    ] = None,
    db: Annotated[
        str,
        typer.Option("--db", envvar="ELFMEM_DB", help="Database path"),
    ] = "~/.elfmem/agent.db",
    config_path: Annotated[
        str,
        typer.Option("--config", envvar="ELFMEM_CONFIG", help="Config YAML path"),
    ] = "~/.elfmem/config.yaml",
    force: Annotated[
        bool,
        typer.Option("--force", help="Overwrite existing config (never overwrites DB)"),
    ] = False,
    seed: Annotated[
        bool,
        typer.Option("--seed/--no-seed", help="Seed constitutional cognitive loop blocks"),
    ] = True,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Initialise elfmem: create config directory, generate config, and seed the cognitive loop.

    Safe to re-run: existing config is preserved unless --force is given.
    Constitutional and SELF blocks are never duplicated (duplicate content is silently skipped).

    By default, seeds 10 constitutional blocks that form the cognitive loop
    (curiosity, feedback, balance, etc.). Use --no-seed to skip this.
    """
    from elfmem.config import render_default_config

    db_expanded = os.path.expanduser(db)
    config_expanded = os.path.expanduser(config_path)
    config_file = Path(config_expanded)

    # Step 1: create config directory
    config_file.parent.mkdir(parents=True, exist_ok=True)

    # Step 2: write config — skip if already exists (unless --force)
    if config_file.exists() and not force:
        config_action = "exists (skipped)"
    else:
        config_file.write_text(render_default_config(), encoding="utf-8")
        config_action = "created"

    # Step 3: seed constitutional blocks (default: on)
    seed_results: list[dict[str, str]] = []
    if seed:
        seed_results = _run(_init_seed(db_expanded, config_expanded))

    # Step 4: seed SELF block if --self provided
    self_result: dict[str, str] | None = None
    if self_description:
        learn_result: LearnResult = _run(
            _init_self(db_expanded, config_expanded, self_description)
        )
        self_result = learn_result.to_dict()

    if json_output:
        out: dict[str, Any] = {
            "config_path": config_expanded,
            "config_action": config_action,
            "db_path": db_expanded,
        }
        if seed_results:
            created = sum(1 for r in seed_results if r["status"] == "created")
            out["constitutional_blocks"] = {"created": created, "total": len(seed_results)}
        if self_result is not None:
            out["self_block"] = self_result
        _json(out)
    else:
        typer.echo(f"✓  Config:   {config_expanded} ({config_action})")
        typer.echo(f"✓  Database: {db_expanded} (ready)")
        if seed_results:
            created = sum(1 for r in seed_results if r["status"] == "created")
            skipped = len(seed_results) - created
            if created > 0:
                typer.echo(f"✓  Seed:     {created} constitutional blocks created.")
            else:
                typer.echo(f"✓  Seed:     Constitutional blocks already present ({skipped} skipped).")
        if self_result is not None:
            status_msg = self_result["status"]
            if status_msg == "created":
                typer.echo(f"✓  SELF:     Stored block {self_result['block_id'][:8]}. Status: created.")
            elif status_msg == "duplicate_rejected":
                typer.echo("✓  SELF:     Block already exists (skipped — no duplicate created).")
            else:
                typer.echo(f"✓  SELF:     {self_result['block_id'][:8]}. Status: {status_msg}.")
        if not self_description:
            typer.echo(
                "\n  Tip: personalise your identity with:\n"
                "  elfmem init --self 'Describe your agent here'"
            )


@app.command()
def doctor(
    db: Annotated[
        str | None,
        typer.Option("--db", envvar="ELFMEM_DB", help="Database path"),
    ] = None,
    config: Annotated[
        str | None,
        typer.Option("--config", envvar="ELFMEM_CONFIG", help="Config YAML path"),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Diagnose your elfmem setup. Reports what is configured and what is missing.

    Exits with code 1 if any required item is missing; 0 if fully configured.
    Read-only: never writes to the database or config files.
    """
    db_path = os.path.expanduser(db or "~/.elfmem/agent.db")
    config_path = os.path.expanduser(config or "~/.elfmem/config.yaml")

    checks: list[dict[str, Any]] = []
    failed = False

    def _check(label: str, ok: bool, detail: str, suggestion: str = "") -> None:
        nonlocal failed
        checks.append({"label": label, "ok": ok, "detail": detail, "suggestion": suggestion})
        if not ok:
            failed = True

    # Filesystem checks (read-only)
    config_file = Path(config_path)
    _check("Config dir", config_file.parent.exists(), str(config_file.parent),
           f"mkdir -p {config_file.parent}")
    _check("Config file", config_file.exists(), config_path,
           f"elfmem init --config {config_path}")
    _check("Database", Path(db_path).exists(), db_path,
           f"elfmem init --db {db_path}")

    # API key checks (warn, not fail)
    has_anthropic = bool(os.getenv("ANTHROPIC_API_KEY"))
    has_openai = bool(os.getenv("OPENAI_API_KEY"))
    _check(
        "API keys",
        has_anthropic or has_openai,
        "ANTHROPIC_API_KEY" if has_anthropic else ("OPENAI_API_KEY" if has_openai else "none set"),
        "export ANTHROPIC_API_KEY='sk-ant-...' or OPENAI_API_KEY='sk-...'",
    )

    # SELF block check — requires DB access (read-only)
    self_count = -1
    if Path(db_path).exists():
        self_count = _run(_doctor_self_count(db_path))

    if self_count < 0:
        _check("SELF frame", False, "DB not accessible",
               f"elfmem init --self 'Describe your agent here' --db {db_path}")
    elif self_count == 0:
        _check("SELF frame", False, "No SELF blocks found",
               f"elfmem init --self 'Describe your agent here' --db {db_path}")
    else:
        _check("SELF frame", True, f"{self_count} SELF block(s) found")

    if json_output:
        _json({"checks": checks, "passed": not failed})
    else:
        for c in checks:
            symbol = "✓" if c["ok"] else "✗"
            typer.echo(f"{symbol}  {c['label']:<12} {c['detail']}")
            if not c["ok"] and c["suggestion"]:
                typer.echo(f"   Suggestion: {c['suggestion']}")
        typer.echo("")
        if failed:
            typer.echo("Setup incomplete. Follow the suggestions above.")
        else:
            typer.echo("All checks passed. elfmem is ready.")

    if failed:
        raise typer.Exit(1)


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
def dream(
    db: Annotated[str | None, typer.Option("--db", envvar="ELFMEM_DB")] = None,
    config: Annotated[
        str | None, typer.Option("--config", envvar="ELFMEM_CONFIG")
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Consolidate pending knowledge: embed, align, detect contradictions.

    Call when elfmem_remember indicates should_dream=True, or at natural pause
    points during a session. Automatically triggered on session exit if pending.
    """
    result = _run(_dream(_resolve_db(db), _resolve_config(config)))
    if result is None:
        msg = "No pending blocks — nothing to consolidate."
        _json({"message": msg, "status": "idle"}) if json_output else typer.echo(msg)
    else:
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


async def _dream(db_path: str, config: str | None) -> Any:
    """Consolidate pending blocks. Returns ConsolidateResult or None if no pending."""
    async with SmartMemory.managed(db_path, config=config) as mem:
        return await mem.dream()


async def _curate(db_path: str, config: str | None) -> CurateResult:
    async with SmartMemory.managed(db_path, config=config) as mem:
        return await mem.curate()


async def _init_seed(db_path: str, config: str) -> list[dict[str, str]]:
    """Store all 10 constitutional seed blocks. Idempotent — duplicates are silently skipped."""
    from elfmem.seed import CONSTITUTIONAL_SEED
    async with SmartMemory.managed(db_path, config=config) as mem:
        results = []
        for block in CONSTITUTIONAL_SEED:
            r = await mem.remember(
                block["content"],  # type: ignore[arg-type]
                tags=block["tags"],  # type: ignore[arg-type]
            )
            results.append(r.to_dict())
        return results


async def _init_self(db_path: str, config: str, content: str) -> LearnResult:
    """Store an identity block tagged self/context. Used by elfmem init --self."""
    async with SmartMemory.managed(db_path, config=config) as mem:
        return await mem.remember(content, tags=["self/context"])


async def _doctor_self_count(db_path: str) -> int:
    """Count active SELF blocks. Returns -1 if DB is not accessible.

    Uses a raw engine connection — no session, no schema changes, no side effects.
    """
    from elfmem.db.engine import create_engine
    from elfmem.db.queries import count_self_blocks
    try:
        engine = await create_engine(db_path)
        async with engine.connect() as conn:
            count = await count_self_blocks(conn)
        await engine.dispose()
        return count
    except Exception:
        return -1
