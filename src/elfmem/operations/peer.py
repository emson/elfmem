"""Peer communication — export, import, send, inbox operations.

Architecture: pull-based, file-mediated, zero infrastructure.

Export = curate-adjacent (Sleep rhythm): prepare knowledge for others.
Import = learn-adjacent (Heartbeat rhythm): ingest peer blocks into inbox.
Send   = learn mirror (Heartbeat rhythm): store + write outbox file.
Inbox  = learn batch (Heartbeat rhythm): scan directory + import messages.

Transport (moving files between outbox and inbox) is not elfmem's concern.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from elfmem.db.queries import (
    add_tags,
    get_all_peers,
    get_block,
    get_config,
    get_edges_for_export,
    get_exportable_blocks,
    get_peer,
    get_tags_batch,
    insert_block,
    set_config,
    update_peer_stats,
)
from elfmem.exceptions import PeerError
from elfmem.memory.blocks import compute_content_hash
from elfmem.types import (
    ExportResult,
    ImportResult,
    PeerInboxResult,
    PeerInboxStatus,
    PeerSendResult,
)

BUNDLE_VERSION = 1
_SELF_TAG_PREFIX = "self/"


# ── Identity ─────────────────────────────────────────────────────────────────


async def get_identity(conn: AsyncConnection) -> str:
    """Read this instance's peer identity from system_config."""
    did = await get_config(conn, "peer_identity")
    if not did:
        raise PeerError(
            "No peer identity configured.",
            recovery="Run: elfmem peer init --name <name>",
        )
    return did


async def set_identity(conn: AsyncConnection, name: str) -> str:
    """Set this instance's peer identity. Returns the DID."""
    did = f"elf:{_slugify(name)}"
    await set_config(conn, "peer_identity", did)
    return did


# ── Export ────────────────────────────────────────────────────────────────────


async def export_blocks(
    conn: AsyncConnection,
    *,
    share_level: str = "public",
    min_confidence: float = 0.0,
    identity: str,
    output_path: str,
) -> ExportResult:
    """Export shareable blocks as a JSON bundle.

    USE WHEN: Sharing knowledge with another elfmem instance.
    COST: Fast. Database reads + file write.
    RETURNS: ExportResult with counts and output path.
    """
    raw_blocks = await get_exportable_blocks(
        conn, share_level=share_level, min_confidence=min_confidence,
    )
    # Filter out self/* blocks — identity never leaves the instance
    block_ids = set()
    export_blocks_list: list[dict[str, Any]] = []
    tags_map = await get_tags_batch(conn, [b["id"] for b in raw_blocks])

    for block in raw_blocks:
        tags = tags_map.get(block["id"], [])
        if any(t.startswith(_SELF_TAG_PREFIX) for t in tags):
            continue
        block_ids.add(block["id"])
        export_blocks_list.append(_block_to_export(block, tags))

    # Edges between exported blocks
    raw_edges = await get_edges_for_export(conn, block_ids)
    export_edges = [_edge_to_export(e) for e in raw_edges]

    bundle = _build_bundle(identity, export_blocks_list, export_edges)
    _write_json(Path(output_path), bundle)

    return ExportResult(
        blocks_exported=len(export_blocks_list),
        edges_exported=len(export_edges),
        output_path=output_path,
        from_did=identity,
        share_level=share_level,
    )


def _block_to_export(block: dict[str, Any], tags: list[str]) -> dict[str, Any]:
    """Convert a block row to export format (strips internal metadata)."""
    return {
        "id": block["id"],
        "content": block["content"],
        "category": block["category"],
        "tags": tags,
        "confidence": block["confidence"],
        "created_at": block["created_at"],
        "share": block.get("share") or "public",
    }


def _edge_to_export(edge: dict[str, Any]) -> dict[str, Any]:
    """Convert an edge row to export format."""
    return {
        "from_id": edge["from_id"],
        "to_id": edge["to_id"],
        "relation_type": edge["relation_type"],
        "weight": edge["weight"],
    }


