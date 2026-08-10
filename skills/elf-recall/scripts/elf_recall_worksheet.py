#!/usr/bin/env python3
"""Render judged candidates into a checkbox worksheet.

Forked from ctx's ctx_worksheet.py (D-003 lineage): judging happens upstream
(Claude reads matched passages, produces relevance judgments); this script
only formats them deterministically — checkboxes default checked, secrets
redacted before anything is written to disk.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from elf_recall_common import resolve_memory_dir, scan_secrets, workspace_dir  # noqa: E402

VALID_KINDS = {"decision", "example", "caveat", "data", "question", "other"}
VALID_CONFIDENCE = {"high", "medium", "low"}


def render_worksheet(query: str, judgments: list[dict[str, Any]], created: str) -> str:
    lines = [
        f'# elf-recall worksheet — "{query}"',
        f"<!-- elf-recall:worksheet query={json.dumps(query)} created={created} unranked=true -->",
        "",
        "Unranked, live-grep results — not the same as `frame()`/`recall()`'s "
        "relevance-scored output, and the two can disagree (see model.md S11).",
        "",
        "Uncheck anything you don't want. Edit the quoted text to trim it.",
        "Tags in `[brackets]` are the judge's opinion, not fact.",
        "",
    ]
    for i, j in enumerate(judgments, start=1):
        sid = f"s{i:02d}"
        kind = str(j.get("kind")) if j.get("kind") in VALID_KINDS else "other"
        confidence = (
            str(j.get("confidence"))
            if j.get("confidence") in VALID_CONFIDENCE
            else "medium"
        )
        tag_bits = [kind, confidence]

        heading = (j.get("heading_path") or "").strip()
        heading_str = f" › {heading}" if heading else ""

        text = j["excerpt"].strip()
        findings = scan_secrets(text)
        if findings:
            text = (
                f"⚠️ possible secret redacted ({findings[0].kind}) — "
                f"review {j['file']}:{j.get('line', '?')} directly if you need this"
            )

        lines.append(
            f"- [x] **{Path(j['file']).name}**{heading_str}  `{sid}` `[{', '.join(tag_bits)}]`"
        )
        for text_line in text.splitlines() or [""]:
            lines.append(f"  > {text_line}")
        if j.get("reason"):
            lines.append(f"  <!-- elf-recall:reason {j['reason']} -->")
        lines.append("")

    lines.append("<!-- elf-recall:worksheet-end -->")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render judged candidates as a checkbox worksheet."
    )
    parser.add_argument("--query", required=True)
    parser.add_argument(
        "--in", dest="in_path", help="JSON file of judgments; reads stdin if omitted."
    )
    parser.add_argument(
        "--out", help="Output path; defaults to a timestamped file under .elfmem/.elf-recall/"
    )
    args = parser.parse_args()

    memory_dir = resolve_memory_dir()
    raw = Path(args.in_path).read_text() if args.in_path else sys.stdin.read()
    judgments = json.loads(raw)
    relevant = [j for j in judgments if j.get("relevant", True)]

    created = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    content = render_worksheet(args.query, relevant, created)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        out_path = workspace_dir(memory_dir) / f"worksheet-{ts}.md"

    out_path.write_text(content, encoding="utf-8")
    print(str(out_path))


if __name__ == "__main__":
    main()
