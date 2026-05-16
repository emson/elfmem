"""Project-local config discovery, agent doc integration, and MCP snippet generation.

Stateless module — pure functions only. No globals. No I/O side effects except
the explicit write_agent_section() function.

The config discovery chain (used by every CLI command and the MCP server):
    1. --config PATH flag           (explicit always wins)
    2. ELFMEM_CONFIG env var        (ELFMEM_CONFIG_PATH accepted with a deprecation warning)
    3. .elfmem/config.yaml          (walk up from cwd to project root)
    4. ~/.elfmem/config.yaml        (global fallback, if it exists)

The DB discovery chain:
    1. --db PATH flag
    2. ELFMEM_DB env var            (ELFMEM_DB_PATH accepted with a deprecation warning)
    3. project.db from discovered config (relative paths resolve against the
       caller's cwd — same as 0.12.x; the 0.13.0 config-dir-relative semantics
       caused silent DB relocation and have been reverted)
    4. ~/.elfmem/agent.db           (global fallback)
"""
from __future__ import annotations

import json
import os
import re
import sys
import tomllib
from pathlib import Path
from typing import NamedTuple

# Env vars whose values may be paths but should NEVER be tilde-expanded
# (legacy compatibility). The canonical name comes first; aliases are deprecated.
_CONFIG_ENV_NAMES: tuple[str, ...] = ("ELFMEM_CONFIG", "ELFMEM_CONFIG_PATH")
_DB_ENV_NAMES: tuple[str, ...] = ("ELFMEM_DB", "ELFMEM_DB_PATH")

# Track which deprecated env vars we've already warned about, so a single
# process doesn't spam stderr on every CLI sub-call.
_warned_envs: set[str] = set()

# ── Constants ─────────────────────────────────────────────────────────────────

# Markers that indicate a project root, in priority order.
_PROJECT_MARKERS: tuple[str, ...] = (
    ".git",
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
    ".elfmem",  # already initialised — stop here
)

# Agent doc filenames, in preference order.
_AGENT_DOC_CANDIDATES: tuple[str, ...] = (
    "CLAUDE.md",
    "AGENTS.md",
    "claude.md",
    "agents.md",
)

# MCP config filenames, in preference order.
_MCP_CANDIDATES: tuple[str, ...] = (
    ".claude.json",
    ".claude/claude-code.yaml",
    ".cursor/mcp.json",
)

# Delimiters for the managed elfmem block inside agent doc files.
# HTML comments: invisible when rendered, readable by tools.
SECTION_START = "<!-- elfmem:start -->"   # legacy — kept for compat detection
SECTION_START_PREFIX = "<!-- elfmem:start"  # matches both versioned and unversioned
SECTION_END = "<!-- elfmem:end -->"

# ── Key module map ────────────────────────────────────────────────────────────
# Single source of truth for the project's module layout.
# Add an entry here when adding a significant new module.
# Displayed by: elfmem doctor --modules
KEY_MODULES: dict[str, str] = {
    "src/elfmem/api.py": "MemorySystem — all public operations",
    "src/elfmem/types.py": "Result types, exceptions",
    "src/elfmem/operations/": "learn, consolidate, curate, outcome, recall, peer",
    "src/elfmem/db/migrate.py": "Schema migration + backup utilities",
    "src/elfmem/adapters/factory.py": "make_llm_adapter() / make_embedding_adapter()",
    "src/elfmem/adapters/anthropic.py": "AnthropicLLMAdapter — Claude via official SDK",
    "src/elfmem/adapters/openai.py": "OpenAILLMAdapter + OpenAIEmbeddingAdapter",
    "src/elfmem/adapters/mock.py": "MockLLMService, MockEmbeddingService",
    "src/elfmem/guide.py": "AgentGuide + GUIDES dict — add entry for every new public op",
    "tests/conftest.py": "Shared test fixtures — always use these",
    "CHANGELOG.md": "Update this for every user-facing change",
    "docs/amgs_architecture.md": "Full technical specification",
}

