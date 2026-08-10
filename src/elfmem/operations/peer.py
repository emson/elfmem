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
import logging
import os
import re
import time
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
    insert_peer,
    set_config,
    update_block_scoring,
    update_peer_stats,
)
from elfmem.exceptions import PeerError
from elfmem.memory.blockfile import Block, parse_blocks, write_blocks
from elfmem.memory.blocks import compute_content_hash
from elfmem.ports.services import EmbeddingService
from elfmem.types import (
    ExportResult,
    ImportResult,
    PeerInboxResult,
    PeerInboxStatus,
    PeerSendResult,
)

logger = logging.getLogger(__name__)

BUNDLE_VERSION = 2
# v1 bundles (from v0.15 / v0.16 peers) are still readable — they ship only
# `confidence`, and the importer bootstraps (α, β) from it via the trust-scaled
# Jeffreys seed in ``merge_peer_evidence`` (same code path as v2).
_MIN_READABLE_BUNDLE_VERSION = 1

_SELF_TAG_PREFIX = "self/"

# Jeffreys prior on a freshly seeded peer block — uniform-on-log-odds; matches
# the default in db.models.blocks. See ADR 0002 / plan_memory_scoring.
_PEER_PRIOR_ALPHA = 0.5
_PEER_PRIOR_BETA = 0.5


def merge_peer_evidence(
    local_alpha: float,
    local_beta: float,
    remote_alpha: float,
    remote_beta: float,
    trust: float,
) -> tuple[float, float]:
    """Trust-weighted arithmetic merge of two Beta-Binomial observations.

    USE WHEN: A peer has sent us evidence (α, β) about a block, and we
        either don't have a local copy yet (start from the Jeffreys prior
        passed as ``local_alpha``/``local_beta``) or already do (pass the
        current local sufficient statistics).
    DON'T USE WHEN: You only have ``confidence`` — bootstrap (α, β) from
        it first via ``α = confidence``, ``β = 1 - confidence`` (the v1
        bundle path does this in ``_import_single_block``).
    COST: Pure arithmetic, O(1), no I/O.
    RETURNS: ``(new_alpha, new_beta)``. Confidence is the denormalised view
        ``α / (α + β)`` — let the writer derive it inside the same UPDATE.
    NEXT: Persist with ``update_block_outcome`` (existing block) or
        ``insert_block`` with explicit success/failure counts (fresh).
    """
    return (
        local_alpha + remote_alpha * trust,
        local_beta + remote_beta * trust,
    )


