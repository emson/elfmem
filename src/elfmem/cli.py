"""elfmem CLI — adaptive memory as shell commands.

Commands:
    elfmem init [--self TEXT] [--db PATH] [--config PATH] [--global]
                [--docs-file PATH] [--no-docs] [--force] [--json]
    elfmem doctor [--db PATH] [--config PATH] [--json]
    elfmem remember CONTENT [--tags t1,t2] [--category C] [--json]
    elfmem recall QUERY [--top-k N] [--frame F] [--json]
    elfmem status [--json]
    elfmem outcome BLOCK_IDS SIGNAL [--weight N] [--source LABEL] [--json]
    elfmem curate [--json]
    elfmem guide [METHOD]
    elfmem serve [--db PATH] [--config PATH]

Config discovery chain (all commands):
    1. --config PATH flag
    2. ELFMEM_CONFIG env var
    3. .elfmem/config.yaml  (walk up from cwd to project root)
    4. ~/.elfmem/config.yaml

DB discovery chain (all commands):
    1. --db PATH flag
    2. ELFMEM_DB env var
    3. project.db in discovered config
    4. ~/.elfmem/agent.db
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Annotated, Any

try:
    import typer
except ImportError:
    raise SystemExit(
        "elfmem CLI requires the 'cli' extra:\n"
        "  pip install 'elfmem[cli]'  or  uv add 'elfmem[cli]'"
    ) from None

from elfmem import project as _project
from elfmem.api import MemorySystem, format_recall_response
from elfmem.exceptions import ElfmemError
from elfmem.guide import get_guide
from elfmem.types import CurateResult, FrameResult, LearnResult, OutcomeResult, SystemStatus

app = typer.Typer(
    name="elfmem",
    help="Adaptive memory for AI agents.",
    no_args_is_help=True,
)


# ── Shared helpers ────────────────────────────────────────────────────────────


def _resolve_paths(
    db: str | None,
    config: str | None,
) -> tuple[str, str | None]:
    """Resolve (db_path, config_path) via the full discovery chain.

    config_path may be None if no config file exists anywhere.
    db_path always resolves to something (falls back to ~/.elfmem/agent.db).
    Exits with code 1 only if both explicit --db and all fallbacks are absent
    — which in practice means the global fallback path is always returned.
    """
    config_path, _source = _project.resolve_config(config)
    db_path, _db_source = _project.resolve_db(db, config_path)
    return db_path, config_path


def _run(coro: Any) -> Any:
    """Execute an async coroutine. Catches ElfmemError at the CLI boundary."""
    try:
        return asyncio.run(coro)
    except ElfmemError as e:
        typer.echo(f"Error: {e.args[0]}\nRecovery: {e.recovery}", err=True)
        raise typer.Exit(1) from e


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
        str | None,
        typer.Option("--db", envvar="ELFMEM_DB", help="Database path (auto from project name)"),
    ] = None,
    config_path: Annotated[
        str | None,
        typer.Option("--config", envvar="ELFMEM_CONFIG", help="Config YAML path (auto)"),
    ] = None,
    use_global: Annotated[
        bool,
        typer.Option("--global", help="Force global ~/.elfmem/ (ignore project detection)"),
    ] = False,
    force: Annotated[
        bool,
        typer.Option("--force", help="Overwrite existing config (never overwrites DB)"),
    ] = False,
    seed: Annotated[
        bool,
        typer.Option("--seed/--no-seed", help="Seed constitutional cognitive loop blocks"),
    ] = True,
    template: Annotated[
        str | None,
        typer.Option(
            "--template",
            help="Add domain-specific blocks on top of constitutional seed. "
            "Run 'elfmem templates' to list options.",
        ),
    ] = None,
    docs_file: Annotated[
        str | None,
        typer.Option("--docs-file", help="Write elfmem section to this agent doc file"),
    ] = None,
    no_docs: Annotated[
        bool,
        typer.Option("--no-docs", help="Skip writing to CLAUDE.md / AGENTS.md"),
    ] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Initialise elfmem: create config, database, and seed the cognitive loop.

    When run inside a project directory (detected by .git, pyproject.toml, etc.),
    creates a project-local .elfmem/config.yaml and stores the database in
    ~/.elfmem/databases/{project-name}.db. Also writes an elfmem section to
    CLAUDE.md or AGENTS.md so Claude and other agents know how to use memory.

    When run outside a project (or with --global), falls back to the global
    ~/.elfmem/ directory, matching previous behaviour.

    Safe to re-run: existing config is preserved unless --force is given.
    Constitutional and SELF blocks are idempotent (duplicates are silently skipped).

    Examples:

        elfmem init --self "I am a software engineering assistant"
        elfmem init --template coding --self "My coding principles..."
        elfmem init --global   # force global config regardless of project
        elfmem init --no-docs  # skip CLAUDE.md update
    """
    from elfmem.config import ProjectConfig, render_default_config
    from elfmem.seed import get_template

    # Validate template early, before touching the filesystem.
    if template is not None:
        try:
            get_template(template)
        except ValueError as e:
            typer.echo(f"Error: {e}", err=True)
            typer.echo("Run 'elfmem templates' to see available templates.", err=True)
            raise typer.Exit(code=1) from e

    # ── Resolve project context ────────────────────────────────────────────

    project_info = None if use_global else _project.get_project_info()
    in_project = project_info is not None and not use_global

    if in_project and project_info is not None:
        # Project-local mode.
        resolved_config = config_path or str(project_info.config)
        resolved_db = db or project_info.db
        proj_name = project_info.name
        proj_root = project_info.root
    else:
        # Global mode.
        resolved_config = config_path or str(Path("~/.elfmem/config.yaml").expanduser())
        resolved_db = db or str(Path("~/.elfmem/agent.db").expanduser())
        proj_name = ""
        proj_root = None

    resolved_config = str(Path(resolved_config).expanduser())
    resolved_db = str(Path(resolved_db).expanduser())
    config_file = Path(resolved_config)

    # ── Create config directory ────────────────────────────────────────────

    config_file.parent.mkdir(parents=True, exist_ok=True)

    # ── Write config ───────────────────────────────────────────────────────

    if config_file.exists() and not force:
        config_action = "exists (skipped)"
    else:
        project_cfg: ProjectConfig | None = None
        if in_project:
            import datetime
            project_cfg = ProjectConfig(
                name=proj_name,
                db=resolved_db,
                identity=self_description or "",
                created=datetime.date.today().isoformat(),
            )
        config_file.write_text(render_default_config(project_cfg), encoding="utf-8")
        config_action = "created"

    # Also create the database directory if needed.
    Path(resolved_db).parent.mkdir(parents=True, exist_ok=True)

    # ── Seed constitutional blocks ─────────────────────────────────────────

    seed_results: list[dict[str, str]] = []
    if seed:
        seed_results = _run(_init_seed(resolved_db, resolved_config, template=template))

    # ── Seed SELF block ────────────────────────────────────────────────────

    self_result: dict[str, str] | None = None
    if self_description:
        learn_result: LearnResult = _run(
            _init_self(resolved_db, resolved_config, self_description)
        )
        self_result = learn_result.to_dict()

    # ── Write agent doc section ────────────────────────────────────────────

    doc_action: str | None = None
    doc_path_str: str | None = None

    if not no_docs and proj_root is not None:
        if docs_file:
            doc_path = Path(docs_file)
        else:
            detected = _project.detect_agent_doc(proj_root)
            # No doc found → create CLAUDE.md (most common convention)
            doc_path = detected if detected is not None else (proj_root / "CLAUDE.md")

        doc_action = _project.write_agent_section(
            doc_path,
            name=proj_name,
            db_path=resolved_db,
            config_path=resolved_config,
            identity=self_description or "",
        )
        doc_path_str = str(doc_path)

    # ── Output ─────────────────────────────────────────────────────────────

    mcp_snippet = _project.mcp_json_snippet(config_path=resolved_config)

    if json_output:
        out: dict[str, Any] = {
            "mode": "project" if in_project else "global",
            "config_path": resolved_config,
            "config_action": config_action,
            "db_path": resolved_db,
        }
        if in_project:
            out["project_name"] = proj_name
        if template:
            out["template"] = template
        if seed_results:
            created = sum(1 for r in seed_results if r["status"] == "created")
            out["constitutional_blocks"] = {"created": created, "total": len(seed_results)}
        if self_result is not None:
            out["self_block"] = self_result
        if doc_action is not None:
            out["agent_doc"] = {"path": doc_path_str, "action": doc_action}
        out["mcp_snippet"] = mcp_snippet
        _json(out)
    else:
        if in_project:
            typer.echo(f"✓  Project:   {proj_name} (detected)")
        typer.echo(f"✓  Config:    {resolved_config} ({config_action})")
        typer.echo(f"✓  Database:  {resolved_db} (ready)")
        if seed_results:
            created = sum(1 for r in seed_results if r["status"] == "created")
            skipped = len(seed_results) - created
            label = f" + {template}" if template else ""
            if created > 0:
                typer.echo(f"✓  Seed:      {created} blocks created (constitutional{label}).")
            else:
                typer.echo(
                    f"✓  Seed:      Blocks already present "
                    f"({skipped} skipped, constitutional{label})."
                )
        if self_result is not None:
            status_msg = self_result["status"]
            if status_msg == "created":
                typer.echo(
                    f"✓  SELF:      Block {self_result['block_id'][:8]} created."
                )
            elif status_msg == "duplicate_rejected":
                typer.echo("✓  SELF:      Block already exists (skipped).")
            else:
                typer.echo(f"✓  SELF:      {self_result['block_id'][:8]} — {status_msg}.")
        if doc_action is not None:
            typer.echo(f"✓  Agent doc: {doc_path_str} ({doc_action})")
        elif not no_docs and proj_root is None:
            typer.echo("   Agent doc: skipped (not in a project directory)")

        if not self_description:
            typer.echo(
                "\n  Tip: personalise your identity with:\n"
                "  elfmem init --self 'Describe your agent here'\n"
                "  elfmem templates    # see available domain templates"
            )

        typer.echo("\n  Add to .claude.json to enable persistent memory:\n")
        typer.echo(mcp_snippet)
        typer.echo("\n  Run 'elfmem doctor' to verify your setup.")