_LOCAL_CONFIG_SUBDIR = ".elfmem"
_LOCAL_CONFIG_NAME = "config.yaml"


# ── Data types ────────────────────────────────────────────────────────────────


class ProjectInfo(NamedTuple):
    """Resolved project context returned by get_project_info()."""

    root: Path
    name: str
    config: Path          # .elfmem/config.yaml (may not exist yet)
    db: str               # resolved db path
    agent_doc: Path | None


# ── Project root detection ────────────────────────────────────────────────────


def find_project_root(start: Path | None = None) -> Path | None:
    """Walk up from *start* (default: cwd) to find the project root.

    Returns the first ancestor containing a recognised project marker.
    Stops at the filesystem root or the user's home directory — never escapes home.
    """
    current = (start or Path.cwd()).resolve()
    home = Path.home().resolve()

    while True:
        # Home is a data/config boundary, never a project root.
        # Checking this before markers prevents ~/.elfmem from satisfying the
        # ".elfmem" marker and making ~ look like a project root.
        if current == home:
            return None
        for marker in _PROJECT_MARKERS:
            if (current / marker).exists():
                return current
        parent = current.parent
        if parent == current:
            return None
        current = parent


def project_name(root: Path) -> str:
    """Infer project name from pyproject.toml, package.json, or directory name."""
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        try:
            with open(pyproject, "rb") as f:
                data = tomllib.load(f)
            name = data.get("project", {}).get("name", "")
            if name:
                return str(name)
        except Exception:
            pass

    package_json = root / "package.json"
    if package_json.exists():
        try:
            data = json.loads(package_json.read_text(encoding="utf-8"))
            name = data.get("name", "")
            if name:
                return str(name)
        except Exception:
            pass

    return root.name


def default_db_path(name: str) -> str:
    """Return the canonical database path for a project: ~/.elfmem/databases/{name}.db."""
    return str(Path(f"~/.elfmem/databases/{name}.db").expanduser())


# ── Config discovery ──────────────────────────────────────────────────────────


def find_local_config(start: Path | None = None) -> Path | None:
    """Walk up from *start* to find an existing .elfmem/config.yaml."""
    root = find_project_root(start)
    if root is None:
        return None
    candidate = root / _LOCAL_CONFIG_SUBDIR / _LOCAL_CONFIG_NAME
    return candidate if candidate.exists() else None


def _read_env(names: tuple[str, ...]) -> tuple[str | None, str | None]:
    """Read the first set env var in *names*, warning about deprecated aliases.

    *names* lists env vars in priority order: canonical first, deprecated aliases
    after. If both canonical and a deprecated alias are set with conflicting
    values, raises ConfigError — silent precedence would hide bugs. If the
    deprecated alias wins (canonical unset), prints a one-time stderr warning.

    Returns (value, source_name) where source_name is the env var that won, or
    (None, None) if nothing is set.
    """
    canonical = names[0]
    aliases = names[1:]
    canonical_val = os.getenv(canonical)
    alias_hits = [(a, os.getenv(a)) for a in aliases if os.getenv(a)]

    if canonical_val and alias_hits:
        # Both set — check for conflict.
        for alias, alias_val in alias_hits:
            if alias_val != canonical_val:
                from elfmem.exceptions import ConfigError
                raise ConfigError(
                    f"Conflicting env vars: {canonical}={canonical_val!r} "
                    f"and deprecated {alias}={alias_val!r}. Unset {alias}.",
                    recovery=f"unset {alias}",
                )
        # Same value — warn once that the alias is deprecated and ignored.
        for alias, _ in alias_hits:
            _warn_deprecated_env(alias, canonical)
        return canonical_val, canonical

    if canonical_val:
        return canonical_val, canonical

    if alias_hits:
        alias, alias_val = alias_hits[0]
        _warn_deprecated_env(alias, canonical)
        return alias_val, alias

    return None, None


