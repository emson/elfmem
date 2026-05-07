"""Generate and validate agent-docs fragments from guide.GUIDES.

The fragment (.elfmem/AGENT.md) is a markdown projection of the library's
runtime documentation (guide.GUIDES). It's the single source of truth for
how agents should invoke elfmem — always current, automatically generated.

Validation detects drift: version mismatch (library upgraded) or content
hash mismatch (user hand-edited).
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, cast

from elfmem.guide import GUIDES


def render_agent_docs() -> str:
    """Generate markdown fragment from guide.GUIDES.

    Returns the full markdown content ready to write to .elfmem/AGENT.md.
    Includes: header with version/hash frontmatter, overview, and per-op
    guidance cards.
    """
    from importlib.metadata import version as pkg_version

    try:
        lib_version = pkg_version("elfmem")
    except Exception:
        lib_version = "unknown"

    # Content hash for drift detection
    guides_content = _guides_to_markdown(GUIDES)
    content_hash = hashlib.sha256(guides_content.encode()).hexdigest()[:16]

    # Frontmatter
    header = (
        f"<!-- elfmem:agent-docs v{lib_version} {content_hash} -->\n"
        f"# elfmem — Agent Documentation\n\n"
        f"Auto-generated from `guide.GUIDES` (v{lib_version}).\n"
        f"Single source of truth for how to invoke elfmem.\n\n"
    )

    # Overview — CLI is the primary interface for agent-driven usage.
    overview = (
        "## Quick Start (CLI)\n\n"
        "elfmem is a CLI-first tool. Agents invoke it via shell commands, "
        "typically through `uv run --env-file .env elfmem ...` in a project.\n\n"
        "```bash\n"
        "# Memory\n"
        "elfmem remember 'I learned X'\n"
        "elfmem recall 'topic' --frame attention --top-k 5\n"
        "elfmem outcome <block-id> 0.9          # reinforce useful blocks\n"
        "\n"
        "# Health & introspection\n"
        "elfmem status                          # health snapshot\n"
        "elfmem doctor                          # setup verification, drift checks\n"
        "elfmem guide                           # all operations\n"
        "elfmem guide <operation>               # detail for one operation\n"
        "\n"
        "# Maintenance\n"
        "elfmem dream                           # consolidate pending blocks\n"
        "elfmem curate                          # archive stale, prune weak edges\n"
        "elfmem agent-docs install              # regenerate this file after upgrade\n"
        "\n"
        "# Peer communication (inter-agent messaging)\n"
        "elfmem peer list                       # registered peers + DIDs + delivery paths\n"
        "elfmem peer send <did> '<message>'     # e.g. elfmem peer send elf:elf 'hello'\n"
        "elfmem peer inbox                      # check pending peer messages\n"
        "```\n\n"
        "Subcommand syntax uses spaces (`elfmem peer send`), not underscores. "
        "The operation cards below use Python-style names (`peer_send`) because "
        "they document the underlying API; the CLI form is `elfmem peer send`.\n\n"
    )

    # Python embedding — secondary, for library consumers.
    python_note = (
        "## Embedding in Python (library use)\n\n"
        "If you are embedding elfmem in a Python application rather than calling "
        "it from the shell, the same operations are available on `MemorySystem`:\n\n"
        "```python\n"
        "from elfmem import MemorySystem\n\n"
        "system = await MemorySystem.from_config('agent.db')\n"
        "async with system.session():\n"
        "    await system.remember('I learned something')\n"
        "    result = await system.frame('attention', query='what do I know about X?')\n"
        "    print(result.text)\n"
        "```\n\n"
    )

    # Per-operation cards
    operations = _operation_cards(GUIDES)

    return header + overview + python_note + operations


def _guides_to_markdown(guides: dict[str, Any]) -> str:
    """Serialize guides dict to a stable string for hashing.

    Ensures version-independent hashing (guides change, hash changes).
    """
    lines = []
    for name in sorted(guides.keys()):
        guide = guides[name]
        lines.append(f"{name}|{guide.what}|{guide.when}|{guide.returns}")
    return "\n".join(lines)


def _operation_cards(guides: dict[str, Any]) -> str:
    """Render per-operation markdown cards."""
    lines = ["## Operations\n"]

    for name in sorted(guides.keys()):
        guide = guides[name]
        lines.append(f"### `{name}`\n")
        lines.append(f"**What:** {guide.what}\n")
        lines.append(f"**When:** {guide.when}\n")
        lines.append(f"**Returns:** {guide.returns}\n")
        lines.append(f"**Cost:** {guide.cost}\n\n")

    return "\n".join(lines)


def get_fragment_hash(content: str) -> str:
    """Hash fragment content for drift detection."""
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def write_lock_file(lock_path: Path, lib_version: str, content_hash: str) -> None:
    """Write .agent-docs.lock with version and content hash."""
    import json

    lock_data = {"library_version": lib_version, "content_sha256": content_hash}
    lock_path.write_text(json.dumps(lock_data, indent=2), encoding="utf-8")


def read_lock_file(lock_path: Path) -> dict[str, str] | None:
    """Read lock file. Returns None if missing or corrupt."""
    if not lock_path.exists():
        return None
    try:
        import json

        return cast(dict[str, str], json.loads(lock_path.read_text(encoding="utf-8")))
    except Exception:
        return None


def check_drift(
    fragment_path: Path, lock_path: Path, current_lib_version: str
) -> tuple[bool, str]:
    """Check for agent-docs drift.

    Returns (is_drifted, reason). Reasons: 'missing' | 'stale_version' | 'edited' | 'ok'.
    """
    if not fragment_path.exists():
        return True, "missing"

    lock = read_lock_file(lock_path)
    if lock is None:
        return True, "missing_lock"

    lock_version = lock.get("library_version", "")
    lock_hash = lock.get("content_sha256", "")

    if lock_version != current_lib_version:
        return True, "stale_version"

    current_content = render_agent_docs()
    current_hash = get_fragment_hash(current_content)
    if current_hash != lock_hash:
        return True, "edited"

    return False, "ok"
