"""Tests for config-declared peers and their sync into peer_roster.

Covers the v0.19 fix: ``peers:`` in YAML used to be silently dropped by the
config loader. ``PeerSpec`` parses it, validators reject malformed/duplicate
entries, and ``sync_peers_from_config`` upserts the canonical DIDs into
``peer_roster`` so ``peer_list`` returns them.
"""

from __future__ import annotations

import pytest

from elfmem.api import MemorySystem
from elfmem.config import (
    ElfmemConfig,
    MemoryConfig,
    PeerConfig,
    PeerSpec,
    _derive_did,
    _slug,
)
from elfmem.db.queries import get_all_peers, get_peer
from elfmem.operations.peer import canonical_did, sync_peers_from_config

# ── PeerSpec validation ──────────────────────────────────────────────────────


class TestPeerSpecDerivation:
    def test_did_defaults_to_elf_scheme_from_name(self):
        spec = PeerSpec(name="Alv")
        assert spec.did == "elf:alv"

    def test_did_lowercases_and_dashes_name(self):
        spec = PeerSpec(name="Vault Keeper 2")
        assert spec.did == "elf:vault-keeper-2"

    def test_explicit_did_is_preserved(self):
        spec = PeerSpec(name="Alv", did="elf:alv-prime")
        assert spec.did == "elf:alv-prime"

    def test_malformed_did_is_rejected(self):
        with pytest.raises(ValueError, match="malformed"):
            PeerSpec(name="Alv", did="ELF:ALV")  # uppercase

    def test_legacy_identity_field_remaps_to_description(self):
        spec = PeerSpec.model_validate(
            {"name": "Alv", "identity": "Vault keeper for Ben"}
        )
        assert spec.description == "Vault keeper for Ben"

    def test_delivery_path_derived_from_project_root(self, tmp_path):
        spec = PeerSpec(name="Alv", project_root=str(tmp_path))
        assert spec.delivery_path == str(tmp_path / ".elfmem" / "inbox")

    def test_delivery_path_explicit_wins(self, tmp_path):
        explicit = str(tmp_path / "custom")
        spec = PeerSpec(
            name="Alv", project_root=str(tmp_path), delivery_path=explicit
        )
        assert spec.delivery_path == explicit


# ── ElfmemConfig.peers validation ────────────────────────────────────────────


class TestElfmemConfigPeers:
    def test_peers_field_parsed_from_dict(self):
        cfg = ElfmemConfig.model_validate(
            {"peers": [{"name": "Alv"}, {"name": "Kit"}]}
        )
        assert [p.did for p in cfg.peers] == ["elf:alv", "elf:kit"]

    def test_empty_peers_is_default(self):
        assert ElfmemConfig().peers == []

    def test_duplicate_did_rejected(self):
        with pytest.raises(ValueError, match="Duplicate peer did"):
            ElfmemConfig.model_validate(
                {
                    "peers": [
                        {"name": "Alv"},
                        {"name": "alv"},  # different display, same slug
                    ]
                }
            )

    def test_self_referencing_peer_rejected(self):
        with pytest.raises(ValueError, match="self-referential"):
            ElfmemConfig.model_validate(
                {
                    "peer": {"identity": "elf:elf"},
                    "peers": [{"name": "Elf"}],  # derives elf:elf
                }
            )

    def test_duplicate_project_root_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="share project_root"):
            ElfmemConfig.model_validate(
                {
                    "peers": [
                        {"name": "Alv", "project_root": str(tmp_path)},
                        {"name": "Kit", "project_root": str(tmp_path)},
                    ]
                }
            )


# ── Slug consistency with operations layer ───────────────────────────────────


class TestSlugConsistency:
    def test_slug_matches_operations(self):
        from elfmem.operations.peer import _slugify

        for raw in ["Alv", "elf:alv", "vault keeper", "Mixed-CASE_99"]:
            assert _slug(raw) == _slugify(raw)

    def test_derive_did_round_trip(self):
        assert _derive_did("Alv") == "elf:alv"


# ── Sync into peer_roster ────────────────────────────────────────────────────


@pytest.fixture
async def conn(test_engine):
    async with test_engine.begin() as c:
        yield c


