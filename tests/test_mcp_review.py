"""Tests for the v0.18 constitutional review MCP tools.

Calls the private ``_tool_*`` coroutines directly (per the project's
testing principles and the pattern in ``tests/test_mcp.py``); the
``@mcp.tool()`` registrations are smoke-checked separately for name
presence in the FastMCP registry.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from elfmem.api import MemorySystem
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
    ProposedAmendment,
)

# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_mem(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Inject a mock MemorySystem into the mcp module (mirrors test_mcp.py)."""
    import elfmem.mcp as mcp_module

    mem: AsyncMock = AsyncMock(spec=MemorySystem)
    monkeypatch.setattr(mcp_module, "_memory", mem)
    return mem


def _proposal(block_id: str = "blk-abcdef12", drift: float = 0.55) -> ProposedAmendment:
    return ProposedAmendment(
        block_id=block_id,
        original_content="Original principle.",
        proposed_content="Updated principle.",
        rationale="Evidence X said Y.",
        drift_score=drift,
    )


def _review_result(
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


def _amendment_result() -> AmendmentResult:
    return AmendmentResult(
        amendment_id=7,
        block_id="blk-abcdef12",
        pre_content="Original.",
        post_content="Updated.",
        timestamp=datetime(2026, 5, 23, 12, 0, 0, tzinfo=UTC),
        acceptor="agent",
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
        pre_content="Original.",
        post_content="Updated.",
        pre_summary=None,
        post_summary=None,
        drift_score=0.42,
        rationale="r",
        acceptor="agent",
        reverted_at=(
            datetime(2026, 5, 23, 13, 0, 0, tzinfo=UTC) if reverted else None
        ),
    )


# ── elfmem_review_constitutional ─────────────────────────────────────────────


class TestMcpReviewConstitutional:
    async def test_returns_dict_with_proposals(self, mock_mem: AsyncMock) -> None:
        from elfmem.mcp import _tool_review_constitutional
        mock_mem.review_constitutional.return_value = _review_result(
            proposals=[_proposal()], reviewed=10,
        )
        result = await _tool_review_constitutional()
        assert isinstance(result["proposals"], list)
        assert result["proposals"][0]["block_id"] == "blk-abcdef12"
        assert result["proposals"][0]["drift_score"] == pytest.approx(0.55)

    async def test_overrides_pass_through(self, mock_mem: AsyncMock) -> None:
        from elfmem.mcp import _tool_review_constitutional
        mock_mem.review_constitutional.return_value = _review_result()
        await _tool_review_constitutional(
            drift_threshold=0.7, max_proposals=3,
            min_block_evidence=4.0, min_age_days=60.0,
        )
        _, kwargs = mock_mem.review_constitutional.call_args
        assert kwargs["drift_threshold"] == pytest.approx(0.7)
        assert kwargs["max_proposals"] == 3
        assert kwargs["min_block_evidence"] == pytest.approx(4.0)
        assert kwargs["min_age_days"] == pytest.approx(60.0)

    async def test_insufficient_history_surfaced(
        self, mock_mem: AsyncMock,
    ) -> None:
        from elfmem.mcp import _tool_review_constitutional
        mock_mem.review_constitutional.return_value = _review_result(
            insufficient=True,
        )
        result = await _tool_review_constitutional()
        assert result["insufficient_history"] is True
        assert result["proposals"] == []

    async def test_zero_proposals_returns_empty_list(
        self, mock_mem: AsyncMock,
    ) -> None:
        from elfmem.mcp import _tool_review_constitutional
        mock_mem.review_constitutional.return_value = _review_result(
            reviewed=10, skipped=10,
        )
        result = await _tool_review_constitutional()
        assert result["proposals"] == []
        assert result["reviewed_count"] == 10
        assert result["skipped_count"] == 10


# ── elfmem_accept_amendment ──────────────────────────────────────────────────


class TestMcpAcceptAmendment:
    async def test_returns_amendment_result_dict(
        self, mock_mem: AsyncMock,
    ) -> None:
        from elfmem.mcp import _tool_accept_amendment
        mock_mem.accept_amendment.return_value = _amendment_result()
        result = await _tool_accept_amendment(
            block_id="blk-abcdef12",
            proposed_content="Updated.",
        )
        assert result["amendment_id"] == 7
        assert result["block_id"] == "blk-abcdef12"
        assert result["post_content"] == "Updated."

    async def test_hardcodes_agent_acceptor(
        self, mock_mem: AsyncMock,
    ) -> None:
        from elfmem.mcp import _tool_accept_amendment
        mock_mem.accept_amendment.return_value = _amendment_result()
        await _tool_accept_amendment(
            block_id="b", proposed_content="x",
        )
        _, kwargs = mock_mem.accept_amendment.call_args
        assert kwargs["acceptor"] == "agent"

    async def test_block_not_found_returns_envelope(
        self, mock_mem: AsyncMock,
    ) -> None:
        from elfmem.mcp import _tool_accept_amendment
        mock_mem.accept_amendment.side_effect = BlockNotFound("blk-bad")
        result = await _tool_accept_amendment(
            block_id="blk-bad", proposed_content="x",
        )
        assert "error" in result
        assert result["recovery"] is not None
        assert "block_id" in result["recovery"]

    async def test_drift_score_passes_through(
        self, mock_mem: AsyncMock,
    ) -> None:
        from elfmem.mcp import _tool_accept_amendment
        mock_mem.accept_amendment.return_value = _amendment_result()
        await _tool_accept_amendment(
            block_id="b", proposed_content="x", drift_score=0.91,
        )
        _, kwargs = mock_mem.accept_amendment.call_args
        assert kwargs["drift_score"] == pytest.approx(0.91)


# ── elfmem_revert_amendment ──────────────────────────────────────────────────


class TestMcpRevertAmendment:
    async def test_returns_amendment_result_dict(
        self, mock_mem: AsyncMock,
    ) -> None:
        from elfmem.mcp import _tool_revert_amendment
        mock_mem.revert_amendment.return_value = _amendment_result()
        result = await _tool_revert_amendment(7)
        assert result["amendment_id"] == 7
        mock_mem.revert_amendment.assert_called_once_with(7)

    async def test_already_reverted_returns_envelope(
        self, mock_mem: AsyncMock,
    ) -> None:
        from elfmem.mcp import _tool_revert_amendment
        mock_mem.revert_amendment.side_effect = AmendmentAlreadyReverted(7)
        result = await _tool_revert_amendment(7)
        assert "error" in result
        assert result["recovery"] is not None

    async def test_not_found_returns_envelope(
        self, mock_mem: AsyncMock,
    ) -> None:
        from elfmem.mcp import _tool_revert_amendment
        mock_mem.revert_amendment.side_effect = AmendmentNotFound(99999)
        result = await _tool_revert_amendment(99999)
        assert "error" in result
        assert result["recovery"] is not None


# ── elfmem_list_amendments ───────────────────────────────────────────────────


class TestMcpListAmendments:
    async def test_returns_amendments_array(
        self, mock_mem: AsyncMock,
    ) -> None:
        from elfmem.mcp import _tool_list_amendments
        mock_mem.list_amendments.return_value = [
            _amendment_record(id_=1),
            _amendment_record(id_=2, reverted=True),
        ]
        result = await _tool_list_amendments()
        assert isinstance(result["amendments"], list)
        assert len(result["amendments"]) == 2
        assert result["amendments"][0]["id"] == 1
        # Reverted amendments carry a non-null reverted_at.
        reverted = next(r for r in result["amendments"] if r["id"] == 2)
        assert reverted["reverted_at"] is not None

    async def test_filters_by_block_id(self, mock_mem: AsyncMock) -> None:
        from elfmem.mcp import _tool_list_amendments
        mock_mem.list_amendments.return_value = []
        await _tool_list_amendments(block_id="blk-target")
        _, kwargs = mock_mem.list_amendments.call_args
        assert kwargs["block_id"] == "blk-target"

    async def test_respects_limit(self, mock_mem: AsyncMock) -> None:
        from elfmem.mcp import _tool_list_amendments
        mock_mem.list_amendments.return_value = []
        await _tool_list_amendments(limit=5)
        _, kwargs = mock_mem.list_amendments.call_args
        assert kwargs["limit"] == 5

    async def test_empty_for_unknown_block(self, mock_mem: AsyncMock) -> None:
        from elfmem.mcp import _tool_list_amendments
        mock_mem.list_amendments.return_value = []
        result = await _tool_list_amendments(block_id="does-not-exist")
        assert result["amendments"] == []


# ── review_corpus (v2 step 6a) ───────────────────────────────────────────────


class TestReviewCorpus:
    async def test_returns_dict_with_proposals(self, mock_mem: AsyncMock) -> None:
        from elfmem.mcp import _tool_review_corpus

        mock_mem.review_corpus.return_value = CorpusReviewResult(
            reviewed_count=10,
            proposals=[
                CorpusProposal(
                    block_id="blk-1", kind="stale",
                    reason="not reinforced in 812h", content_preview="x",
                )
            ],
        )
        result = await _tool_review_corpus()
        assert result["reviewed_count"] == 10
        assert result["proposals"][0]["block_id"] == "blk-1"

    async def test_no_proposals_returns_empty_list(self, mock_mem: AsyncMock) -> None:
        from elfmem.mcp import _tool_review_corpus

        mock_mem.review_corpus.return_value = CorpusReviewResult(reviewed_count=5)
        result = await _tool_review_corpus()
        assert result["proposals"] == []


# ── Tool registration ────────────────────────────────────────────────────────


class TestToolRegistration:
    async def test_all_four_review_tools_registered(self) -> None:
        """Verify the four new tool names are present in the FastMCP registry.

        Probes the registry via ``mcp.get_tool(name)`` — the supported
        FastMCP 2.x API. Any missing registration raises and the test fails.
        """
        from elfmem.mcp import mcp

        for name in (
            "elfmem_review_constitutional",
            "elfmem_accept_amendment",
            "elfmem_revert_amendment",
            "elfmem_list_amendments",
            "elfmem_review_corpus",
        ):
            tool = await mcp.get_tool(name)
            assert tool is not None, f"missing MCP tool registration: {name}"