def _peer_remote_priors(block_data: dict[str, Any]) -> tuple[float, float]:
    """Resolve (remote_alpha, remote_beta) from a v1 OR v2 block payload.

    v2 sends both ``success_count`` and ``failure_count`` directly.
    v1 sends only ``confidence``; we bootstrap with total mass 1.0
    (α=confidence, β=1-confidence) — the same convention ``insert_block``
    uses for raw-inserted blocks since v0.17.
    """
    alpha = block_data.get("success_count")
    beta = block_data.get("failure_count")
    if alpha is not None and beta is not None:
        return float(alpha), float(beta)
    confidence = float(block_data.get("confidence", 0.5))
    return confidence, 1.0 - confidence


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
    """Convert a block row to export format (strips internal metadata).

    v0.17 bundles (BUNDLE_VERSION=2) ship the Beta sufficient statistics
    ``success_count`` and ``failure_count`` alongside ``confidence``. Older
    importers (v0.15/v0.16, BUNDLE_VERSION=1) ignore the extra fields and
    bootstrap (α, β) from ``confidence`` — so v2 bundles remain readable
    by older peers.
    """
    return {
        "id": block["id"],
        "content": block["content"],
        "category": block["category"],
        "tags": tags,
        "confidence": block["confidence"],
        "success_count": block.get("success_count"),
        "failure_count": block.get("failure_count"),
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
    confidence_floor: float = 0.3,  # DEPRECATED in v0.17 — see ImportResult
) -> ImportResult:
    """Import a block bundle with provenance tracking.

    USE WHEN: Receiving knowledge from another elfmem instance.
    COST: Fast. Database writes only. Imported blocks enter inbox.
    RETURNS: ImportResult with counts.

    Note (v0.17): ``confidence_floor`` is retained on the signature and on
    ``ImportResult`` for one release of backward compatibility but is no
    longer consulted — peer evidence is folded in arithmetically via
    ``merge_peer_evidence`` (trust-scaled). See ADR 0002.
    """
    version = bundle_data.get("version")
    if (
        not isinstance(version, int)
        or version < _MIN_READABLE_BUNDLE_VERSION
        or version > BUNDLE_VERSION
    ):
        raise PeerError(
            f"Unsupported bundle version: {version}",
            recovery=(
                f"Expected version in [{_MIN_READABLE_BUNDLE_VERSION}, "
                f"{BUNDLE_VERSION}]."
            ),
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
    trust: float,
) -> bool:
    """Import or merge one peer block. Returns True on import, False on skip.

    Two paths (v0.17, ADR 0002):

    1. **Fresh import** — no local copy of this content. Seed (α, β) on top
       of the Jeffreys prior, weighted by trust:
           α = 0.5 + remote_α * trust
           β = 0.5 + remote_β * trust
       trust=0.0 ⇒ pure Jeffreys prior (peer entirely discarded).
       trust=1.0 ⇒ full remote evidence accepted.

    2. **Merge into existing** — content already known locally. Fold the
       remote evidence in arithmetically (no second Jeffreys prior — that
       is paid once at first import):
           α' = local_α + remote_α * trust
           β' = local_β + remote_β * trust
       The local block_id and status are preserved; tags are appended.

    Self-merge (``is_self_merge=True``) bypasses trust-scaling and folds
    the remote evidence directly — the peer is a previous instance of this
    same agent.
    """
    content = block_data["content"]
    content_id = compute_content_hash(content)
    remote_alpha, remote_beta = _peer_remote_priors(block_data)
    weight = 1.0 if is_self_merge else trust

    existing = await get_block(conn, content_id)
    if existing is None:
        await _seed_fresh_peer_block(
            conn,
            block_id=content_id,
            content=content,
            category=block_data.get("category", "knowledge"),
            tags=list(block_data.get("tags", [])),
            from_peer=from_peer,
            is_self_merge=is_self_merge,
            remote_alpha=remote_alpha,
            remote_beta=remote_beta,
            weight=weight,
        )
        return True

    await _merge_into_existing_block(
        conn,
        block=existing,
        remote_alpha=remote_alpha,
        remote_beta=remote_beta,
        weight=weight,
        from_peer=from_peer,
        extra_tags=list(block_data.get("tags", [])),
        is_self_merge=is_self_merge,
    )
    return True


async def _seed_fresh_peer_block(
    conn: AsyncConnection,
    *,
    block_id: str,
    content: str,
    category: str,
    tags: list[str],
    from_peer: str,
    is_self_merge: bool,
    remote_alpha: float,
    remote_beta: float,
    weight: float,
) -> None:
    """Insert a brand-new peer-sourced block at Jeffreys + trust × remote."""
    new_alpha, new_beta = merge_peer_evidence(
        _PEER_PRIOR_ALPHA, _PEER_PRIOR_BETA,
        remote_alpha, remote_beta, weight,
    )
    confidence = new_alpha / (new_alpha + new_beta)

    await insert_block(
        conn,
        block_id=block_id,
        content=content,
        category=category,
        source="peer_import",
        status="inbox",
        confidence=confidence,
        success_count=new_alpha,
        failure_count=new_beta,
    )

    if not is_self_merge:
        tags = [*tags, f"peer/{from_peer}"]
    if tags:
        await add_tags(conn, block_id, tags)

    from sqlalchemy import text
    await conn.execute(
        text(
            "UPDATE blocks SET source_peer = :peer, share = 'private' "
            "WHERE id = :id"
        ),
        {"peer": from_peer if not is_self_merge else None, "id": block_id},
    )


