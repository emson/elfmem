#!/usr/bin/env python3
"""Discover: live ripgrep/glob search over `.elfmem/memory/`. No index.

Forked from ctx's ctx_find.py (D-002 lineage) and adapted: no wikilink
expansion (elfmem's block format has no wikilink convention — dropping
speculative machinery rather than porting it unused), fixed target directory
instead of a configurable vault.

Term-variant generation happens upstream — the calling agent should pass the
literal query plus 2-4 rephrasings via --terms, mitigating grep's one real
weakness (vocabulary mismatch, per the research doc's Sim II Iteration 5).
This script only does deterministic search.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from elf_recall_common import (  # noqa: E402
    heading_path_at,
    iter_markdown_files,
    print_json,
    resolve_memory_dir,
)

CONTEXT_LINES = 6
MAX_FILES_DEFAULT = 15


def rg_available() -> bool:
    return shutil.which("rg") is not None


def search_with_ripgrep(memory_dir: Path, terms: list[str]) -> dict[str, list[int]]:
    pattern = "|".join(re.escape(t) for t in terms)
    result = subprocess.run(
        ["rg", "-n", "-i", "-g", "*.md", "--no-heading", pattern, str(memory_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    hits: dict[str, list[int]] = {}
    for line in result.stdout.splitlines():
        parts = line.split(":", 2)
        if len(parts) < 2 or not parts[1].isdigit():
            continue
        hits.setdefault(parts[0], []).append(int(parts[1]))
    return hits


def search_with_python(memory_dir: Path, terms: list[str]) -> dict[str, list[int]]:
    pattern = re.compile("|".join(re.escape(t) for t in terms), re.IGNORECASE)
    hits: dict[str, list[int]] = {}
    for path in iter_markdown_files(memory_dir):
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        matches = [i + 1 for i, line in enumerate(lines) if pattern.search(line)]
        if matches:
            hits[str(path)] = matches
    return hits


def filename_matches(
    memory_dir: Path, terms: list[str], already: set[str]
) -> list[str]:
    lowered = [t.lower() for t in terms]
    found = []
    for path in iter_markdown_files(memory_dir):
        if str(path) in already:
            continue
        stem = path.stem.lower().replace("-", " ").replace("_", " ")
        if any(t in stem for t in lowered):
            found.append(str(path))
    return found


def build_candidate(path: Path, match_lines: list[int]) -> dict[str, object]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()
    excerpts = []
    seen_ranges: list[tuple[int, int]] = []
    for lineno in sorted(set(match_lines))[:6]:
        start = max(0, lineno - 1 - CONTEXT_LINES)
        end = min(len(lines), lineno + CONTEXT_LINES)
        if any(start < e and s < end for s, e in seen_ranges):
            continue
        seen_ranges.append((start, end))
        excerpts.append(
            {
                "line": lineno,
                "heading_path": heading_path_at(lines, lineno - 1),
                "context": "\n".join(lines[start:end]),
            }
        )
    return {"file": str(path), "matches": excerpts}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Discover candidate blocks via live grep/glob over .elfmem/memory/ — no index."
    )
    parser.add_argument("--terms", nargs="+", required=True, help="Query plus 2-4 rephrasings.")
    parser.add_argument("--max-files", type=int, default=MAX_FILES_DEFAULT)
    args = parser.parse_args()

    memory_dir = resolve_memory_dir()
    hits = (
        search_with_ripgrep(memory_dir, args.terms)
        if rg_available()
        else search_with_python(memory_dir, args.terms)
    )

    for path_str in filename_matches(memory_dir, args.terms, already=set(hits)):
        hits[path_str] = [1]

    all_paths = list(hits.keys())
    truncated = len(all_paths) > args.max_files
    shown_paths = all_paths[: args.max_files]
    candidates = [build_candidate(Path(p), hits[p]) for p in shown_paths]

    print_json(
        {
            "memory_dir": str(memory_dir),
            "terms": args.terms,
            "candidate_count": len(all_paths),
            "shown_count": len(shown_paths),
            "truncated": truncated,
            "candidates": candidates,
            "search_backend": "ripgrep" if rg_available() else "python",
            "unranked": True,  # S11 (model.md) -- always label this, never imply relevance ranking
        }
    )


if __name__ == "__main__":
    main()