def _warn_deprecated_env(deprecated: str, canonical: str) -> None:
    """Emit a one-time stderr deprecation warning for an env var alias."""
    if deprecated in _warned_envs:
        return
    _warned_envs.add(deprecated)
    print(
        f"[elfmem] {deprecated} is deprecated; use {canonical} instead. "
        "Support will be removed in v0.14.",
        file=sys.stderr,
    )


def resolve_config(
    explicit: str | None = None,
    cwd: Path | None = None,
) -> tuple[str | None, str]:
    """Resolve the config path and return (path, source_label).

    Chain: explicit flag → ELFMEM_CONFIG env → project-local → global → None.
    source_label describes which step won (useful for doctor output).
    """
    if explicit:
        return str(Path(explicit).expanduser()), "explicit flag"

    env, env_name = _read_env(_CONFIG_ENV_NAMES)
    if env:
        return str(Path(env).expanduser()), f"{env_name} env"

    local = find_local_config(cwd)
    if local is not None:
        return str(local), "project-local (.elfmem/config.yaml)"

    global_path = Path("~/.elfmem/config.yaml").expanduser()
    if global_path.exists():
        return str(global_path), "global (~/.elfmem/config.yaml)"

    return None, "not found"


def resolve_db(
    explicit: str | None = None,
    config_path: str | None = None,
    cwd: Path | None = None,
) -> tuple[str, str]:
    """Resolve the database path and return (path, source_label).

    Chain: explicit flag → ELFMEM_DB env → project.db in config → global fallback.
    Relative project.db values are resolved against the config file's directory.
    """
    if explicit:
        return str(Path(explicit).expanduser()), "explicit flag"

    env, env_name = _read_env(_DB_ENV_NAMES)
    if env:
        return str(Path(env).expanduser()), f"{env_name} env"

    if config_path:
        db_from_config = _read_project_db(config_path)
        if db_from_config:
            return db_from_config, "project.db in config"

    _guard_test_fallback(config_path)
    return str(Path("~/.elfmem/agent.db").expanduser()), "global fallback (~/.elfmem/agent.db)"


def _guard_test_fallback(config_path: str | None) -> None:
    """Refuse to fall through to the user's home DB when running under pytest.

    Tests that hit the global fallback usually indicate a missing fixture, not
    a real intent to write to the developer's actual memory. Failing fast here
    catches the leak before it corrupts production data.
    """
    if not os.getenv("PYTEST_CURRENT_TEST"):
        return
    if os.getenv("ELFMEM_ALLOW_GLOBAL_FALLBACK"):
        return  # explicit opt-out for tests that legitimately need this path
    from elfmem.exceptions import ConfigError
    raise ConfigError(
        "Refusing to use ~/.elfmem/agent.db fallback under pytest "
        f"(config_path={config_path!r}). Pass --db, set ELFMEM_DB, or set "
        "ELFMEM_ALLOW_GLOBAL_FALLBACK=1 if this is intentional.",
        recovery="use tmp_path fixture and pass --db explicitly",
    )


def _read_project_db(config_path: str) -> str:
    """Read project.db from a config YAML file. Returns '' on any error.

    Path semantics:
    - Absolute paths are used as-is.
    - ``~``-prefixed paths are expanded against ``$HOME``.
    - Bare-relative paths resolve against the caller's cwd (this matches
      0.12.x behavior; the 0.13.0 config-dir-relative semantics caused
      silent DB relocation for users with existing relative configs and
      have been reverted).

    Recommendation: configs generated by ``elfmem init`` use absolute paths.
    Hand-edited relative paths still work but require the operator to invoke
    the CLI from a stable cwd.
    """
    try:
        import yaml
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        raw = str((data.get("project") or {}).get("db", ""))
    except Exception:
        return ""
    if not raw:
        return ""
    return str(Path(raw).expanduser())