async def _merge_into_existing_block(
    conn: AsyncConnection,
    *,
    block: dict[str, Any],
    remote_alpha: float,
    remote_beta: float,
    weight: float,
    from_peer: str,
    extra_tags: list[str],
    is_self_merge: bool,
) -> None:
    """Fold remote evidence into an already-known local block (additive)."""
    from elfmem.db.queries import update_block_outcome

    local_alpha = float(block.get("success_count") or _PEER_PRIOR_ALPHA)
    local_beta = float(block.get("failure_count") or _PEER_PRIOR_BETA)
    new_alpha, new_beta = merge_peer_evidence(
        local_alpha, local_beta, remote_alpha, remote_beta, weight,
    )
    await update_block_outcome(
        conn,
        block_id=block["id"],
        new_success_count=new_alpha,
        new_failure_count=new_beta,
    )

    # Provenance: every peer that contributes evidence gets a tag (idempotent
    # via UNIQUE constraint in block_tags).
    if not is_self_merge:
        extra_tags = [*extra_tags, f"peer/{from_peer}"]
    if extra_tags:
        await add_tags(conn, block["id"], extra_tags)


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


# ── Config → roster sync ─────────────────────────────────────────────────────


async def sync_peers_from_config(
    conn: AsyncConnection, peers: list[Any],
) -> int:
    """Insert any config-declared peers that are not yet in ``peer_roster``.

    USE WHEN: Engine startup, after schema migrations. Idempotent.
    DON'T USE WHEN: You want to overwrite trust/delivery_path on an existing
        peer — this function is insert-only by design so operational state
        (trust adjustments, counters) is preserved across restarts.
    COST: One ``get_peer`` + at most one ``insert_peer`` per config entry.
    RETURNS: Number of peers newly inserted.
    NEXT: ``peer_list()`` will now surface every declared peer.
    """
    inserted = 0
    for spec in peers:
        # PeerSpec.did is filled by config-time validator; never None here.
        existing = await get_peer(conn, spec.did)
        if existing is not None:
            continue
        await insert_peer(
            conn,
            did=spec.did,
            name=spec.name,
            is_self=False,
            delivery_path=spec.delivery_path,
        )
        # Apply config-declared trust on first insert. After that, the value
        # belongs to operational state and is mutated only via peer_trust.
        if spec.trust != 0.0:
            from elfmem.db.queries import update_peer_trust
            await update_peer_trust(conn, spec.did, spec.trust)
        inserted += 1
    return inserted


# ── Legacy slug migration ────────────────────────────────────────────────────


async def migrate_legacy_outbox_slugs(
    conn: AsyncConnection, outbox_dir: Path,
) -> dict[str, str]:
    """One-shot rename of pre-canonical outbox folders to the DID-slug form.

    Before the slug-canonicalisation fix, ``_resolve_delivery`` derived the
    outbox subdirectory from whatever string the caller passed to
    ``peer_send`` — so a message addressed to display-name ``"Alv"`` landed
    in ``outbox/alv/`` while a message to ``"elf:alv"`` landed in
    ``outbox/elf-alv/``. The canonical form (DID slug) is now the only path
    written; this function cleans up any legacy folders left behind.

    USE WHEN: Engine startup, after ``sync_peers_from_config``.
    COST: One ``iterdir`` over ``outbox_dir`` (skipped if absent).
    RETURNS: Mapping ``{legacy_slug: canonical_slug}`` of renames performed.
        Empty when no drift is detected.

    Safety: refuses to rename when the canonical destination already exists.
    The caller (operator) must reconcile manually — silent merge would risk
    losing audit history.
    """
    if not outbox_dir.exists():
        return {}
    renamed: dict[str, str] = {}
    for peer in await get_all_peers(conn):
        did = peer.get("did")
        name = peer.get("name")
        if not did or not name:
            continue
        did_slug = _slugify(did)
        name_slug = _slugify(name)
        if not name_slug or name_slug == did_slug:
            continue
        legacy = outbox_dir / name_slug
        canonical = outbox_dir / did_slug
        if legacy.exists() and legacy.is_dir() and not canonical.exists():
            legacy.rename(canonical)
            renamed[name_slug] = did_slug
    return renamed


# ── Canonical recipient resolution ───────────────────────────────────────────


