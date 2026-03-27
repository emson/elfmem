"""Project-local config discovery, agent doc integration, and MCP snippet generation.

Stateless module — pure functions only. No globals. No I/O side effects except
the explicit write_agent_section() function.

The config discovery chain (used by every CLI command):
    1. --config PATH flag           (explicit always wins)
    2. ELFMEM_CONFIG env var
    3. .elfmem/config.yaml          (walk up from cwd to project root)
    4. ~/.elfmem/config.yaml        (global fallback, if it exists)

The DB discovery chain:
    1. --db PATH flag
    2. ELFMEM_DB env var
    3. project.db from discovered config
    4. ~/.elfmem/agent.db           (global fallback)
"""
from __future__ import annotations

import json
import os
import re
import tomllib
from pathlib import Path
from typing import NamedTuple

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
SECTION_START = "<!-- elfmem:start -->"
SECTION_END = "<!-- elfmem:end -->"

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
        for marker in _PROJECT_MARKERS:
            if (current / marker).exists():
                return current
        parent = current.parent
        if parent == current or current == home:
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

    env = os.getenv("ELFMEM_CONFIG")
    if env:
        return str(Path(env).expanduser()), "ELFMEM_CONFIG env"

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
    """
    if explicit:
        return str(Path(explicit).expanduser()), "explicit flag"

    env = os.getenv("ELFMEM_DB")
    if env:
        return str(Path(env).expanduser()), "ELFMEM_DB env"

    if config_path:
        db_from_config = _read_project_db(config_path)
        if db_from_config:
            return str(Path(db_from_config).expanduser()), "project.db in config"

    return str(Path("~/.elfmem/agent.db").expanduser()), "global fallback (~/.elfmem/agent.db)"


def _read_project_db(config_path: str) -> str:
    """Read project.db from a config YAML file. Returns '' on any error."""
    try:
        import yaml
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return str((data.get("project") or {}).get("db", ""))
    except Exception:
        return ""


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


def has_agent_section(doc_path: Path) -> bool:
    """Return True if *doc_path* contains the managed elfmem section."""
    if not doc_path.exists():
        return False
    return SECTION_START in doc_path.read_text(encoding="utf-8")


def has_mcp_entry(mcp_path: Path) -> bool:
    """Return True if *mcp_path* mentions 'elfmem'."""
    if not mcp_path.exists():
        return False
    return "elfmem" in mcp_path.read_text(encoding="utf-8")


# ── Agent doc writing ─────────────────────────────────────────────────────────


def _build_section(
    *,
    name: str,
    db_path: str,
    config_path: str,
    identity: str = "",
) -> str:
    """Build the managed elfmem block for insertion into an agent doc file."""
    mcp = mcp_json_snippet(config_path=config_path)
    identity_line = f"- **Identity:** {identity}\n" if identity else ""
    return (
        f"{SECTION_START}\n"
        f"## elfmem — Project Memory\n\n"
        f"- **Project:** {name}\n"
        f"- **Database:** `{db_path}`\n"
        f"- **Config:** `{config_path}`\n"
        f"{identity_line}"
        f"\n"
        f"Commands:\n"
        f"- `elfmem doctor` — verify setup, show paths\n"
        f"- `elfmem status` — memory health\n"
        f"- `elfmem guide` — all operations\n"
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

    if SECTION_START in existing and SECTION_END in existing:
        # Replace the existing managed block.
        pattern = re.compile(
            re.escape(SECTION_START) + r".*?" + re.escape(SECTION_END),
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