@app.command()
def templates(
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List available seed templates for 'elfmem init --template <name>'.

    Templates add domain-specific principles on top of the constitutional base.
    They are additive — the 10 constitutional blocks are always included.

    Example:

        elfmem init --template coding
    """
    from elfmem.seed import list_templates

    available = list_templates()
    if json_output:
        _json({"templates": [{"name": k, "description": v} for k, v in available.items()]})
    else:
        typer.echo("Available seed templates:\n")
        for name, description in available.items():
            typer.echo(f"  {name:<12}  {description}")
        typer.echo(
            "\nUsage: elfmem init --template <name>\n"
            "       Templates are added on top of the 10 constitutional blocks."
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

    Walks the full config and DB discovery chain to show exactly which files are
    active for the current directory. Checks API keys, SELF blocks, and whether
    the project's agent doc (CLAUDE.md / AGENTS.md) is configured.

    Exits with code 1 if any required item is missing; 0 if fully configured.
    Read-only: never writes to the database or config files.
    """
    checks: list[dict[str, Any]] = []
    failed = False

    def _check(label: str, ok: bool, detail: str, suggestion: str = "") -> None:
        nonlocal failed
        checks.append({"label": label, "ok": ok, "detail": detail, "suggestion": suggestion})
        if not ok:
            failed = True

    # ── Config discovery ───────────────────────────────────────────────────

    config_path, config_source = _project.resolve_config(config)
    _check(
        "Config",
        config_path is not None and Path(config_path).exists(),
        f"{config_path or 'not found'} ({config_source})",
        "elfmem init" if config_path is None else f"elfmem init --config {config_path}",
    )

    # ── Project section ────────────────────────────────────────────────────

    project_name_str = ""
    if config_path and Path(config_path).exists():
        try:
            import yaml
            with open(config_path, encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
            proj = raw.get("project") or {}
            project_name_str = proj.get("name", "")
        except Exception:
            pass

    if project_name_str:
        _check("Project", True, project_name_str)

    # ── DB discovery ───────────────────────────────────────────────────────

    db_path, db_source = _project.resolve_db(db, config_path)
    db_file = Path(db_path)
    _check(
        "Database",
        db_file.exists(),
        f"{db_path} ({db_source})",
        f"elfmem init --db {db_path}",
    )

    # ── API keys ───────────────────────────────────────────────────────────

    has_anthropic = bool(os.getenv("ANTHROPIC_API_KEY"))
    has_openai = bool(os.getenv("OPENAI_API_KEY"))
    _check(
        "API keys",
        has_anthropic or has_openai,
        "ANTHROPIC_API_KEY" if has_anthropic else ("OPENAI_API_KEY" if has_openai else "none set"),
        "export ANTHROPIC_API_KEY='sk-ant-...' or OPENAI_API_KEY='sk-...'",
    )

    # ── SELF blocks ────────────────────────────────────────────────────────

    self_count = -1
    if db_file.exists():
        self_count = _run(_doctor_self_count(db_path))

    if self_count < 0:
        _check("SELF frame", False, "DB not accessible",
               f"elfmem init --self 'Describe your agent' --db {db_path}")
    elif self_count == 0:
        _check("SELF frame", False, "No SELF blocks found",
               f"elfmem init --self 'Describe your agent' --db {db_path}")
    else:
        _check("SELF frame", True, f"{self_count} SELF block(s) found")

    # ── Agent doc ──────────────────────────────────────────────────────────

    cwd = Path.cwd()
    proj_root = _project.find_project_root(cwd)
    if proj_root is not None:
        agent_doc = _project.detect_agent_doc(proj_root)
        if agent_doc is None:
            _check(
                "Agent doc",
                False,
                "CLAUDE.md / AGENTS.md not found in project root",
                "elfmem init  (creates CLAUDE.md with elfmem section)",
            )
        elif _project.has_agent_section(agent_doc):
            _check("Agent doc", True, f"{agent_doc.name} has elfmem section")
        else:
            _check(
                "Agent doc",
                False,
                f"{agent_doc.name} exists but has no elfmem section",
                f"elfmem init  (adds elfmem section to {agent_doc.name})",
            )

        # ── MCP config ─────────────────────────────────────────────────────

        mcp_file = _project.detect_mcp_config(proj_root)
        if mcp_file is None:
            _check(
                "MCP config",
                False,
                ".claude.json / claude-code.yaml not found",
                "Add MCP entry (shown at end of elfmem init output)",
            )
        elif _project.has_mcp_entry(mcp_file):
            _check("MCP config", True, f"{mcp_file.name} has elfmem entry")
        else:
            _check(
                "MCP config",
                False,
                f"{mcp_file.name} exists but has no elfmem entry",
                "Add elfmem MCP server (shown at end of elfmem init output)",
            )

    # ── Output ─────────────────────────────────────────────────────────────

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
    db_path, config_path = _resolve_paths(db, config)
    tag_list = [t.strip() for t in tags.split(",")] if tags else None
    result: LearnResult = _run(
        _remember(db_path, config_path, content, tag_list, category)
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
    db_path, config_path = _resolve_paths(db, config)
    result: FrameResult = _run(
        _recall(db_path, config_path, query, top_k, frame)
    )
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
    db_path, config_path = _resolve_paths(db, config)
    result: SystemStatus = _run(_status(db_path, config_path))
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
    db_path, config_path = _resolve_paths(db, config)
    ids = [bid.strip() for bid in block_ids.split(",")]
    result: OutcomeResult = _run(
        _outcome(db_path, config_path, ids, signal, weight, source)
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
    db_path, config_path = _resolve_paths(db, config)
    result = _run(_dream(db_path, config_path))
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
    db_path, config_path = _resolve_paths(db, config)
    result: CurateResult = _run(_curate(db_path, config_path))
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
    db: Annotated[
        str | None,
        typer.Option(
            "--db",
            envvar="ELFMEM_DB",
            help="Database path. Optional when project.db is set in config.",
        ),
    ] = None,
    config: Annotated[
        str | None,
        typer.Option("--config", envvar="ELFMEM_CONFIG", help="Config YAML path"),
    ] = None,
    adaptive_policy: Annotated[
        bool,
        typer.Option(
            "--adaptive-policy/--no-adaptive-policy",
            help="Enable self-tuning consolidation policy.",
        ),
    ] = False,
) -> None:
    """Start the elfmem MCP server for agent tool integration.

    --db is optional when a project config with project.db is discoverable
    from the current directory (set up via 'elfmem init').
    """
    try:
        from elfmem.mcp import main as mcp_main
    except ImportError:
        typer.echo(
            "MCP server requires the 'mcp' extra:\n"
            "  pip install 'elfmem[mcp]'  or  uv add 'elfmem[mcp]'",
            err=True,
        )
        raise typer.Exit(1) from None

    # Resolve config first, then db (db may come from project.db in config).
    config_path, _config_source = _project.resolve_config(config)
    db_path, db_source = _project.resolve_db(db, config_path)

    # If after full discovery we still have no meaningful db, fail clearly.
    if not db_path:
        typer.echo(
            "Error: cannot determine database path.\n"
            "Run 'elfmem init' in your project directory, or pass --db PATH.",
            err=True,
        )
        raise typer.Exit(1)

    mcp_main(db_path=db_path, config_path=config_path, use_adaptive_policy=adaptive_policy)


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
    async with MemorySystem.managed(db_path, config=config) as mem:
        return await mem.remember(content, tags=tags, category=category)


async def _recall(
    db_path: str,
    config: str | None,
    query: str,
    top_k: int,
    frame: str,
) -> FrameResult:
    async with MemorySystem.managed(db_path, config=config) as mem:
        return await mem.frame(frame, query=query or None, top_k=top_k)


async def _status(db_path: str, config: str | None) -> SystemStatus:
    async with MemorySystem.managed(db_path, config=config) as mem:
        return await mem.status()


async def _outcome(
    db_path: str,
    config: str | None,
    block_ids: list[str],
    signal: float,
    weight: float,
    source: str,
) -> OutcomeResult:
    async with MemorySystem.managed(db_path, config=config) as mem:
        return await mem.outcome(block_ids, signal, weight=weight, source=source)


async def _dream(db_path: str, config: str | None) -> Any:
    """Consolidate pending blocks. Returns ConsolidateResult or None if no pending."""
    async with MemorySystem.managed(db_path, config=config) as mem:
        return await mem.dream()


async def _curate(db_path: str, config: str | None) -> CurateResult:
    async with MemorySystem.managed(db_path, config=config) as mem:
        return await mem.curate()


async def _init_seed(
    db_path: str, config: str, template: str | None = None
) -> list[dict[str, str]]:
    """Store constitutional seed blocks plus optional template blocks. Idempotent."""
    from elfmem.seed import CONSTITUTIONAL_SEED, get_template

    blocks = CONSTITUTIONAL_SEED[:]
    if template:
        blocks = blocks + get_template(template)

    async with MemorySystem.managed(db_path, config=config) as mem:
        results = []
        for block in blocks:
            r = await mem.remember(
                block["content"],  # type: ignore[arg-type]
                tags=block["tags"],  # type: ignore[arg-type]
            )
            results.append(r.to_dict())
        return results


async def _init_self(db_path: str, config: str, content: str) -> LearnResult:
    """Store an identity block tagged self/context. Used by elfmem init --self."""
    async with MemorySystem.managed(db_path, config=config) as mem:
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
