"""Markdown block file format: frontmatter parsing, block extraction, and
round-trip serialization for ``.elfmem/memory/**.md``.

Two modes:

- **raw** (``self.md``): the whole file is the constitution, read directly,
  never parsed into blocks (Invariant 2 — it never enters the block table).
- **block** (``notes/*.md``, ``log/*.md``): one block per ``##`` heading, with
  an optional HTML-comment frontmatter line carrying ``id``/``tags``/
  ``pinned``/``created`` and any further fields. The frontmatter schema is
  deliberately open (unrecognised keys land in ``Block.extra``) rather than
  fixed, so peer-specific fields (``source_peer``, ``msg_id``, ``received_at``)
  round-trip through this module without it needing to know their meaning.

A block's ``id`` is permanent and content-independent once assigned
(Invariant 3): editing a block's content through this module never changes
its ``id``. A block with no ``id`` yet (freshly written, never round-tripped)
is assigned one at write time, seeded from ``compute_content_hash`` — after
that first write, the id is fixed regardless of further edits.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from elfmem.exceptions import ElfmemError
from elfmem.memory.blocks import compute_content_hash

_HEADING_RE = re.compile(r"^##[ \t]+(?P<title>.+?)[ \t]*$", re.MULTILINE)
_FRONTMATTER_RE = re.compile(r"^<!--(?P<body>.*)-->[ \t]*$", re.DOTALL)
_FIELD_RE = re.compile(
    r"(?P<key>[a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*(?P<value>\[[^\]]*\]|\S+)"
)
_KNOWN_FIELDS = {"id", "tags", "pinned", "created"}


class BlockFileError(ElfmemError):
    """Raised on a structural violation within one block file.

    Individual malformed frontmatter is recoverable (collected as a
    ``ParseError`` so the rest of the file still parses); a duplicate ``id``
    within one file is not, because nothing in this module can decide which
    of the two blocks is authoritative.
    """


@dataclass
class Block:
    """One ``##``-headed block: title, body content, and frontmatter fields."""

    title: str
    content: str
    id: str | None = None
    tags: list[str] = field(default_factory=list)
    pinned: bool = False
    created: str | None = None
    extra: dict[str, str] = field(default_factory=dict)


@dataclass
class ParseError:
    """One block whose frontmatter comment could not be parsed.

    The block's title and raw content are still recoverable from the file;
    only its structured frontmatter fields were unreadable.
    """

    title: str
    raw_frontmatter: str
    reason: str


@dataclass
class ParseResult:
    """Everything ``parse_blocks`` found: successfully parsed blocks, and
    any per-block frontmatter that could not be read.

    USE WHEN: reading a ``notes/*.md`` or ``log/*.md`` file into memory.
    DON'T USE WHEN: reading ``self.md`` — use ``read_raw`` instead, since
        constitution files are never parsed into blocks.
    COST: pure string parsing, no I/O, no LLM calls.
    RETURNS: ``ParseResult`` — malformed frontmatter is reported here, not
        raised, so one bad block doesn't hide the rest of a good file.
    NEXT: ``elfmem index --check`` surfaces ``.errors`` across every file.
    """

    blocks: list[Block]
    errors: list[ParseError]


def read_raw(text: str) -> str:
    """Constitution mode: the file *is* the content, never parsed into blocks.

    USE WHEN: reading ``self.md``.
    DON'T USE WHEN: reading anything under ``notes/`` or ``log/`` — those are
        block mode; use ``parse_blocks``.
    COST: none — this is the identity function, kept as a named boundary so
        callers can't accidentally block-parse a constitution file.
    RETURNS: the file's content, unchanged.
    NEXT: inject directly as the ``self`` frame's preamble.
    """
    return text


def _next_line_is_frontmatter(text: str, pos: int) -> bool:
    """Does the next non-blank line after *pos* look like a frontmatter comment?

    Used to distinguish a genuine block-boundary heading from a markdown
    sub-heading embedded in another block's own content (see `parse_blocks`).
    """
    remainder = text[pos:]
    for line in remainder.splitlines():
        if line.strip() == "":
            continue
        return _FRONTMATTER_RE.match(line.strip()) is not None
    return False


def _parse_frontmatter(comment_body: str) -> tuple[dict[str, str], str | None]:
    """Extract fields from one frontmatter comment body.

    Returns ``(fields, error_reason)``. ``error_reason`` is ``None`` on
    success. A comment that *looks* like it was attempting fields (contains a
    ``:``) but has unbalanced ``[`` / ``]`` in a tags-like value is malformed.
    """
    if "[" in comment_body and comment_body.count("[") != comment_body.count("]"):
        return {}, "unbalanced '[' / ']' in frontmatter"
    fields: dict[str, str] = {}
    for m in _FIELD_RE.finditer(comment_body):
        fields[m.group("key")] = m.group("value")
    if not fields and ":" in comment_body:
        return {}, "no valid key: value pairs found"
    return fields, None