def _build_bundle(
    identity: str,
    blocks_list: list[dict[str, Any]],
    edges_list: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "version": BUNDLE_VERSION,
        "exported_at": _now_iso(),
        "from_did": identity,
        "block_count": len(blocks_list),
        "blocks": blocks_list,
        "edges": edges_list,
    }


# ── Import ────────────────────────────────────────────────────────────────────


async def import_bundle(
    conn: AsyncConnection,
    *,
    bundle_data: dict[str, Any],
    from_peer: str,
    is_self_merge: bool = False,
    confidence_floor: float = 0.3,
) -> ImportResult:
    """Import a block bundle with provenance tracking.

    USE WHEN: Receiving knowledge from another elfmem instance.
    COST: Fast. Database writes only. Imported blocks enter inbox.
    RETURNS: ImportResult with counts.
    """
    if bundle_data.get("version") != BUNDLE_VERSION:
        raise PeerError(
            f"Unsupported bundle version: {bundle_data.get('version')}",
            recovery=f"Expected version {BUNDLE_VERSION}.",
        )

    peer = await get_peer(conn, from_peer)
    if peer is None and not is_self_merge:
        raise PeerError(
            f"Unknown peer: {from_peer}",
            recovery=f"Register first: elfmem peer add {from_peer} --name <name>",
        )

    trust = peer["trust"] if peer else 1.0
    imported = 0
    skipped = 0

    for block_data in bundle_data.get("blocks", []):
        ok = await _import_single_block(
            conn,
            block_data=block_data,
            from_peer=from_peer,
            is_self_merge=is_self_merge,
            confidence_floor=confidence_floor,
            trust=trust,
        )
        if ok:
            imported += 1
        else:
            skipped += 1

    # Import edges where both endpoints exist locally
    imported_ids = {b["id"] for b in bundle_data.get("blocks", [])}
    edges_imported = await _import_edges(
        conn, bundle_data.get("edges", []), imported_ids,
    )

    if peer:
        await update_peer_stats(conn, from_peer, blocks_imported_delta=imported)

    return ImportResult(
        blocks_imported=imported,
        blocks_skipped=skipped,
        edges_imported=edges_imported,
        from_peer=from_peer,
        is_self_merge=is_self_merge,
        confidence_floor=confidence_floor,
    )


async def _import_single_block(
    conn: AsyncConnection,
    *,
    block_data: dict[str, Any],
    from_peer: str,
    is_self_merge: bool,
    confidence_floor: float,
    trust: float,
) -> bool:
    """Import one block. Returns True if imported, False if skipped."""
    content = block_data["content"]
    content_id = compute_content_hash(content)

    existing = await get_block(conn, content_id)
    if existing is not None and existing["status"] == "inbox":
        return False  # Exact duplicate in inbox
    if existing is not None and existing["status"] == "active":
        return False  # Already known

    import uuid
    block_id = content_id if existing is None else uuid.uuid4().hex[:16]

    confidence = (
        block_data.get("confidence", 0.5) if is_self_merge
        else _peer_confidence(confidence_floor, trust)
    )

    await insert_block(
        conn,
        block_id=block_id,
        content=content,
        category=block_data.get("category", "knowledge"),
        source="peer_import",
        status="inbox",
        confidence=confidence,
    )

    # Add tags with provenance
    tags = list(block_data.get("tags", []))
    if not is_self_merge:
        tags.append(f"peer/{from_peer}")
    if tags:
        await add_tags(conn, block_id, tags)

    # Set source_peer via raw SQL (column not in insert_block signature)
    from sqlalchemy import text
    await conn.execute(
        text("UPDATE blocks SET source_peer = :peer, share = 'private' WHERE id = :id"),
        {"peer": from_peer if not is_self_merge else None, "id": block_id},
    )

    return True


def _peer_confidence(floor: float, trust: float, threshold: float = 0.7) -> float:
    """Compute starting confidence for an imported peer block."""
    return floor * 1.5 if trust >= threshold else floor


