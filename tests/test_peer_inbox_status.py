"""Tests for peer_inbox_status: lightweight inbox scanning without import."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from elfmem.api import MemorySystem
from elfmem.config import ElfmemConfig, MemoryConfig, PeerConfig
from elfmem.operations.peer import scan_peer_inbox
from elfmem.types import PeerInboxStatus

# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def inbox_dir(tmp_path: Path) -> Path:
    """Create a temp inbox directory structure."""
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    return inbox


def _write_message(
    inbox_dir: Path,
    peer_slug: str,
    msg_id: str,
    from_did: str,
    content: str = "hello",
) -> Path:
    """Write a test message file matching elfmem's format."""
    peer_dir = inbox_dir / peer_slug
    peer_dir.mkdir(parents=True, exist_ok=True)
    path = peer_dir / f"msg_{msg_id}.json"
    message = {
        "version": 1,
        "msg_id": msg_id,
        "direction": "outbound",
        "from_did": from_did,
        "to_did": "elf:test",
        "in_reply_to": None,
        "sent_at": "2026-05-01T10:00:00+00:00",
        "content": content,
        "tags": ["peer/outbound", "peer/to:elf:test"],
        "category": "message",
    }
    path.write_text(json.dumps(message), encoding="utf-8")
    return path


# ── scan_peer_inbox() pure function tests ───────────────────────────────────


class TestScanPeerInbox:
    def test_empty_inbox(self, inbox_dir: Path):
        result = scan_peer_inbox(inbox_dir)
        assert result.pending == 0
        assert result.oldest_at is None
        assert result.newest_at is None
        assert result.from_peers == []

    def test_missing_inbox_dir_elfmem_exists(self, tmp_path: Path):
        # .elfmem/ present (project initialised), inbox/ just hasn't been created yet.
        elfmem_dir = tmp_path / ".elfmem"
        elfmem_dir.mkdir()
        result = scan_peer_inbox(elfmem_dir / "inbox")
        assert result.pending == 0
        assert result.from_peers == []
        assert not result.warning  # no warning — setup was run

    def test_missing_elfmem_dir_warns(self, tmp_path: Path):
        # .elfmem/ absent — project found via .git but elfmem setup never run.
        (tmp_path / ".git").mkdir()
        result = scan_peer_inbox(tmp_path / ".elfmem" / "inbox")
        assert result.pending == 0
        assert "elfmem setup" in result.warning
        assert "warning" in result.to_dict()
        assert "elfmem setup" in result.summary

    def test_single_message(self, inbox_dir: Path):
        _write_message(inbox_dir, "elf-sender", "m_abc1", "elf:sender")
        result = scan_peer_inbox(inbox_dir)
        assert result.pending == 1
        assert result.from_peers == ["elf:sender"]
        assert result.oldest_at is not None
        assert result.newest_at is not None

    def test_multiple_messages_same_peer(self, inbox_dir: Path):
        _write_message(inbox_dir, "elf-sender", "m_abc1", "elf:sender", "msg 1")
        _write_message(inbox_dir, "elf-sender", "m_abc2", "elf:sender", "msg 2")
        result = scan_peer_inbox(inbox_dir)
        assert result.pending == 2
        assert result.from_peers == ["elf:sender"]

    def test_multiple_peers(self, inbox_dir: Path):
        _write_message(inbox_dir, "elf-alpha", "m_a1", "elf:alpha")
        _write_message(inbox_dir, "elf-beta", "m_b1", "elf:beta")
        result = scan_peer_inbox(inbox_dir)
        assert result.pending == 2
        assert len(result.from_peers) == 2
        assert "elf:alpha" in result.from_peers
        assert "elf:beta" in result.from_peers

    def test_processed_dir_excluded(self, inbox_dir: Path):
        _write_message(inbox_dir, "elf-sender", "m_abc1", "elf:sender")
        processed = inbox_dir / "processed"
        processed.mkdir()
        (processed / "msg_old.json").write_text('{"from_did": "elf:old"}')
        result = scan_peer_inbox(inbox_dir)
        assert result.pending == 1

    def test_malformed_file_counted_but_peer_skipped(self, inbox_dir: Path):
        _write_message(inbox_dir, "elf-good", "m_ok", "elf:good")
        bad_dir = inbox_dir / "elf-bad"
        bad_dir.mkdir()
        (bad_dir / "msg_bad.json").write_text("not json {{{")
        result = scan_peer_inbox(inbox_dir)
        assert result.pending == 2
        assert result.from_peers == ["elf:good"]

    def test_oldest_newest_timestamps(self, inbox_dir: Path):
        p1 = _write_message(inbox_dir, "elf-a", "m_old", "elf:a", "old")
        import os
        import time
        old_time = time.time() - 3600
        os.utime(p1, (old_time, old_time))
        _write_message(inbox_dir, "elf-a", "m_new", "elf:a", "new")
        result = scan_peer_inbox(inbox_dir)
        assert result.oldest_at is not None
        assert result.newest_at is not None
        assert result.oldest_at < result.newest_at

    def test_inbox_dir_in_result(self, inbox_dir: Path):
        result = scan_peer_inbox(inbox_dir)
        assert result.inbox_dir == str(inbox_dir)