def _build_block(title: str, raw_fields: dict[str, str], content: str) -> Block:
    tags_raw = raw_fields.get("tags", "")
    tags = (
        [t.strip() for t in tags_raw.strip("[]").split(",") if t.strip()]
        if tags_raw
        else []
    )
    pinned = raw_fields.get("pinned", "false").strip().lower() == "true"
    extra = {
        k: v for k, v in raw_fields.items() if k not in _KNOWN_FIELDS
    }
    return Block(
        title=title,
        content=content,
        id=raw_fields.get("id"),
        tags=tags,
        pinned=pinned,
        created=raw_fields.get("created"),
        extra=extra,
    )


def parse_blocks(text: str) -> ParseResult:
    """Block mode: extract one ``Block`` per genuine ``##`` heading.

    A candidate ``##`` line is a *genuine* block boundary — as opposed to a
    markdown sub-heading inside another block's own content (real content
    does this: a Theory-of-Mind block's body legitimately contains its own
    ``## Goals`` / ``## Beliefs`` sections) — when either: no block has
    started yet, the block currently being accumulated has no frontmatter
    of its own (a bare, hand-authored block — nothing to disambiguate
    against, so every ``##`` line splits, matching pre-frontmatter
    behaviour), or the candidate line is itself immediately followed by a
    frontmatter comment (confirming it, not the surrounding prose, is the
    real boundary). Found via a real-data migration dry run — a naive
    "every ``##`` line is a boundary" reading mis-split 15 blocks in a
    140-block production corpus.

    USE WHEN: reading a ``notes/*.md`` or ``log/*.md`` file.
    DON'T USE WHEN: reading ``self.md`` — use ``read_raw``.
    COST: pure string parsing, no I/O, no LLM calls.
    RETURNS: ``ParseResult``.
    NEXT: ``write_blocks`` to round-trip; ``elfmem index`` to derive L2 from
        the result.
    """
    all_headings = list(_HEADING_RE.finditer(text))
    blocks: list[Block] = []
    errors: list[ParseError] = []

    i = 0
    while i < len(all_headings):
        start = all_headings[i]
        title = start.group("title")
        this_has_frontmatter = _next_line_is_frontmatter(text, start.end())

        j = i + 1
        while j < len(all_headings):
            if not this_has_frontmatter or _next_line_is_frontmatter(
                text, all_headings[j].end()
            ):
                break
            j += 1
        end_pos = all_headings[j].start() if j < len(all_headings) else len(text)

        remainder = text[start.end():end_pos]
        lines = remainder.splitlines(keepends=True)
        idx = 0
        while idx < len(lines) and lines[idx].strip() == "":
            idx += 1

        raw_fields: dict[str, str] = {}
        if idx < len(lines):
            fm_match = _FRONTMATTER_RE.match(lines[idx].strip())
            if fm_match:
                comment_body = fm_match.group("body").strip()
                raw_fields, error_reason = _parse_frontmatter(comment_body)
                if error_reason is not None:
                    errors.append(
                        ParseError(
                            title=title,
                            raw_frontmatter=lines[idx].strip(),
                            reason=error_reason,
                        )
                    )
                idx += 1

        content = "".join(lines[idx:]).strip()
        blocks.append(_build_block(title, raw_fields, content))
        i = j

    seen: dict[str, int] = {}
    for b in blocks:
        if b.id is not None:
            seen[b.id] = seen.get(b.id, 0) + 1
    duplicates = sorted(i for i, count in seen.items() if count > 1)
    if duplicates:
        raise BlockFileError(
            f"Duplicate block id(s) in one file: {', '.join(duplicates)}",
            recovery=(
                "Each block's id must be unique within its file. Assign a "
                "new id to one of the duplicates (delete the id: field and "
                "let the next write assign one), or merge the two blocks."
            ),
        )

    return ParseResult(blocks=blocks, errors=errors)


def _render_frontmatter(b: Block) -> str:
    parts = [f"id: {b.id}"]
    if b.tags:
        parts.append(f"tags: [{', '.join(b.tags)}]")
    parts.append(f"pinned: {'true' if b.pinned else 'false'}")
    if b.created:
        parts.append(f"created: {b.created}")
    for k, v in b.extra.items():
        parts.append(f"{k}: {v}")
    return f"<!-- {'  '.join(parts)} -->"


def write_blocks(blocks: list[Block]) -> str:
    """Serialize blocks back to markdown, one ``##`` section each.

    USE WHEN: persisting blocks (new or edited) back to a ``notes/*.md`` or
        ``log/*.md`` file.
    DON'T USE WHEN: writing ``self.md`` — write its raw text directly.
    COST: pure string formatting, no I/O, no LLM calls.
    RETURNS: the file's new full text.
    NEXT: write the result to disk; re-``parse_blocks`` to verify round-trip
        if the caller needs to confirm nothing was lost.

    A block with ``id is None`` is assigned one here, seeded from
    ``compute_content_hash(content)`` — mutates the block in place, since
    after this the id is permanent (Invariant 3).
    """
    sections = []
    for b in blocks:
        if b.id is None:
            b.id = compute_content_hash(b.content)[:16]
        sections.append(f"## {b.title}\n{_render_frontmatter(b)}\n\n{b.content}\n")
    return "\n".join(sections)
