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
from elfmem.config import ElfmemConfig
from elfmem.exceptions import ElfmemError
from elfmem.guide import get_guide
from elfmem.types import (
    ArchiveReason,
    BlockSummary,
    CorpusReviewResult,
    CurateResult,
    EditResult,
    ForgetResult,
    FrameResult,
    InboxBlockSummary,
    LearnResult,
    MindOutcomeResult,
    MindPredictResult,
    MindShowResult,
    MindSummary,
    OutcomeResult,
    PeerInboxStatus,
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


def _load_project_env() -> Path | None:
    """Auto-load a project-root .env into os.environ, if one exists (v2 step 3).

    Previously .env was only loaded via ``serve --env-file``, an opt-in flag
    — every other command (remember, recall, doctor, ...) never saw a
    project's .env at all unless the key happened to already be in the real
    shell environment. Real environment variables always win — see
    ``load_env_file``'s setdefault semantics. Returns the .env path found
    (for callers that want to report the source), or None.
    """
    env_path = _project.find_env_file()
    if env_path is not None:
        _project.load_env_file(env_path)
    return env_path


def _resolve_paths(
    db: str | None,
    config: str | None,
) -> tuple[str, str | None]:
    """Resolve (db_path, config_path) via the full discovery chain.

    config_path may be None if no config file exists anywhere.
    db_path always resolves to something (falls back to ~/.elfmem/agent.db).
    Exits with code 1 only if both explicit --db and all fallbacks are absent
    — which in practice means the global fallback path is always returned.

    Also auto-loads a project-root .env before resolving — see
    ``_load_project_env``.
    """
    _load_project_env()
    config_path, _source = _project.resolve_config(config)
    db_path, _db_source = _project.resolve_db(db, config_path)
    return db_path, config_path


def _resolve_config_only(config: str | None) -> str | None:
    """Resolve just the config path — for commands with no notion of "the
    database" (``index check``/``index rebuild`` write to files/--to only).

    Deliberately does not call ``_resolve_paths``: that also resolves a db
    path via the global-fallback chain, which refuses to run under pytest
    (see ``_project._guard_test_fallback``) even though these commands never
    use the db path at all.
    """
    _load_project_env()
    config_path, _source = _project.resolve_config(config)
    return config_path


def _resolve_memory_dir(memory_dir: str | None, config_path: str | None) -> Path:
    """Resolve the L1 file-substrate directory: ``--memory-dir``, else
    ``<config_dir>/memory`` (``.elfmem/memory`` sits alongside
    ``.elfmem/config.yaml``), else the project root's ``.elfmem/memory``,
    else ``~/.elfmem/memory``.
    """
    if memory_dir is not None:
        return Path(memory_dir).expanduser()
    if config_path is not None:
        return Path(config_path).expanduser().resolve().parent / "memory"
    root = _project.find_project_root()
    if root is not None:
        return root / ".elfmem" / "memory"
    return Path("~/.elfmem/memory").expanduser()


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
    agent_name: Annotated[
        str,
        typer.Option(
            "--name",
            help=(
                "Agent invocation name (e.g. 'elf', 'Nim'). Renders the "
                "'Agent Identity' section into .elfmem/AGENT.md so the host "
                "LLM knows to recall SELF when called by name. Empty = no "
                "named-agent behaviour."
            ),
        ),
    ] = "",
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
    force_new: Annotated[
        bool,
        typer.Option(
            "--force-new",
            help=(
                "Bypass the rescue check and create a fresh DB even if a "
                "populated DB exists at a neighbouring path. Almost never "
                "needed — prefer 'elfmem rescue'."
            ),
        ),
    ] = False,
    seed: Annotated[
        bool,
        typer.Option(
            "--seed/--no-seed",
            help="Seed constitutional cognitive loop blocks (opt-in, v2 step 4)",
        ),
    ] = False,
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
    """Initialise OR refresh elfmem: state-aware setup, idempotent by design.

    One verb, three behaviours selected by lifecycle state:

    - **Fresh install** (no config, no DB, or empty DB): creates the config
      and writes the elfmem section to CLAUDE.md / AGENTS.md. Writes zero
      memory blocks by default (v2 step 4) — nothing is added to memory
      before you've expressed a preference. Pass ``--seed`` to also seed the
      constitutional cognitive loop.
    - **Established instance** (config + DB with content rows): refresh-only
      mode. Skips config write; re-renders the agent doc section from the
      LIVE config (not from inferred defaults — Bug A fix); if ``--seed`` is
      passed, runs the constitutional seed idempotently (no-op when all
      roles are filled); installs the AGENT.md fragment. Print banner:
      ``[established — refreshing only]``.
    - **Orphaned DB** (configured DB is empty but populated DB exists at a
      neighbour path): refuses with a pointer to ``elfmem rescue``.
    - **Unreadable DB**: refuses with a pointer to ``elfmem doctor``.

    Safe to re-run anywhere, anytime. The principle: authoritative state is
    read, never inferred. Config is truth; defaults are bootstrap only on
    first install.

    Flags:

    - ``--force`` overwrites the config even on established instances. Use
      when you genuinely want to rewrite from defaults.
    - ``--force-new`` bypasses the orphan-DB check. Almost never needed —
      prefer ``elfmem rescue``.
    - ``--global`` uses ``~/.elfmem/`` regardless of project detection.
    - ``--no-docs`` skips CLAUDE.md / AGENTS.md updates.
    - ``--seed`` opts into the 10 constitutional cognitive-loop blocks — an
      opinionated starting personality, not a requirement. Previously this
      was the default; a fresh install now writes zero blocks unless asked.

    Examples:

        elfmem init                                   # fresh or refresh, auto — writes zero blocks
        elfmem init --seed                            # also seed the constitutional cognitive loop
        elfmem init --self "I am a software engineering assistant"
        elfmem init --seed --template coding --self "My coding principles..."
        elfmem init --global                          # force global config
        elfmem init --no-docs                         # skip doc update
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
        # State-aware --name update on established instances: full config
        # rewrite is too destructive, but silently dropping --name is too
        # surprising. Surgically update just the agent_name field iff it was
        # explicitly passed and differs from the current value. This preserves
        # comments, custom values, and any hand-edits.
        if agent_name and in_project:
            current_name = _project.read_agent_name_from_config(resolved_config)
            if current_name != agent_name:
                action = _project.set_agent_name_in_config(resolved_config, agent_name)
                config_action = f"exists (agent_name {action}: {current_name!r} → {agent_name!r})"
    else:
        project_cfg: ProjectConfig | None = None
        if in_project:
            import datetime
            project_cfg = ProjectConfig(
                name=proj_name,
                db=resolved_db,
                identity=self_description or "",
                agent_name=agent_name,
                created=datetime.date.today().isoformat(),
            )
        config_file.write_text(render_default_config(project_cfg), encoding="utf-8")
        config_action = "created"

    # ── State detection: pick the right behaviour for THIS invocation ─────
    # The `init` command is state-aware: one verb, three behaviours selected
    # by the lifecycle detector. Established instances get refresh-only
    # treatment (don't rewrite config; render docs from config; idempotent
    # seed); orphan and unreadable states refuse with the right pointer
    # rather than mutating something the user didn't intend to mutate.
    from elfmem.lifecycle import is_established_instance
    state = is_established_instance(
        resolved_config if config_file.exists() else None,
        resolved_db,
    )

    if state.kind == "orphan" and not force_new:
        plan = state.rescue_plan
        assert plan is not None  # populated by detector for orphan kind
        if plan.action == "rebind":
            target = plan.suggested_target
            typer.echo(
                f"Refusing to create or refresh against {resolved_db}.\n"
                f"A populated DB exists at {target} "
                f"({plan._target_candidate.block_count} blocks, "
                f"{plan._target_candidate.peer_count} peers).\n"
                "\n"
                "This is likely the 0.13.0 path-resolution regression. "
                "Recover with:\n"
                "  elfmem rescue --apply --yes\n"
                "\n"
                "If you genuinely want a fresh DB and accept the orphan, "
                "re-run with --force-new.",
                err=True,
            )
        else:
            typer.echo(
                "Refusing — multiple populated DBs found at neighbour paths:",
                err=True,
            )
            for c in plan.populated_alternatives:
                typer.echo(
                    f"  • {c.path}  ({c.block_count} blocks, "
                    f"{c.peer_count} peers)",
                    err=True,
                )
            typer.echo(
                "\nReview the candidates, set project.db in config to an "
                "absolute path pointing at the right one, then re-run.\n"
                "Or run 'elfmem rescue' for the structured plan.",
                err=True,
            )
        raise typer.Exit(code=1)

    if state.kind == "unreadable":
        typer.echo(
            f"Refusing — configured DB exists but cannot be read:\n"
            f"  {resolved_db}\n"
            f"  {state.reason}\n"
            "\n"
            "Back up the file, then run 'elfmem doctor' to diagnose. "
            "init will not silently overwrite an unreadable DB.",
            err=True,
        )
        raise typer.Exit(code=1)

    # Mode banner — visible explicit signal of which branch ran.
    if state.established and not force:
        mode_banner = (
            f"[established — refreshing only "
            f"({state.block_count} blocks, {state.peer_count} peers)]"
        )
    elif state.established and force:
        mode_banner = "[established + --force — overwriting config and refreshing]"
    else:
        mode_banner = "[fresh install]"

    # Also create the database directory if needed.
    Path(resolved_db).parent.mkdir(parents=True, exist_ok=True)

    # ── Create the file substrate ──────────────────────────────────────────
    # `.elfmem/memory/` plus a local .gitignore. This writes no blocks (see
    # the seed section below, which defaults to off); it creates the place
    # memory lives and makes it versionable. Until v2 Phase 0 `init` created
    # neither, so "git is the audit trail" -- the whole answer to RC3 -- had
    # nothing behind it and forget() destroyed text unrecoverably.
    scaffold_agent_name = (
        _project.read_agent_name_from_config(resolved_config)
        if config_file.exists()
        else (agent_name or "elf")
    )
    scaffold_actions = _project.ensure_memory_scaffold(
        config_file.parent, agent_name=scaffold_agent_name
    )
    memory_dir_path = config_file.parent / "memory"
    memory_created = sum(1 for a in scaffold_actions.values() if a == "created")

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

        # Bug A fix: on established instances render values come from the
        # live config, NOT from inferred dir-basename / home-path defaults.
        # On fresh installs there is no config to read from, so defaults
        # are correct and used. The principle: authoritative state is read,
        # never inferred. Config is truth; defaults are bootstrap only.
        if state.established and not force:
            cfg_name, cfg_db = _project.read_render_values_from_config(resolved_config)
            render_name = cfg_name or proj_name
            render_db = cfg_db or resolved_db
        else:
            render_name = proj_name
            render_db = resolved_db

        doc_action = _project.write_agent_section(
            doc_path,
            name=render_name,
            db_path=render_db,
            config_path=resolved_config,
            identity=self_description or "",
            project_root=proj_root,
        )
        doc_path_str = str(doc_path)

    # ── Output ─────────────────────────────────────────────────────────────

    mcp_snippet = _project.mcp_json_snippet(
        config_path=resolved_config, project_root=proj_root
    )

    if json_output:
        out: dict[str, Any] = {
            "mode": "project" if in_project else "global",
            "lifecycle": state.to_dict(),
            "mode_banner": mode_banner,
            "config_path": resolved_config,
            "config_action": config_action,
            "memory_dir": str(memory_dir_path),
            "memory_scaffold": scaffold_actions,
            "db_path": resolved_db,
        }
        if in_project:
            out["project_name"] = proj_name
        if template:
            out["template"] = template
        if seed_results:
            created = sum(1 for r in seed_results if r["status"] == "created")
            out["constitutional_blocks"] = {"created": created, "total": len(seed_results)}
        elif not seed and not state.established:
            out["constitutional_blocks"] = {"skipped": True, "hint": "elfmem init --seed"}
        if self_result is not None:
            out["self_block"] = self_result
        if doc_action is not None:
            out["agent_doc"] = {"path": doc_path_str, "action": doc_action}
        out["mcp_snippet"] = mcp_snippet
        _json(out)
    else:
        typer.echo(f"✓  Mode:      {mode_banner}")
        if in_project:
            typer.echo(f"✓  Project:   {proj_name} (detected)")
        typer.echo(f"✓  Config:    {resolved_config} ({config_action})")
        typer.echo(f"✓  Database:  {resolved_db} (ready)")
        if memory_created:
            typer.echo(
                f"✓  Memory:    {memory_dir_path} "
                f"({memory_created} path(s) created)"
            )
            typer.echo(
                "   Commit it: git add .elfmem/memory && git commit "
                "— git history is the undo path for forget()/edit()."
            )
        else:
            typer.echo(f"✓  Memory:    {memory_dir_path} (exists)")
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
        elif not seed and not state.established:
            typer.echo(
                "   Seed:      Skipped (default). Nothing was added to memory. "
                "Run 'elfmem init --seed' for the 10 constitutional "
                "cognitive-loop blocks, or add --template <name> alongside it."
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

        # Auto-generate agent-docs fragment for CI/automation integration
        if in_project:
            try:
                from importlib.metadata import version as _pkg_version

                from elfmem.agent_docs import get_fragment_hash, render_agent_docs, write_lock_file
                fragment_path = Path(resolved_config).parent.parent / "AGENT.md"
                # Source agent_name from live config — handles both fresh (just-written)
                # and established (refresh-only) cases identically.
                fragment_agent_name = _project.read_agent_name_from_config(resolved_config)
                content = render_agent_docs(agent_name=fragment_agent_name)
                fragment_path.write_text(content, encoding="utf-8")
                lib_version = _pkg_version("elfmem")
                hash_val = get_fragment_hash(content)
                write_lock_file(Path(resolved_config).parent / ".agent-docs.lock", lib_version, hash_val)
            except Exception:
                pass  # Non-fatal if agent-docs setup fails

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
    migrate_mcp: Annotated[
        bool,
        typer.Option(
            "--migrate-mcp",
            help="Scan Claude MCP configs for stale elfmem entries and print fixes.",
        ),
    ] = False,
    resolve: Annotated[
        bool,
        typer.Option(
            "--resolve",
            help="Also make one real LLM call to confirm the configured key works.",
        ),
    ] = False,
    frames: Annotated[
        bool,
        typer.Option(
            "--frames",
            help="Render every frame and print exactly what the agent receives.",
        ),
    ] = False,
) -> None:
    """Diagnose your elfmem setup. Reports what is configured and what is missing.

    Walks the full config and DB discovery chain to show exactly which files are
    active for the current directory. Checks API keys, SELF blocks, peer
    communication setup (identity, inbox/outbox paths, delivery paths, inbox
    drift), and whether the project's agent doc (CLAUDE.md / AGENTS.md) is
    configured. A project-root .env is auto-loaded before any check runs (same
    discovery as every other command), so the "API keys" check reflects it.

    Exits with code 1 if any required item is missing; 0 if fully configured.
    Read-only: never writes to the database or config files. --resolve is the
    one exception to "free" — it makes a real LLM call, so it costs time and
    (for hosted models) money, which is why it is opt-in rather than a
    default check.

    Use --modules to print the key module map without running health checks.
    Use --migrate-mcp to scan ~/.claude/claude_code_config.json and the local
    .claude.json for elfmem MCP entries that use deprecated env vars or the
    legacy 'python -m elfmem.mcp' launch pattern. Prints a diff per finding;
    never writes — you apply the change yourself.
    Use --resolve to confirm the configured LLM key actually works, not just
    that a string is present in the environment — this is the check that
    would have caught a silently-degrading mock/no-op adapter at setup time
    rather than at first real use.
    Use --frames to render every frame and print what the agent actually
    receives: rendered vs dropped blocks, the reason for each drop, token
    budget used, and how many blocks are still sitting in the inbox
    invisible to all of them. Exits non-zero only when a *guaranteed* block
    was dropped — the one case a frame's guarantee exists to prevent.
    """
    if modules:
        typer.echo(_project.format_key_modules())
        return
    if migrate_mcp:
        _doctor_migrate_mcp(json_output)
        return
    if frames:
        _doctor_frames(db, config, json_output)
        return
    env_path = _load_project_env()
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

    # Suggestion path branches on cause: "DB missing AND a populated neighbour
    # exists" is the 0.13.0 regression — ALWAYS suggest 'elfmem rescue', NEVER
    # 'elfmem init' (init created the empty DB in the first place).
    db_suggestion = f"elfmem init --db {db_path}"
    if not db_file.exists() or _looks_empty(db_file):
        try:
            from elfmem.rescue import build_rescue_plan
            plan = build_rescue_plan(db_path, config_path)
            if plan.action in ("rebind", "ambiguous"):
                db_suggestion = "elfmem rescue"
        except Exception:
            pass

    _check(
        "Database",
        db_file.exists(),
        f"{db_path} ({db_source})",
        db_suggestion,
    )

    # Drift check: surface populated neighbour DBs even when the configured
    # DB is fine. This is informational unless the configured DB is empty.
    try:
        from elfmem.rescue import build_rescue_plan
        plan = build_rescue_plan(db_path, config_path)
        if plan.action == "rebind":
            assert plan.suggested_target is not None
            _check(
                "DB drift",
                False,
                f"populated DB at {plan.suggested_target} "
                f"({plan._target_candidate.block_count} blocks) is not the "
                "configured target — likely 0.13.0 path regression",
                "elfmem rescue --apply --yes",
            )
        elif plan.action == "ambiguous":
            _check(
                "DB drift",
                False,
                f"{len(plan.populated_alternatives)} populated DBs at neighbour paths",
                "elfmem rescue  # inspect candidates",
            )
    except Exception:
        pass

    # ── Scoring drift ──────────────────────────────────────────────────────
    # Memory-health surface for deep-sleep rescoring (v0.13.3): unscored
    # blocks (debt from --no-llm or LLM timeouts) and stale blocks (last
    # scored too long ago) are both drift; doctor's job is to tell the user
    # to act when drift exceeds tolerance, with a self-scaled --max
    # suggestion so the action is concrete.
    if db_file.exists():
        try:
            drift = _run(_doctor_scoring_drift(db_path, config_path))
            if drift is not None:
                cfg_warn_count = drift.get("warn_count", 25)
                cfg_warn_pct = drift.get("warn_percent", 25)
                stats = drift["stats"]
                drift_count = stats["drift"]
                pct = stats["percent_drift"]
                healthy = not (
                    drift_count > cfg_warn_count
                    or pct > cfg_warn_pct
                )
                detail = (
                    f"{stats['unscored']} unscored, {stats['stale']} stale "
                    f"(>{stats['target_max_age_days']}d, {pct:.1f}%)"
                )
                if healthy:
                    _check("Scoring drift", True, detail)
                else:
                    rec_max = drift["recommended_max"]
                    _check(
                        "Scoring drift", False, detail,
                        f"elfmem dream --rescore --max {rec_max}",
                    )
        except Exception:
            pass

    # ── Embedding lock ─────────────────────────────────────────────────────
    # Per-row blocks.embedding_model is the truth; system_config holds the
    # cached write-default. Doctor surfaces mismatches non-raising — the
    # wrapper does the hard-fail; doctor explains.
    if db_file.exists():
        try:
            lock = _run(_doctor_embedding_lock(db_path))
            if lock is None:
                pass  # DB inaccessible — covered by other checks
            elif not lock["model"]:
                _check(
                    "Embedding lock", True,
                    "FRESH (no lock yet — will set on first dream)",
                )
            else:
                cfg_model = ""
                try:
                    from elfmem.config import ElfmemConfig
                    cfg_obj = (
                        ElfmemConfig.from_yaml(config_path)
                        if config_path and Path(config_path).exists()
                        else ElfmemConfig.from_env()
                    )
                    cfg_model = cfg_obj.embeddings.model
                except Exception:
                    pass
                if not cfg_model or cfg_model == lock["model"]:
                    _check(
                        "Embedding lock", True,
                        f"OK ({lock['model']}, {lock['dims']}-dim)",
                    )
                else:
                    _check(
                        "Embedding lock", False,
                        f"MISMATCH — DB locked to {lock['model']!r}; "
                        f"config says {cfg_model!r}",
                        f"Edit embeddings.model to {lock['model']!r} OR "
                        "run `elfmem migrate-embeddings --execute`",
                    )
        except Exception:
            pass

    # ── API keys ───────────────────────────────────────────────────────────
    # env_path was loaded (if found) before any check ran, so this reflects
    # .env-provided keys too, not just the real shell environment.

    has_anthropic = bool(os.getenv("ANTHROPIC_API_KEY"))
    has_openai = bool(os.getenv("OPENAI_API_KEY"))
    key_name = "ANTHROPIC_API_KEY" if has_anthropic else ("OPENAI_API_KEY" if has_openai else None)
    key_source = f" (from {env_path})" if key_name and env_path is not None else ""
    _check(
        "API keys",
        has_anthropic or has_openai,
        f"{key_name}{key_source}" if key_name else "none set",
        "export ANTHROPIC_API_KEY='sk-ant-...' or OPENAI_API_KEY='sk-...', "
        "or add it to a .env file at your project root",
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

        # ── Agent docs fragment ─────────────────────────────────────────────
        from importlib.metadata import version as _pkg_version

        from elfmem.agent_docs import check_drift

        fragment_path = proj_root / ".elfmem" / "AGENT.md"
        lock_path = proj_root / ".elfmem" / ".agent-docs.lock"
        lib_version = _pkg_version("elfmem")
        doctor_config_path = proj_root / ".elfmem" / "config.yaml"
        doctor_agent_name = _project.read_agent_name_from_config(
            doctor_config_path if doctor_config_path.exists() else None
        )
        drifted, reason = check_drift(
            fragment_path, lock_path, lib_version, agent_name=doctor_agent_name
        )
        if drifted:
            _check(
                "Agent docs",
                False,
                f"Fragment {reason} ({lib_version})",
                "Run: elfmem agent-docs install",
            )
        else:
            _check("Agent docs", True, f".elfmem/AGENT.md current ({lib_version})")

        # ── MCP config ─────────────────────────────────────────────────────

        # Checks where each entry POINTS, not merely that one exists. An entry
        # naming a different project's config starts a server that reads
        # another instance's memory while every other surface still reports
        # healthy — that drift has now hidden in plain sight twice.
        reports = _project.check_mcp_entries(
            proj_root,
            expected_config=Path(config_path) if config_path else proj_root / ".elfmem" / "config.yaml",
            expected_db=db_path,
        )
        if not reports:
            _check(
                "MCP config",
                False,
                ".claude.json / claude-code.yaml has no elfmem entry",
                "Add MCP entry (shown at end of elfmem init output)",
            )
        else:
            broken = [r for r in reports if not r.ok]
            if not broken:
                noun = "entry" if len(reports) == 1 else "entries"
                _check(
                    "MCP config",
                    True,
                    f"{len(reports)} elfmem {noun}, all pointed at this project",
                )
            else:
                for report in broken:
                    _check(
                        "MCP config",
                        False,
                        f"{report.path.name} → {report.server}: {report.issues[0]}",
                        "Run: elfmem migrate   (proposes the corrected entry)",
                    )
                    for extra in report.issues[1:]:
                        _check("MCP config", False, f"{report.path.name} → {extra}")

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

    # ── Peer communication ─────────────────────────────────────────────────

    if db_file.exists():
        peer_checks = _run(_doctor_peer_checks(db_path, config_path))
        for pc in peer_checks:
            _check(pc["label"], pc["ok"], pc["detail"], pc["suggestion"])

    # ── LLM preflight (opt-in — the only check that makes a real call) ──────

    if resolve:
        ok, detail = _run(_doctor_preflight(config_path))
        _check(
            "LLM preflight",
            ok,
            detail,
            "Check llm.model / llm.base_url in config.yaml and the API key "
            "reported above." if not ok else "",
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
    cue: Annotated[
        str | None,
        typer.Option("--cue", help="When a future agent should recall this"),
    ] = None,
) -> None:
    """Store knowledge for future retrieval."""
    db_path, config_path = _resolve_paths(db, config)
    tag_list = [t.strip() for t in tags.split(",")] if tags else None
    result, should_dream = _run(
        _remember(db_path, config_path, content, tag_list, category, cue)
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
        str, typer.Option("--frame", help="attention|self|task|simulate")
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
def edit(
    block_id: Annotated[str | None, typer.Argument()] = None,
    content: Annotated[str | None, typer.Argument()] = None,
    cue: Annotated[
        str | None,
        typer.Option("--cue", help="When a future agent should recall this block"),
    ] = None,
    cues_from: Annotated[
        str | None,
        typer.Option(
            "--cues-from",
            help='Batch-apply cues from a JSON file: {"block_id": "cue", ...}',
        ),
    ] = None,
    missing_cues: Annotated[
        bool,
        typer.Option("--missing-cues", help="List active blocks with no cue line"),
    ] = False,
    db: Annotated[str | None, typer.Option("--db", envvar="ELFMEM_DB")] = None,
    config: Annotated[
        str | None, typer.Option("--config", envvar="ELFMEM_CONFIG")
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Edit an active block's content and/or its cue line. No LLM mediation.

    A cue says *when* a future agent should recall the block, which is what
    lexical search matches against when the query's wording differs from the
    block's. Setting one does not re-embed or re-queue the block.

    Examples:

        elfmem edit a1b2c3d4 "Corrected content."
        elfmem edit a1b2c3d4 --cue "choosing a sync strategy"
        elfmem edit --missing-cues --json
        elfmem edit --cues-from cues.json
    """
    db_path, config_path = _resolve_paths(db, config)

    if missing_cues:
        rows = _run(_list_missing_cues(db_path, config_path))
        if json_output:
            _json([
                {"id": r["id"], "category": r["category"], "content": r["content"]}
                for r in rows
            ])
        else:
            typer.echo(f"{len(rows)} active block(s) with no cue line")
            for r in rows:
                first = r["content"].strip().splitlines()[0][:70]
                typer.echo(f"  {r['id']}  [{r['category']}]  {first}")
        return

    if cues_from is not None:
        cues = json.loads(Path(cues_from).expanduser().read_text(encoding="utf-8"))
        applied, skipped = _run(_edit_cues_batch(db_path, config_path, cues))
        if json_output:
            _json({"applied": applied, "skipped": skipped})
        else:
            typer.echo(f"Applied {applied} cue(s); {len(skipped)} block(s) not found.")
        return

    if block_id is None:
        typer.echo(
            "Error: provide a block id, or use --missing-cues / --cues-from.",
            err=True,
        )
        raise typer.Exit(1)

    result = _run(_edit(db_path, config_path, block_id, content, cue))
    _json(result.to_dict()) if json_output else typer.echo(str(result))


@app.command()
def forget(
    block_id: str,
    db: Annotated[str | None, typer.Option("--db", envvar="ELFMEM_DB")] = None,
    config: Annotated[
        str | None, typer.Option("--config", envvar="ELFMEM_CONFIG")
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Archive a block by explicit request. Idempotent — safe to call twice."""
    db_path, config_path = _resolve_paths(db, config)
    result = _run(_forget(db_path, config_path, block_id))
    _json(result.to_dict()) if json_output else typer.echo(str(result))


@app.command(name="ls")
def ls_cmd(
    tag: Annotated[
        str | None, typer.Option("--tag", help="SQL LIKE pattern, e.g. 'self/%'")
    ] = None,
    category: Annotated[str | None, typer.Option("--category")] = None,
    limit: Annotated[int, typer.Option("--limit")] = 50,
    db: Annotated[str | None, typer.Option("--db", envvar="ELFMEM_DB")] = None,
    config: Annotated[
        str | None, typer.Option("--config", envvar="ELFMEM_CONFIG")
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List active blocks — a deterministic, unscored view of memory."""
    db_path, config_path = _resolve_paths(db, config)
    results = _run(_ls(db_path, config_path, tag, category, limit))
    if json_output:
        _json([r.to_dict() for r in results])
    elif not results:
        typer.echo("No active blocks match.")
    else:
        for r in results:
            typer.echo(str(r))


@app.command()
def inbox(
    max_count: Annotated[int | None, typer.Option("--max")] = None,
    db: Annotated[str | None, typer.Option("--db", envvar="ELFMEM_DB")] = None,
    config: Annotated[
        str | None, typer.Option("--config", envvar="ELFMEM_CONFIG")
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List pending blocks not yet consolidated — FIFO order (oldest first).

    Read-only, no LLM calls. USE WHEN reasoning about pending blocks
    yourself before `dream --host-analyses` — e.g. this Claude Code session
    supplying alignment_score/tags/summary instead of a configured LLM
    adapter. See `elfmem guide inbox`.
    """
    db_path, config_path = _resolve_paths(db, config)
    results = _run(_inbox(db_path, config_path, max_count))
    if json_output:
        _json([r.to_dict() for r in results])
    elif not results:
        typer.echo("Inbox empty.")
    else:
        for r in results:
            typer.echo(str(r))


@app.command()
def status(
    db: Annotated[str | None, typer.Option("--db", envvar="ELFMEM_DB")] = None,
    config: Annotated[
        str | None, typer.Option("--config", envvar="ELFMEM_CONFIG")
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
    peer_inbox: Annotated[
        bool, typer.Option("--peer-inbox", help="Show peer inbox status only")
    ] = False,
) -> None:
    """System health and suggested next action."""
    db_path, config_path = _resolve_paths(db, config)
    if peer_inbox:
        result = _run(_peer_inbox_status(db_path, config_path))
        _json(result.to_dict()) if json_output else typer.echo(str(result))
    else:
        result = _run(_status(db_path, config_path))
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
    no_llm: Annotated[
        bool,
        typer.Option(
            "--no-llm",
            help=(
                "Promote without LLM scoring (embed-only). Affected blocks are "
                "tagged for catch-up via --rescore. Use for outages, bulk loads, "
                "cost-sensitive batches. NOT for default use."
            ),
        ),
    ] = False,
    rescore: Annotated[
        bool,
        typer.Option(
            "--rescore",
            help=(
                "After processing inbox, refresh aged or unscored active "
                "blocks against current SELF. Catches up --no-llm debt and "
                "rotates oldest blocks. Run periodically for hygiene."
            ),
        ),
    ] = False,
    max_count: Annotated[
        int | None,
        typer.Option(
            "--max",
            help=(
                "Budget cap (ADR 0007). Applies to inbox processing "
                "(default from consolidation.max_inbox_per_run, typically 5) "
                "and, with --rescore, to the rescore pass too (default from "
                "consolidation.rescore.max_per_run, typically 20) — the same "
                "number caps each stage that actually runs this call. Use a "
                "large value (e.g. 100000) for a one-shot full sweep."
            ),
        ),
    ] = None,
    metabolism_dry_run: Annotated[
        bool,
        typer.Option(
            "--metabolism-dry-run",
            help=(
                "Propose goal-directed connections (judged against self/goal "
                "blocks, not similarity) for rescore-eligible blocks and "
                "report them — writes nothing to the edges table. Stage A of "
                "docs/plans/plan_edge_metabolism.md; ignores --rescore/--no-llm."
            ),
        ),
    ] = False,
    host_analyses_file: Annotated[
        str | None,
        typer.Option(
            "--host-analyses",
            help=(
                "Path to a JSON file supplying your own alignment_score/tags/"
                "summary per block instead of the configured LLM adapter — "
                "{\"block_id\": {\"alignment_score\": 0.8, \"tags\": [...], "
                "\"summary\": \"...\"}, ...}. See `elfmem inbox` for the read "
                "half of this loop. Blocks not covered still use the normal "
                "path (configured adapter, or --no-llm fallback)."
            ),
        ),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Consolidate pending knowledge: embed, align, promote to active memory.

    Default mode processes inbox blocks with LLM scoring, bounded to
    ``consolidation.max_inbox_per_run`` blocks per call (default 5 — ADR
    0007). A larger backlog drains across repeated calls; check the
    ``inbox_remaining`` on the result. Flags adjust the LLM workload
    (--no-llm) or extend the work to include refreshing existing active
    blocks (--rescore).

    USE WHEN:
      Default (no flags): standard consolidation after a learn batch.
      --no-llm:           LLM down / bulk load / cost-sensitive batch.
      --rescore:          catch-up after --no-llm; periodic hygiene; refresh
                          alignment as the agent's identity evolves.
      --max N:            override the inbox-processing budget for this call
                          (and the rescore budget too, if --rescore is set).
      --rescore --max N:  one-shot deep sweep (large N) of both stages.
      --metabolism-dry-run: report goal-directed connections the agent would
                          propose, without writing any — see
                          docs/plans/plan_edge_metabolism.md.
      --host-analyses FILE: supply your own per-block analysis (e.g. from
                          this Claude Code session reasoning over
                          `elfmem inbox`) instead of the configured LLM
                          adapter. Composable with --no-llm (covered blocks
                          get real analysis, the rest get the neutral
                          fallback) and --rescore (rescore still uses the
                          configured adapter — out of scope for this flag).

    DON'T USE:
      --no-llm by default (degrades SELF-frame coherence over time).
      --no-llm in tight loops without --rescore follow-up.
      --rescore on a hot DB during heavy use (brief write locks per block).
    """
    if no_llm and rescore:
        typer.echo(
            "Error: --no-llm and --rescore are mutually exclusive "
            "(rescore requires the LLM).",
            err=True,
        )
        raise typer.Exit(code=1)
    if metabolism_dry_run and (rescore or no_llm or host_analyses_file):
        typer.echo(
            "Error: --metabolism-dry-run is its own read-only pass and "
            "ignores --rescore/--no-llm/--host-analyses — run it on its own.",
            err=True,
        )
        raise typer.Exit(code=1)

    host_analyses: dict[str, dict[str, Any]] | None = None
    if host_analyses_file:
        try:
            with open(host_analyses_file, encoding="utf-8") as f:
                host_analyses = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            typer.echo(f"Error reading --host-analyses {host_analyses_file}: {e}", err=True)
            raise typer.Exit(code=1) from e

    db_path, config_path = _resolve_paths(db, config)

    if metabolism_dry_run:
        dry_result = _run(_metabolism_dry_run_async(db_path, config_path, max_count))
        if json_output:
            _json(dry_result.to_dict())
        else:
            typer.echo(str(dry_result))
            for p in dry_result.proposals:
                typer.echo(f"  {p.block_id} -> {p.candidate_id}: {p.reasoning}")
        return

    result = _run(_dream(
        db_path, config_path,
        skip_llm=no_llm,
        rescore=rescore,
        max_count=max_count,
        host_analyses=host_analyses,
    ))
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
    """Prune weak/decayed edges, reinforce top knowledge."""
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
def agent_docs(
    action: Annotated[
        str,
        typer.Argument(help="install | check | diff"),
    ],
) -> None:
    """Manage agent-docs fragment (.elfmem/AGENT.md).

    The fragment is auto-generated from guide.GUIDES and kept in sync with the
    installed library version. Detect and fix drift with check/diff/install.

    Actions:
        install — Generate/regenerate fragment (idempotent)
        check   — Report drift status (exit non-zero if drifted)
        diff    — Show what would change without writing
    """
    from importlib.metadata import version as pkg_version

    from elfmem.agent_docs import (
        check_drift,
        read_lock_file,
        render_agent_docs,
        write_lock_file,
    )

    lib_version = pkg_version("elfmem")
    root = Path.cwd()
    fragment_path = root / ".elfmem" / "AGENT.md"
    lock_path = root / ".elfmem" / ".agent-docs.lock"
    config_path = root / ".elfmem" / "config.yaml"
    fragment_agent_name = _project.read_agent_name_from_config(
        config_path if config_path.exists() else None
    )

    if action == "install":
        fragment_path.parent.mkdir(parents=True, exist_ok=True)
        content = render_agent_docs(agent_name=fragment_agent_name)
        fragment_path.write_text(content, encoding="utf-8")
        from elfmem.agent_docs import get_fragment_hash

        hash_val = get_fragment_hash(content)
        write_lock_file(lock_path, lib_version, hash_val)
        typer.echo(f"✓ {fragment_path}")

    elif action == "check":
        drifted, reason = check_drift(
            fragment_path, lock_path, lib_version, agent_name=fragment_agent_name
        )
        if not drifted:
            typer.echo(f"✓ Agent docs current ({lib_version})")
            raise typer.Exit(code=0)
        elif reason == "missing":
            typer.echo("✗ Agent docs missing. Run: elfmem agent-docs install")
            raise typer.Exit(code=1)
        elif reason == "stale_version":
            lock = read_lock_file(lock_path) or {}
            old_v = lock.get("library_version", "?")
            typer.echo(
                f"✗ Agent docs stale (lib: {lib_version}, fragment: {old_v}). "
                f"Run: elfmem agent-docs install"
            )
            raise typer.Exit(code=1)
        elif reason == "edited":
            typer.echo(
                "✗ Agent docs edited by hand. "
                "Run: elfmem agent-docs install (with --force to overwrite)"
            )
            raise typer.Exit(code=1)

    elif action == "diff":
        if not fragment_path.exists():
            typer.echo("Fragment missing. Run: elfmem agent-docs install")
            raise typer.Exit(code=1)
        current = render_agent_docs(agent_name=fragment_agent_name)
        existing = fragment_path.read_text(encoding="utf-8")
        if current == existing:
            typer.echo("No changes.")
        else:
            typer.echo("Proposed changes:")
            typer.echo("")
            import difflib

            diff = difflib.unified_diff(
                existing.splitlines(keepends=True),
                current.splitlines(keepends=True),
                fromfile="existing",
                tofile="proposed",
            )
            typer.echo("".join(diff))

    else:
        typer.echo(f"Unknown action: {action}. Use: install | check | diff")
        raise typer.Exit(code=1)


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
    env_file: Annotated[
        str | None,
        typer.Option(
            "--env-file",
            help=(
                "Load KEY=VALUE pairs from this file into the environment "
                "before starting (e.g. OPENAI_API_KEY, ANTHROPIC_API_KEY). "
                "Never overrides variables already set in the environment."
            ),
        ),
    ] = None,
) -> None:
    """Start the elfmem MCP server for agent tool integration.

    --db is optional when a project config with project.db is discoverable
    from the current directory (set up via 'elfmem init').
    """
    if env_file:
        env_file_path = Path(env_file)
        if not env_file_path.exists():
            typer.echo(f"Error: --env-file not found: {env_file_path}", err=True)
            raise typer.Exit(1)
        _project.load_env_file(env_file_path)

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


# ── Rescue ───────────────────────────────────────────────────────────────────


@app.command()
def rescue(
    db: Annotated[
        str | None, typer.Option("--db", envvar="ELFMEM_DB")
    ] = None,
    config: Annotated[
        str | None, typer.Option("--config", envvar="ELFMEM_CONFIG")
    ] = None,
    apply: Annotated[
        bool,
        typer.Option(
            "--apply",
            help=(
                "Rewrite project.db in the config to the suggested absolute "
                "path. A timestamped backup of the config is taken first."
            ),
        ),
    ] = False,
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Skip confirmation prompt.")
    ] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Detect orphaned populated DBs and propose a rebind plan.

    Read-only by default. Surfaces the rescue path for users hit by the
    0.13.0 path-resolution regression: an empty configured DB and a
    populated DB at a sibling path.

    --apply rewrites project.db in the config to the suggested absolute
    path (after writing a timestamped config backup). Requires either the
    plan to be unambiguous (action='rebind') or --yes plus exactly one
    populated alternative.
    """
    from elfmem.rescue import build_rescue_plan

    config_path, _ = _project.resolve_config(config)
    db_path, _ = _project.resolve_db(db, config_path)
    plan = build_rescue_plan(db_path, config_path)

    if json_output:
        _json(plan.to_dict())
        if plan.action in ("rebind", "ambiguous"):
            raise typer.Exit(1)
        return

    typer.echo(plan.summary)
    typer.echo("")
    typer.echo("Configured:")
    c = plan.configured
    typer.echo(
        f"  {'✓' if c.populated else '·'}  {c.path}\n"
        f"      exists={c.exists}  blocks={c.block_count}  "
        f"peers={c.peer_count}  size={c.size_bytes:,} bytes"
    )
    others = [x for x in plan.candidates if x is not c]
    if others:
        typer.echo("")
        typer.echo("Neighbours:")
        for x in others:
            mark = "★" if x.populated else "·"
            typer.echo(
                f"  {mark}  {x.path}\n"
                f"      exists={x.exists}  blocks={x.block_count}  "
                f"peers={x.peer_count}  size={x.size_bytes:,} bytes"
            )

    if plan.action == "ambiguous":
        typer.echo("")
        typer.echo(
            "Multiple populated DBs found — refusing to choose. Inspect each "
            "candidate manually and edit project.db in your config to point "
            "at the correct one (use an absolute path)."
        )
        raise typer.Exit(1)

    if plan.action != "rebind":
        return

    target = plan.suggested_target
    typer.echo("")
    typer.echo(f"Proposed: rewrite project.db → {target}")
    typer.echo(f"Config:   {config_path}")

    if not apply:
        typer.echo("")
        typer.echo("Re-run with --apply to perform the rewrite.")
        raise typer.Exit(1)

    if not yes:
        confirm = typer.confirm("Apply rebind?", default=False)
        if not confirm:
            typer.echo("Aborted.")
            raise typer.Exit(1)

    if config_path is None:
        typer.echo("No config file to rewrite — cannot apply rescue.", err=True)
        raise typer.Exit(1)
    backup = _rewrite_project_db_in_config(Path(config_path), str(target))
    typer.echo(f"✓ rebind applied. Config backup: {backup}")


def _rewrite_project_db_in_config(config_path: Path, new_db_path: str) -> Path:
    """Rewrite ``project.db`` in *config_path* to *new_db_path*. Backup first.

    Returns the backup path. Atomic via tmp-file rename. Pure-yaml
    round-trip — preserves other keys verbatim, only the project.db value
    changes.
    """
    import time

    import yaml

    text = config_path.read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    if "project" not in data or not isinstance(data["project"], dict):
        data["project"] = {}
    data["project"]["db"] = new_db_path

    backup = config_path.with_name(
        f"{config_path.name}.elfmem-bak-rescue-{time.time_ns()}"
    )
    backup.write_bytes(config_path.read_bytes())

    tmp = config_path.with_suffix(config_path.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    os.replace(tmp, config_path)
    return backup


# ── Migration subcommands ────────────────────────────────────────────────────

migrate_app = typer.Typer(
    name="migrate",
    help=(
        "Migrate elfmem between versions: Claude MCP config drift, and (if "
        "pending) the v2 file-substrate export.\n\n"
        "Plan-and-apply model: 'plan' shows what would change (read-only), "
        "'apply' performs the changes atomically with backups. Designed for "
        "agent invocation — every subcommand supports --json."
    ),
    no_args_is_help=False,
    invoke_without_command=True,
)
app.add_typer(migrate_app, name="migrate")


@app.command(name="migrate-embeddings")
def migrate_embeddings(
    execute: Annotated[
        bool,
        typer.Option("--execute", help="Actually re-embed (default: estimate only, no writes)"),
    ] = False,
    to_model: Annotated[
        str | None,
        typer.Option("--to", help="Override target model (default: embeddings.model from config)"),
    ] = None,
    from_model: Annotated[
        str | None,
        typer.Option(
            "--from",
            help=(
                "Only migrate blocks currently tagged with this model. "
                "Used to disambiguate heterogeneous-source DBs."
            ),
        ),
    ] = None,
    batch_size: Annotated[
        int, typer.Option("--batch", help="Blocks per transaction (default 50)")
    ] = 50,
    db: Annotated[
        str | None, typer.Option("--db", envvar="ELFMEM_DB")
    ] = None,
    config_path: Annotated[
        str | None, typer.Option("--config", envvar="ELFMEM_CONFIG")
    ] = None,
) -> None:
    """Migrate stored embeddings to a different model.

    Default mode is **estimate** (no writes). Use ``--execute`` to actually
    re-embed. Re-running ``--execute`` after an interruption resumes
    automatically — blocks already at the target model are skipped.

    Recovery for the ``EmbeddingLockError`` raised when ``embeddings.model``
    in your config disagrees with the DB's lock.

    \b
    Examples:
        # Estimate only (always run this first)
        elfmem migrate-embeddings

        # Execute (re-embed every active block under the configured model)
        elfmem migrate-embeddings --execute

        # Disambiguate a heterogeneous DB
        elfmem migrate-embeddings --from old-model --to new-model --execute

    The migration verb bypasses the LockedEmbeddingService wrapper by
    construction — it uses ``make_embedding_adapter()`` and a bare engine
    directly, so it can re-embed under the new model without self-blocking
    against the old lock. See ``docs/plans/plan_embedding_lock.md``.
    """
    from elfmem.config import ElfmemConfig

    # Resolve config + DB paths via the same chain other commands use.
    resolved_config, resolved_db = _resolve_migrate_paths(config_path, db)
    if not Path(resolved_db).exists():
        typer.echo(f"DB not found: {resolved_db}", err=True)
        raise typer.Exit(code=1)

    try:
        cfg = (
            ElfmemConfig.from_yaml(resolved_config)
            if resolved_config and Path(resolved_config).exists()
            else ElfmemConfig.from_env()
        )
    except Exception as e:
        typer.echo(f"Config load failed: {e}", err=True)
        raise typer.Exit(code=1) from None

    # Determine target model
    target = to_model or cfg.embeddings.model
    if to_model is not None and to_model != cfg.embeddings.model:
        # Mutate cfg so make_embedding_adapter builds for the target.
        # cfg is local — never escapes this function.
        cfg.embeddings.model = to_model

    if execute:
        _run(_migrate_embeddings_execute(resolved_db, cfg, target, from_model, batch_size))
    else:
        _run(_migrate_embeddings_estimate(resolved_db, target, from_model))


def _resolve_migrate_paths(config_path: str | None, db: str | None) -> tuple[str, str]:
    """Mirror the resolution chain used by other write commands."""
    info = _project.get_project_info()
    if info is not None:
        resolved_config = config_path or str(info.config)
        resolved_db = db or info.db
    else:
        resolved_config = config_path or str(Path("~/.elfmem/config.yaml").expanduser())
        resolved_db = db or str(Path("~/.elfmem/agent.db").expanduser())
    return str(Path(resolved_config).expanduser()), str(Path(resolved_db).expanduser())


def _resolve_migrate_memory_dir(
    db: str | None, config_path: str | None,
) -> tuple[str, str, Path]:
    """(resolved_db, resolved_config, memory_dir) for the substrate_export
    step — same resolution chain as every other project-aware command."""
    resolved_config, resolved_db = _resolve_migrate_paths(config_path, db)
    memory_dir = _resolve_memory_dir(None, resolved_config)
    return resolved_db, resolved_config, memory_dir


async def _build_full_plan_for_project(db: str | None, config_path: str | None) -> Any:
    """The plan 'elfmem migrate status/plan/apply' operate on: Claude MCP
    config drift plus (if this project's database has content not yet
    exported) the v2 substrate migration step."""
    from elfmem.migrate import build_full_plan

    resolved_db, _, memory_dir = _resolve_migrate_memory_dir(db, config_path)
    return await build_full_plan(db_path=Path(resolved_db), memory_dir=memory_dir)


async def _apply_substrate_async(
    step: Any, memory_dir: Path, cfg: ElfmemConfig, dry_run: bool,
) -> Any:
    from elfmem.migrate import apply_substrate_step

    return await apply_substrate_step(step, memory_dir=memory_dir, cfg=cfg, dry_run=dry_run)


async def _undo_substrate_async(step: Any, memory_dir: Path, force: bool) -> Any:
    from elfmem.migrate import undo_substrate_step

    return await undo_substrate_step(step, memory_dir=memory_dir, force=force)


async def _migrate_embeddings_estimate(
    db_path: str, target: str, from_model: str | None
) -> None:
    """Report what `--execute` would do, no writes."""
    from sqlalchemy import text

    from elfmem.db.engine import create_engine

    engine = await create_engine(db_path)
    try:
        async with engine.connect() as conn:
            # Critical SQL: must catch NULL embedding_model rows. The naive
            # `embedding_model != :target` evaluates NULL as falsy and skips
            # them. Use IS NULL OR != target.
            query = (
                "SELECT COUNT(*), COALESCE(SUM(LENGTH(content)), 0) "
                "FROM blocks WHERE status = 'active' AND embedding IS NOT NULL "
                "AND (embedding_model IS NULL OR embedding_model != :target)"
            )
            params: dict[str, object] = {"target": target}
            if from_model is not None:
                query += " AND embedding_model = :from_model"
                params["from_model"] = from_model
            row = (await conn.execute(text(query), params)).first()
            count = int(row[0]) if row else 0
            total_chars = int(row[1]) if row else 0
    finally:
        await engine.dispose()

    typer.echo("Migration estimate:")
    typer.echo(f"  blocks to re-embed:    {count}")
    typer.echo(f"  total content chars:   {total_chars}")
    typer.echo(f"  rough token estimate:  ~{total_chars // 4}")
    typer.echo(f"  target model:          {target}")
    if from_model is not None:
        typer.echo(f"  source model (filter): {from_model}")
    typer.echo("")
    if count == 0:
        typer.echo("Nothing to migrate. (Existing blocks already at target model.)")
        return
    typer.echo("This will:")
    typer.echo("  • Create a backup of the DB")
    typer.echo("  • Re-embed each block under the target model")
    typer.echo("  • Drop edges where origin IN ('similarity', 'co_retrieval')")
    typer.echo("    (preserves user-asserted edges)")
    typer.echo("  • Update the embedding lock to the target model")
    typer.echo("")
    typer.echo("Run with --execute to proceed.")


async def _migrate_embeddings_execute(
    db_path: str,
    cfg: object,  # ElfmemConfig — typed as object to avoid heavy import here
    target: str,
    from_model: str | None,
    batch_size: int,
) -> None:
    """Actually re-embed. Auto-resumes if interrupted: blocks already at
    target are skipped via the SQL filter.

    Construct a BARE EmbeddingService — NOT through MemorySystem.from_config(),
    which would apply the LockedEmbeddingService wrapper and self-block.
    """
    from sqlalchemy import text

    from elfmem.adapters.factory import make_embedding_adapter
    from elfmem.db.engine import create_engine
    from elfmem.db.migrate import create_backup
    from elfmem.db.queries import embedding_to_bytes
    from elfmem.token_counter import TokenCounter

    backup_path = create_backup(db_path, suffix="pre-migrate-embeddings")
    if backup_path is None:
        typer.echo(f"Backup failed for {db_path}", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"Backup: {backup_path}")

    counter = TokenCounter()
    embedding_svc = make_embedding_adapter(cfg, counter)  # type: ignore[arg-type]
    # Bare adapter — NOT wrapped. This is the deliberate bypass.

    engine = await create_engine(db_path)
    last_vec_len = 0
    total_processed = 0
    try:
        # Loop pulling next batch until none left.
        while True:
            params: dict[str, object] = {"target": target, "limit": batch_size}
            select_query = (
                "SELECT id, content, summary FROM blocks "
                "WHERE status = 'active' AND embedding IS NOT NULL "
                "AND (embedding_model IS NULL OR embedding_model != :target)"
            )
            if from_model is not None:
                select_query += " AND embedding_model = :from_model"
                params["from_model"] = from_model
            select_query += " LIMIT :limit"

            async with engine.connect() as conn:
                rows = (await conn.execute(text(select_query), params)).all()

            if not rows:
                break

            # Embed + update each block in one transaction per batch
            async with engine.begin() as conn:
                for block_id, content, summary in rows:
                    embed_text = (summary if summary else content).strip().lower()
                    vec = await embedding_svc.embed(embed_text)
                    last_vec_len = len(vec)
                    await conn.execute(
                        text(
                            "UPDATE blocks SET embedding = :emb, embedding_model = :m "
                            "WHERE id = :id"
                        ),
                        {
                            "emb": embedding_to_bytes(vec),
                            "m": target,
                            "id": block_id,
                        },
                    )

            total_processed += len(rows)
            typer.echo(f"  ... {total_processed} blocks migrated")

        if total_processed == 0:
            typer.echo("Nothing to migrate — all blocks already at target.")
            return

        # Drop similarity-derived edges + update lock atomically
        async with engine.begin() as conn:
            edge_result = await conn.execute(
                text(
                    "DELETE FROM edges WHERE origin IN ('similarity', 'co_retrieval')"
                )
            )
            edges_dropped = edge_result.rowcount or 0
            await conn.execute(
                text(
                    "INSERT INTO system_config (key, value) VALUES "
                    "('embedding_model_lock', :m), "
                    "('embedding_dimensions_lock', :d) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
                ),
                {"m": target, "d": str(last_vec_len)},
            )
    finally:
        await engine.dispose()

    typer.echo("")
    typer.echo("✓ Migration complete.")
    typer.echo(f"  blocks migrated:  {total_processed}")
    typer.echo(f"  edges dropped:    {edges_dropped} (similarity-derived)")
    typer.echo(f"  new lock:         ({target}, {last_vec_len}-dim)")
    typer.echo(f"  backup:           {backup_path}")
    typer.echo("")
    typer.echo("Run `elfmem dream` to rebuild similarity edges from the new vectors.")


@migrate_app.callback()
def _migrate_default(ctx: typer.Context) -> None:
    """Default action when 'elfmem migrate' is called with no subcommand: status."""
    if ctx.invoked_subcommand is None:
        # Delegate to status with default args.
        ctx.invoke(migrate_status, json_output=False)


@migrate_app.command("status")
def migrate_status(
    db: Annotated[str | None, typer.Option("--db", envvar="ELFMEM_DB")] = None,
    config_path: Annotated[str | None, typer.Option("--config", envvar="ELFMEM_CONFIG")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """One-line summary per pending migration; exit 0 if nothing to do.

    Cheap to call repeatedly. Use this in scripts and pre-flight checks.
    Covers both Claude MCP config drift and (if this project's database has
    content not yet exported) the v2 file-substrate migration.
    """
    plan = _run(_build_full_plan_for_project(db, config_path))
    if json_output:
        _json({
            "pending_count": plan.pending_count,
            "step_ids": [s.id for s in plan.steps],
            "warnings": [w.to_dict() for w in plan.warnings],
            "summary": plan.summary,
        })
        if plan.pending_count or plan.warnings:
            raise typer.Exit(1)
        return

    if not plan.steps and not plan.warnings:
        typer.echo("No migrations pending.")
        return
    if plan.steps:
        typer.echo(f"{plan.pending_count} migration(s) pending:\n")
        for step in plan.steps:
            typer.echo(f"  • {step.id}")
            typer.echo(f"      {step.summary}")
            typer.echo(f"      file: {step.file}")
            typer.echo("")
    if plan.warnings:
        typer.echo(f"{len(plan.warnings)} unparseable file(s) — migration cannot inspect:\n")
        for w in plan.warnings:
            typer.echo(f"  ! {w.file}")
            typer.echo(f"      {w.error}")
            typer.echo("")
    if plan.steps:
        typer.echo("Next: 'elfmem migrate plan' to inspect, 'elfmem migrate apply' to execute.")
    raise typer.Exit(1)


@migrate_app.command("plan")
def migrate_plan(
    db: Annotated[str | None, typer.Option("--db", envvar="ELFMEM_DB")] = None,
    config_path: Annotated[str | None, typer.Option("--config", envvar="ELFMEM_CONFIG")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Full structured plan: per-step diffs, file hashes, apply commands.

    Read-only. The JSON output is the contract for agents — every step
    includes an 'apply_command' string ready to invoke. Covers both Claude
    MCP config drift and the v2 file-substrate migration.
    """
    plan = _run(_build_full_plan_for_project(db, config_path))
    if json_output:
        _json(plan.to_dict())
        if plan.pending_count:
            raise typer.Exit(1)
        return

    if not plan.steps:
        typer.echo("No migrations pending.")
        return

    typer.echo(f"{plan.pending_count} migration(s) pending.\n")
    for step in plan.steps:
        typer.echo(f"━━━ {step.id} ━━━")
        typer.echo(f"Summary: {step.summary}")
        typer.echo(f"File:    {step.file}")
        hash_label = "Fingerprint:" if step.kind == "substrate_export" else "SHA256:     "
        typer.echo(f"{hash_label} {step.file_sha256[:16]}…")
        typer.echo("Issues:")
        for issue in step.issues:
            typer.echo(f"  - {issue}")
        typer.echo("")
        typer.echo("Before:")
        for ln in json.dumps(step.before, indent=2).splitlines():
            typer.echo(f"  {ln}")
        typer.echo("")
        typer.echo("After:")
        for ln in json.dumps(step.after, indent=2).splitlines():
            typer.echo(f"  {ln}")
        typer.echo("")
        typer.echo(f"Apply: {step.id}  →  elfmem migrate apply --id {step.id} --yes")
        if step.post_apply_step:
            typer.echo(f"After: {step.post_apply_step}")
        typer.echo("")
    raise typer.Exit(1)


@migrate_app.command("apply")
def migrate_apply(
    step_ids: Annotated[
        list[str] | None,
        typer.Option(
            "--id",
            help="Apply only the named migration step. Repeat for multiple.",
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Report what would happen without writing."),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip interactive confirmation prompt."),
    ] = False,
    undo: Annotated[
        bool,
        typer.Option(
            "--undo",
            help=(
                "Roll back an already-applied substrate_export step: removes "
                "the generated .elfmem/memory/ and index.db. Never touches "
                "the live database. Requires --id."
            ),
        ),
    ] = False,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="With --undo: remove generated files even if they look hand-edited since export.",
        ),
    ] = False,
    db: Annotated[str | None, typer.Option("--db", envvar="ELFMEM_DB")] = None,
    config_path: Annotated[str | None, typer.Option("--config", envvar="ELFMEM_CONFIG")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Apply pending migrations atomically, with backups.

    Each step writes a backup before making any change — a
    ``<file>.elfmem-bak-<step_id>-<timestamp>`` for Claude MCP config steps,
    a ``VACUUM INTO`` snapshot for the substrate-export step. Atomic: config
    writes go through a tmp-file rename; the substrate step never writes to
    your live database at all, only to new files alongside it.

    If a target has drifted since the plan was built, it's marked 'stale'
    and skipped. Re-run 'elfmem migrate plan' first.

    Without --yes, prompts for confirmation. Always safe to re-run — already-
    applied steps return 'skipped'. Use --undo --id <step> to roll back an
    applied substrate_export step.
    """
    from elfmem.migrate import ApplyResult, MigrationStep, StepApplyResult, apply_plan

    if undo:
        # Undo doesn't operate on the *pending* plan -- an already-applied
        # migration is by definition no longer pending, so it would never
        # appear in plan.steps. undo_substrate_step only needs a step id
        # (it reads everything else from the recorded marker), and that id
        # is a deterministic function of the db path, so it's reconstructed
        # directly rather than looked up in a plan that wouldn't have it.
        from elfmem.migrate import substrate_step_id

        if not step_ids:
            typer.echo("--undo requires --id <step>.", err=True)
            raise typer.Exit(1)
        resolved_db, _, memory_dir = _resolve_migrate_memory_dir(db, config_path)
        expected_id = substrate_step_id(Path(resolved_db))
        undo_results = [
            _run(_undo_substrate_async(
                MigrationStep(
                    id=sid, kind="substrate_export", summary="", file=Path(resolved_db),
                    file_sha256="", issues=[], before={}, after={}, json_pointer="",
                ),
                memory_dir, force,
            ))
            if sid == expected_id
            else StepApplyResult(sid, "failed", f"no such migration for this project: {sid}")
            for sid in step_ids
        ]
        result = ApplyResult(results=undo_results)
        if json_output:
            _json(result.to_dict())
            if not result.all_ok:
                raise typer.Exit(1)
            return
        for r in result.results:
            symbol = {"applied": "✓", "skipped": "·", "failed": "✗"}.get(r.status, "?")
            typer.echo(f"{symbol}  {r.step_id}: {r.detail}")
        if not result.all_ok:
            raise typer.Exit(1)
        return

    plan = _run(_build_full_plan_for_project(db, config_path))
    targets = tuple(step_ids) if step_ids else None
    target_steps = [s for s in plan.steps if targets is None or s.id in targets]

    if not target_steps:
        msg = (
            "No migrations pending."
            if not targets
            else f"No matching migrations: {', '.join(targets)}"
        )
        if json_output:
            _json({"applied": [], "skipped": [], "failed": [], "results": [], "all_ok": True})
        else:
            typer.echo(msg)
        return

    if not yes and not dry_run and not json_output:
        typer.echo(f"About to apply {len(target_steps)} migration(s):\n")
        for step in target_steps:
            typer.echo(f"  • {step.id} → {step.file}")
        typer.echo("")
        confirm = typer.confirm("Proceed?", default=False)
        if not confirm:
            typer.echo("Aborted.")
            raise typer.Exit(1)

    # Config-drift steps go through the existing JSON-patch machinery;
    # substrate_export dispatches to its own async apply (backup, export,
    # rebuild, verify) — same MigrationStep/StepApplyResult vocabulary,
    # different mechanics because there's no single JSON pointer to patch.
    config_steps = [s for s in target_steps if s.kind != "substrate_export"]
    substrate_steps = [s for s in target_steps if s.kind == "substrate_export"]

    config_results: list[StepApplyResult] = []
    if config_steps:
        # NOTE: only=() (empty tuple) is falsy in Python, which apply_plan
        # treats the same as only=None -- "no filter, apply everything in
        # the plan." Guarding on `if config_steps` keeps that fallback from
        # ever reprocessing the substrate step through the JSON-patch path
        # when there happen to be zero config steps to apply.
        config_results = apply_plan(
            plan, only=tuple(s.id for s in config_steps), dry_run=dry_run,
        ).results
    substrate_results: list[StepApplyResult] = []
    if substrate_steps:
        _, resolved_config, memory_dir = _resolve_migrate_memory_dir(db, config_path)
        cfg = (
            ElfmemConfig.from_yaml(resolved_config)
            if Path(resolved_config).exists()
            else ElfmemConfig()
        )
        substrate_results = [
            _run(_apply_substrate_async(step, memory_dir, cfg, dry_run))
            for step in substrate_steps
        ]
    result = ApplyResult(results=[*config_results, *substrate_results])

    if json_output:
        _json(result.to_dict())
        if not result.all_ok:
            raise typer.Exit(1)
        return

    for step_result in result.results:
        symbol = {
            "applied": "✓",
            "skipped": "·",
            "failed": "✗",
            "stale": "⟲",
        }.get(step_result.status, "?")
        typer.echo(f"{symbol}  {step_result.step_id}: {step_result.detail}")
        if step_result.backup:
            typer.echo(f"   backup: {step_result.backup}")
    typer.echo("")
    if result.all_ok:
        typer.echo(f"Done. Applied: {len(result.applied)}, skipped: {len(result.skipped)}.")
        if any(s.kind != "substrate_export" for s in config_steps) and result.applied and not dry_run:
            typer.echo("Restart Claude Code so MCP servers reload.")
    else:
        typer.echo(f"{len(result.failed)} step(s) need attention.")
        raise typer.Exit(1)


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
    to_markdown: Annotated[
        bool,
        typer.Option(
            "--to-markdown",
            help=(
                "Export to the .elfmem/memory/ file substrate (v2) instead "
                "of a JSON bundle. Read-only against the database; only "
                "writes files under --memory-dir."
            ),
        ),
    ] = False,
    memory_dir: Annotated[
        str | None,
        typer.Option("--memory-dir", help="Target dir for --to-markdown (default: <project>/.elfmem/memory)"),
    ] = None,
    db: Annotated[str | None, typer.Option("--db", envvar="ELFMEM_DB")] = None,
    config: Annotated[str | None, typer.Option("--config", envvar="ELFMEM_CONFIG")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Export shareable blocks as a JSON bundle, or to markdown files.

    Examples:

        elfmem export --share public -o knowledge.json
        elfmem export --share all -o sync.json --min-confidence 0.5
        elfmem export --to-markdown
        elfmem export --to-markdown --memory-dir /tmp/memory-preview
    """
    if to_markdown:
        db_path, config_path = _resolve_paths(db, config)
        resolved_dir = _resolve_memory_dir(memory_dir, config_path)
        md_result = _run(_export_markdown_async(db_path, resolved_dir))
        if json_output:
            _json(
                {
                    "blocks_exported": md_result.blocks_exported,
                    "files_written": [str(p) for p in md_result.files_written],
                }
            )
        else:
            typer.echo(f"Exported {md_result.blocks_exported} block(s) to {resolved_dir}")
            for p in md_result.files_written:
                typer.echo(f"  {p}")
        return

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


# ── Index (v2 file substrate) subcommands ────────────────────────────────────
# The derived L2 index rebuilds from the L1 .elfmem/memory/ files with zero
# LLM calls. None of these write to your live/configured --db: `check` never
# opens a database at all, `rebuild` only ever writes to --to, and `parity`
# opens the live db read-only for comparison. Flipping the live CLI over to
# read/write through files is a separate, later step (not this one).

index_app = typer.Typer(
    name="index",
    help=(
        "The derived L2 index: rebuild it from .elfmem/memory/ files, or "
        "check the files alone. Read-only against your configured database "
        "except where a command explicitly writes to --to."
    ),
    no_args_is_help=True,
)
app.add_typer(index_app, name="index")


@index_app.command("check")
def index_check_cmd(
    memory_dir: Annotated[str | None, typer.Option("--memory-dir")] = None,
    config: Annotated[str | None, typer.Option("--config", envvar="ELFMEM_CONFIG")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Parse .elfmem/memory/**.md and report frontmatter errors. No DB touched.

    Examples:

        elfmem index check
        elfmem index check --memory-dir /tmp/memory-preview
    """
    config_path = _resolve_config_only(config)
    resolved_dir = _resolve_memory_dir(memory_dir, config_path)
    report = _index_check(resolved_dir)
    errors = report["errors"]
    absorbed = report["absorbed"]
    if json_output:
        _json(
            {
                "memory_dir": str(resolved_dir),
                "blocks": report["indexed_blocks"],
                "indexed_blocks": report["indexed_blocks"],
                "total_blocks": report["total_blocks"],
                "per_dir": report["per_dir"],
                "errors": [
                    {"file": str(path), "title": e.title, "reason": e.reason}
                    for path, e in errors
                ],
                "absorbed_headings": [
                    {"file": str(path), "title": a.title,
                     "absorbed_into": a.absorbed_into}
                    for path, a in absorbed
                ],
                "unknown_relations": [
                    {"file": str(f), "title": t, "relation": r}
                    for f, t, r in report["unknown_relations"]
                ],
                "links": len(report["links"]),
                "dangling_links": [
                    {"file": str(f), "source": s_, "relation": r, "target": t}
                    for f, s_, r, t in report["dangling_links"]
                ],
                "missing_cue": len(report["missing_cue"]),
            }
        )
        return
    breakdown = ", ".join(
        f"{name}={count}" for name, count in report["per_dir"].items()
    )
    typer.echo(
        f"{resolved_dir}: {report['indexed_blocks']} indexable block(s) "
        f"[{breakdown or 'no block files'}], {len(errors)} error(s)"
    )
    typer.echo("  (archive/ is parsed here but not read by 'index rebuild')")
    for path, e in errors:
        typer.echo(f"  {path} — {e.title!r}: {e.reason}")
    if absorbed:
        typer.echo(
            f"  {len(absorbed)} '##' heading(s) absorbed as content, not "
            f"treated as block boundaries:"
        )
        for path, a in absorbed:
            typer.echo(f"    {path} — {a.title!r} → inside {a.absorbed_into!r}")

    from elfmem.memory.blockfile import LINK_RELATIONS

    unknown = report["unknown_relations"]
    if unknown:
        typer.echo(
            f"  {len(unknown)} unrecognised relation(s) — link-shaped but not "
            f"in the vocabulary ({', '.join(LINK_RELATIONS)}):"
        )
        for path, title, relation in unknown:
            typer.echo(f"    {path} — {title!r}: {relation}::")

    dangling = report["dangling_links"]
    if dangling:
        typer.echo(f"  {len(dangling)} link(s) point at an unknown block:")
        for path, source, relation, target in dangling:
            typer.echo(f"    {path} — {source} {relation}:: [[{target}]]")

    typer.echo(
        f"  {len(report['links'])} typed link(s); "
        f"{len(report['missing_cue'])} block(s) with no cue:: line"
    )


@index_app.command("rebuild")
def index_rebuild_cmd(
    to: Annotated[str, typer.Option("--to", help="Target index db path — never your live database")],
    memory_dir: Annotated[str | None, typer.Option("--memory-dir")] = None,
    force: Annotated[
        bool,
        typer.Option("--force", help="Wipe --to's blocks/block_tags/edges first if it already has any"),
    ] = False,
    config: Annotated[str | None, typer.Option("--config", envvar="ELFMEM_CONFIG")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Rebuild a derived SQLite index from .elfmem/memory/ — zero LLM calls.

    Writes only to --to; your configured/live database is never opened.

    Examples:

        elfmem index rebuild --to /tmp/index-preview.db
        elfmem index rebuild --memory-dir .elfmem/memory --to .elfmem/index.db --force
    """
    config_path = _resolve_config_only(config)
    resolved_dir = _resolve_memory_dir(memory_dir, config_path)
    cfg = ElfmemConfig.from_yaml(config_path) if config_path else ElfmemConfig()
    result = _run(_index_rebuild_async(str(Path(to).expanduser()), resolved_dir, cfg, force))
    if json_output:
        _json(
            {
                "target_db": to,
                "memory_dir": str(resolved_dir),
                "blocks_written": result.blocks_written,
                "self_md_found": result.self_content is not None,
                "parse_errors": len(result.parse_errors),
            }
        )
        return
    typer.echo(f"Rebuilt {to} from {resolved_dir}: {result.blocks_written} block(s) written")
    if result.self_content is not None:
        typer.echo("  self.md found")
    if result.parse_errors:
        typer.echo(f"  {len(result.parse_errors)} frontmatter parse error(s) — see 'elfmem index check'")


@index_app.command("parity")
def index_parity_cmd(
    live_db: Annotated[
        str | None,
        typer.Option("--live-db", help="Database being migrated FROM (default: resolved project db)"),
    ] = None,
    memory_dir: Annotated[str | None, typer.Option("--memory-dir")] = None,
    query: Annotated[
        list[str] | None,
        typer.Option("--query", help="Extra semantic query to compare via the attention frame (repeatable)"),
    ] = None,
    config: Annotated[str | None, typer.Option("--config", envvar="ELFMEM_CONFIG")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Read-only migration rehearsal: rebuild a throwaway index from
    --memory-dir and compare retrieval against --live-db. Never writes to
    --live-db — this is the plan's Phase 4 gate (docs/plans/v2_substrate),
    runnable against a real corpus before Phase 5/6 touch it for real.

    Examples:

        elfmem index parity
        elfmem index parity --live-db ~/.elfmem/databases/elfmem.db --query "error handling"
    """
    db_path, config_path = _resolve_paths(None, config)
    resolved_live_db = live_db or db_path
    if not Path(resolved_live_db).exists():
        typer.echo(f"--live-db not found: {resolved_live_db}", err=True)
        raise typer.Exit(code=1)
    resolved_dir = _resolve_memory_dir(memory_dir, config_path)
    cfg = ElfmemConfig.from_yaml(config_path) if config_path else ElfmemConfig()
    result = _run(_index_parity_async(resolved_live_db, resolved_dir, cfg, query or []))
    if json_output:
        _json(
            {
                "live_db": resolved_live_db,
                "memory_dir": str(resolved_dir),
                "passed": result.passed,
                "block_count_before": result.block_count_before,
                "block_count_after": result.block_count_after,
                "diverging_queries": [
                    {"query": c.query, "frame": c.frame_name, "before": c.before_ids, "after": c.after_ids}
                    for c in result.diverging_queries()
                ],
                "stale_edges_in_source": result.stale_edges_in_source,
                "diagnosis": result.diagnosis,
            }
        )
        return
    status = "PASS" if result.passed else "FAIL"
    typer.echo(f"Parity gate: {status}")
    typer.echo(f"  blocks: {result.block_count_before} -> {result.block_count_after}"
               f" ({'match' if result.block_count_matches else 'MISMATCH'})")
    diverging = result.diverging_queries()
    typer.echo(f"  queries: {len(result.query_checks) - len(diverging)}/{len(result.query_checks)} match")
    for c in diverging:
        typer.echo(f"    diverges — frame={c.frame_name} query={c.query!r}")
        typer.echo(f"      before: {c.before_ids}")
        typer.echo(f"      after:  {c.after_ids}")
    if not result.passed:
        diagnosis = result.diagnosis
        if diagnosis:
            typer.echo(f"\nDiagnosis:\n  {diagnosis}")
        else:
            typer.echo(
                "\nDo not treat a diverging ranking as probably fine — "
                "diagnose before proceeding."
            )


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


# ── Constitutional review subcommands (v0.18) ────────────────────────────────

review_app = typer.Typer(
    name="review",
    help=(
        "Constitutional review: surface drifted self/constitutional blocks "
        "as proposed amendments; accept, revert, or list."
    ),
    invoke_without_command=True,
)
app.add_typer(review_app, name="review")


def _read_content_source(content_file: str | None) -> str:
    """Return amendment content from --content-file or piped stdin.

    Exits 2 with a clear message when neither source is provided.
    """
    import sys
    if content_file:
        return Path(content_file).expanduser().read_text(encoding="utf-8").strip()
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    typer.echo(
        "Error: no proposed content provided.\n"
        "Recovery: pass --content-file PATH, or pipe content via stdin "
        "(e.g. `cat new.md | elfmem review accept <block_id>`).",
        err=True,
    )
    raise typer.Exit(2)


def _render_proposal(idx: int, total: int, proposal: Any) -> None:
    """Render one proposal block to stdout in the interactive flow."""
    short = proposal.block_id[:8]
    typer.echo("")
    typer.echo(f"[{idx}/{total}] block {short}…")
    typer.echo(f"  Drift: {proposal.drift_score:.2f}")
    typer.echo("")
    typer.echo("  Original:")
    for line in proposal.original_content.splitlines() or [""]:
        typer.echo(f"    {line}")
    typer.echo("")
    typer.echo("  Proposed:")
    for line in proposal.proposed_content.splitlines() or [""]:
        typer.echo(f"    {line}")
    typer.echo("")
    typer.echo("  Rationale:")
    for line in (proposal.rationale or "(none)").splitlines():
        typer.echo(f"    {line}")


def _ttys_attached() -> bool:
    """True when both stdin and stdout are attached to a TTY."""
    import sys
    return sys.stdin.isatty() and sys.stdout.isatty()


@review_app.callback(invoke_without_command=True)
def review_default(
    ctx: typer.Context,
    db: Annotated[str | None, typer.Option("--db", envvar="ELFMEM_DB")] = None,
    config: Annotated[
        str | None, typer.Option("--config", envvar="ELFMEM_CONFIG"),
    ] = None,
    drift_threshold: Annotated[
        float | None,
        typer.Option("--drift-threshold", help="Override review.drift_threshold."),
    ] = None,
    max_proposals: Annotated[
        int | None,
        typer.Option("--max", help="Cap proposals returned."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
    yes: Annotated[
        bool,
        typer.Option(
            "--yes", "-y",
            help="Auto-accept every proposal (skip interactive prompt).",
        ),
    ] = False,
) -> None:
    """Run a constitutional review cycle.

    Default: interactive — walks through each proposal and prompts
    accept/reject/skip/quit. With --json or when stdin/stdout is not a
    TTY, the proposals are emitted as JSON and nothing is applied.
    """
    if ctx.invoked_subcommand is not None:
        return

    from elfmem.types import ConstitutionalReviewResult

    db_path, config_path = _resolve_paths(db, config)
    result: ConstitutionalReviewResult = _run(_review_async(
        db_path, config_path,
        drift_threshold=drift_threshold,
        max_proposals=max_proposals,
    ))

    if json_output or not _ttys_attached():
        _json(result.to_dict())
        return

    if result.insufficient_history:
        typer.echo(
            "Constitutional review: insufficient operational history yet — "
            "no proposals. Build more recent activity before reviewing."
        )
        return

    if not result.proposals:
        typer.echo(
            f"Constitutional review: {result.reviewed_count} reviewed, "
            f"{result.skipped_count} skipped, no amendments proposed."
        )
        return

    n = len(result.proposals)
    typer.echo(
        f"Reviewing {result.reviewed_count} constitutional block(s); "
        f"{n} proposal(s) above drift threshold."
    )

    accepted = rejected = skipped = quit_count = 0
    for i, prop in enumerate(result.proposals, start=1):
        _render_proposal(i, n, prop)
        if yes:
            choice = "a"
        else:
            choice = typer.prompt(
                "  Action [a]ccept / [r]eject / [s]kip / [q]uit",
                default="s", show_default=False,
            ).strip().lower()[:1]
        if choice == "a":
            outcome = _run(_accept_async(
                db_path, config_path,
                block_id=prop.block_id,
                proposed_content=prop.proposed_content,
                rationale=prop.rationale,
                drift_score=prop.drift_score,
                acceptor="user",
            ))
            accepted += 1
            typer.echo(f"  Accepted. Audit ID: {outcome.amendment_id}.")
        elif choice == "q":
            quit_count = n - i + 1
            break
        elif choice == "r":
            rejected += 1
            typer.echo("  Rejected.")
        else:
            skipped += 1
            typer.echo("  Skipped.")

    typer.echo("")
    typer.echo(
        f"Done. Accepted: {accepted}, rejected: {rejected}, "
        f"skipped: {skipped}, abandoned: {quit_count}."
    )


def _render_corpus_proposal(idx: int, total: int, proposal: Any) -> None:
    """Render one corpus-review proposal block to stdout (interactive flow)."""
    short = proposal.block_id[:8]
    typer.echo("")
    typer.echo(f"[{idx}/{total}] block {short}… ({proposal.kind})")
    typer.echo(f"  {proposal.reason}")
    typer.echo(f"  {proposal.content_preview}")


@review_app.command("corpus")
def review_corpus_cmd(
    db: Annotated[str | None, typer.Option("--db", envvar="ELFMEM_DB")] = None,
    config: Annotated[
        str | None, typer.Option("--config", envvar="ELFMEM_CONFIG"),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
    yes: Annotated[
        bool,
        typer.Option(
            "--yes", "-y",
            help="Auto-accept every proposal (skip interactive prompt).",
        ),
    ] = False,
) -> None:
    """Run a corpus-level review cycle: deterministic staleness detection.

    Default: interactive — walks through each proposal and prompts
    accept/reject/skip/quit. With --json or when stdin/stdout is not a
    TTY, the proposals are emitted as JSON and nothing is applied.

    Zero LLM calls. Distinct from the bare `elfmem review` (constitutional
    review) — this reviews ordinary memory for staleness, not drift between
    recent activity and self/constitutional blocks.
    """
    db_path, config_path = _resolve_paths(db, config)
    result: CorpusReviewResult = _run(_review_corpus(db_path, config_path))

    if json_output or not _ttys_attached():
        _json(result.to_dict())
        return

    if not result.proposals:
        typer.echo(
            f"Corpus review: {result.reviewed_count} active block(s) reviewed, "
            "no proposals."
        )
        return

    n = len(result.proposals)
    typer.echo(
        f"Reviewing {result.reviewed_count} active block(s); "
        f"{n} proposal(s)."
    )

    accepted = rejected = skipped = quit_count = 0
    for i, prop in enumerate(result.proposals, start=1):
        _render_corpus_proposal(i, n, prop)
        if yes:
            choice = "a"
        else:
            choice = typer.prompt(
                "  Action [a]ccept / [r]eject / [s]kip / [q]uit",
                default="s", show_default=False,
            ).strip().lower()[:1]
        if choice == "a":
            outcome = _run(_forget(
                db_path, config_path, prop.block_id, reason=ArchiveReason.DECAYED,
            ))
            accepted += 1
            typer.echo(f"  {outcome}")
        elif choice == "q":
            quit_count = n - i + 1
            break
        elif choice == "r":
            rejected += 1
            typer.echo("  Rejected.")
        else:
            skipped += 1
            typer.echo("  Skipped.")

    typer.echo("")
    typer.echo(
        f"Done. Accepted: {accepted}, rejected: {rejected}, "
        f"skipped: {skipped}, abandoned: {quit_count}."
    )


@review_app.command("accept")
def review_accept(
    block_id: str,
    content_file: Annotated[
        str | None,
        typer.Option("--content-file", help="Path to file holding the new content."),
    ] = None,
    rationale: Annotated[
        str | None,
        typer.Option("--rationale", help="Optional audit-trail rationale."),
    ] = None,
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Skip the confirmation prompt."),
    ] = False,
    db: Annotated[str | None, typer.Option("--db", envvar="ELFMEM_DB")] = None,
    config: Annotated[
        str | None, typer.Option("--config", envvar="ELFMEM_CONFIG"),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Apply a constitutional amendment from --content-file or stdin.

    Examples:

        elfmem review accept 7a3b4c5d --content-file new.md
        cat new.md | elfmem review accept 7a3b4c5d --yes
    """
    from elfmem.types import AmendmentResult

    proposed = _read_content_source(content_file)
    db_path, config_path = _resolve_paths(db, config)

    if not yes and _ttys_attached():
        typer.echo(f"Accept amendment on block {block_id[:8]}…?")
        typer.echo("")
        typer.echo("Proposed content:")
        for line in proposed.splitlines() or [""]:
            typer.echo(f"  {line}")
        if not typer.confirm("Proceed?", default=False):
            typer.echo("Cancelled.")
            raise typer.Exit(1)

    result: AmendmentResult = _run(_accept_async(
        db_path, config_path,
        block_id=block_id,
        proposed_content=proposed,
        rationale=rationale,
        drift_score=None,
        acceptor="user",
    ))
    if json_output:
        _json(result.to_dict())
    else:
        typer.echo(str(result))


@review_app.command("revert")
def review_revert(
    amendment_id: int,
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Skip the confirmation prompt."),
    ] = False,
    db: Annotated[str | None, typer.Option("--db", envvar="ELFMEM_DB")] = None,
    config: Annotated[
        str | None, typer.Option("--config", envvar="ELFMEM_CONFIG"),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Revert one amendment (one-step undo)."""
    from elfmem.types import AmendmentRecord, AmendmentResult

    db_path, config_path = _resolve_paths(db, config)

    if not yes and _ttys_attached():
        # Show what will be restored before confirming.
        existing: list[AmendmentRecord] = _run(_list_async(
            db_path, config_path, block_id=None, limit=1000,
        ))
        target = next((a for a in existing if a.id == amendment_id), None)
        if target is None:
            typer.echo(
                f"Error: amendment {amendment_id} not found.\n"
                "Recovery: run `elfmem review list` to see valid ids.",
                err=True,
            )
            raise typer.Exit(1)
        if target.reverted_at is not None:
            typer.echo(
                f"Error: amendment {amendment_id} has already been reverted.\n"
                "Recovery: pick a different amendment id.",
                err=True,
            )
            raise typer.Exit(1)
        typer.echo(
            f"Reverting amendment {amendment_id} on block "
            f"{target.block_id[:8]}…"
        )
        typer.echo("")
        typer.echo("Will restore content to:")
        for line in target.pre_content.splitlines() or [""]:
            typer.echo(f"  {line}")
        if not typer.confirm("Proceed?", default=False):
            typer.echo("Cancelled.")
            raise typer.Exit(1)

    result: AmendmentResult = _run(_revert_async(
        db_path, config_path, amendment_id=amendment_id,
    ))
    if json_output:
        _json(result.to_dict())
    else:
        typer.echo(str(result))


@review_app.command("list")
def review_list(
    block: Annotated[
        str | None,
        typer.Option("--block", help="Filter by block id."),
    ] = None,
    limit: Annotated[
        int, typer.Option("--limit", help="Maximum rows to return."),
    ] = 20,
    db: Annotated[str | None, typer.Option("--db", envvar="ELFMEM_DB")] = None,
    config: Annotated[
        str | None, typer.Option("--config", envvar="ELFMEM_CONFIG"),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List amendment history (newest first)."""
    from elfmem.types import AmendmentRecord

    db_path, config_path = _resolve_paths(db, config)
    rows: list[AmendmentRecord] = _run(_list_async(
        db_path, config_path, block_id=block, limit=limit,
    ))

    if json_output:
        _json([r.to_dict() for r in rows])
        return

    if not rows:
        if block is not None:
            typer.echo(f"No amendments for block {block[:8]}….")
        else:
            typer.echo("No amendments recorded.")
        return

    # Compact text table.
    header = (
        f"{'ID':>5}  {'TIMESTAMP':<25}  {'BLOCK':<10}  "
        f"{'ACCEPTOR':<8}  {'DRIFT':>5}  STATUS"
    )
    typer.echo(header)
    typer.echo("-" * len(header))
    for r in rows:
        status = "reverted" if r.reverted_at else "active"
        ts = r.timestamp.isoformat()[:25]
        typer.echo(
            f"{r.id:>5}  {ts:<25}  {r.block_id[:8]:<10}  "
            f"{r.acceptor:<8}  {r.drift_score:>5.2f}  {status}"
        )


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
    cue: str | None = None,
) -> tuple[LearnResult, bool]:
    async with MemorySystem.managed(db_path, config=config, auto_dream=False) as mem:
        result = await mem.remember(
            content, tags=tags, category=category, cue=cue
        )
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


async def _edit(
    db_path: str, config: str | None, block_id: str,
    content: str | None, cue: str | None,
) -> EditResult:
    async with MemorySystem.managed(db_path, config=config, auto_dream=False) as mem:
        return await mem.edit(block_id, content, cue=cue)


async def _edit_cues_batch(
    db_path: str, config: str | None, cues: dict[str, str]
) -> tuple[int, list[str]]:
    """Apply many cue lines in one session. Returns (applied, skipped ids)."""
    from elfmem.exceptions import BlockNotFound

    applied = 0
    skipped: list[str] = []
    async with MemorySystem.managed(db_path, config=config, auto_dream=False) as mem:
        for block_id, cue in cues.items():
            try:
                await mem.edit(block_id, cue=cue)
                applied += 1
            except BlockNotFound:
                # A backfill file outlives the corpus it was generated from;
                # a since-archived block is expected, not an error.
                skipped.append(block_id)
    return applied, skipped


async def _list_missing_cues(db_path: str, config: str | None) -> list[dict[str, Any]]:
    from elfmem.db.engine import create_engine
    from elfmem.db.migrate import ensure_schema_current
    from elfmem.db.queries import get_blocks_missing_cue

    engine = await create_engine(db_path)
    try:
        async with engine.begin() as conn:
            await ensure_schema_current(conn, db_path=db_path)
        async with engine.connect() as conn:
            return await get_blocks_missing_cue(conn)
    finally:
        await engine.dispose()


async def _forget(
    db_path: str,
    config: str | None,
    block_id: str,
    *,
    reason: ArchiveReason = ArchiveReason.FORGOTTEN,
) -> ForgetResult:
    async with MemorySystem.managed(db_path, config=config, auto_dream=False) as mem:
        return await mem.forget(block_id, reason=reason)


async def _review_corpus(db_path: str, config: str | None) -> CorpusReviewResult:
    async with MemorySystem.managed(db_path, config=config, auto_dream=False) as mem:
        return await mem.review_corpus()


async def _ls(
    db_path: str,
    config: str | None,
    tag: str | None,
    category: str | None,
    limit: int,
) -> list[BlockSummary]:
    async with MemorySystem.managed(db_path, config=config, auto_dream=False) as mem:
        return await mem.ls(tag, category, limit=limit)


async def _status(db_path: str, config: str | None) -> SystemStatus:
    async with MemorySystem.managed(db_path, config=config, auto_dream=False) as mem:
        return await mem.status()


async def _frames_report(db_path: str, config: str | None) -> dict[str, Any]:
    """Render every frame and report what the agent actually receives.

    Answers the question a caller otherwise has to answer by reading
    `context/rendering.py`: is what I stored what the agent sees? Renders
    queryless, so ATTENTION/SIMULATE show what is in reach rather than what
    a specific question surfaces.
    """
    from elfmem.context.frames import BUILTIN_FRAMES

    async with MemorySystem.managed(db_path, config=config, auto_dream=False) as mem:
        report: dict[str, Any] = {"frames": [], "inbox_pending": 0}
        report["inbox_pending"] = len(await mem.inbox())
        for name, frame_def in BUILTIN_FRAMES.items():
            # reinforce=False keeps doctor's documented read-only contract:
            # retrieval normally strengthens what it returns, so a diagnostic
            # rendering all four frames would inflate the scores of exactly
            # the blocks it is reporting on -- worse on every re-run, and
            # this command is meant to be safe to put in CI.
            result = await mem.frame(name, reinforce=False)
            guaranteed_dropped = [
                d for d in result.dropped
                if any(
                    _tag_matches(tag, pattern)
                    for tag in d.tags
                    for pattern in frame_def.guarantees
                )
            ]
            report["frames"].append({
                "frame": name,
                "rendered": len(result.blocks),
                "dropped": [d.to_dict() for d in result.dropped],
                "guaranteed_dropped": [d.to_dict() for d in guaranteed_dropped],
                "budget_used": result.budget_used,
                "budget_total": result.budget_total,
                "over_budget": result.budget_used > result.budget_total,
                "excluded_by_filter": result.excluded_by_filter,
            })
        return report


def _tag_matches(tag: str, pattern: str) -> bool:
    """SQL-LIKE style match for the `%` suffix used in frame guarantees."""
    return tag.startswith(pattern[:-1]) if pattern.endswith("%") else tag == pattern


def _doctor_frames(db: str | None, config: str | None, json_output: bool) -> None:
    """`elfmem doctor --frames` — print exactly what each frame renders."""
    db_path, config_path = _resolve_paths(db, config)
    report = _run(_frames_report(db_path, config_path))

    if json_output:
        _json(report)
    else:
        for row in report["frames"]:
            over = " OVER BUDGET" if row["over_budget"] else ""
            excluded = (
                f" | {row['excluded_by_filter']} excluded by filter"
                if row["excluded_by_filter"] else ""
            )
            line = (
                f"{row['frame'].upper():<10} {row['rendered']:>3} rendered | "
                f"{len(row['dropped']):>3} dropped | "
                f"{row['budget_used']}/{row['budget_total']} tokens{over}{excluded}"
            )
            typer.echo(line)
            for d in row["dropped"]:
                preview = d["content"][:60].replace("\n", " ")
                flag = " [GUARANTEED]" if d in row["guaranteed_dropped"] else ""
                typer.echo(f"    dropped: {d['id'][:8]}… ({d['reason']}){flag} {preview}…")
        if report["inbox_pending"]:
            typer.echo(
                f"\nInbox: {report['inbox_pending']} block(s) pending consolidation — "
                "stored but invisible to every frame above. Run: elfmem dream"
            )
        typer.echo(
            "\nRendered queryless: ATTENTION and SIMULATE vary with the query "
            "you actually pass."
        )

    # A dropped *guaranteed* block is the one failure that is never routine:
    # it is precisely the case the guarantee exists to prevent, and it means
    # the agent is running on a partial identity it believes is whole.
    # Ordinary top_k/budget drops on ATTENTION are normal and do not fail.
    if any(row["guaranteed_dropped"] for row in report["frames"]):
        raise typer.Exit(1)


async def _inbox(
    db_path: str, config: str | None, max_count: int | None,
) -> list[InboxBlockSummary]:
    async with MemorySystem.managed(db_path, config=config, auto_dream=False) as mem:
        return await mem.inbox(max_count)


async def _peer_inbox_status(db_path: str, config: str | None) -> PeerInboxStatus:
    async with MemorySystem.managed(db_path, config=config, auto_dream=False) as mem:
        return mem.peer_inbox_status()


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


async def _doctor_preflight(config: str | None) -> tuple[bool, str]:
    """Make one real LLM call to confirm the configured key actually works (v2 step 3).

    Unlike every other doctor check, this is a real network call — costs
    time and (for hosted models) money — so it is opt-in via --resolve, not
    part of the default fast/free check set. Scoped to the current single
    llm: config section; profile-based routing is a later step, not this one.

    Returns (ok, detail). Never raises — construction or call failures
    (bad key, unreachable base_url, network error) are caught and reported
    as a failed check, matching the "fails loudly instead of degrading
    silently" fix this step exists for.
    """
    import time

    from elfmem.adapters.factory import make_llm_adapter
    from elfmem.config import ElfmemConfig
    from elfmem.token_counter import TokenCounter

    cfg = ElfmemConfig.from_yaml(config) if config else ElfmemConfig()
    try:
        llm = make_llm_adapter(cfg, TokenCounter())
        start = time.monotonic()
        await llm.process_block("preflight check", "")
        elapsed_ms = (time.monotonic() - start) * 1000
        return True, f"{cfg.llm.model} — OK ({elapsed_ms:.0f}ms)"
    except Exception as e:
        return False, f"{cfg.llm.model} — {type(e).__name__}: {e}"


async def _doctor_scoring_drift(
    db_path: str, config: str | None,
) -> dict[str, Any] | None:
    """Compute scoring-drift stats for the doctor surface. None on error.

    Returns ``{"stats": {...}, "warn_count": N, "warn_percent": M,
    "recommended_max": K}`` for the caller to render. The recommended
    --max is auto-scaled to the observed drift (rounds up to nearest 50,
    floored at 20) so doctor's suggestion is actionable, not aspirational.
    """
    from elfmem.config import ElfmemConfig
    from elfmem.db.engine import create_engine
    from elfmem.operations.rescore import RescoreFilter, compute_drift_stats

    cfg = (
        ElfmemConfig.from_yaml(config) if config else ElfmemConfig()
    ).rescore
    if not cfg.enabled:
        return None
    filt = RescoreFilter(
        exclude_categories=tuple(cfg.exclude_categories),
        exclude_tags=tuple(cfg.exclude_tags),
        min_age_hours=cfg.min_age_hours,
        target_max_age_days=cfg.target_max_age_days,
    )
    try:
        engine = await create_engine(db_path)
        async with engine.connect() as conn:
            stats = await compute_drift_stats(conn, filt=filt)
        await engine.dispose()
    except Exception:
        return None
    return {
        "stats": {
            "total_active": stats.total_active,
            "unscored": stats.unscored,
            "stale": stats.stale,
            "drift": stats.drift,
            "percent_drift": stats.percent_drift_of_total(),
            "target_max_age_days": stats.target_max_age_days,
        },
        "warn_count": cfg.drift_warning_count,
        "warn_percent": cfg.drift_warning_percent,
        "recommended_max": stats.recommended_max(),
    }


async def _dream(
    db_path: str,
    config: str | None,
    *,
    skip_llm: bool = False,
    rescore: bool = False,
    max_count: int | None = None,
    host_analyses: dict[str, dict[str, Any]] | None = None,
) -> Any:
    """Consolidate pending blocks. Returns ConsolidateResult or None if no pending.

    ``max_count`` (CLI ``--max``, ADR 0007) applies to both inbox processing
    and rescore catch-up when both run in this call — one flag, same cap on
    each stage that actually executes.
    """
    async with MemorySystem.managed(db_path, config=config, auto_dream=False) as mem:
        return await mem.dream(
            skip_llm=skip_llm,
            rescore=rescore,
            rescore_max=max_count,
            inbox_max=max_count,
            host_analyses=host_analyses,
        )


async def _metabolism_dry_run_async(
    db_path: str, config: str | None, max_count: int | None,
) -> Any:
    """Edge-metabolism Stage A — read-only, writes nothing to `edges`."""
    async with MemorySystem.managed(db_path, config=config, auto_dream=False) as mem:
        return await mem.metabolism_dry_run(max_count=max_count)


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


# ── Index (v2 file substrate) async helpers ──────────────────────────────────


async def _export_markdown_async(db_path: str, memory_dir: Path) -> Any:
    """Export every DB-native block to `.elfmem/memory/` files. Read-only
    against the database — see `migration.export.export_to_markdown`.

    Also seeds the ledger beside the memory directory, carrying across the
    accumulated history (reinforcement, recency, α/β) that the block format
    deliberately does not encode."""
    from elfmem.db.engine import create_engine
    from elfmem.db.migrate import ensure_schema_current
    from elfmem.memory.ledger import ledger_dir_for
    from elfmem.migration.export import export_to_markdown

    engine = await create_engine(db_path)
    try:
        # Bring the schema current first, exactly as `from_config()` does on
        # every other entry point. Without this, exporting a database that
        # predates the running version fails on the columns the ORM selects.
        # Migrations are additive and take their own backup, so this does not
        # weaken the "export never mutates memory content" guarantee.
        async with engine.begin() as conn:
            await ensure_schema_current(conn, db_path=db_path)
        async with engine.connect() as conn:
            return await export_to_markdown(
                conn, memory_dir, ledger_dir=ledger_dir_for(memory_dir)
            )
    finally:
        await engine.dispose()


def _index_check(memory_dir: Path) -> dict[str, Any]:
    """Parse every block file under *memory_dir* and report what it found.

    Pure file I/O — no DB, no LLM, no embedding calls.

    Counts are reported per source directory rather than as one total,
    because ``index rebuild`` reads ``notes/`` and ``log/`` but deliberately
    does *not* read ``archive/`` (archived content stays recoverable in git,
    not re-entered into the index — see ``migration/export.py``). A single
    conflated total made ``check`` and ``rebuild`` look like they disagreed
    on any corpus with archived blocks, when they were answering different
    questions.
    """
    from elfmem.memory.blockfile import parse_blocks
    from elfmem.memory.index_rebuild import _BLOCK_SOURCES

    indexed_subdirs = {name for name, _ in _BLOCK_SOURCES}
    per_dir: dict[str, int] = {}
    all_errors: list[tuple[Path, Any]] = []
    all_absorbed: list[tuple[Path, Any]] = []
    unknown_relations: list[tuple[Path, str, str]] = []
    missing_cue: list[tuple[Path, str]] = []
    all_links: list[tuple[Path, str, str, str]] = []
    known_ids: set[str] = set()
    for subdir_name in ("notes", "log", "archive"):
        subdir = memory_dir / subdir_name
        if not subdir.is_dir():
            continue
        count = 0
        for path in sorted(subdir.glob("**/*.md")):
            result = parse_blocks(path.read_text(encoding="utf-8"))
            count += len(result.blocks)
            all_errors.extend((path, e) for e in result.errors)
            all_absorbed.extend((path, a) for a in result.absorbed)
            for block in result.blocks:
                if block.id:
                    known_ids.add(block.id)
                unknown_relations.extend(
                    (path, block.title, key) for key in block.unknown_relations
                )
                if not block.cue and subdir_name in indexed_subdirs:
                    missing_cue.append((path, block.title))
                all_links.extend(
                    (path, block.id or block.title, ln.relation, ln.target)
                    for ln in block.links
                )
        per_dir[subdir_name] = count
    dangling = [
        (path, src, rel, tgt)
        for path, src, rel, tgt in all_links
        if tgt not in known_ids
    ]
    return {
        "per_dir": per_dir,
        "indexed_blocks": sum(
            n for name, n in per_dir.items() if name in indexed_subdirs
        ),
        "total_blocks": sum(per_dir.values()),
        "errors": all_errors,
        "absorbed": all_absorbed,
        "unknown_relations": unknown_relations,
        "missing_cue": missing_cue,
        "links": all_links,
        "dangling_links": dangling,
    }


async def _fresh_index_engine(target_db_path: str, *, force: bool) -> Any:
    """An AsyncEngine at *target_db_path* with the schema created, and its
    blocks/block_tags/edges tables verified empty (or wiped, with --force).

    Never touches any database other than *target_db_path*.
    """
    from sqlalchemy import text

    from elfmem.db.engine import create_engine
    from elfmem.db.models import metadata

    engine = await create_engine(target_db_path)
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
        existing = (await conn.execute(text("SELECT COUNT(*) FROM blocks"))).scalar_one()
        if existing and not force:
            await engine.dispose()
            raise ElfmemError(
                f"{target_db_path} already has {existing} block(s).",
                recovery=(
                    "Pass --force to wipe blocks/block_tags/edges and "
                    "rebuild, or point --to at a fresh path."
                ),
            )
        if existing:
            await conn.execute(text("DELETE FROM edges"))
            await conn.execute(text("DELETE FROM block_tags"))
            await conn.execute(text("DELETE FROM blocks"))
    return engine


async def _index_rebuild_async(
    target_db_path: str, memory_dir: Path, cfg: ElfmemConfig, force: bool,
) -> Any:
    from elfmem.adapters.factory import make_embedding_adapter
    from elfmem.memory.index_rebuild import rebuild_index
    from elfmem.token_counter import TokenCounter

    engine = await _fresh_index_engine(target_db_path, force=force)
    try:
        embedding_svc = make_embedding_adapter(cfg, TokenCounter())
        async with engine.begin() as conn:
            from elfmem.memory.ledger import ledger_dir_for
            return await rebuild_index(
                conn, memory_dir, embedding_svc, cfg.embeddings.model,
                ledger_dir=ledger_dir_for(memory_dir),
            )
    finally:
        await engine.dispose()


async def _index_parity_async(
    live_db_path: str, memory_dir: Path, cfg: ElfmemConfig, extra_queries: list[str],
) -> Any:
    import tempfile

    from elfmem.adapters.factory import make_embedding_adapter
    from elfmem.context.frames import ATTENTION_FRAME, SELF_FRAME, SIMULATE_FRAME, TASK_FRAME
    from elfmem.db.engine import create_engine
    from elfmem.memory.index_rebuild import rebuild_index
    from elfmem.migration.parity import check_retrieval_parity
    from elfmem.token_counter import TokenCounter

    embedding_svc = make_embedding_adapter(cfg, TokenCounter())

    with tempfile.TemporaryDirectory() as tmp:
        rebuild_engine = await _fresh_index_engine(str(Path(tmp) / "index-rebuilt.db"), force=True)
        try:
            async with rebuild_engine.begin() as conn:
                from elfmem.memory.ledger import ledger_dir_for
                await rebuild_index(
                    conn, memory_dir, embedding_svc, cfg.embeddings.model,
                    ledger_dir=ledger_dir_for(memory_dir),
                )

            live_engine = await create_engine(live_db_path)
            try:
                queries: list[tuple[str | None, Any]] = [
                    (None, ATTENTION_FRAME),
                    (None, SELF_FRAME),
                    (None, TASK_FRAME),
                    (None, SIMULATE_FRAME),
                ]
                queries.extend((q, ATTENTION_FRAME) for q in extra_queries)

                async with (
                    live_engine.connect() as conn_before,
                    rebuild_engine.connect() as conn_after,
                ):
                    return await check_retrieval_parity(
                        conn_before, conn_after, embedding_svc, queries,
                    )
            finally:
                await live_engine.dispose()
        finally:
            await rebuild_engine.dispose()


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


def _resolve_doctor_inbox(cfg: ElfmemConfig, config_path: str | None) -> Path | None:
    """Resolve the inbox path the same way MemorySystem does, for doctor display.

    Returns None when no override is set and no project root can be found —
    matches the ProjectNotFound behaviour the runtime would surface.
    """
    if cfg.peer.inbox_dir:
        return Path(cfg.peer.inbox_dir).expanduser()
    if config_path:
        cfg_path = Path(config_path).expanduser().resolve()
        if cfg_path.parent.name == ".elfmem":
            return cfg_path.parent.parent / ".elfmem" / "inbox"
    root = _project.find_project_root()
    if root is not None:
        return root / ".elfmem" / "inbox"
    return None


async def _doctor_peer_checks(
    db_path: str, config_path: str | None,
) -> list[dict[str, Any]]:
    """Validate peer communication config-vs-DB consistency.

    Returns a list of check dicts (label, ok, detail, suggestion).
    Returns empty list if peer communication is not configured.
    """
    from elfmem.config import ElfmemConfig
    from elfmem.db.engine import create_engine
    from elfmem.db.queries import get_all_peers, get_config

    checks: list[dict[str, Any]] = []

    def _add(label: str, ok: bool, detail: str, suggestion: str = "") -> None:
        checks.append({"label": label, "ok": ok, "detail": detail, "suggestion": suggestion})

    try:
        engine = await create_engine(db_path)
    except Exception:
        return []

    try:
        cfg = ElfmemConfig.from_yaml(config_path) if config_path else ElfmemConfig()
    except Exception:
        cfg = ElfmemConfig()

    try:
        async with engine.connect() as conn:
            identity = await get_config(conn, "peer_identity")
            peers = await get_all_peers(conn)
            stored_inbox = await get_config(conn, "peer_inbox_dir")
    except Exception:
        await engine.dispose()
        return []

    await engine.dispose()

    # Skip entirely if peer communication was never configured
    if not identity and not peers:
        return []

    # Check 1: Peer identity
    if identity:
        _add("Peer identity", True, identity)
    else:
        _add(
            "Peer identity", False, "Not set",
            "elfmem peer init --name <name>",
        )

    # Check 2: Inbox path — resolved project-local unless explicitly overridden.
    inbox_dir = _resolve_doctor_inbox(cfg, config_path)
    if inbox_dir is None:
        _add(
            "Peer inbox", False,
            "No project root and no explicit override",
            "Run 'elfmem init' inside your project directory",
        )
    elif inbox_dir.exists() and inbox_dir.is_dir():
        _add("Peer inbox", True, str(inbox_dir))
    elif not inbox_dir.exists():
        _add(
            "Peer inbox", True,
            f"{inbox_dir} (will be created on first message)",
        )
    else:
        _add(
            "Peer inbox", False,
            f"{inbox_dir} exists but is not a directory",
            f"Check path: {inbox_dir}",
        )

    # Check 3: Inbox drift — DB-stored path differs from currently-resolved path.
    current_inbox_str = str(inbox_dir) if inbox_dir is not None else ""
    if stored_inbox and current_inbox_str and stored_inbox != current_inbox_str:
        _add(
            "Inbox drift", False,
            f"Was {stored_inbox}, now {current_inbox_str}",
            "Re-run: elfmem peer init --name <name>",
        )

    # Check 3b: Legacy global inbox at ~/.elfmem/inbox.
    # Project-local is now the only supported layout. Warn if old data exists.
    legacy_inbox = Path("~/.elfmem/inbox").expanduser()
    if legacy_inbox.exists() and legacy_inbox != inbox_dir:
        legacy_msgs = sum(
            1 for sub in legacy_inbox.iterdir()
            if sub.is_dir() and sub.name != "processed"
            for _ in sub.glob("msg_*.json")
        ) if legacy_inbox.is_dir() else 0
        if legacy_msgs > 0:
            _add(
                "Legacy inbox", False,
                f"{legacy_msgs} message(s) in {legacy_inbox} (no longer scanned)",
                f"Move them: mv {legacy_inbox}/* {inbox_dir}/",
            )

    # Check 4: Per-peer delivery paths
    for peer in peers:
        dp = peer.get("delivery_path")
        if not dp:
            continue
        dp_path = Path(dp).expanduser()
        name = peer.get("name", peer["did"])
        if dp_path.exists() and dp_path.is_dir():
            _add(f"Deliver→{name}", True, str(dp_path))
        else:
            _add(
                f"Deliver→{name}", False,
                f"{dp_path} not accessible",
                f"Check path or update: elfmem peer add {peer['did']} --name {name} --delivery-path <path>",
            )

    # Check 5: Pending messages (info only)
    if inbox_dir is not None and inbox_dir.exists():
        pending = 0
        for sub in inbox_dir.iterdir():
            if sub.is_dir() and sub.name != "processed":
                pending += len(list(sub.glob("msg_*.json")))
        if pending > 0:
            _add("Peer inbox", True, f"{pending} message(s) pending import")

    return checks


def _looks_empty(db_file: Path) -> bool:
    """Quick row-count heuristic: True if the DB has zero content rows.

    Used to branch doctor's recovery suggestion. Returns False on any error
    (we'd rather under-flag drift than mis-direct users).
    """
    if not db_file.exists():
        return True
    try:
        from elfmem.rescue import inspect
        return not inspect(db_file).populated
    except Exception:
        return False


def _doctor_migrate_mcp(json_output: bool) -> None:
    """Scan Claude MCP configs for stale elfmem entries and print suggested fixes.

    Read-only — never edits user files. Exits 0 if nothing needs migrating,
    1 if any findings were reported.
    """
    from elfmem import migrate

    findings = migrate.scan()

    if json_output:
        _json({
            "findings": [
                {
                    "file": str(f.file),
                    "project_path": f.project_path,
                    "server_name": f.server_name,
                    "issues": f.issues,
                    "current": f.current,
                    "suggested": f.suggested,
                }
                for f in findings
            ],
            "needs_migration": bool(findings),
        })
        if findings:
            raise typer.Exit(1)
        return

    if not findings:
        typer.echo("MCP configs: no migration needed.")
        typer.echo("")
        typer.echo("Scanned:")
        for path in migrate.DEFAULT_SCAN_PATHS:
            typer.echo(f"  - {path.expanduser()}")
        return

    typer.echo(f"Found {len(findings)} elfmem MCP entr"
               f"{'y' if len(findings) == 1 else 'ies'} that need updating.\n")
    for finding in findings:
        typer.echo(migrate.format_finding(finding))
        typer.echo("")
    typer.echo(
        "These changes are NOT applied automatically — your Claude config is\n"
        "user-owned. Edit each file by hand to match the 'Suggested' block.\n"
        "After editing, restart Claude Code so MCP servers reload."
    )
    raise typer.Exit(1)


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


async def _doctor_embedding_lock(db_path: str) -> dict[str, str] | None:
    """Read the embedding lock from system_config. Returns None if DB
    is not accessible; returns ``{"model": "", "dims": ""}`` if no lock
    is set yet; returns the stored values otherwise.

    Doctor calls this without going through MemorySystem.from_config so
    it never triggers the LockedEmbeddingService wrapper — diagnostic
    must never be blocked by the very state it's diagnosing.
    """
    from elfmem.db.engine import create_engine
    from elfmem.db.queries import get_config
    try:
        engine = await create_engine(db_path)
        async with engine.connect() as conn:
            model = await get_config(conn, "embedding_model_lock") or ""
            dims = await get_config(conn, "embedding_dimensions_lock") or ""
        await engine.dispose()
        return {"model": model, "dims": dims}
    except Exception:
        return None


# ── Review (v0.18) async helpers ─────────────────────────────────────────────


async def _review_async(
    db_path: str,
    config: str | None,
    *,
    drift_threshold: float | None,
    max_proposals: int | None,
) -> Any:
    async with MemorySystem.managed(
        db_path, config=config, auto_dream=False,
    ) as mem:
        return await mem.review_constitutional(
            drift_threshold=drift_threshold,
            max_proposals=max_proposals,
        )


async def _accept_async(
    db_path: str,
    config: str | None,
    *,
    block_id: str,
    proposed_content: str,
    rationale: str | None,
    drift_score: float | None,
    acceptor: str,
) -> Any:
    async with MemorySystem.managed(
        db_path, config=config, auto_dream=False,
    ) as mem:
        return await mem.accept_amendment(
            block_id, proposed_content, rationale,
            drift_score=drift_score, acceptor=acceptor,
        )


async def _revert_async(
    db_path: str,
    config: str | None,
    *,
    amendment_id: int,
) -> Any:
    async with MemorySystem.managed(
        db_path, config=config, auto_dream=False,
    ) as mem:
        return await mem.revert_amendment(amendment_id)


async def _list_async(
    db_path: str,
    config: str | None,
    *,
    block_id: str | None,
    limit: int,
) -> Any:
    async with MemorySystem.managed(
        db_path, config=config, auto_dream=False,
    ) as mem:
        return await mem.list_amendments(block_id=block_id, limit=limit)