# ── PeerInboxStatus type tests ──────────────────────────────────────────────


class TestPeerInboxStatusType:
    def test_str_empty(self):
        status = PeerInboxStatus(
            pending=0, oldest_at=None, newest_at=None,
            from_peers=[], inbox_dir="/tmp/test",
        )
        assert str(status) == "Peer inbox: empty"

    def test_str_with_messages(self):
        status = PeerInboxStatus(
            pending=3, oldest_at="2026-05-01T10:00:00+00:00",
            newest_at="2026-05-01T11:00:00+00:00",
            from_peers=["elf:alpha", "elf:beta"], inbox_dir="/tmp/test",
        )
        assert "3 unprocessed" in str(status)
        assert "elf:alpha, elf:beta" in str(status)

    def test_summary_equals_str(self):
        status = PeerInboxStatus(
            pending=1, oldest_at="2026-05-01T10:00:00+00:00",
            newest_at="2026-05-01T10:00:00+00:00",
            from_peers=["elf:x"], inbox_dir="/tmp/test",
        )
        assert status.summary == str(status)

    def test_to_dict(self):
        status = PeerInboxStatus(
            pending=2, oldest_at="2026-05-01T10:00:00+00:00",
            newest_at="2026-05-01T11:00:00+00:00",
            from_peers=["elf:a"], inbox_dir="/tmp/test",
        )
        d = status.to_dict()
        assert d["pending"] == 2
        assert d["from_peers"] == ["elf:a"]
        assert d["inbox_dir"] == "/tmp/test"
        assert "oldest_at" in d
        assert "newest_at" in d

    def test_frozen(self):
        status = PeerInboxStatus(
            pending=0, oldest_at=None, newest_at=None,
            from_peers=[], inbox_dir="/tmp/test",
        )
        with pytest.raises(AttributeError):
            status.pending = 5  # type: ignore[misc]


# ── MemorySystem.peer_inbox_status() integration ────────────────────────────


class TestMemorySystemPeerInboxStatus:
    @pytest.fixture
    async def system(self, test_engine, mock_llm, mock_embedding, tmp_path):
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        cfg = ElfmemConfig(
            memory=MemoryConfig(inbox_threshold=3),
            peer=PeerConfig(inbox_dir=str(inbox)),
        )
        return MemorySystem(
            engine=test_engine,
            llm_service=mock_llm,
            embedding_service=mock_embedding,
            config=cfg,
        )

    async def test_returns_peer_inbox_status(self, system):
        result = system.peer_inbox_status()
        assert isinstance(result, PeerInboxStatus)

    async def test_empty_inbox(self, system):
        result = system.peer_inbox_status()
        assert result.pending == 0

    async def test_detects_messages(self, system, tmp_path):
        inbox = tmp_path / "inbox"
        _write_message(inbox, "elf-sender", "m_t1", "elf:sender")
        result = system.peer_inbox_status()
        assert result.pending == 1
        assert result.from_peers == ["elf:sender"]

    async def test_records_operation(self, system):
        system.peer_inbox_status()
        history = system.history(last_n=1)
        assert len(history) == 1
        assert history[0].operation == "peer_inbox_status"