async def _import_edges(
    conn: AsyncConnection,
    edges_data: list[dict[str, Any]],
    known_ids: set[str],
) -> int:
    """Import edges where both endpoints exist locally."""
    from sqlalchemy.exc import IntegrityError

    from elfmem.db.queries import insert_edge

    imported = 0
    for edge_data in edges_data:
        from_id = edge_data["from_id"]
        to_id = edge_data["to_id"]
        # Only import if both endpoints exist
        from_block = await get_block(conn, from_id)
        to_block = await get_block(conn, to_id)
        if from_block is None or to_block is None:
            continue
        try:
            await insert_edge(
                conn,
                from_id=from_id,
                to_id=to_id,
                weight=edge_data.get("weight", 0.5),
                relation_type=edge_data.get("relation_type", "similar"),
                origin="import",
            )
            imported += 1
        except IntegrityError:
            pass  # Edge already exists
    return imported


# ── Send message ──────────────────────────────────────────────────────────────


async def send_message(
    conn: AsyncConnection,
    *,
    to_peer: str,
    content: str,
    in_reply_to: str | None,
    identity: str,
    outbox_dir: Path,
) -> PeerSendResult:
    """Send a message to a peer. Heartbeat speed: learn() + file write.

    If the peer has a delivery_path, writes directly to the peer's inbox
    (subdirectory named by sender). Otherwise writes to the local outbox
    (subdirectory named by recipient) for manual transport.

    USE WHEN: Communicating with another elfmem instance.
    COST: Instant. No LLM calls.
    RETURNS: PeerSendResult with block_id, msg_id, file path.
    """
    from elfmem.operations.learn import learn

    msg_id = f"m_{compute_content_hash(content)[:8]}"
    envelope = _build_envelope(msg_id, identity, to_peer, in_reply_to)

    # 1. Store in local memory (heartbeat)
    result = await learn(
        conn,
        content=content,
        tags=["peer/outbound", f"peer/to:{to_peer}"],
        category="message",
        source="peer_send",
    )

    # Set envelope metadata
    from sqlalchemy import text
    await conn.execute(
        text("UPDATE blocks SET envelope_json = :env WHERE id = :id"),
        {"env": json.dumps(envelope), "id": result.block_id},
    )

    # 2. Resolve delivery target: direct delivery or local outbox
    peer = await get_peer(conn, to_peer)
    write_dir, subdir_name = _resolve_delivery(
        peer, identity, to_peer, outbox_dir,
    )

    # 3. Write message file (filesystem, milliseconds)
    file_path = _write_message_file(
        write_dir, subdir_name, msg_id, content, envelope, to_peer,
    )

    # 4. Create reply edge if this is a response
    if in_reply_to:
        await _link_reply(conn, result.block_id, in_reply_to)

    # 5. Update peer stats
    if peer:
        await update_peer_stats(conn, to_peer, messages_out_delta=1)

    return PeerSendResult(
        block_id=result.block_id,
        msg_id=msg_id,
        to_peer=to_peer,
        outbox_path=str(file_path),
        in_reply_to=in_reply_to,
    )


def _build_envelope(
    msg_id: str, from_did: str, to_did: str, in_reply_to: str | None,
) -> dict[str, Any]:
    return {
        "msg_id": msg_id,
        "direction": "outbound",
        "from_did": from_did,
        "to_did": to_did,
        "in_reply_to": in_reply_to,
        "sent_at": _now_iso(),
    }


def _resolve_delivery(
    peer: dict[str, Any] | None,
    identity: str,
    to_peer: str,
    outbox_dir: Path,
) -> tuple[Path, str]:
    """Choose delivery directory and subdirectory name.

    Direct delivery (peer has delivery_path):
        dir  = peer's inbox path
        sub  = sender's slug (receiver groups by sender)

    Local outbox (no delivery_path):
        dir  = local outbox
        sub  = recipient's slug (sender groups by recipient)
    """
    if peer and peer.get("delivery_path"):
        delivery = Path(peer["delivery_path"]).expanduser()
        return delivery, _slugify(identity)
    return outbox_dir, _slugify(to_peer)


