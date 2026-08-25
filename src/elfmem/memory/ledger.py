"""Append-only event ledger — the durable history the file substrate cannot hold.

Markdown files are authoritative for *what a block says*. They cannot express
*what has happened to it*: how often it was retrieved, when it was last
reinforced, what outcomes it accumulated, which edges formed. Before this
module those lived only in the derived index, so rebuilding the index from
files zeroed them — and three of them (``recency``, ``reinforcement``,
``centrality``) are live terms in the retrieval composite. That is why the
Phase 4 retrieval-parity gate reported ``GATE PASSED: False`` on 5 of 5
queries and could not be tuned into passing.

The ledger closes that hole. Files say what memory *is*; the ledger says what
*happened*; the index is a materialised view of both and stays disposable.

Design rules, each earning its place:

- **One JSON object per line, appended with ``O_APPEND``.** Every line is kept
  under ``_MAX_LINE_BYTES`` so a single ``write(2)`` is atomic under POSIX
  (``PIPE_BUF`` is 512 bytes minimum, 4096 on Linux/macOS). Two processes can
  therefore append concurrently with no locking — the property that makes
  concurrent agents and git merges both benign.
- **Content is never inlined, only ids.** Keeps lines short (see above) and
  keeps the ledger a record of events rather than a second copy of memory.
- **``ah`` (cumulative active hours) on every event.** ``last_reinforced_at``
  is measured on the session-aware activity clock, not wall time. Replaying
  from timestamps alone would silently reconstruct the wrong decay clock.
  This is the non-obvious requirement.
- **Malformed lines are skipped and counted, never raised.** Fail-soft is
  correct here specifically because the ledger feeds *derived* state — the
  opposite of the fail-fast rule governing block parsing, where the file is
  the truth being asserted.
- **Monthly files.** Rotation is what makes a checkpoint a later 30-line
  addition rather than a redesign. Deliberately not built yet: at the current
  corpus a full replay is microseconds, and an unmeasured optimisation is the
  layer this project's ADRs consistently decline to pay for in advance.

See ``docs/research/block_ledger_synthesis_research.md`` §6.2.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import count
from pathlib import Path

# One write(2) below this stays atomic on every POSIX platform we target.
_MAX_LINE_BYTES = 4000
# An `asm` event lists every block in one assembled frame. Chunked rather than
# truncated, so a wide frame never silently loses reinforcement.
_MAX_IDS_PER_EVENT = 64

LEDGER_DIRNAME = "ledger"

# Never dropped to fit a line: without these an event cannot be replayed.
_REQUIRED_FIELDS = frozenset({"t", "s", "ah", "k", "id", "from", "to", "rel"})

# Event kinds. Deliberately short keys — these lines are written on every
# recall, and the byte budget above is a correctness constraint, not thrift.
KIND_BIRTH = "birth"
KIND_EDIT = "edit"
KIND_REMOVE = "remove"
KIND_ASSEMBLE = "asm"
KIND_USE = "use"
KIND_OUTCOME = "out"
KIND_LINK = "link"
KIND_UNLINK = "unlink"
# Carried-over state from before the ledger existed. Written once, by the
# export that migrates a DB-native corpus onto the file substrate. It is the
# checkpoint mechanism arriving early: replay treats it as a starting balance
# rather than as something that happened, which is exactly what it is.
KIND_SEED = "seed"
# Instance-level state that belongs to no single block: the session-aware
# activity clock. Every block's `recency` is measured against it, so an index
# rebuilt without it computes recency from zero and inverts the scale.
KIND_INSTANCE = "instance"

_JEFFREYS = 0.5

logger = logging.getLogger(__name__)

_seq = count()


@dataclass
class BlockState:
    """Everything a replay can reconstruct about one block's history."""

    created_at: str | None = None
    reinforcement_count: int = 0
    last_reinforced_at: float = 0.0
    alpha: float = _JEFFREYS
    beta: float = _JEFFREYS
    decay_lambda: float | None = None
    # The LLM's distillation of the block. Not hand-authored, so it does not
    # belong in the file; expensive to regenerate (one LLM call per block, 210
    # of them on one real corpus), so it does not belong nowhere either.
    summary: str | None = None
    removed: bool = False


@dataclass
class EdgeState:
    """One graph edge, as the ledger remembers it.

    Every field is carried rather than recomputed. Similarity edges look
    derivable from content and are not: consolidation scores them against
    *summary* embeddings (the LLM's distillation, stored per block) and folds
    in temporal proximity measured at the moment both blocks were promoted.
    Rebuilding them from content embeddings produces a different graph, not
    this one. Co-retrieval edges are pure history and were never derivable.
    """

    relation: str = "similar"
    origin: str = "similarity"
    weight: float = 0.65
    reinforcement_count: int = 0
    last_active_hours: float | None = None
    declared_by: str | None = None
    note: str | None = None


