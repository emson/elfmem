"""Tests for the `elfmem review` CLI subcommand group (v0.18).

Mocks ``MemorySystem.managed`` to return a pre-configured AsyncMock so the
CLI's behaviour can be exercised without spinning up a real database.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from typer.testing import CliRunner

from elfmem.api import MemorySystem
from elfmem.cli import app
from elfmem.exceptions import (
    AmendmentAlreadyReverted,
    AmendmentNotFound,
    BlockNotFound,
)
from elfmem.types import (
    AmendmentRecord,
    AmendmentResult,
    ConstitutionalReviewResult,
    CorpusProposal,
    CorpusReviewResult,
    ForgetResult,
    ProposedAmendment,
)

runner = CliRunner()


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_mem(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Replace ``MemorySystem.managed`` with a context manager yielding a mock."""
    mem: AsyncMock = AsyncMock(spec=MemorySystem)

    @asynccontextmanager
    async def _managed(*args: object, **kwargs: object) -> object:
        yield mem

    monkeypatch.setattr(MemorySystem, "managed", _managed)
    return mem


def _proposal(block_id: str = "blk-abcdef12", drift: float = 0.55) -> ProposedAmendment:
    return ProposedAmendment(
        block_id=block_id,
        original_content="Original principle.",
        proposed_content="Updated principle.",
        rationale="Evidence X said Y.",
        drift_score=drift,
    )


def _result(
    proposals: list[ProposedAmendment] | None = None,
    *,
    reviewed: int = 5,
    skipped: int = 0,
    insufficient: bool = False,
    failed: int = 0,
) -> ConstitutionalReviewResult:
    return ConstitutionalReviewResult(
        proposals=proposals or [],
        reviewed_count=reviewed,
        skipped_count=skipped,
        insufficient_history=insufficient,
        failed_proposal_count=failed,
    )


def _amendment_result(amendment_id: int = 7, block_id: str = "blk-abcdef12") -> AmendmentResult:
    return AmendmentResult(
        amendment_id=amendment_id,
        block_id=block_id,
        pre_content="Original principle.",
        post_content="Updated principle.",
        timestamp=datetime(2026, 5, 23, 12, 0, 0, tzinfo=UTC),
        acceptor="user",
    )


def _amendment_record(
    id_: int = 7,
    block_id: str = "blk-abcdef12",
    reverted: bool = False,
) -> AmendmentRecord:
    return AmendmentRecord(
        id=id_,
        block_id=block_id,
        timestamp=datetime(2026, 5, 23, 12, 0, 0, tzinfo=UTC),
        pre_content="Original principle.",
        post_content="Updated principle.",
        pre_summary=None,
        post_summary=None,
        drift_score=0.42,
        rationale="r",
        acceptor="user",
        reverted_at=(
            datetime(2026, 5, 23, 13, 0, 0, tzinfo=UTC) if reverted else None
        ),
    )


# ── review (interactive default) ─────────────────────────────────────────────