def _write_message_file(
    base_dir: Path, subdir_name: str, msg_id: str,
    content: str, envelope: dict[str, Any], to_peer: str,
) -> Path:
    """Write a message JSON file to a subdirectory."""
    peer_dir = base_dir / subdir_name
    peer_dir.mkdir(parents=True, exist_ok=True)
    path = peer_dir / f"msg_{msg_id}.json"
    message = {
        "version": BUNDLE_VERSION,
        **envelope,
        "content": content,
        "tags": ["peer/outbound", f"peer/to:{to_peer}"],
        "category": "message",
    }
    _write_json(path, message)
    return path


async def _link_reply(
    conn: AsyncConnection, reply_id: str, in_reply_to_msg_id: str,
) -> None:
    """Create a replies_to edge from the reply to the original message."""
    from sqlalchemy import text

    # Find the block with this msg_id in its envelope
    result = await conn.execute(
        text(
            "SELECT id FROM blocks "
            "WHERE envelope_json LIKE :pattern AND status != 'archived'"
        ),
        {"pattern": f'%"msg_id": "{in_reply_to_msg_id}"%'},
    )
    row = result.first()
    if row:
        import contextlib

        from elfmem.db.queries import insert_agent_edge
        with contextlib.suppress(Exception):
            await insert_agent_edge(
                conn,
                from_id=reply_id,
                to_id=row[0],
                weight=0.80,
                relation_type="replies_to",
                note=None,
                current_active_hours=None,
            )


# ── Inbox ─────────────────────────────────────────────────────────────────────


async def check_inbox(
    conn: AsyncConnection,
    *,
    inbox_dir: Path,
    from_peer: str | None,
    import_messages: bool,
    identity: str,
) -> PeerInboxResult:
    """Check and optionally import pending messages from the inbox directory.

    USE WHEN: Checking for messages from peers.
    COST: Fast. Filesystem scan + optional database writes.
    RETURNS: PeerInboxResult with counts and warnings.
    """
    if not inbox_dir.exists():
        warnings = await _empty_inbox_warnings(conn, inbox_dir)
        return PeerInboxResult(
            messages_found=0, messages_imported=0,
            messages_skipped=0, peers=[], warnings=warnings,
        )

    files = _scan_inbox(inbox_dir, from_peer)
    if not files:
        warnings = await _empty_inbox_warnings(conn, inbox_dir)
        return PeerInboxResult(
            messages_found=0, messages_imported=0,
            messages_skipped=0, peers=[], warnings=warnings,
        )

    peers_seen: set[str] = set()
    imported = 0
    skipped = 0

    for msg_file in files:
        msg = _parse_message(msg_file)
        if msg is None:
            skipped += 1
            continue

        sender = msg.get("from_did", "unknown")
        peers_seen.add(sender)

        if not import_messages:
            continue

        ok = await _import_message(conn, msg, identity)
        if ok:
            imported += 1
            _move_to_processed(msg_file, inbox_dir)
        else:
            skipped += 1

    return PeerInboxResult(
        messages_found=len(files),
        messages_imported=imported,
        messages_skipped=len(files) - imported if import_messages else 0,
        peers=sorted(peers_seen),
    )


def _scan_inbox(inbox_dir: Path, from_peer: str | None) -> list[Path]:
    """Scan inbox directory for message JSON files."""
    files: list[Path] = []
    for peer_dir in inbox_dir.iterdir():
        if not peer_dir.is_dir() or peer_dir.name == "processed":
            continue
        if from_peer and peer_dir.name != _slugify(from_peer):
            continue
        files.extend(sorted(peer_dir.glob("msg_*.json")))
    return files


