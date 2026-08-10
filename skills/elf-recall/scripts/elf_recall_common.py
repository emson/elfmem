#!/usr/bin/env python3
"""Shared utilities for the elf-recall skill scripts.

Forked from ctx (skill_forge/skills/ctx/scripts/ctx_common.py, D-001/D-004
lineage) and adapted: elf-recall has exactly one valid target per project
(`.elfmem/memory/`), resolved the same way elfmem itself resolves its
project root — no CTX_VAULT-style config, no `--vault` flag, no `init` step.

No index, no database — everything here supports live filesystem search,
matching the research doc's Iteration 4 (Sim II): elf-recall is the
occasional, human-supervised, unranked read path, distinct from the
index-backed `frame()`/`recall()` MCP tools.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

MEMORY_DIRNAME = Path(".elfmem") / "memory"
WORKSPACE_DIRNAME = Path(".elfmem") / ".elf-recall"
_MARKER = ".elfmem"


def resolve_memory_dir(start: Path | None = None) -> Path:
    """Walk up from *start* (default cwd) to find `.elfmem/memory/`.

    Mirrors `elfmem.project.find_local_config`'s walk-up convention — one
    memory directory per project, no configuration needed to point at it.
    """
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        marker = candidate / _MARKER
        if marker.is_dir():
            memory_dir = marker / "memory"
            if not memory_dir.is_dir():
                raise SystemExit(
                    f"Found {marker} but no memory/ inside it. "
                    "Run 'elfmem init' to create .elfmem/memory/."
                )
            return memory_dir
    raise SystemExit(
        "No .elfmem/ project found walking up from the current directory. "
        "Run 'elfmem init' first, or cd into a project that has one."
    )


def workspace_dir(memory_dir: Path) -> Path:
    """Sidecar output directory for worksheets — never inside memory/ itself.

    memory/ is the authoritative substrate (Invariant 1); elf-recall is
    read-only over it (mirrors ctx's read-only-vault invariant, D-004), so
    worksheets land in a sibling directory, not merged into the corpus.
    """
    project_root = memory_dir.parent.parent  # .elfmem/memory -> project root
    ws = (project_root / WORKSPACE_DIRNAME).resolve()
    if ws.is_relative_to(memory_dir.resolve()):
        raise SystemExit(
            f"Refusing to write elf-recall output inside memory/ ({memory_dir})."
        )
    ws.mkdir(parents=True, exist_ok=True)
    return ws


def iter_markdown_files(memory_dir: Path) -> Iterable[Path]:
    for root, dirnames, filenames in os.walk(memory_dir):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            if name.endswith(".md"):
                yield Path(root) / name


def heading_path_at(lines: list[str], line_index: int) -> str:
    """Derive the heading breadcrumb ('H1 › H2 › H3') for a 0-indexed line.

    elfmem block files use `##` per-block headings (U-001's frontmatter
    format), so this doubles as "which block is this line inside" — not
    just document structure the way it was in ctx's free-form vault.
    """
    heading_re = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
    stack: dict[int, str] = {}
    for i in range(0, min(line_index + 1, len(lines))):
        m = heading_re.match(lines[i])
        if not m:
            continue
        level = len(m.group(1))
        stack[level] = m.group(2).strip()
        for deeper in [lvl for lvl in stack if lvl > level]:
            del stack[deeper]
    return " › ".join(stack[lvl] for lvl in sorted(stack))


# Deliberately a short list of high-confidence patterns, not an entropy
# analyzer — same rationale and same list as ctx_common.py's original;
# personal memory is exactly as likely to accidentally contain a pasted key.
SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9\-_]{10,}")),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9]{20,}")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}")),
    ("aws_access_key_id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    (
        "generic_secret_assignment",
        re.compile(
            r"(?i)\b(api[_-]?key|access[_-]?token|secret)\b\s*[:=]\s*"
            r"['\"]?[A-Za-z0-9_\-]{16,}"
        ),
    ),
]


@dataclass
class SecretFinding:
    kind: str
    line: int
    excerpt: str


def scan_secrets(text: str) -> list[SecretFinding]:
    findings: list[SecretFinding] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for kind, pattern in SECRET_PATTERNS:
            if pattern.search(line):
                findings.append(
                    SecretFinding(kind=kind, line=lineno, excerpt=line.strip()[:80])
                )
    return findings


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def print_json(obj: object) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False))