class TestReviewDefault:
    def test_no_proposals_renders_clean_message(self, mock_mem: AsyncMock) -> None:
        mock_mem.review_constitutional.return_value = _result(
            reviewed=10, skipped=2,
        )
        # Non-TTY (CliRunner default) → emits text summary, no prompt.
        result = runner.invoke(app, ["review", "--db", "t.db"])
        assert result.exit_code == 0
        out = result.output
        # Either "no amendments proposed" (text) or JSON (when forced off-TTY).
        assert "no amendments proposed" in out or "proposals" in out

    def test_insufficient_history_renders_warning(
        self, mock_mem: AsyncMock,
    ) -> None:
        mock_mem.review_constitutional.return_value = _result(insufficient=True)
        result = runner.invoke(app, ["review", "--db", "t.db", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["insufficient_history"] is True

    def test_json_flag_emits_machine_readable(self, mock_mem: AsyncMock) -> None:
        mock_mem.review_constitutional.return_value = _result(
            proposals=[_proposal()], reviewed=10,
        )
        result = runner.invoke(app, ["review", "--db", "t.db", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data["proposals"], list)
        assert data["proposals"][0]["block_id"] == "blk-abcdef12"
        assert data["proposals"][0]["drift_score"] == pytest.approx(0.55)

    def test_yes_flag_auto_accepts_all_in_tty(
        self, mock_mem: AsyncMock, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """--yes auto-accepts every proposal without interactive prompts.

        We force the TTY check to return True so the interactive branch
        runs even under CliRunner (which would otherwise be non-TTY).
        """
        monkeypatch.setattr("elfmem.cli._ttys_attached", lambda: True)
        mock_mem.review_constitutional.return_value = _result(
            proposals=[_proposal("blk-1"), _proposal("blk-2")],
        )
        mock_mem.accept_amendment.return_value = _amendment_result()
        result = runner.invoke(app, ["review", "--db", "t.db", "--yes"])
        assert result.exit_code == 0
        # Each proposal triggered one accept_amendment call.
        assert mock_mem.accept_amendment.call_count == 2
        # acceptor="user" was used.
        _, kwargs = mock_mem.accept_amendment.call_args
        assert kwargs.get("acceptor") == "user"

    def test_per_call_overrides_forwarded(self, mock_mem: AsyncMock) -> None:
        mock_mem.review_constitutional.return_value = _result()
        result = runner.invoke(app, [
            "review", "--db", "t.db", "--json",
            "--drift-threshold", "0.7", "--max", "3",
        ])
        assert result.exit_code == 0
        _, kwargs = mock_mem.review_constitutional.call_args
        assert kwargs["drift_threshold"] == pytest.approx(0.7)
        assert kwargs["max_proposals"] == 3

    def test_interactive_quit_aborts_remaining(
        self, mock_mem: AsyncMock, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("elfmem.cli._ttys_attached", lambda: True)
        mock_mem.review_constitutional.return_value = _result(
            proposals=[_proposal("blk-1"), _proposal("blk-2"), _proposal("blk-3")],
        )
        mock_mem.accept_amendment.return_value = _amendment_result()
        # Input: accept first, then quit; third never seen.
        result = runner.invoke(app, ["review", "--db", "t.db"], input="a\nq\n")
        assert result.exit_code == 0
        assert mock_mem.accept_amendment.call_count == 1
        assert "abandoned: 2" in result.output

    def test_interactive_reject_does_not_accept(
        self, mock_mem: AsyncMock, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("elfmem.cli._ttys_attached", lambda: True)
        mock_mem.review_constitutional.return_value = _result(
            proposals=[_proposal("blk-1")],
        )
        result = runner.invoke(app, ["review", "--db", "t.db"], input="r\n")
        assert result.exit_code == 0
        mock_mem.accept_amendment.assert_not_called()


# ── accept ───────────────────────────────────────────────────────────────────


class TestReviewAccept:
    def test_reads_content_from_file(
        self, mock_mem: AsyncMock, tmp_path,
    ) -> None:
        f = tmp_path / "new.md"
        f.write_text("Revised content here.")
        mock_mem.accept_amendment.return_value = _amendment_result()
        result = runner.invoke(app, [
            "review", "accept", "blk-1",
            "--content-file", str(f),
            "--yes", "--db", "t.db",
        ])
        assert result.exit_code == 0
        args, kwargs = mock_mem.accept_amendment.call_args
        # Positional args: block_id, proposed_content, rationale
        assert args[0] == "blk-1"
        assert args[1] == "Revised content here."

    def test_reads_content_from_stdin(self, mock_mem: AsyncMock) -> None:
        mock_mem.accept_amendment.return_value = _amendment_result()
        result = runner.invoke(
            app,
            ["review", "accept", "blk-1", "--yes", "--db", "t.db"],
            input="Piped content.",
        )
        assert result.exit_code == 0
        args, _kwargs = mock_mem.accept_amendment.call_args
        assert args[1] == "Piped content."

    def test_errors_when_no_content_source(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When stdin is a TTY and no --content-file is given, exit non-zero
        with a clear recovery hint pointing at both input modes."""
        # Test the helper directly: CliRunner replaces sys.stdin so an
        # end-to-end "neither source" scenario can't be reproduced via
        # invoke(). The helper is the only branch point so this is fine.
        import sys

        import typer

        from elfmem.cli import _read_content_source

        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        with pytest.raises(typer.Exit) as exc:
            _read_content_source(None)
        assert exc.value.exit_code == 2

    def test_uses_user_acceptor(
        self, mock_mem: AsyncMock,
    ) -> None:
        mock_mem.accept_amendment.return_value = _amendment_result()
        runner.invoke(app, [
            "review", "accept", "blk-1", "--yes", "--db", "t.db",
        ], input="content")
        _args, kwargs = mock_mem.accept_amendment.call_args
        assert kwargs.get("acceptor") == "user"

    def test_invalid_block_id_renders_recovery(
        self, mock_mem: AsyncMock,
    ) -> None:
        mock_mem.accept_amendment.side_effect = BlockNotFound("blk-bad")
        result = runner.invoke(app, [
            "review", "accept", "blk-bad", "--yes", "--db", "t.db",
        ], input="content")
        assert result.exit_code != 0
        assert "Recovery" in result.output
        assert "block_id" in result.output

    def test_json_output(self, mock_mem: AsyncMock) -> None:
        mock_mem.accept_amendment.return_value = _amendment_result()
        result = runner.invoke(app, [
            "review", "accept", "blk-1",
            "--yes", "--json", "--db", "t.db",
        ], input="content")
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["amendment_id"] == 7
        assert data["acceptor"] == "user"


# ── revert ───────────────────────────────────────────────────────────────────


class TestReviewRevert:
    def test_with_yes_flag_skips_confirmation(self, mock_mem: AsyncMock) -> None:
        mock_mem.revert_amendment.return_value = _amendment_result()
        result = runner.invoke(app, [
            "review", "revert", "7", "--yes", "--db", "t.db",
        ])
        assert result.exit_code == 0
        mock_mem.revert_amendment.assert_called_once_with(7)

    def test_already_reverted_renders_recovery(self, mock_mem: AsyncMock) -> None:
        mock_mem.revert_amendment.side_effect = AmendmentAlreadyReverted(7)
        result = runner.invoke(app, [
            "review", "revert", "7", "--yes", "--db", "t.db",
        ])
        assert result.exit_code != 0
        assert "Recovery" in result.output

    def test_invalid_id_renders_recovery(self, mock_mem: AsyncMock) -> None:
        mock_mem.revert_amendment.side_effect = AmendmentNotFound(99999)
        result = runner.invoke(app, [
            "review", "revert", "99999", "--yes", "--db", "t.db",
        ])
        assert result.exit_code != 0
        assert "Recovery" in result.output

    def test_json_output(self, mock_mem: AsyncMock) -> None:
        mock_mem.revert_amendment.return_value = _amendment_result()
        result = runner.invoke(app, [
            "review", "revert", "7", "--yes", "--json", "--db", "t.db",
        ])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["amendment_id"] == 7


# ── list ─────────────────────────────────────────────────────────────────────


class TestReviewList:
    def test_no_amendments_renders_empty_message(self, mock_mem: AsyncMock) -> None:
        mock_mem.list_amendments.return_value = []
        result = runner.invoke(app, ["review", "list", "--db", "t.db"])
        assert result.exit_code == 0
        assert "No amendments" in result.output

    def test_filter_by_block(self, mock_mem: AsyncMock) -> None:
        mock_mem.list_amendments.return_value = [
            _amendment_record(id_=1, block_id="blk-target"),
        ]
        result = runner.invoke(app, [
            "review", "list", "--block", "blk-target", "--db", "t.db",
        ])
        assert result.exit_code == 0
        _, kwargs = mock_mem.list_amendments.call_args
        assert kwargs["block_id"] == "blk-target"

    def test_default_limit_forwarded(self, mock_mem: AsyncMock) -> None:
        mock_mem.list_amendments.return_value = []
        runner.invoke(app, ["review", "list", "--db", "t.db"])
        _, kwargs = mock_mem.list_amendments.call_args
        # CLI's default limit is 20 (smaller than API default 100).
        assert kwargs["limit"] == 20

    def test_explicit_limit_forwarded(self, mock_mem: AsyncMock) -> None:
        mock_mem.list_amendments.return_value = []
        runner.invoke(app, ["review", "list", "--limit", "5", "--db", "t.db"])
        _, kwargs = mock_mem.list_amendments.call_args
        assert kwargs["limit"] == 5

    def test_json_emits_array(self, mock_mem: AsyncMock) -> None:
        mock_mem.list_amendments.return_value = [
            _amendment_record(id_=1),
            _amendment_record(id_=2, reverted=True),
        ]
        result = runner.invoke(app, ["review", "list", "--json", "--db", "t.db"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 2
        # Reverted amendment carries reverted_at non-null.
        reverted = next(r for r in data if r["id"] == 2)
        assert reverted["reverted_at"] is not None

    def test_table_includes_reverted_status(self, mock_mem: AsyncMock) -> None:
        mock_mem.list_amendments.return_value = [
            _amendment_record(id_=1, reverted=False),
            _amendment_record(id_=2, reverted=True),
        ]
        result = runner.invoke(app, ["review", "list", "--db", "t.db"])
        assert result.exit_code == 0
        assert "active" in result.output
        assert "reverted" in result.output


# ── review corpus (v2 step 6a) ───────────────────────────────────────────────


def _corpus_proposal(block_id: str = "blk-abcdef12") -> CorpusProposal:
    return CorpusProposal(
        block_id=block_id,
        kind="stale",
        reason="not reinforced in 812h (reinforced 0x, no outcome evidence)",
        content_preview="Some stale fact.",
    )


def _corpus_result(
    proposals: list[CorpusProposal] | None = None, *, reviewed: int = 10,
) -> CorpusReviewResult:
    return CorpusReviewResult(reviewed_count=reviewed, proposals=proposals or [])


class TestReviewCorpus:
    def test_no_proposals_renders_clean_message(
        self, mock_mem: AsyncMock, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("elfmem.cli._ttys_attached", lambda: True)
        mock_mem.review_corpus.return_value = _corpus_result(reviewed=10)
        result = runner.invoke(app, ["review", "corpus", "--db", "t.db"])
        assert result.exit_code == 0
        assert "no proposals" in result.output

    def test_json_flag_emits_machine_readable(self, mock_mem: AsyncMock) -> None:
        mock_mem.review_corpus.return_value = _corpus_result(
            proposals=[_corpus_proposal()],
        )
        result = runner.invoke(app, ["review", "corpus", "--db", "t.db", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["proposals"][0]["block_id"] == "blk-abcdef12"
        assert data["proposals"][0]["kind"] == "stale"

    def test_yes_flag_auto_accepts_all_via_forget(
        self, mock_mem: AsyncMock, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("elfmem.cli._ttys_attached", lambda: True)
        mock_mem.review_corpus.return_value = _corpus_result(
            proposals=[_corpus_proposal("blk-1"), _corpus_proposal("blk-2")],
        )
        mock_mem.forget.return_value = ForgetResult(block_id="blk-1", status="forgotten")
        result = runner.invoke(app, ["review", "corpus", "--db", "t.db", "--yes"])
        assert result.exit_code == 0
        assert mock_mem.forget.call_count == 2
        # Accepted proposals apply with reason=DECAYED, not the forget()
        # default (FORGOTTEN) — distinguishes an accepted review finding
        # from a direct human decision in the audit trail.
        from elfmem.types import ArchiveReason
        args, kwargs = mock_mem.forget.call_args
        assert kwargs.get("reason") == ArchiveReason.DECAYED

    def test_interactive_quit_aborts_remaining(
        self, mock_mem: AsyncMock, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("elfmem.cli._ttys_attached", lambda: True)
        mock_mem.review_corpus.return_value = _corpus_result(
            proposals=[
                _corpus_proposal("blk-1"), _corpus_proposal("blk-2"),
                _corpus_proposal("blk-3"),
            ],
        )
        mock_mem.forget.return_value = ForgetResult(block_id="blk-1", status="forgotten")
        result = runner.invoke(app, ["review", "corpus", "--db", "t.db"], input="a\nq\n")
        assert result.exit_code == 0
        assert mock_mem.forget.call_count == 1
        assert "abandoned: 2" in result.output

    def test_interactive_reject_does_not_forget(
        self, mock_mem: AsyncMock, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("elfmem.cli._ttys_attached", lambda: True)
        mock_mem.review_corpus.return_value = _corpus_result(
            proposals=[_corpus_proposal("blk-1")],
        )
        result = runner.invoke(app, ["review", "corpus", "--db", "t.db"], input="r\n")
        assert result.exit_code == 0
        mock_mem.forget.assert_not_called()

    def test_bare_review_still_runs_constitutional_not_corpus(
        self, mock_mem: AsyncMock,
    ) -> None:
        """`elfmem review` (no subcommand) must remain constitutional review
        — the naming collision this step deliberately avoided."""
        mock_mem.review_constitutional.return_value = _result(reviewed=3)
        result = runner.invoke(app, ["review", "--db", "t.db"])
        assert result.exit_code == 0
        mock_mem.review_corpus.assert_not_called()