@dataclass
class ReplayResult:
    """Derived state from a full ledger replay.

    ``skipped_lines`` is the count of unparseable lines. Surfaced rather than
    hidden so a corrupt ledger is visible in ``elfmem index check`` instead of
    quietly producing a thinner history.
    """

    blocks: dict[str, BlockState] = field(default_factory=dict)
    co_retrieval: dict[tuple[str, str], int] = field(default_factory=dict)
    links: dict[tuple[str, str], EdgeState] = field(default_factory=dict)
    total_active_hours: float | None = None
    events_read: int = 0
    skipped_lines: int = 0


def ledger_dir_for(memory_dir: Path) -> Path:
    """The ledger directory that pairs with a given memory directory.

    Sits beside ``memory/`` under ``.elfmem/`` rather than inside it, so that
    ``index check``/``rebuild`` globbing ``memory/**/*.md`` never has to learn
    to skip it.
    """
    return memory_dir.parent / LEDGER_DIRNAME


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _month_file(ledger_dir: Path, *, when: str | None = None) -> Path:
    stamp = when or _now_iso()
    return ledger_dir / f"{stamp[:7]}.jsonl"


def append(
    ledger_dir: Path,
    kind: str,
    *,
    active_hours: float,
    **payload: object,
) -> None:
    """Append one event. Atomic, lock-free, and safe across processes.

    USE WHEN: any operation that changes memory or reads it into a frame —
        `learn`, `edit`, `forget`, `recall`, `outcome`, `connect`.
    DON'T USE WHEN: recording block *content* — that belongs in the markdown
        file. The ledger records that something happened, never what it said.
    COST: one `write(2)`. No DB, no LLM, no embedding.
    RETURNS: None.
    NEXT: `replay()` folds the whole ledger into `ReplayResult`.

    An event carrying more ids than one line can hold is split across several
    lines, each a valid standalone event.
    """
    ledger_dir.mkdir(parents=True, exist_ok=True)
    path = _month_file(ledger_dir)
    for chunk in _chunk_payload(payload):
        record = {
            "t": _now_iso(),
            "s": next(_seq),
            "ah": round(float(active_hours), 6),
            "k": kind,
            **chunk,
        }
        line = json.dumps(record, separators=(",", ":"), ensure_ascii=False)
        data = (line + "\n").encode("utf-8")
        while len(data) > _MAX_LINE_BYTES:
            # A line over PIPE_BUF can tear under a concurrent append, so it
            # cannot be written as-is. Drop the single largest free-text field
            # and say which one: an earlier version dropped a fixed whitelist,
            # which silently discarded seeded evidence (alpha/beta/counts)
            # rather than the oversized prose that actually caused it.
            text_fields = {
                k: v for k, v in record.items()
                if isinstance(v, str) and k not in _REQUIRED_FIELDS
            }
            if not text_fields:
                raise ValueError(
                    f"Ledger event {kind!r} exceeds {_MAX_LINE_BYTES} bytes with "
                    "no droppable text field; refusing to write a line that "
                    "could tear under a concurrent append."
                )
            biggest = max(text_fields, key=lambda k: len(text_fields[k]))
            logger.warning(
                "Ledger event %r too large; dropping field %r (%d chars)",
                kind, biggest, len(text_fields[biggest]),
            )
            record.pop(biggest)
            data = (
                json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n"
            ).encode("utf-8")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)


def _chunk_payload(payload: dict[str, object]) -> Iterator[dict[str, object]]:
    """Split an event whose ``ids`` list is too long into several events."""
    ids = payload.get("ids")
    if not isinstance(ids, (list, tuple)) or len(ids) <= _MAX_IDS_PER_EVENT:
        yield payload
        return
    rest = {k: v for k, v in payload.items() if k != "ids"}
    for i in range(0, len(ids), _MAX_IDS_PER_EVENT):
        yield {**rest, "ids": list(ids[i:i + _MAX_IDS_PER_EVENT])}


