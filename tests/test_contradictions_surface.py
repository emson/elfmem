"""Regression test for issue #50 item 1: `contradictions_detected` was running
but never surfaced on `ConsolidateResult` / `to_dict()`.

The pipeline already detected and inserted contradictions; only the count
was missing from the result object. This test reproduces Dmitry's exact
scenario (two contradicting birthday dates) and asserts the count is now
both present on the dataclass and in the JSON payload.
"""

from __future__ import annotations

import pytest

from elfmem.adapters.mock import MockEmbeddingService, MockLLMService
from elfmem.db.engine import create_test_engine
from elfmem.db.queries import seed_builtin_data
from elfmem.operations.consolidate import consolidate
from elfmem.operations.learn import learn


@pytest.fixture
async def setup():
    engine = await create_test_engine()
    async with engine.begin() as conn:
        await seed_builtin_data(conn)
    # Force the two blocks into the contradiction-detection band: above
    # the similarity prefilter (0.40) but below the near-dup threshold
    # (0.90). At ≥0.90 the second block would just supersede the first
    # (treated as a near-duplicate), bypassing contradiction detection
    # entirely — which is what happened to Dmitry's literal repro with
    # default embeddings. Embeddings are computed on lowercased content,
    # so override keys must also be lowercase.
    mock_embedding = MockEmbeddingService(
        dimensions=128,
        similarity_overrides={
            frozenset({
                "dima's birthday is january 15th",
                "dima's birthday is july 20th",
            }): 0.75,
        },
    )
    mock_llm = MockLLMService(
        default_alignment=0.7,
        # MockLLMService.detect_contradiction matches "sub_a in block_a AND
        # sub_b in block_b" in argument order. consolidate.py passes the
        # *new* block first, the *existing* active block second — so the
        # second-consolidated block (July) is block_a and the first (January)
        # is block_b. Cover both directions to stay order-independent.
        contradiction_overrides={
            ("July 20th", "January 15th"): 0.92,
            ("January 15th", "July 20th"): 0.92,
        },
    )
    yield engine, mock_llm, mock_embedding
    await engine.dispose()


async def test_contradictions_detected_appears_in_result(setup) -> None:
    """Dmitry's repro: two contradicting facts → result must include the count."""
    engine, mock_llm, mock_embedding = setup

    # First fact: consolidate it so it lives in `active` (the contradiction
    # detector compares each new inbox block against existing active blocks).
    async with engine.begin() as conn:
        await learn(
            conn,
            content="Dima's birthday is January 15th",
            category="knowledge",
            source="test",
        )
        await consolidate(
            conn,
            llm=mock_llm,
            embedding_svc=mock_embedding,
            current_active_hours=1.0,
        )

    # Second fact contradicts the first. Consolidating it must trigger
    # detect_contradiction() and surface the count.
    async with engine.begin() as conn:
        await learn(
            conn,
            content="Dima's birthday is July 20th",
            category="knowledge",
            source="test",
        )
        result = await consolidate(
            conn,
            llm=mock_llm,
            embedding_svc=mock_embedding,
            current_active_hours=1.0,
        )

    # The count must reach the dataclass…
    assert result.contradictions_detected >= 1, (
        f"contradictions_detected={result.contradictions_detected} — "
        "detection ran in the pipeline but never reached the result"
    )
    # …and the JSON projection the MCP server / CLI consumers use.
    payload = result.to_dict()
    assert "contradictions_detected" in payload
    assert payload["contradictions_detected"] == result.contradictions_detected


async def test_no_contradictions_reports_zero(setup) -> None:
    """Healthy case: no contradicting pair → count is 0, not missing."""
    engine, mock_llm, mock_embedding = setup

    async with engine.begin() as conn:
        await learn(
            conn,
            content="Unrelated fact about the weather",
            category="knowledge",
            source="test",
        )
        result = await consolidate(
            conn,
            llm=mock_llm,
            embedding_svc=mock_embedding,
            current_active_hours=1.0,
        )

    assert result.contradictions_detected == 0
    assert result.to_dict()["contradictions_detected"] == 0


def test_summary_mentions_contradictions_when_nonzero() -> None:
    """`summary` should surface the contradiction count in the human-readable form."""
    from elfmem.types import ConsolidateResult

    r = ConsolidateResult(
        processed=2, promoted=2, deduplicated=0, edges_created=4,
        contradictions_detected=1,
    )
    assert "1 contradictions" in r.summary


def test_summary_omits_contradictions_when_zero() -> None:
    """Zero count should not clutter the summary line."""
    from elfmem.types import ConsolidateResult

    r = ConsolidateResult(
        processed=2, promoted=2, deduplicated=0, edges_created=4,
    )
    assert "contradictions" not in r.summary