class TestSyncPeersFromConfig:
    async def test_sync_inserts_new_peers(self, conn):
        specs = [PeerSpec(name="Alv"), PeerSpec(name="Kit")]
        inserted = await sync_peers_from_config(conn, specs)
        assert inserted == 2
        peers = await get_all_peers(conn)
        dids = {p["did"] for p in peers}
        assert {"elf:alv", "elf:kit"} <= dids

    async def test_sync_is_idempotent(self, conn):
        specs = [PeerSpec(name="Alv")]
        await sync_peers_from_config(conn, specs)
        second_run = await sync_peers_from_config(conn, specs)
        assert second_run == 0

    async def test_sync_preserves_existing_trust(self, conn):
        from elfmem.db.queries import insert_peer, update_peer_trust

        await insert_peer(conn, did="elf:alv", name="Alv")
        await update_peer_trust(conn, "elf:alv", 0.42)
        await sync_peers_from_config(conn, [PeerSpec(name="Alv", trust=1.0)])
        alv = await get_peer(conn, "elf:alv")
        assert alv["trust"] == pytest.approx(0.42)

    async def test_sync_applies_config_trust_on_first_insert(self, conn):
        await sync_peers_from_config(conn, [PeerSpec(name="Alv", trust=0.75)])
        alv = await get_peer(conn, "elf:alv")
        assert alv["trust"] == pytest.approx(0.75)

    async def test_sync_records_delivery_path(self, conn, tmp_path):
        spec = PeerSpec(name="Alv", project_root=str(tmp_path))
        await sync_peers_from_config(conn, [spec])
        alv = await get_peer(conn, "elf:alv")
        assert alv["delivery_path"] == str(tmp_path / ".elfmem" / "inbox")


# ── Canonical DID resolution ─────────────────────────────────────────────────


class TestCanonicalDID:
    async def test_did_passes_through(self, conn):
        assert await canonical_did(conn, "elf:alv") == "elf:alv"

    async def test_did_lowercased(self, conn):
        assert await canonical_did(conn, "ELF:ALV") == "elf:alv"

    async def test_name_resolved_via_roster(self, conn):
        from elfmem.db.queries import insert_peer

        await insert_peer(conn, did="elf:alv", name="Alv")
        assert await canonical_did(conn, "Alv") == "elf:alv"
        assert await canonical_did(conn, "alv") == "elf:alv"

    async def test_unknown_name_derives_elf_scheme(self, conn):
        assert await canonical_did(conn, "Nobody") == "elf:nobody"


# ── YAML round-trip ──────────────────────────────────────────────────────────


class TestYamlPeersParsing:
    def test_yaml_peers_section_loads(self, tmp_path):
        cfg_yaml = tmp_path / "config.yaml"
        cfg_yaml.write_text(
            "peer:\n"
            "  identity: elf:test\n"
            "peers:\n"
            "  - name: Alv\n"
            "    description: Vault keeper\n"
            "    trust: 0.9\n"
            "  - name: Kit\n"
        )
        cfg = ElfmemConfig.from_yaml(str(cfg_yaml))
        dids = {p.did for p in cfg.peers}
        assert dids == {"elf:alv", "elf:kit"}
        alv = next(p for p in cfg.peers if p.did == "elf:alv")
        assert alv.trust == pytest.approx(0.9)
        assert alv.description == "Vault keeper"

    def test_yaml_legacy_identity_field_accepted(self, tmp_path):
        cfg_yaml = tmp_path / "config.yaml"
        cfg_yaml.write_text(
            "peers:\n"
            "  - name: Alv\n"
            "    identity: legacy prose description\n"
        )
        cfg = ElfmemConfig.from_yaml(str(cfg_yaml))
        assert cfg.peers[0].description == "legacy prose description"


# ── Integration: sync + peer_list via MemorySystem ───────────────────────────


@pytest.fixture
async def system(test_engine, mock_llm, mock_embedding) -> MemorySystem:
    cfg = ElfmemConfig(
        memory=MemoryConfig(inbox_threshold=3),
        peer=PeerConfig(),
        peers=[
            PeerSpec(name="Alv", trust=0.9),
            PeerSpec(name="Kit", trust=0.6),
        ],
    )
    return MemorySystem(
        engine=test_engine,
        llm_service=mock_llm,
        embedding_service=mock_embedding,
        config=cfg,
    )


class TestPeerListAfterSync:
    async def test_synced_peers_appear_in_peer_list(self, system, test_engine):
        # Direct sync call simulates from_config's startup hook.
        async with test_engine.begin() as conn:
            await sync_peers_from_config(conn, system._config.peers)
        peers = await system.peer_list()
        dids = {p.did for p in peers}
        assert dids == {"elf:alv", "elf:kit"}
        alv = next(p for p in peers if p.did == "elf:alv")
        assert alv.trust == pytest.approx(0.9)