# ── Agent doc detection ───────────────────────────────────────────────────────


def detect_agent_doc(root: Path) -> Path | None:
    """Return the first agent doc file found in *root*, or None."""
    for name in _AGENT_DOC_CANDIDATES:
        candidate = root / name
        if candidate.exists():
            return candidate
    return None


def detect_mcp_config(root: Path) -> Path | None:
    """Return the first MCP config file found in *root*, or None."""
    for name in _MCP_CANDIDATES:
        candidate = root / name
        if candidate.exists():
            return candidate
    return None


def format_key_modules() -> str:
    """Return KEY_MODULES as a formatted markdown table for terminal output."""
    lines = ["Key module paths (from project.py KEY_MODULES):", ""]
    col_w = max(len(k) for k in KEY_MODULES) + 2
    lines.append(f"  {'Path':<{col_w}}  Purpose")
    lines.append(f"  {'-' * col_w}  {'-' * 40}")
    for path, purpose in KEY_MODULES.items():
        lines.append(f"  {path:<{col_w}}  {purpose}")
    lines.append("")
    lines.append("Maintained in src/elfmem/project.py KEY_MODULES.")
    return "\n".join(lines)


def _section_start(version: str) -> str:
    """Return a versioned section-start comment for the given elfmem version."""
    return f"<!-- elfmem:start v{version} -->"


def extract_section_version(doc_path: Path) -> str | None:
    """Return the elfmem version embedded in the section start comment, or None.

    Handles both legacy (unversioned) and current (versioned) section starts.
    """
    if not doc_path.exists():
        return None
    text = doc_path.read_text(encoding="utf-8")
    # Versioned: <!-- elfmem:start v0.9.1 -->
    m = re.search(r"<!-- elfmem:start v([\d.]+)\s*-->", text)
    if m:
        return m.group(1)
    # Legacy unversioned: <!-- elfmem:start -->
    if SECTION_START in text:
        return "legacy"
    return None


def has_agent_section(doc_path: Path) -> bool:
    """Return True if *doc_path* contains the managed elfmem section."""
    if not doc_path.exists():
        return False
    return SECTION_START_PREFIX in doc_path.read_text(encoding="utf-8")


def has_mcp_entry(mcp_path: Path) -> bool:
    """Return True if *mcp_path* mentions 'elfmem'."""
    if not mcp_path.exists():
        return False
    return "elfmem" in mcp_path.read_text(encoding="utf-8")


# ── Agent doc writing ─────────────────────────────────────────────────────────


def read_render_values_from_config(config_path: str | Path) -> tuple[str, str]:
    """Return ``(name, db)`` from a config's ``project`` section, never raising.

    Returns empty strings for missing or unreadable fields. Render callers
    treat empty strings as "missing — omit the line", never as a license
    to fall back to inferred defaults. This is the principle:

        Authoritative state is read, never inferred. Config is truth; the
        absence of a field is a fact about the config, not a request for
        the renderer to fabricate one.
    """
    try:
        import yaml
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        proj = data.get("project") or {}
        name = str(proj.get("name", "")).strip()
        db = str(proj.get("db", "")).strip()
        return name, db
    except Exception:
        return "", ""


def read_agent_name_from_config(config_path: str | Path | None) -> str:
    """Return ``project.agent_name`` from config, or "" when absent/unreadable.

    Same never-raises contract as ``read_render_values_from_config``. Used by
    the AGENT.md fragment renderer to decide whether to include the
    "Agent Identity" section: empty string → omit section (project has no
    named agent), non-empty → render protocol bound to that name.
    """
    if config_path is None:
        return ""
    try:
        import yaml
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        proj = data.get("project") or {}
        return str(proj.get("agent_name", "")).strip()
    except Exception:
        return ""