def _read_events(ledger_dir: Path) -> tuple[list[dict], int]:
    """Read every event from every monthly file, ordered deterministically.

    Order is ``(t, s)`` — the wall-clock stamp with a per-process sequence
    number breaking ties. Determinism matters because the parity gate compares
    rankings that depend on replayed state.
    """
    if not ledger_dir.is_dir():
        return [], 0
    events: list[dict] = []
    skipped = 0
    for path in sorted(ledger_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue
            if not isinstance(event, dict) or "k" not in event:
                skipped += 1
                continue
            events.append(event)
    events.sort(key=lambda e: (str(e.get("t", "")), int(e.get("s", 0))))
    return events, skipped


def _canonical(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a < b else (b, a)


def replay(ledger_dir: Path) -> ReplayResult:
    """Fold the whole ledger into the state a rebuild needs.

    USE WHEN: rebuilding the derived index from files — this supplies
        everything the files themselves cannot express.
    DON'T USE WHEN: answering a live query. Read the index; it is the
        materialised view this produces.
    COST: pure file I/O and arithmetic. No DB, no LLM, no embedding.
    RETURNS: `ReplayResult`. Unparseable lines are counted in
        `skipped_lines`, never raised — a damaged ledger degrades the history
        it can reconstruct rather than blocking the rebuild entirely.
    NEXT: `rebuild_index()` applies this over the parsed block files.
    """
    events, skipped = _read_events(ledger_dir)
    result = ReplayResult(events_read=len(events), skipped_lines=skipped)

    for event in events:
        kind = event.get("k")
        ah = float(event.get("ah", 0.0) or 0.0)

        if kind == KIND_INSTANCE:
            hours = event.get("total_ah")
            if hours is not None:
                result.total_active_hours = float(hours)

        elif kind == KIND_SEED:
            block_id = event.get("id")
            if isinstance(block_id, str):
                state = result.blocks.setdefault(block_id, BlockState())
                state.created_at = str(event.get("created") or "") or None
                # `.get(k, default)` only, never `or default`: 0 is a valid
                # value for every one of these and `0.0 or 0.5` is 0.5.
                state.reinforcement_count = int(event.get("n", 0))
                state.last_reinforced_at = float(event.get("lah", 0.0))
                state.alpha = float(event.get("a", _JEFFREYS))
                state.beta = float(event.get("b", _JEFFREYS))
                lam = event.get("lam")
                state.decay_lambda = None if lam is None else float(lam)
                state.summary = event.get("sum")

        elif kind == KIND_BIRTH:
            block_id = event.get("id")
            if isinstance(block_id, str):
                state = result.blocks.setdefault(block_id, BlockState())
                # First birth wins: a re-learned block keeps its original
                # creation date rather than being re-dated by a later event.
                if state.created_at is None:
                    state.created_at = str(event.get("t", ""))
                state.removed = False

        elif kind == KIND_REMOVE:
            block_id = event.get("id")
            if isinstance(block_id, str):
                result.blocks.setdefault(block_id, BlockState()).removed = True

        elif kind in (KIND_ASSEMBLE, KIND_USE):
            ids = [i for i in event.get("ids", []) if isinstance(i, str)]
            for block_id in ids:
                state = result.blocks.setdefault(block_id, BlockState())
                state.reinforcement_count += 1
                state.last_reinforced_at = max(state.last_reinforced_at, ah)
            # Co-retrieval: blocks assembled together associate. This is the
            # learned half of the graph; the declared half lives in the files.
            for i, a in enumerate(ids):
                for b in ids[i + 1:]:
                    pair = _canonical(a, b)
                    result.co_retrieval[pair] = result.co_retrieval.get(pair, 0) + 1

        elif kind == KIND_OUTCOME:
            block_id = event.get("id")
            if isinstance(block_id, str):
                signal = float(event.get("sig", 0.0) or 0.0)
                weight = float(event.get("w", 0.0) or 0.0)
                state = result.blocks.setdefault(block_id, BlockState())
                state.alpha += signal * weight
                state.beta += (1.0 - signal) * weight

        elif kind == KIND_LINK:
            a, b = event.get("from"), event.get("to")
            if isinstance(a, str) and isinstance(b, str) and a != b:
                lah = event.get("lah")
                result.links[_canonical(a, b)] = EdgeState(
                    relation=str(event.get("rel", "similar")),
                    origin=str(event.get("o", "similarity")),
                    weight=float(event.get("w", 0.65)),
                    reinforcement_count=int(event.get("rc", 0)),
                    last_active_hours=None if lah is None else float(lah),
                    declared_by=event.get("by"),
                    note=event.get("note"),
                )

        elif kind == KIND_UNLINK:
            a, b = event.get("from"), event.get("to")
            if isinstance(a, str) and isinstance(b, str):
                result.links.pop(_canonical(a, b), None)

    return result


def record_assembly(
    ledger_dir: Path | None,
    block_ids: Sequence[str],
    *,
    active_hours: float,
    frame: str | None = None,
    session_id: str | None = None,
) -> None:
    """Record that these blocks were assembled into a frame.

    This is the label tier that costs nothing and needs no cooperation from
    the calling agent — which matters, because across three real instances
    the *voluntary* feedback verb has been called nine times in total. A
    design that only works when an agent volunteers feedback does not work.
    """
    if ledger_dir is None or not block_ids:
        return
    payload: dict[str, object] = {"ids": list(block_ids)}
    if frame:
        payload["frame"] = frame
    if session_id:
        payload["sid"] = session_id
    append(ledger_dir, KIND_ASSEMBLE, active_hours=active_hours, **payload)
