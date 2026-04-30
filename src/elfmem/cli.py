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

from elfmem import __version__
from elfmem import project as _project
from elfmem.api import MemorySystem, format_recall_response
from elfmem.exceptions import ElfmemError
from elfmem.guide import get_guide
from elfmem.types import (
    CurateResult,
    FrameResult,
    LearnResult,
    MindOutcomeResult,
    MindPredictResult,
    MindShowResult,
    MindSummary,
    OutcomeResult,
    SystemStatus,
)


def _version_callback(value: bool) -> None:
    if value:
        print(f"elfmem {__version__}")
        raise typer.Exit()


app = typer.Typer(
    name="elfmem",
    help="Adaptive memory for AI agents.",
    no_args_is_help=True,
)


@app.callback()
def _main(
    version: Annotated[
        bool,
        typer.Option(
            "--version", "-V",
            help="Show version and exit.",
            callback=_version_callback,
            is_eager=True,
        ),
    ] = False,
) -> None:
    """Adaptive memory for AI agents."""


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
    modules: Annotated[
        bool, typer.Option("--modules", help="Print key module paths and exit")
    ] = False,
) -> None:
    """Diagnose your elfmem setup. Reports what is configured and what is missing.

    Walks the full config and DB discovery chain to show exactly which files are
    active for the current directory. Checks API keys, SELF blocks, and whether
    the project's agent doc (CLAUDE.md / AGENTS.md) is configured.

    Exits with code 1 if any required item is missing; 0 if fully configured.
    Read-only: never writes to the database or config files.

    Use --modules to print the key module map without running health checks.
    """
    if modules:
        typer.echo(_project.format_key_modules())
        return
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
            from importlib.metadata import version as _pkg_version
            installed = _pkg_version("elfmem")
            section_ver = _project.extract_section_version(agent_doc)
            if section_ver == "legacy" or (
                section_ver is not None and section_ver != installed
            ):
                _check(
                    "Agent doc",
                    False,
                    f"{agent_doc.name} elfmem section is from v{section_ver},"
                    f" installed is v{installed}",
                    "Run: elfmem init  (refreshes section, idempotent)",
                )
            else:
                _check(
                    "Agent doc", True,
                    f"{agent_doc.name} has elfmem section (v{installed})",
                )
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

    # ── Backups ────────────────────────────────────────────────────────────

    if db_file.exists():
        from elfmem.db.migrate import list_backups

        backups = list_backups(db_path)
        if backups:
            total_size = sum(int(b["size"]) for b in backups)
            newest = backups[0]["name"]
            _check(
                "Backups",
                True,
                f"{len(backups)} backup(s), {total_size / 1024:.1f} KB total. Latest: {newest}",
                f"Clean up with: rm {Path(db_path).parent}/*.bak" if len(backups) > 3 else "",
            )
        else:
            _check(
                "Backups",
                False,
                "No backups found",
                "Run: elfmem backup",
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
def backup(
    db: Annotated[str | None, typer.Option("--db", envvar="ELFMEM_DB")] = None,
    config: Annotated[str | None, typer.Option("--config", envvar="ELFMEM_CONFIG")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Create a clean backup of the database.

    Uses VACUUM INTO to produce a self-contained backup file with no
    pending WAL state. Ideal before risky operations or as a periodic
    safety net.

    Examples:

        elfmem backup
        elfmem backup --db ~/.elfmem/databases/elfmem.db
    """
    db_path, config_path = _resolve_paths(db, config)
    result: dict[str, Any] = _run(_backup_async(db_path, config_path))
    if json_output:
        _json(result)
    else:
        typer.echo(f"Backed up to {result['path']} ({result['size_kb']:.1f} KB)")


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
    result, should_dream = _run(
        _remember(db_path, config_path, content, tag_list, category)
    )
    if json_output:
        data = result.to_dict()
        data["should_dream"] = should_dream
        _json(data)
    else:
        typer.echo(str(result))
        if should_dream:
            typer.echo("Inbox full — run 'elfmem dream' to consolidate.")


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


# ── Peer communication subcommands ───────────────────────────────────────────

peer_app = typer.Typer(
    name="peer",
    help="Peer communication: exchange knowledge and messages between elfmem instances.",
    no_args_is_help=True,
)
app.add_typer(peer_app, name="peer")


@peer_app.command("init")
def peer_init(
    name: Annotated[str, typer.Option("--name", help="Name for this instance")],
    db: Annotated[str | None, typer.Option("--db", envvar="ELFMEM_DB")] = None,
    config: Annotated[str | None, typer.Option("--config", envvar="ELFMEM_CONFIG")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Set this instance's peer identity for communication."""
    db_path, config_path = _resolve_paths(db, config)
    did: str = _run(_peer_init_async(db_path, config_path, name))
    if json_output:
        _json({"did": did})
    else:
        typer.echo(f"Identity set: {did}")


@peer_app.command("add")
def peer_add(
    did: str,
    name: Annotated[str, typer.Option("--name", help="Human-readable name")],
    is_self: Annotated[bool, typer.Option("--self", help="Same identity, different machine")] = False,
    delivery_path: Annotated[str | None, typer.Option("--delivery-path", help="Filesystem path to peer's inbox dir (direct delivery)")] = None,
    db: Annotated[str | None, typer.Option("--db", envvar="ELFMEM_DB")] = None,
    config: Annotated[str | None, typer.Option("--config", envvar="ELFMEM_CONFIG")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Register a peer for communication.

    With --delivery-path, messages are written directly to the peer's
    inbox directory (instant delivery, no transport needed).

    Examples:

        elfmem peer add elf:trader --name "Trading Elf"
        elfmem peer add elf:server --name "Server Elf" --self
        elfmem peer add elf:vault --name "Vault" \\
            --delivery-path ~/Dropbox/vaults/elf_vault_proj/.elfmem/inbox
    """
    from elfmem.types import PeerInfo

    db_path, config_path = _resolve_paths(db, config)
    result: PeerInfo = _run(_peer_add_async(db_path, config_path, did, name, is_self, delivery_path))
    if json_output:
        _json(result.to_dict())
    else:
        typer.echo(str(result))


@peer_app.command("remove")
def peer_remove(
    did: str,
    db: Annotated[str | None, typer.Option("--db", envvar="ELFMEM_DB")] = None,
    config: Annotated[str | None, typer.Option("--config", envvar="ELFMEM_CONFIG")] = None,
) -> None:
    """Unregister a peer."""
    db_path, config_path = _resolve_paths(db, config)
    removed: bool = _run(_peer_remove_async(db_path, config_path, did))
    typer.echo(f"{'Removed' if removed else 'Not found'}: {did}")


@peer_app.command("list")
def peer_list(
    db: Annotated[str | None, typer.Option("--db", envvar="ELFMEM_DB")] = None,
    config: Annotated[str | None, typer.Option("--config", envvar="ELFMEM_CONFIG")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List all registered peers with trust scores."""
    from elfmem.types import PeerInfo

    db_path, config_path = _resolve_paths(db, config)
    results: list[PeerInfo] = _run(_peer_list_async(db_path, config_path))
    if json_output:
        _json([r.to_dict() for r in results])
    else:
        if not results:
            typer.echo("No peers registered. Add one with: elfmem peer add <did> --name <name>")
        else:
            for r in results:
                typer.echo(str(r))


@peer_app.command("trust")
def peer_trust(
    did: str,
    set_value: Annotated[float | None, typer.Option("--set", help="Set trust to this value")] = None,
    db: Annotated[str | None, typer.Option("--db", envvar="ELFMEM_DB")] = None,
    config: Annotated[str | None, typer.Option("--config", envvar="ELFMEM_CONFIG")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """View or set trust for a peer."""
    from elfmem.types import PeerInfo

    db_path, config_path = _resolve_paths(db, config)
    result: PeerInfo = _run(_peer_trust_async(db_path, config_path, did, set_value))
    if json_output:
        _json(result.to_dict())
    else:
        typer.echo(str(result))


@peer_app.command("send")
def peer_send(
    did: str,
    content: str,
    reply_to: Annotated[str | None, typer.Option("--reply-to", help="msg_id of prior message")] = None,
    db: Annotated[str | None, typer.Option("--db", envvar="ELFMEM_DB")] = None,
    config: Annotated[str | None, typer.Option("--config", envvar="ELFMEM_CONFIG")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Send a message to a peer.

    Creates a message block in your memory and writes a JSON file
    to the outbox directory for transport.

    Examples:

        elfmem peer send elf:trader "What is your view on UK gilts?"
        elfmem peer send elf:trader "I agree" --reply-to m_a1b2c3d4
    """
    from elfmem.types import PeerSendResult

    db_path, config_path = _resolve_paths(db, config)
    result: PeerSendResult = _run(
        _peer_send_async(db_path, config_path, did, content, reply_to)
    )
    if json_output:
        _json(result.to_dict())
    else:
        typer.echo(str(result))


@peer_app.command("inbox")
def peer_inbox(
    from_peer: Annotated[str | None, typer.Option("--from", help="Filter by peer DID")] = None,
    import_all: Annotated[bool, typer.Option("--import-all", help="Import all pending messages")] = False,
    db: Annotated[str | None, typer.Option("--db", envvar="ELFMEM_DB")] = None,
    config: Annotated[str | None, typer.Option("--config", envvar="ELFMEM_CONFIG")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Check for and optionally import pending messages."""
    from elfmem.types import PeerInboxResult

    db_path, config_path = _resolve_paths(db, config)
    result: PeerInboxResult = _run(
        _peer_inbox_async(db_path, config_path, from_peer, import_all)
    )
    if json_output:
        _json(result.to_dict())
    else:
        typer.echo(str(result))


@app.command("export")
def export_cmd(
    share: Annotated[str, typer.Option("--share", help="Share level: public|peer|all")] = "public",
    min_confidence: Annotated[float, typer.Option("--min-confidence")] = 0.0,
    output: Annotated[str, typer.Option("-o", "--output", help="Output file path")] = "export.json",
    db: Annotated[str | None, typer.Option("--db", envvar="ELFMEM_DB")] = None,
    config: Annotated[str | None, typer.Option("--config", envvar="ELFMEM_CONFIG")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Export shareable blocks as a JSON bundle.

    Examples:

        elfmem export --share public -o knowledge.json
        elfmem export --share all -o sync.json --min-confidence 0.5
    """
    from elfmem.types import ExportResult

    db_path, config_path = _resolve_paths(db, config)
    result: ExportResult = _run(
        _export_async(db_path, config_path, share, min_confidence, output)
    )
    if json_output:
        _json(result.to_dict())
    else:
        typer.echo(str(result))


@app.command("import")
def import_cmd(
    path: str,
    from_peer: Annotated[str | None, typer.Option("--from", help="Source peer DID")] = None,
    self_merge: Annotated[bool, typer.Option("--self-merge", help="Same identity, trust 1.0")] = False,
    db: Annotated[str | None, typer.Option("--db", envvar="ELFMEM_DB")] = None,
    config: Annotated[str | None, typer.Option("--config", envvar="ELFMEM_CONFIG")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Import a block bundle from another elfmem instance.

    Examples:

        elfmem import knowledge.json --from elf:researcher
        elfmem import sync.json --self-merge
    """
    from elfmem.types import ImportResult

    db_path, config_path = _resolve_paths(db, config)
    result: ImportResult = _run(
        _import_async(db_path, config_path, path, from_peer, self_merge)
    )
    if json_output:
        _json(result.to_dict())
    else:
        typer.echo(str(result))


# ── Mind (Theory of Mind) subcommands ────────────────────────────────────────

mind_app = typer.Typer(
    name="mind",
    help="Theory of Mind blocks: model other minds, make predictions, close outcomes.",
    no_args_is_help=True,
)
app.add_typer(mind_app, name="mind")


@mind_app.command("create")
def mind_create(
    subject: str,
    goals: Annotated[
        list[str] | None, typer.Option("--goal", help="Goal (repeatable)")
    ] = None,
    beliefs: Annotated[
        list[str] | None, typer.Option("--belief", help="Belief (repeatable)")
    ] = None,
    fears: Annotated[
        list[str] | None, typer.Option("--fear", help="Fear (repeatable)")
    ] = None,
    motivations: Annotated[
        list[str] | None, typer.Option("--motivation", help="Motivation (repeatable)")
    ] = None,
    db: Annotated[str | None, typer.Option("--db", envvar="ELFMEM_DB")] = None,
    config: Annotated[str | None, typer.Option("--config", envvar="ELFMEM_CONFIG")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Create a Theory of Mind block for a subject.

    Models another agent's goals, beliefs, fears, and motivations as an
    explicit, falsifiable representation. Decay tier: DURABLE (~6 month half-life).

    Examples:

        elfmem mind create "customer-archetype" \\
            --goal "Ship fast without learning infra" \\
            --goal "Keep API costs predictable" \\
            --belief "Agent-ready code is a moat" \\
            --fear "Complex setup causes abandonment"
    """
    db_path, config_path = _resolve_paths(db, config)
    result: LearnResult = _run(
        _mind_create(db_path, config_path, subject, goals, beliefs, fears, motivations)
    )
    if json_output:
        _json(result.to_dict())
    else:
        typer.echo(str(result))


@mind_app.command("predict")
def mind_predict(
    mind_block_id: str,
    prediction: Annotated[
        str, typer.Option("--prediction", help="Falsifiable prediction text")
    ],
    verify_at: Annotated[
        str, typer.Option("--verify-at", help="Verification date (YYYY-MM-DD)")
    ],
    reasoning: Annotated[
        str | None, typer.Option("--reasoning", help="Why this prediction")
    ] = None,
    db: Annotated[str | None, typer.Option("--db", envvar="ELFMEM_DB")] = None,
    config: Annotated[str | None, typer.Option("--config", envvar="ELFMEM_CONFIG")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Add a falsifiable prediction linked to a mind block.

    Creates a decision block with the prediction content and links it
    to the mind block via a 'predicts' edge.

    Examples:

        elfmem mind predict abc12345 \\
            --prediction "Will pay 49/mo for hosted version" \\
            --verify-at 2026-06-30 \\
            --reasoning "Prefers predictable cost over setup friction"
    """
    db_path, config_path = _resolve_paths(db, config)
    result: MindPredictResult = _run(
        _mind_predict(db_path, config_path, mind_block_id, prediction, verify_at, reasoning)
    )
    if json_output:
        _json(result.to_dict())
    else:
        typer.echo(str(result))


@mind_app.command("list")
def mind_list(
    db: Annotated[str | None, typer.Option("--db", envvar="ELFMEM_DB")] = None,
    config: Annotated[str | None, typer.Option("--config", envvar="ELFMEM_CONFIG")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List all active mind blocks with prediction statistics."""
    db_path, config_path = _resolve_paths(db, config)
    results: list[MindSummary] = _run(_mind_list(db_path, config_path))
    if json_output:
        _json([r.to_dict() for r in results])
    else:
        if not results:
            typer.echo("No mind blocks found. Create one with: elfmem mind create <subject>")
        else:
            for r in results:
                typer.echo(str(r))


@mind_app.command("show")
def mind_show(
    mind_block_id: str,
    db: Annotated[str | None, typer.Option("--db", envvar="ELFMEM_DB")] = None,
    config: Annotated[str | None, typer.Option("--config", envvar="ELFMEM_CONFIG")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show a mind block with all linked predictions."""
    db_path, config_path = _resolve_paths(db, config)
    result: MindShowResult = _run(_mind_show(db_path, config_path, mind_block_id))
    if json_output:
        _json(result.to_dict())
    else:
        typer.echo(str(result))


@mind_app.command("outcome")
def mind_outcome_cmd(
    decision_block_id: str,
    hit: Annotated[bool, typer.Option("--hit/--miss", help="Did the prediction come true?")] = True,
    reason: Annotated[str, typer.Option("--reason", help="Why this outcome")] = "",
    db: Annotated[str | None, typer.Option("--db", envvar="ELFMEM_DB")] = None,
    config: Annotated[str | None, typer.Option("--config", envvar="ELFMEM_CONFIG")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Close a prediction: record hit/miss and calibrate the mind model.

    Updates confidence on both the decision block and the linked mind block.
    Creates a 'validates' edge from the decision to the mind.

    Examples:

        elfmem mind outcome def67890 --hit --reason "Signed up week 1 at tier price"
        elfmem mind outcome def67890 --miss --reason "Requested full bespoke integration"
    """
    if not reason:
        typer.echo("Error: --reason is required for audit trail.", err=True)
        raise typer.Exit(1)
    db_path, config_path = _resolve_paths(db, config)
    result: MindOutcomeResult = _run(
        _mind_outcome(db_path, config_path, decision_block_id, hit, reason)
    )
    if json_output:
        _json(result.to_dict())
    else:
        typer.echo(str(result))


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
) -> tuple[LearnResult, bool]:
    async with MemorySystem.managed(db_path, config=config, auto_dream=False) as mem:
        result = await mem.remember(content, tags=tags, category=category)
        return result, mem.should_dream


async def _recall(
    db_path: str,
    config: str | None,
    query: str,
    top_k: int,
    frame: str,
) -> FrameResult:
    async with MemorySystem.managed(db_path, config=config, auto_dream=False) as mem:
        return await mem.frame(frame, query=query or None, top_k=top_k)


async def _status(db_path: str, config: str | None) -> SystemStatus:
    async with MemorySystem.managed(db_path, config=config, auto_dream=False) as mem:
        return await mem.status()


async def _outcome(
    db_path: str,
    config: str | None,
    block_ids: list[str],
    signal: float,
    weight: float,
    source: str,
) -> OutcomeResult:
    async with MemorySystem.managed(db_path, config=config, auto_dream=False) as mem:
        return await mem.outcome(block_ids, signal, weight=weight, source=source)


async def _dream(db_path: str, config: str | None) -> Any:
    """Consolidate pending blocks. Returns ConsolidateResult or None if no pending."""
    async with MemorySystem.managed(db_path, config=config, auto_dream=False) as mem:
        return await mem.dream()


async def _curate(db_path: str, config: str | None) -> CurateResult:
    async with MemorySystem.managed(db_path, config=config, auto_dream=False) as mem:
        return await mem.curate()


async def _init_seed(
    db_path: str, config: str, template: str | None = None
) -> list[dict[str, str]]:
    """Store constitutional seed blocks plus optional template blocks. Idempotent."""
    from elfmem.seed import CONSTITUTIONAL_SEED, get_template

    blocks = CONSTITUTIONAL_SEED[:]
    if template:
        blocks = blocks + get_template(template)

    async with MemorySystem.managed(db_path, config=config, auto_dream=False) as mem:
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
    async with MemorySystem.managed(db_path, config=config, auto_dream=False) as mem:
        return await mem.remember(content, tags=["self/context"])


async def _backup_async(db_path: str, config: str | None) -> dict[str, Any]:
    from datetime import UTC, datetime
    from pathlib import Path

    from elfmem.db.migrate import vacuum_backup

    async with MemorySystem.managed(db_path, config=config, auto_dream=False) as mem:
        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        src = Path(db_path)
        out_path = str(src.with_suffix(f".backup.{timestamp}.bak"))
        async with mem._engine.begin() as conn:
            result_path = await vacuum_backup(conn, out_path)
            from elfmem.db.queries import set_config
            await set_config(conn, "last_backup_path", result_path)
            await set_config(conn, "last_backup_at", datetime.now(UTC).isoformat())
        size = Path(result_path).stat().st_size
        return {"path": result_path, "size_kb": size / 1024}


async def _peer_init_async(db_path: str, config: str | None, name: str) -> str:
    async with MemorySystem.managed(db_path, config=config, auto_dream=False) as mem:
        return await mem.peer_init(name)


async def _peer_add_async(
    db_path: str, config: str | None, did: str, name: str, is_self: bool,
    delivery_path: str | None = None,
) -> Any:
    async with MemorySystem.managed(db_path, config=config, auto_dream=False) as mem:
        return await mem.peer_add(did, name, is_self=is_self, delivery_path=delivery_path)


async def _peer_remove_async(db_path: str, config: str | None, did: str) -> bool:
    async with MemorySystem.managed(db_path, config=config, auto_dream=False) as mem:
        return await mem.peer_remove(did)


async def _peer_list_async(db_path: str, config: str | None) -> Any:
    async with MemorySystem.managed(db_path, config=config, auto_dream=False) as mem:
        return await mem.peer_list()


async def _peer_trust_async(
    db_path: str, config: str | None, did: str, set_value: float | None,
) -> Any:
    async with MemorySystem.managed(db_path, config=config, auto_dream=False) as mem:
        return await mem.peer_trust(did, set_value=set_value)


async def _peer_send_async(
    db_path: str, config: str | None, did: str, content: str, reply_to: str | None,
) -> Any:
    async with MemorySystem.managed(db_path, config=config, auto_dream=False) as mem:
        return await mem.peer_send(did, content, in_reply_to=reply_to)


async def _peer_inbox_async(
    db_path: str, config: str | None, from_peer: str | None, import_all: bool,
) -> Any:
    async with MemorySystem.managed(db_path, config=config, auto_dream=False) as mem:
        return await mem.peer_inbox(from_peer=from_peer, import_all=import_all)


async def _export_async(
    db_path: str, config: str | None, share: str, min_confidence: float, output: str,
) -> Any:
    async with MemorySystem.managed(db_path, config=config, auto_dream=False) as mem:
        return await mem.export_blocks(
            share_level=share, min_confidence=min_confidence, output_path=output,
        )


async def _import_async(
    db_path: str, config: str | None, path: str, from_peer: str | None, self_merge: bool,
) -> Any:
    async with MemorySystem.managed(db_path, config=config, auto_dream=False) as mem:
        return await mem.import_blocks(path, from_peer=from_peer, self_merge=self_merge)


async def _mind_create(
    db_path: str,
    config: str | None,
    subject: str,
    goals: list[str] | None,
    beliefs: list[str] | None,
    fears: list[str] | None,
    motivations: list[str] | None,
) -> LearnResult:
    async with MemorySystem.managed(db_path, config=config, auto_dream=False) as mem:
        return await mem.mind_create(
            subject, goals=goals, beliefs=beliefs, fears=fears, motivations=motivations,
        )


async def _mind_predict(
    db_path: str,
    config: str | None,
    mind_block_id: str,
    prediction: str,
    verify_at: str,
    reasoning: str | None,
) -> MindPredictResult:
    async with MemorySystem.managed(db_path, config=config, auto_dream=False) as mem:
        return await mem.mind_predict(
            mind_block_id, prediction, verify_at=verify_at, reasoning=reasoning,
        )


async def _mind_list(db_path: str, config: str | None) -> list[MindSummary]:
    async with MemorySystem.managed(db_path, config=config, auto_dream=False) as mem:
        return await mem.mind_list()


async def _mind_show(
    db_path: str, config: str | None, mind_block_id: str,
) -> MindShowResult:
    async with MemorySystem.managed(db_path, config=config, auto_dream=False) as mem:
        return await mem.mind_show(mind_block_id)


async def _mind_outcome(
    db_path: str,
    config: str | None,
    decision_block_id: str,
    hit: bool,
    reason: str,
) -> MindOutcomeResult:
    async with MemorySystem.managed(db_path, config=config, auto_dream=False) as mem:
        return await mem.mind_outcome(decision_block_id, hit=hit, reason=reason)


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