async def canonical_did(conn: AsyncConnection, to_peer: str) -> str:
    """Resolve a ``to_peer`` argument (DID or display name) to a canonical DID.

    Rules:
    - Contains ``:`` → treated as DID, returned verbatim (lowercased).
    - No ``:`` → matched against ``peer_roster.name`` (case-insensitive).
      First match wins. No match → derive ``elf:<slug>`` from the name.

    Pure resolution; does not mutate state.
    """
    if ":" in to_peer:
        return to_peer.lower()
    rows = await get_all_peers(conn)
    target = to_peer.lower()
    for row in rows:
        if (row.get("name") or "").lower() == target:
            return str(row["did"])
    return f"elf:{_slugify(to_peer)}"


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

    # Canonicalise the recipient identifier up-front so every downstream step
    # (envelope, tag, outbox slug, stats update) uses the same DID. Resolves
    # the historical 'outbox/alv/' vs 'inbox/elf-alv/' inconsistency: callers
    # that pass a display name are normalised to the registered DID before
    # slug derivation. See ADR/peer-protocol refactor.
    to_did = await canonical_did(conn, to_peer)

    msg_id = f"m_{compute_content_hash(content)[:8]}"
    envelope = _build_envelope(msg_id, identity, to_did, in_reply_to)

    # 1. Store in local memory (heartbeat)
    result = await learn(
        conn,
        content=content,
        tags=["peer/outbound", f"peer/to:{to_did}"],
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
    peer = await get_peer(conn, to_did)
    write_dir, subdir_name = _resolve_delivery(
        peer, identity, to_did, outbox_dir,
    )

    # 3. Write message file (filesystem, milliseconds)
    file_path = _write_message_file(
        write_dir, subdir_name, msg_id, content, envelope, to_did,
    )

    # 4. Create reply edge if this is a response
    if in_reply_to:
        await _link_reply(conn, result.block_id, in_reply_to)

    # 5. Update peer stats
    if peer:
        await update_peer_stats(conn, to_did, messages_out_delta=1)

    return PeerSendResult(
        block_id=result.block_id,
        msg_id=msg_id,
        to_peer=to_did,
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


def _verify_recipient_initialized(delivery_path: Path) -> None:
    """Refuse to drop a message into a directory that is not an elfmem project.

    An elfmem project is identified by ``<project_root>/.elfmem/config.yaml``.
    ``delivery_path`` is the recipient's inbox dir
    (``<project_root>/.elfmem/inbox``), so the config sits one level up.

    Raises ``PeerError`` with an actionable ``.recovery`` when the marker is
    missing — silent black-hole sends are worse than a clear failure.
    """
    config_path = delivery_path.parent / "config.yaml"
    if config_path.exists():
        return
    project_root = delivery_path.parent.parent
    raise PeerError(
        f"Recipient is not an initialised elfmem instance: "
        f"missing {config_path}.",
        recovery=(
            f"Verify {project_root} is mounted and reachable, then run "
            f"'elfmem init' there before retrying."
        ),
    )


def _resolve_delivery(
    peer: dict[str, Any] | None,
    identity: str,
    to_did: str,
    outbox_dir: Path,
) -> tuple[Path, str]:
    """Choose delivery directory and subdirectory name.

    ``to_did`` is the canonical recipient DID (see ``canonical_did``).
    Slug derivation uses this DID directly so the same recipient always
    lands in the same folder regardless of the caller's input form.

    Direct delivery (peer has delivery_path):
        dir  = peer's inbox path
        sub  = sender's slug (receiver groups by sender)

    Local outbox (no delivery_path):
        dir  = local outbox
        sub  = recipient's slug (sender groups by recipient)
    """
    if peer and peer.get("delivery_path"):
        delivery = Path(peer["delivery_path"]).expanduser()
        _verify_recipient_initialized(delivery)
        return delivery, _slugify(identity)
    return outbox_dir, _slugify(to_did)


def _write_message_file(
    base_dir: Path, subdir_name: str, msg_id: str,
    content: str, envelope: dict[str, Any], to_peer: str,
) -> Path:
    """Write a message JSON file to a subdirectory atomically.

    Atomicity: writes are staged through a dotfile (``.foo.json.tmp``) and
    promoted via ``os.rename`` — readers globbing ``msg_*.json`` never see
    a partial file. Idempotent: if the destination already exists (same
    msg_id ⇒ same content), the write is skipped and the existing path
    returned. This makes retried sends safe under the existing
    content-hash-based msg_id scheme.
    """
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
    _write_envelope_atomic(path, message)
    return path


def _write_envelope_atomic(path: Path, data: dict[str, Any]) -> bool:
    """Write ``data`` to ``path`` via temp-file + os.rename. Idempotent.

    Returns True on write, False when ``path`` already exists. The temp
    file is a dotfile (excluded by ``msg_*.json`` glob) so concurrent
    scanners never observe a half-written envelope.

    Duplicate-skip path logs ``peer.envelope.duplicate_skipped`` with the
    existing file's age. Short age (seconds) ⇒ retry-class dedup (correct
    behaviour). Long age (hours) ⇒ legitimate repeat-content collision —
    the signal that would trigger reopening phase 5 of ADR 0005
    (time-bucketed ``msg_id``). No counter is persisted; ``grep`` on age
    distribution is the analysis path.
    """
    if path.exists():
        age_seconds = time.time() - path.stat().st_mtime
        logger.info(
            "peer.envelope.duplicate_skipped path=%s age=%.1fs",
            path, age_seconds,
        )
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.tmp"
    tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    os.rename(tmp, path)
    return True


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
        # Distinguish: .elfmem/ absent (setup never run) vs inbox/ absent (normal).
        elfmem_dir = inbox_path.parent
        if not elfmem_dir.exists():
            return PeerInboxStatus(
                pending=0, oldest_at=None, newest_at=None,
                from_peers=[], inbox_dir=str(inbox_path),
                warning=(
                    f"elfmem not initialised at {elfmem_dir.parent} — "
                    f"run 'elfmem setup' to enable peer messaging"
                ),
            )
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
    """Write a JSON file (overwrite semantics).

    Used for export bundles where the caller expects a fresh-on-each-call
    artefact. Peer messages go through ``_write_envelope_atomic`` instead,
    which adds dotfile-staging and idempotent skip.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


# ── File-native peer landing (v2 step 8, U-012) ────────────────────────────────
#
# Additive, not a replacement: import_bundle()/_build_bundle() above are
# unchanged and still live until U-007 switches MemorySystem's callers over
# (see build-plan.md's revision note on this unit). These two functions are
# the new append-only-log landing path and its rebuild-time reconciliation,
# resolving model.md's peer-bundle defect (D-002) and the confirmed
# content-hash double-count bug in _import_single_block above.


async def land_peer_log_entry(
    memory_dir: Path,
    *,
    content: str,
    tags: list[str],
    from_peer: str,
    msg_id: str,
    remote_alpha: float | None = None,
    remote_beta: float | None = None,
) -> None:
    """Land one received peer message as its own file. Never mutates an
    existing file — one message, one file, named by ``msg_id`` — for two
    reasons at once: (1) fully append-only under concurrent peer writes, no
    file is ever opened for read-modify-write; (2) sidesteps U-001's
    per-file duplicate-``id:`` invariant, which is correct and desirable for
    ordinary notes/log content but would wrongly reject two distinct peer
    messages that happen to carry identical content (the exact
    "distinct-messages-same-content" scenario this unit exists to fix) if
    they landed in one shared file. ``fold_peer_log`` deduplicates by
    ``msg_id`` and reconciles at rebuild time, not here (Invariant 6).

    USE WHEN: a peer bundle message has been received and accepted (after
        the existing bundle-version and peer-registration checks).
    DON'T USE WHEN: writing the merged, final block — that's
        ``fold_peer_log``'s job, run later, once, at rebuild time.
    COST: one file write. No LLM, no embedding call — those happen once per
        distinct fact at fold time, not once per received message.
    RETURNS: None.
    NEXT: ``fold_peer_log`` (registered as an `index_rebuild.py`
        `additional_fold_steps` entry) folds this into the derived index.
    """
    peer_dir = memory_dir / "log" / "peer"
    peer_dir.mkdir(parents=True, exist_ok=True)
    entry_path = peer_dir / f"{_slugify(from_peer)}-{_slugify(msg_id)}.md"

    extra: dict[str, str] = {"source_peer": from_peer, "msg_id": msg_id}
    if remote_alpha is not None:
        extra["remote_alpha"] = f"{remote_alpha:.4f}"
    if remote_beta is not None:
        extra["remote_beta"] = f"{remote_beta:.4f}"

    stripped = content.strip()
    first_line = stripped.splitlines()[0] if stripped else "Untitled"
    entry_path.write_text(
        write_blocks(
            [
                Block(
                    title=first_line[:60],
                    content=content,
                    id=compute_content_hash(content)[:16],
                    tags=tags,
                    extra=extra,
                )
            ]
        ),
        encoding="utf-8",
    )


async def fold_peer_log(
    conn: AsyncConnection,
    embedding_service: EmbeddingService,
    embedding_model: str,
    *,
    memory_dir: Path,
) -> int:
    """Rebuild-time reconciliation of every peer log entry into final blocks.

    Two-stage dedup, matching the ``/simulate`` resolution in model.md:
    (1) dedup by ``msg_id`` — an exact resend of the same message must not
    double-count; (2) group the deduplicated entries by their content-hash
    ``id`` — distinct messages about the same fact are genuine new evidence
    and *do* accumulate, via the existing trust-weighted
    ``merge_peer_evidence`` (unchanged math, just triggered here instead of
    at import time).

    USE WHEN: registered as one of `rebuild_index`'s `additional_fold_steps`
        (bind `memory_dir` via `functools.partial` at the call site — not
        built yet, belongs to whatever CLI command invokes a full rebuild).
    DON'T USE WHEN: importing a single message — that's `land_peer_log_entry`.
    COST: one embedding call per **distinct fact** (not per message received)
        — the same amortisation `elfmem index` already gives local content.
    RETURNS: number of blocks written.
    NEXT: nothing further — the fold is terminal for this rebuild cycle.
    """
    peer_dir = memory_dir / "log" / "peer"
    if not peer_dir.is_dir():
        return 0

    entries: list[Block] = []
    for path in sorted(peer_dir.glob("*.md")):
        for block in parse_blocks(path.read_text(encoding="utf-8")).blocks:
            if "source_peer" in block.extra:
                entries.append(block)

    seen_msg_ids: set[str] = set()
    deduped: list[Block] = []
    for block in entries:
        msg_id = block.extra.get("msg_id")
        if msg_id is not None:
            if msg_id in seen_msg_ids:
                continue
            seen_msg_ids.add(msg_id)
        deduped.append(block)

    by_id: dict[str, list[Block]] = {}
    for block in deduped:
        assert block.id is not None  # land_peer_log_entry always assigns one
        by_id.setdefault(block.id, []).append(block)

    written = 0
    for block_id, group in by_id.items():
        from_peer = group[0].extra["source_peer"]
        peer = await get_peer(conn, from_peer)
        trust = peer["trust"] if peer else 1.0

        alpha, beta = _PEER_PRIOR_ALPHA, _PEER_PRIOR_BETA
        for block in group:
            remote_alpha = float(block.extra.get("remote_alpha", "0.5"))
            remote_beta = float(block.extra.get("remote_beta", "0.5"))
            alpha, beta = merge_peer_evidence(alpha, beta, remote_alpha, remote_beta, trust)
        confidence = alpha / (alpha + beta)

        latest = group[-1]
        await insert_block(
            conn,
            block_id=block_id,
            content=latest.content,
            category="knowledge",
            source="peer_import",
            status="inbox",
            confidence=confidence,
            success_count=alpha,
            failure_count=beta,
        )
        if latest.tags:
            await add_tags(conn, block_id, latest.tags)
        vec = await embedding_service.embed(latest.content.strip().lower())
        await update_block_scoring(
            conn,
            block_id,
            embedding=vec,
            embedding_model=embedding_service.model_name,
        )
        written += 1

    return written