# Matches the ``agent_name:`` line inside the project block. Captures everything
# up to and including the colon + leading whitespace so we can substitute just
# the value. Limited to single-line scalars (the only form ``init`` writes).
_AGENT_NAME_LINE_RE = re.compile(
    r"^(\s*agent_name:\s*)(?:\".*?\"|'.*?'|[^\n#]*)(\s*(?:#.*)?)$",
    re.MULTILINE,
)
# Matches the ``identity:`` line in the project block — used as the insertion
# anchor when ``agent_name`` is missing entirely. We splice the new line in
# immediately after, preserving every other byte of the file.
_IDENTITY_LINE_RE = re.compile(
    r"^(\s*identity:\s*(?:\".*?\"|'.*?'|[^\n]*))$",
    re.MULTILINE,
)


def set_agent_name_in_config(config_path: str | Path, name: str) -> str:
    """Surgically set ``project.agent_name`` in a config.yaml, preserving formatting.

    Used by ``elfmem init --name X`` on an established instance, where a full
    config rewrite would clobber the user's hand-edits and comments. We update
    one line — either replacing an existing ``agent_name:`` value or inserting
    one immediately after the ``identity:`` line — and leave every other byte
    untouched.

    Returns the action taken: ``"replaced"``, ``"inserted"``, or ``"unchanged"``.
    Raises ``FileNotFoundError`` if the config doesn't exist (caller should
    distinguish established vs fresh) and ``ValueError`` if neither an
    ``agent_name:`` line nor an ``identity:`` anchor can be found — refusing to
    invent structure in a config we don't understand.
    """
    path = Path(config_path).expanduser()
    if not path.exists():
        raise FileNotFoundError(path)
    quoted = f'"{name}"'
    text = path.read_text(encoding="utf-8")

    existing = _AGENT_NAME_LINE_RE.search(text)
    if existing is not None:
        # Replace value, preserve indent / trailing comment.
        new_text = _AGENT_NAME_LINE_RE.sub(
            lambda m: f"{m.group(1)}{quoted}{m.group(2)}", text, count=1
        )
        if new_text == text:
            return "unchanged"
        path.write_text(new_text, encoding="utf-8")
        return "replaced"

    anchor = _IDENTITY_LINE_RE.search(text)
    if anchor is None:
        raise ValueError(
            f"No `agent_name:` line and no `identity:` anchor in {path}; "
            "refusing to invent project-section structure."
        )
    # Insert immediately after identity line, matching its indentation.
    indent = anchor.group(1)[: len(anchor.group(1)) - len(anchor.group(1).lstrip())]
    insertion = f"\n{indent}agent_name: {quoted}"
    new_text = text[: anchor.end()] + insertion + text[anchor.end():]
    path.write_text(new_text, encoding="utf-8")
    return "inserted"