# ── Phase 7: recipient precondition ──────────────────────────────────────────


class TestRecipientInitialisedPrecondition:
    async def test_send_to_uninitialised_recipient_raises(
        self, test_engine, mock_llm, mock_embedding, tmp_path,
    ):
        from elfmem.exceptions import PeerError

        # delivery_path exists but lacks the sibling config.yaml marker.
        peer_inbox = tmp_path / "vault_inbox"
        peer_inbox.mkdir()

        cfg = ElfmemConfig(memory=MemoryConfig(inbox_threshold=3))
        system = MemorySystem(
            engine=test_engine,
            llm_service=mock_llm,
            embedding_service=mock_embedding,
            config=cfg,
        )
        await system.peer_init("test-elf")
        await system.peer_add(
            "elf:vault", "Vault", delivery_path=str(peer_inbox),
        )
        with pytest.raises(PeerError, match="not an initialised elfmem"):
            await system.peer_send("elf:vault", "ping")

    async def test_send_succeeds_with_marker_present(
        self, test_engine, mock_llm, mock_embedding, tmp_path,
    ):
        peer_inbox = tmp_path / "vault_inbox"
        peer_inbox.mkdir()
        (tmp_path / "config.yaml").touch()  # the marker

        cfg = ElfmemConfig(memory=MemoryConfig(inbox_threshold=3))
        system = MemorySystem(
            engine=test_engine,
            llm_service=mock_llm,
            embedding_service=mock_embedding,
            config=cfg,
        )
        await system.peer_init("test-elf")
        await system.peer_add(
            "elf:vault", "Vault", delivery_path=str(peer_inbox),
        )
        result = await system.peer_send("elf:vault", "ping")
        assert result.to_peer == "elf:vault"


# ── Phase 8: legacy folder migration ─────────────────────────────────────────


class TestLegacyOutboxMigration:
    async def test_migrates_bare_name_slug_to_did_slug(self, test_engine, tmp_path):
        from elfmem.db.queries import insert_peer
        from elfmem.operations.peer import migrate_legacy_outbox_slugs

        outbox = tmp_path / "outbox"
        legacy = outbox / "alv"
        legacy.mkdir(parents=True)
        (legacy / "msg_old1.json").write_text("{}")

        async with test_engine.begin() as conn:
            await insert_peer(conn, did="elf:alv", name="Alv")
            renamed = await migrate_legacy_outbox_slugs(conn, outbox)

        assert renamed == {"alv": "elf-alv"}
        assert not legacy.exists()
        assert (outbox / "elf-alv" / "msg_old1.json").exists()

    async def test_skip_when_canonical_already_exists(self, test_engine, tmp_path):
        from elfmem.db.queries import insert_peer
        from elfmem.operations.peer import migrate_legacy_outbox_slugs

        outbox = tmp_path / "outbox"
        (outbox / "alv").mkdir(parents=True)
        (outbox / "elf-alv").mkdir(parents=True)
        (outbox / "alv" / "old.json").write_text("{}")
        (outbox / "elf-alv" / "new.json").write_text("{}")

        async with test_engine.begin() as conn:
            await insert_peer(conn, did="elf:alv", name="Alv")
            renamed = await migrate_legacy_outbox_slugs(conn, outbox)

        # Refuses to merge — preserves both folders for operator review.
        assert renamed == {}
        assert (outbox / "alv" / "old.json").exists()
        assert (outbox / "elf-alv" / "new.json").exists()

    async def test_idempotent_when_already_canonical(self, test_engine, tmp_path):
        from elfmem.db.queries import insert_peer
        from elfmem.operations.peer import migrate_legacy_outbox_slugs

        outbox = tmp_path / "outbox"
        (outbox / "elf-alv").mkdir(parents=True)

        async with test_engine.begin() as conn:
            await insert_peer(conn, did="elf:alv", name="Alv")
            first = await migrate_legacy_outbox_slugs(conn, outbox)
            second = await migrate_legacy_outbox_slugs(conn, outbox)
        assert first == {}
        assert second == {}

    async def test_noop_when_outbox_absent(self, test_engine, tmp_path):
        from elfmem.operations.peer import migrate_legacy_outbox_slugs

        async with test_engine.begin() as conn:
            renamed = await migrate_legacy_outbox_slugs(conn, tmp_path / "missing")
        assert renamed == {}
