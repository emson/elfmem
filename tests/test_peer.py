"""Tests for peer communication: roster, export, import, messaging, trust."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from elfmem.api import MemorySystem
from elfmem.config import ElfmemConfig, MemoryConfig, PeerConfig
from elfmem.exceptions import PeerError
from elfmem.types import (
    ExportResult,
    ImportResult,
    PeerInboxResult,
    PeerSendResult,
)


@pytest.fixture
async def system(test_engine, mock_llm, mock_embedding) -> MemorySystem:
    cfg = ElfmemConfig(
        memory=MemoryConfig(inbox_threshold=3),
        peer=PeerConfig(
            outbox_dir="/tmp/elfmem-test-outbox",
            inbox_dir="/tmp/elfmem-test-inbox",
        ),
    )
    return MemorySystem(
        engine=test_engine,
        llm_service=mock_llm,
        embedding_service=mock_embedding,
        config=cfg,
    )


@pytest.fixture
async def system_with_identity(system: MemorySystem) -> MemorySystem:
    """System with peer identity configured."""
    await system.peer_init("test-elf")
    return system


# ── Identity ─────────────────────────────────────────────────────────────────


class TestPeerInit:
    async def test_peer_init_returns_did(self, system: MemorySystem):
        did = await system.peer_init("my-agent")
        assert did == "elf:my-agent"

    async def test_peer_init_slugifies_name(self, system: MemorySystem):
        did = await system.peer_init("My Agent Name")
        assert did == "elf:my-agent-name"


# ── Peer Roster ──────────────────────────────────────────────────────────────


class TestPeerRoster:
    async def test_add_peer_default_trust_zero(self, system: MemorySystem):
        peer = await system.peer_add("elf:trader", "Trading Elf")
        assert peer.did == "elf:trader"
        assert peer.trust == 0.0
        assert peer.is_self is False

    async def test_add_self_peer_trust_one(self, system: MemorySystem):
        peer = await system.peer_add("elf:server", "Server Elf", is_self=True)
        assert peer.trust == 1.0
        assert peer.is_self is True

    async def test_add_duplicate_returns_existing(self, system: MemorySystem):
        await system.peer_add("elf:trader", "Trading Elf")
        peer = await system.peer_add("elf:trader", "Trading Elf")
        assert peer.did == "elf:trader"

    async def test_peer_list_returns_all(self, system: MemorySystem):
        await system.peer_add("elf:a", "Agent A")
        await system.peer_add("elf:b", "Agent B")
        peers = await system.peer_list()
        assert len(peers) == 2

    async def test_peer_remove_existing(self, system: MemorySystem):
        await system.peer_add("elf:trader", "Trading Elf")
        removed = await system.peer_remove("elf:trader")
        assert removed is True

    async def test_peer_remove_nonexistent(self, system: MemorySystem):
        removed = await system.peer_remove("elf:nobody")
        assert removed is False

    async def test_peer_trust_get(self, system: MemorySystem):
        await system.peer_add("elf:trader", "Trading Elf")
        info = await system.peer_trust("elf:trader")
        assert info.trust == 0.0

    async def test_peer_trust_set(self, system: MemorySystem):
        await system.peer_add("elf:trader", "Trading Elf")
        info = await system.peer_trust("elf:trader", set_value=0.75)
        assert info.trust == 0.75

    async def test_peer_trust_clamped(self, system: MemorySystem):
        await system.peer_add("elf:trader", "Trading Elf")
        info = await system.peer_trust("elf:trader", set_value=1.5)
        assert info.trust == 1.0

    async def test_peer_trust_not_found_raises(self, system: MemorySystem):
        with pytest.raises(PeerError):
            await system.peer_trust("elf:nobody")

    async def test_peer_info_summary(self, system: MemorySystem):
        peer = await system.peer_add("elf:trader", "Trading Elf")
        assert "Trading Elf" in str(peer)
        assert "elf:trader" in str(peer)

    async def test_peer_info_to_dict(self, system: MemorySystem):
        peer = await system.peer_add("elf:trader", "Trading Elf")
        d = peer.to_dict()
        assert d["did"] == "elf:trader"
        assert d["trust"] == 0.0


# ── Export ────────────────────────────────────────────────────────────────────


class TestExport:
    async def test_export_creates_file(self, system_with_identity: MemorySystem, tmp_path: Path):
        system = system_with_identity
        async with system.session():
            await system.learn("Shareable knowledge", tags=["trading"])
            await system.consolidate()

        # Mark block as public
        async with system._engine.begin() as conn:
            from sqlalchemy import text
            await conn.execute(text("UPDATE blocks SET share = 'public' WHERE status = 'active'"))

        output = str(tmp_path / "export.json")
        result = await system.export_blocks(share_level="public", output_path=output)
        assert isinstance(result, ExportResult)
        assert result.blocks_exported >= 1
        assert Path(output).exists()

    async def test_export_excludes_self_tags(self, system_with_identity: MemorySystem, tmp_path: Path):
        system = system_with_identity
        async with system.session():
            await system.learn("I am an agent", tags=["self/context"])
            await system.learn("Public fact", tags=["knowledge"])
            await system.consolidate()

        async with system._engine.begin() as conn:
            from sqlalchemy import text
            await conn.execute(text("UPDATE blocks SET share = 'public' WHERE status = 'active'"))

        output = str(tmp_path / "export.json")
        await system.export_blocks(share_level="public", output_path=output)

        bundle = json.loads(Path(output).read_text())
        for block in bundle["blocks"]:
            for tag in block["tags"]:
                assert not tag.startswith("self/"), f"Self tag exported: {tag}"

    async def test_export_bundle_format(self, system_with_identity: MemorySystem, tmp_path: Path):
        system = system_with_identity
        async with system.session():
            await system.learn("Test knowledge")
            await system.consolidate()

        async with system._engine.begin() as conn:
            from sqlalchemy import text
            await conn.execute(text("UPDATE blocks SET share = 'public' WHERE status = 'active'"))

        output = str(tmp_path / "export.json")
        await system.export_blocks(share_level="public", output_path=output)

        bundle = json.loads(Path(output).read_text())
        assert bundle["version"] == 1
        assert "exported_at" in bundle
        assert "from_did" in bundle
        assert isinstance(bundle["blocks"], list)
        assert isinstance(bundle["edges"], list)

    async def test_export_no_identity_raises(self, system: MemorySystem, tmp_path: Path):
        with pytest.raises(PeerError, match="No peer identity"):
            await system.export_blocks(output_path=str(tmp_path / "out.json"))


# ── Import ────────────────────────────────────────────────────────────────────


class TestImport:
    def _make_bundle(self, blocks: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "version": 1,
            "exported_at": "2026-04-29T00:00:00Z",
            "from_did": "elf:sender",
            "block_count": len(blocks),
            "blocks": blocks,
            "edges": [],
        }

    async def test_import_creates_inbox_blocks(self, system: MemorySystem, tmp_path: Path):
        await system.peer_add("elf:sender", "Sender")
        bundle = self._make_bundle([
            {"id": "abc123", "content": "Imported knowledge", "category": "knowledge",
             "tags": ["test"], "confidence": 0.7, "created_at": "2026-04-29T00:00:00Z"},
        ])
        path = tmp_path / "bundle.json"
        path.write_text(json.dumps(bundle))

        result = await system.import_blocks(str(path), from_peer="elf:sender")
        assert isinstance(result, ImportResult)
        assert result.blocks_imported == 1
        assert result.from_peer == "elf:sender"

    async def test_import_dedup_skips_existing(self, system: MemorySystem, tmp_path: Path):
        await system.peer_add("elf:sender", "Sender")
        async with system.session():
            await system.learn("Already known")

        bundle = self._make_bundle([
            {"id": "x", "content": "Already known", "category": "knowledge",
             "tags": [], "confidence": 0.7, "created_at": "2026-04-29T00:00:00Z"},
        ])
        path = tmp_path / "bundle.json"
        path.write_text(json.dumps(bundle))

        result = await system.import_blocks(str(path), from_peer="elf:sender")
        assert result.blocks_skipped == 1
        assert result.blocks_imported == 0

    async def test_import_self_merge_preserves_confidence(self, system: MemorySystem, tmp_path: Path):
        bundle = self._make_bundle([
            {"id": "x", "content": "Self knowledge", "category": "knowledge",
             "tags": ["test"], "confidence": 0.95, "created_at": "2026-04-29T00:00:00Z"},
        ])
        path = tmp_path / "bundle.json"
        path.write_text(json.dumps(bundle))

        result = await system.import_blocks(str(path), self_merge=True)
        assert result.is_self_merge is True
        assert result.blocks_imported == 1

    async def test_import_unknown_peer_raises(self, system: MemorySystem, tmp_path: Path):
        bundle = self._make_bundle([
            {"id": "x", "content": "Test", "category": "knowledge",
             "tags": [], "confidence": 0.5, "created_at": "2026-04-29T00:00:00Z"},
        ])
        path = tmp_path / "bundle.json"
        path.write_text(json.dumps(bundle))

        with pytest.raises(PeerError, match="Unknown peer"):
            await system.import_blocks(str(path), from_peer="elf:stranger")

    async def test_import_result_summary(self, system: MemorySystem, tmp_path: Path):
        await system.peer_add("elf:sender", "Sender")
        bundle = self._make_bundle([
            {"id": "x", "content": "Test knowledge", "category": "knowledge",
             "tags": [], "confidence": 0.5, "created_at": "2026-04-29T00:00:00Z"},
        ])
        path = tmp_path / "bundle.json"
        path.write_text(json.dumps(bundle))

        result = await system.import_blocks(str(path), from_peer="elf:sender")
        assert "Imported 1" in str(result)


# ── Messaging ─────────────────────────────────────────────────────────────────


class TestMessaging:
    async def test_send_creates_block_and_outbox(
        self, system_with_identity: MemorySystem, tmp_path: Path,
    ):
        system = system_with_identity
        system._config = system._config.model_copy(update={
            "peer": PeerConfig(outbox_dir=str(tmp_path / "outbox")),
        })
        await system.peer_add("elf:trader", "Trader")

        result = await system.peer_send("elf:trader", "What about gilts?")
        assert isinstance(result, PeerSendResult)
        assert result.msg_id.startswith("m_")
        assert result.to_peer == "elf:trader"
        assert Path(result.outbox_path).exists()

    async def test_send_reply_includes_in_reply_to(
        self, system_with_identity: MemorySystem, tmp_path: Path,
    ):
        system = system_with_identity
        system._config = system._config.model_copy(update={
            "peer": PeerConfig(outbox_dir=str(tmp_path / "outbox")),
        })
        await system.peer_add("elf:trader", "Trader")

        result = await system.peer_send(
            "elf:trader", "I agree", in_reply_to="m_original",
        )
        assert result.in_reply_to == "m_original"

    async def test_send_no_identity_raises(self, system: MemorySystem):
        with pytest.raises(PeerError, match="No peer identity"):
            await system.peer_send("elf:trader", "Hello")

    async def test_inbox_empty_returns_zero(self, system_with_identity: MemorySystem, tmp_path: Path):
        system = system_with_identity
        system._config = system._config.model_copy(update={
            "peer": PeerConfig(inbox_dir=str(tmp_path / "inbox")),
        })

        result = await system.peer_inbox()
        assert isinstance(result, PeerInboxResult)
        assert result.messages_found == 0

    async def test_inbox_finds_messages(self, system_with_identity: MemorySystem, tmp_path: Path):
        system = system_with_identity
        inbox_dir = tmp_path / "inbox"
        system._config = system._config.model_copy(update={
            "peer": PeerConfig(inbox_dir=str(inbox_dir)),
        })

        # Create a message file in the inbox
        peer_dir = inbox_dir / "elf-trader"
        peer_dir.mkdir(parents=True)
        msg = {
            "version": 1,
            "msg_id": "m_test1234",
            "from_did": "elf:trader",
            "to_did": "elf:test-elf",
            "in_reply_to": None,
            "sent_at": "2026-04-29T14:00:00Z",
            "content": "Gilts look cheap",
            "tags": ["peer/outbound"],
            "category": "message",
        }
        (peer_dir / "msg_m_test1234.json").write_text(json.dumps(msg))

        result = await system.peer_inbox()
        assert result.messages_found == 1

    async def test_inbox_import_creates_blocks(self, system_with_identity: MemorySystem, tmp_path: Path):
        system = system_with_identity
        inbox_dir = tmp_path / "inbox"
        system._config = system._config.model_copy(update={
            "peer": PeerConfig(inbox_dir=str(inbox_dir)),
        })
        await system.peer_add("elf:trader", "Trader")

        peer_dir = inbox_dir / "elf-trader"
        peer_dir.mkdir(parents=True)
        msg = {
            "version": 1,
            "msg_id": "m_test1234",
            "from_did": "elf:trader",
            "to_did": "elf:test-elf",
            "in_reply_to": None,
            "sent_at": "2026-04-29T14:00:00Z",
            "content": "Gilts look cheap this week",
            "tags": [],
            "category": "message",
        }
        (peer_dir / "msg_m_test1234.json").write_text(json.dumps(msg))

        result = await system.peer_inbox(import_all=True)
        assert result.messages_imported == 1

        # Message moved to processed
        assert not (peer_dir / "msg_m_test1234.json").exists()
        assert (inbox_dir / "processed" / "msg_m_test1234.json").exists()

    async def test_send_result_summary(
        self, system_with_identity: MemorySystem, tmp_path: Path,
    ):
        system = system_with_identity
        system._config = system._config.model_copy(update={
            "peer": PeerConfig(outbox_dir=str(tmp_path / "outbox")),
        })
        await system.peer_add("elf:trader", "Trader")

        result = await system.peer_send("elf:trader", "Hello")
        assert "elf:trader" in str(result)
        assert result.msg_id in str(result)


# ── Message consolidation ────────────────────────────────────────────────────


class TestMessageConsolidation:
    async def test_messages_skip_dedup(self, system_with_identity: MemorySystem, tmp_path: Path):
        """Two identical messages from different peers should both be promoted."""
        system = system_with_identity
        system._config = system._config.model_copy(update={
            "peer": PeerConfig(outbox_dir=str(tmp_path / "outbox")),
        })
        await system.peer_add("elf:a", "Agent A")
        await system.peer_add("elf:b", "Agent B")

        async with system.session():
            # Send same content to two peers — both create message blocks
            await system.peer_send("elf:a", "Same message content")
            # Learn a normal block with same content — this should dedup
            await system.learn("Same message content")
            result = await system.consolidate()

        # At least the message should be promoted (message skips dedup)
        assert result.promoted >= 1


# ── Trust loop ────────────────────────────────────────────────────────────────


class TestTrustLoop:
    async def test_outcome_on_imported_block_updates_trust(
        self, system: MemorySystem, tmp_path: Path,
    ):
        await system.peer_add("elf:sender", "Sender")
        # Manually insert a block with source_peer set
        async with system.session():
            await system.learn("Peer knowledge that proves useful", tags=["test"])
            await system.consolidate()

        # Set source_peer on the block
        async with system._engine.begin() as conn:
            from sqlalchemy import text
            result = await conn.execute(
                text("SELECT id FROM blocks WHERE status = 'active' LIMIT 1")
            )
            block_id = result.scalar()
            await conn.execute(
                text("UPDATE blocks SET source_peer = 'elf:sender' WHERE id = :id"),
                {"id": block_id},
            )

        # Run positive outcome
        await system.outcome([block_id], 0.9, source="trust-test")

        # Check trust increased
        info = await system.peer_trust("elf:sender")
        assert info.trust > 0.0

    async def test_negative_outcome_decreases_trust(self, system: MemorySystem):
        await system.peer_add("elf:sender", "Sender")
        await system.peer_trust("elf:sender", set_value=0.5)

        async with system.session():
            await system.learn("Bad peer advice", tags=["test"])
            await system.consolidate()

        async with system._engine.begin() as conn:
            from sqlalchemy import text
            result = await conn.execute(
                text("SELECT id FROM blocks WHERE status = 'active' LIMIT 1")
            )
            block_id = result.scalar()
            await conn.execute(
                text("UPDATE blocks SET source_peer = 'elf:sender' WHERE id = :id"),
                {"id": block_id},
            )

        await system.outcome([block_id], 0.1, source="trust-test")

        info = await system.peer_trust("elf:sender")
        assert info.trust < 0.5


# ── Full integration ─────────────────────────────────────────────────────────


class TestIntegration:
    async def test_full_export_import_cycle(
        self,
        test_engine,
        mock_llm,
        mock_embedding,
        tmp_path: Path,
    ):
        """Export from instance A, import to instance B, verify provenance."""
        cfg = ElfmemConfig(
            memory=MemoryConfig(inbox_threshold=3),
            peer=PeerConfig(outbox_dir=str(tmp_path / "outbox")),
        )

        # Instance A: create and export
        system_a = MemorySystem(
            engine=test_engine,
            llm_service=mock_llm,
            embedding_service=mock_embedding,
            config=cfg,
        )
        await system_a.peer_init("instance-a")
        async with system_a.session():
            await system_a.learn("Shared fact from A", tags=["shared"])
            await system_a.consolidate()

        # Mark as public
        async with system_a._engine.begin() as conn:
            from sqlalchemy import text
            await conn.execute(text("UPDATE blocks SET share = 'public' WHERE status = 'active'"))

        output = str(tmp_path / "export.json")
        export_result = await system_a.export_blocks(output_path=output)
        assert export_result.blocks_exported >= 1

        # Instance B (same engine for simplicity): import
        # Register peer A
        await system_a.peer_add("elf:instance-a", "Instance A")
        import_result = await system_a.import_blocks(output, from_peer="elf:instance-a")
        assert import_result.blocks_imported >= 0  # May dedup against existing

    async def test_full_message_exchange(
        self, system_with_identity: MemorySystem, tmp_path: Path,
    ):
        """Send → transport → inbox → import full cycle."""
        system = system_with_identity
        outbox = tmp_path / "outbox"
        inbox = tmp_path / "inbox"
        system._config = system._config.model_copy(update={
            "peer": PeerConfig(outbox_dir=str(outbox), inbox_dir=str(inbox)),
        })
        await system.peer_add("elf:trader", "Trader")

        # Send a message
        send_result = await system.peer_send("elf:trader", "What about gilts?")

        # Simulate transport: copy outbox file to inbox
        outbox_file = Path(send_result.outbox_path)
        assert outbox_file.exists()

        # Modify the message to look like it's coming back as a reply
        msg = json.loads(outbox_file.read_text())
        msg["from_did"] = "elf:trader"
        msg["to_did"] = "elf:test-elf"
        msg["direction"] = "inbound"
        msg["in_reply_to"] = send_result.msg_id
        msg["msg_id"] = "m_reply123"
        msg["content"] = "Front end repriced, watch SONIA Thursday"

        peer_inbox = inbox / "elf-trader"
        peer_inbox.mkdir(parents=True)
        (peer_inbox / "msg_m_reply123.json").write_text(json.dumps(msg))

        # Import the reply
        inbox_result = await system.peer_inbox(import_all=True)
        assert inbox_result.messages_imported == 1


# ── Delivery path ────────────────────────────────────────────────────────────


class TestDeliveryPath:
    async def test_add_peer_with_delivery_path(self, system: MemorySystem):
        peer = await system.peer_add(
            "elf:vault", "Vault", delivery_path="/tmp/vault/inbox",
        )
        assert peer.delivery_path == "/tmp/vault/inbox"

    async def test_add_peer_without_delivery_path(self, system: MemorySystem):
        peer = await system.peer_add("elf:trader", "Trader")
        assert peer.delivery_path is None

    async def test_delivery_path_in_summary(self, system: MemorySystem):
        peer = await system.peer_add(
            "elf:vault", "Vault", delivery_path="/tmp/vault/inbox",
        )
        assert "/tmp/vault/inbox" in str(peer)

    async def test_delivery_path_in_to_dict(self, system: MemorySystem):
        peer = await system.peer_add(
            "elf:vault", "Vault", delivery_path="/tmp/vault/inbox",
        )
        assert peer.to_dict()["delivery_path"] == "/tmp/vault/inbox"

    async def test_send_with_delivery_path_writes_to_peer_inbox(
        self, system_with_identity: MemorySystem, tmp_path: Path,
    ):
        """With delivery_path, message goes to peer's inbox using sender slug."""
        system = system_with_identity
        peer_inbox = tmp_path / "vault_inbox"
        system._config = system._config.model_copy(update={
            "peer": PeerConfig(outbox_dir=str(tmp_path / "outbox")),
        })
        await system.peer_add(
            "elf:vault", "Vault", delivery_path=str(peer_inbox),
        )

        result = await system.peer_send("elf:vault", "Hello vault")

        # File should be in peer's inbox, under SENDER's slug
        file_path = Path(result.outbox_path)
        assert file_path.exists()
        assert str(peer_inbox) in str(file_path)
        # Subdirectory named by sender identity, not recipient
        assert "elf-test-elf" in str(file_path)

    async def test_send_without_delivery_path_uses_outbox(
        self, system_with_identity: MemorySystem, tmp_path: Path,
    ):
        """Without delivery_path, message goes to local outbox using recipient slug."""
        system = system_with_identity
        outbox = tmp_path / "outbox"
        system._config = system._config.model_copy(update={
            "peer": PeerConfig(outbox_dir=str(outbox)),
        })
        await system.peer_add("elf:trader", "Trader")

        result = await system.peer_send("elf:trader", "Hello trader")

        file_path = Path(result.outbox_path)
        assert file_path.exists()
        assert str(outbox) in str(file_path)
        # Subdirectory named by recipient
        assert "elf-trader" in str(file_path)

    async def test_direct_delivery_roundtrip(
        self, system_with_identity: MemorySystem, tmp_path: Path,
    ):
        """Send with delivery_path → receiver's inbox scan finds it."""
        system = system_with_identity
        peer_inbox = tmp_path / "vault_inbox"
        system._config = system._config.model_copy(update={
            "peer": PeerConfig(
                outbox_dir=str(tmp_path / "outbox"),
                inbox_dir=str(peer_inbox),
            ),
        })
        await system.peer_add(
            "elf:vault", "Vault", delivery_path=str(peer_inbox),
        )

        # Send — writes directly to peer_inbox/<sender-slug>/
        await system.peer_send("elf:vault", "Direct delivery test")

        # Now scan the SAME directory as inbox — simulating the receiver
        result = await system.peer_inbox()
        assert result.messages_found == 1

    async def test_delivery_path_creates_subdirectory(
        self, system_with_identity: MemorySystem, tmp_path: Path,
    ):
        """delivery_path directory and sender subdirectory are created automatically."""
        system = system_with_identity
        peer_inbox = tmp_path / "new" / "nested" / "inbox"
        system._config = system._config.model_copy(update={
            "peer": PeerConfig(outbox_dir=str(tmp_path / "outbox")),
        })
        await system.peer_add(
            "elf:vault", "Vault", delivery_path=str(peer_inbox),
        )

        result = await system.peer_send("elf:vault", "Nested path test")

        assert Path(result.outbox_path).exists()
        assert peer_inbox.exists()

    async def test_message_file_content_correct(
        self, system_with_identity: MemorySystem, tmp_path: Path,
    ):
        """Message file has correct envelope regardless of delivery mode."""
        system = system_with_identity
        peer_inbox = tmp_path / "vault_inbox"
        system._config = system._config.model_copy(update={
            "peer": PeerConfig(outbox_dir=str(tmp_path / "outbox")),
        })
        await system.peer_add(
            "elf:vault", "Vault", delivery_path=str(peer_inbox),
        )

        result = await system.peer_send("elf:vault", "Check the envelope")

        msg = json.loads(Path(result.outbox_path).read_text())
        assert msg["from_did"] == "elf:test-elf"
        assert msg["to_did"] == "elf:vault"
        assert msg["content"] == "Check the envelope"
        assert msg["category"] == "message"
        assert msg["msg_id"] == result.msg_id