def _build_section(
    *,
    name: str,
    db_path: str,
    config_path: str,
    identity: str = "",
) -> str:
    """Build the managed elfmem block for an agent doc file.

    Lines render only when their value is non-empty — an empty ``name`` or
    ``db_path`` means "missing from config", and the renderer omits the
    corresponding line rather than displaying blank. The principle this
    enforces:

        Authoritative state is read, never inferred. When config exists,
        it is truth; defaults are bootstrap only on first install.

    Callers in established-instance mode pass values derived from the live
    config (via ``read_render_values_from_config``); callers in fresh-install
    mode pass inferred defaults. The function itself doesn't choose — it
    just renders what it's given, faithfully.
    """
    from importlib.metadata import version as _pkg_version
    try:
        elfmem_version = _pkg_version("elfmem")
    except Exception:
        elfmem_version = "unknown"
    mcp = mcp_json_snippet(config_path=config_path)
    name_line = f"- **Project:** {name}\n" if name else ""
    db_line = f"- **Database:** `{db_path}`\n" if db_path else ""
    config_line = f"- **Config:** `{config_path}`\n" if config_path else ""
    identity_line = f"- **Identity:** {identity}\n" if identity else ""
    return (
        f"{_section_start(elfmem_version)}\n"
        f"## elfmem — Project Memory\n\n"
        f"_auto-generated from `.elfmem/config.yaml` — edit OUTSIDE these markers._\n\n"
        f"{name_line}"
        f"{db_line}"
        f"{config_line}"
        f"{identity_line}"
        f"\n"
        f"**Full agent reference:** see `@.elfmem/AGENT.md` — auto-generated, "
        f"always current with installed library version. Single source of truth "
        f"for every operation, including peer communication.\n"
        f"\n"
        f"Quick commands:\n"
        f"- `elfmem init` — idempotent setup; refresh-only on established instances\n"
        f"- `elfmem doctor` — verify setup, show paths, check fragment freshness\n"
        f"- `elfmem rescue` — recover an orphaned DB (path drift)\n"
        f"- `elfmem status` — memory health\n"
        f"- `elfmem guide` — all operations (always current)\n"
        f"- `elfmem peer list` — registered peers (DIDs + delivery paths)\n"
        f"\n"
        f"Add to `.claude.json` to give Claude persistent memory:\n"
        f"```json\n{mcp}\n```\n"
        f"{SECTION_END}\n"
    )


def write_agent_section(
    doc_path: Path,
    *,
    name: str,
    db_path: str,
    config_path: str,
    identity: str = "",
) -> str:
    """Write or update the managed elfmem section in an agent doc file.

    Returns one of: 'created' | 'updated' | 'appended' | 'unchanged'.

    Idempotent: re-running replaces the existing section, never duplicates it.
    If the file does not exist, it is created with just the elfmem section.
    """
    section = _build_section(
        name=name,
        db_path=db_path,
        config_path=config_path,
        identity=identity,
    )

    if not doc_path.exists():
        doc_path.write_text(section, encoding="utf-8")
        return "created"

    existing = doc_path.read_text(encoding="utf-8")

    if SECTION_START_PREFIX in existing and SECTION_END in existing:
        # Replace the existing managed block (handles both versioned and legacy starts).
        pattern = re.compile(
            re.escape(SECTION_START_PREFIX) + r".*?" + re.escape(SECTION_END),
            re.DOTALL,
        )
        new_content = pattern.sub(section.rstrip("\n"), existing)
        if new_content == existing:
            return "unchanged"
        doc_path.write_text(new_content, encoding="utf-8")
        return "updated"

    # Append the section — ensure a blank line separator.
    sep = "\n\n" if existing and not existing.endswith("\n\n") else "\n"
    doc_path.write_text(existing + sep + section, encoding="utf-8")
    return "appended"


# ── MCP snippet ───────────────────────────────────────────────────────────────


def mcp_json_snippet(config_path: str) -> str:
    """Return the JSON block to paste into .claude.json.

    Uses only --config; the serve command reads project.db from the config,
    so --db is not needed when a project config is in use.
    """
    snippet = {
        "mcpServers": {
            "elfmem": {
                "command": "elfmem",
                "args": ["serve", "--config", config_path],
            }
        }
    }
    return json.dumps(snippet, indent=2)


# ── Convenience ───────────────────────────────────────────────────────────────


def get_project_info(cwd: Path | None = None) -> ProjectInfo | None:
    """Return ProjectInfo for the project rooted at or above *cwd*, or None.

    Used by elfmem init to decide whether to use project-local mode.
    Does not require the project to already have a .elfmem/ directory.
    """
    root = find_project_root(cwd)
    if root is None:
        return None

    name = project_name(root)
    config = root / _LOCAL_CONFIG_SUBDIR / _LOCAL_CONFIG_NAME
    db = default_db_path(name)
    agent_doc = detect_agent_doc(root)

    return ProjectInfo(
        root=root,
        name=name,
        config=config,
        db=db,
        agent_doc=agent_doc,
    )