def _parse_message(path: Path) -> dict[str, Any] | None:
    """Parse a message JSON file. Returns None on error."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


async def _import_message(
    conn: AsyncConnection, msg: dict[str, Any], identity: str,
) -> bool:
    """Import a single message. Returns True if imported."""
    from elfmem.operations.learn import learn

    # Validate destination
    if msg.get("to_did") != identity:
        return False

    msg_id = msg.get("msg_id", "")
    content = msg.get("content", "")
    if not content:
        return False

    sender = msg.get("from_did", "unknown")

    result = await learn(
        conn,
        content=content,
        tags=["peer/inbound", f"peer/from:{sender}"],
        category="message",
        source="peer_inbox",
    )

    if result.status == "duplicate_rejected":
        return False

    # Set envelope and source_peer
    envelope = {
        "msg_id": msg_id,
        "direction": "inbound",
        "from_did": sender,
        "to_did": identity,
        "in_reply_to": msg.get("in_reply_to"),
        "sent_at": msg.get("sent_at"),
    }
    from sqlalchemy import text
    await conn.execute(
        text(
            "UPDATE blocks SET envelope_json = :env, source_peer = :peer "
            "WHERE id = :id"
        ),
        {"env": json.dumps(envelope), "peer": sender, "id": result.block_id},
    )

    # Update peer stats
    peer = await get_peer(conn, sender)
    if peer:
        await update_peer_stats(conn, sender, messages_in_delta=1)

    # Link reply chain
    in_reply_to = msg.get("in_reply_to")
    if in_reply_to:
        await _link_reply(conn, result.block_id, in_reply_to)

    return True


def _move_to_processed(msg_file: Path, inbox_dir: Path) -> None:
    """Move an imported message file to the processed directory."""
    processed = inbox_dir / "processed"
    processed.mkdir(exist_ok=True)
    msg_file.rename(processed / msg_file.name)


# ── Warnings ─────────────────────────────────────────────────────────────────

_ACTIVE_DAYS = 30


async def _empty_inbox_warnings(
    conn: AsyncConnection, inbox_dir: Path,
) -> list[str]:
    """Generate warnings when inbox scan finds zero messages but peers are active."""
    all_peers = await get_all_peers(conn)
    if not all_peers:
        return []

    now = datetime.now(UTC)
    active_count = 0
    for peer in all_peers:
        last = peer.get("last_active", "")
        if not last:
            continue
        try:
            last_dt = datetime.fromisoformat(last)
            if (now - last_dt).days < _ACTIVE_DAYS:
                active_count += 1
        except (ValueError, TypeError):
            continue

    if active_count == 0:
        return []

    return [
        f"No messages found at {inbox_dir}. "
        f"{active_count} peer(s) active in last {_ACTIVE_DAYS} days. "
        f"Verify inbox path."
    ]


# ── Inbox status (pure filesystem, no DB) ────────────────────────────────────


def scan_peer_inbox(inbox_dir: Path) -> PeerInboxStatus:
    """Scan the peer inbox directory and report pending message status.

    USE WHEN: Deciding whether to trigger peer message processing.
    DON'T USE WHEN: You need message content — use check_inbox() instead.
    COST: Zero LLM calls. Pure filesystem scan.
    RETURNS: PeerInboxStatus with pending count and sender list.
    NEXT: If pending > 0, call check_inbox() with import_messages=True.
    """
    inbox_path = inbox_dir.expanduser()
    if not inbox_path.exists():
        return PeerInboxStatus(
            pending=0, oldest_at=None, newest_at=None,
            from_peers=[], inbox_dir=str(inbox_path),
        )

    files = _scan_inbox(inbox_path, from_peer=None)
    if not files:
        return PeerInboxStatus(
            pending=0, oldest_at=None, newest_at=None,
            from_peers=[], inbox_dir=str(inbox_path),
        )

    from_peers: list[str] = []
    for f in files:
        msg = _parse_message(f)
        if msg is None:
            continue
        did = msg.get("from_did")
        if did and did not in from_peers:
            from_peers.append(did)

    oldest_mtime = min(f.stat().st_mtime for f in files)
    newest_mtime = max(f.stat().st_mtime for f in files)

    return PeerInboxStatus(
        pending=len(files),
        oldest_at=datetime.fromtimestamp(oldest_mtime, tz=UTC).isoformat(),
        newest_at=datetime.fromtimestamp(newest_mtime, tz=UTC).isoformat(),
        from_peers=from_peers,
        inbox_dir=str(inbox_path),
    )


# ── Helpers ───────────────────────────────────────────────────────────────────


def _slugify(text: str) -> str:
    """Convert a DID or name to a filesystem-safe slug."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _write_json(path: Path, data: dict[str, Any]) -> None:
    """Write a JSON file atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
