"""mind operations — Theory of Mind block lifecycle.

Creates, queries, and calibrates mind (ToM) blocks. Mind blocks model
other agents' goals, beliefs, fears, motivations, and predictions.
Predictions are tracked as separate decision blocks linked via ``predicts``
edges. Outcome closure updates confidence on both the mind and decision
blocks and creates ``validates`` edges.

No LLM calls. All operations are database reads/writes.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from elfmem.db import queries
from elfmem.exceptions import BlockNotActiveError, ElfmemError
from elfmem.db.queries import insert_agent_edge
from elfmem.operations.connect import do_connect
from elfmem.operations.learn import learn as _learn
from elfmem.operations.outcome import compute_bayesian_update, record_outcome
from elfmem.types import (
    Edge,
    LearnResult,
    MindOutcomeResult,
    MindPredictResult,
    MindShowResult,
    MindSummary,
    PredictionDetail,
)


def _slugify(subject: str) -> str:
    """Convert a subject name to a tag-safe slug."""
    return re.sub(r"[^a-z0-9-]", "-", subject.strip().lower()).strip("-")


def _build_mind_content(
    subject: str,
    *,
    goals: list[str] | None = None,
    beliefs: list[str] | None = None,
    fears: list[str] | None = None,
    motivations: list[str] | None = None,
) -> str:
    """Build structured markdown content for a mind block."""
    lines = [f"# Mind Model: {subject}", ""]
    if goals:
        lines.append("## Goals")
        for g in goals:
            lines.append(f"- {g}")
        lines.append("")
    if beliefs:
        lines.append("## Beliefs")
        for b in beliefs:
            lines.append(f"- {b}")
        lines.append("")
    if fears:
        lines.append("## Fears")
        for f in fears:
            lines.append(f"- {f}")
        lines.append("")
    if motivations:
        lines.append("## Motivations")
        for m in motivations:
            lines.append(f"- {m}")
        lines.append("")
    return "\n".join(lines)


def _build_prediction_content(
    prediction: str,
    verify_at: str,
    reasoning: str | None = None,
) -> str:
    """Build content for a prediction decision block."""
    lines = [f"Prediction: {prediction}", f"Verify at: {verify_at}"]
    if reasoning:
        lines.append(f"Reasoning: {reasoning}")
    return "\n".join(lines)


def _extract_verify_at(content: str) -> str | None:
    """Extract verify_at date from prediction block content."""
    match = re.search(r"Verify at:\s*(.+)", content)
    return match.group(1).strip() if match else None


def _extract_subject(tags: list[str]) -> str:
    """Extract subject name from mind/* tags."""
    for tag in tags:
        if tag.startswith("mind/"):
            return tag[5:]
    return "unknown"


async def create_mind(
    conn: AsyncConnection,
    *,
    subject: str,
    goals: list[str] | None = None,
    beliefs: list[str] | None = None,
    fears: list[str] | None = None,
    motivations: list[str] | None = None,
) -> LearnResult:
    """Create a mind (ToM) block for a subject.

    The block is stored with category="mind" and tagged ``mind/<subject-slug>``.
    Decay tier is DURABLE (λ=0.001, ~6 month half-life).

    Returns LearnResult — reuses the standard learn pathway.
    """
    if not subject.strip():
        raise ValueError("subject must be non-empty")

    slug = _slugify(subject)
    content = _build_mind_content(
        subject, goals=goals, beliefs=beliefs, fears=fears, motivations=motivations,
    )
    tags = [f"mind/{slug}"]

    return await _learn(
        conn,
        content=content,
        tags=tags,
        category="mind",
        source="mind_create",
    )


async def predict(
    conn: AsyncConnection,
    *,
    mind_block_id: str,
    prediction: str,
    verify_at: str,
    reasoning: str | None = None,
    edge_degree_cap: int = 5,
    edge_reinforce_delta: float = 0.10,
    current_active_hours: float | None = None,
) -> MindPredictResult:
    """Add a falsifiable prediction linked to a mind block.

    Creates a decision block with the prediction content, then creates
    a ``predicts`` edge from the mind block to the decision block.

    The mind block must exist and be active.
    """
    # Validate mind block exists
    mind_block = await queries.get_block(conn, mind_block_id)
    if mind_block is None or mind_block.get("status") != "active":
        raise BlockNotActiveError(mind_block_id)
    if mind_block.get("category") != "mind":
        raise ElfmemError(
            f"Block {mind_block_id[:8]}… is not a mind block (category={mind_block.get('category')!r}).",
            recovery=f"Use a block with category='mind'. List minds with mind_list().",
        )

    # Extract subject from mind block tags
    mind_tags = await queries.get_tags(conn, mind_block_id)
    subject_slug = "unknown"
    for tag in mind_tags:
        if tag.startswith("mind/"):
            subject_slug = tag[5:]
            break

    # Create decision block for the prediction
    content = _build_prediction_content(prediction, verify_at, reasoning)
    decision_tags = [f"mind/{subject_slug}", "prediction"]
    decision_result = await _learn(
        conn,
        content=content,
        tags=decision_tags,
        category="decision",
        source="mind_predict",
    )

    # Create predicts edge: mind → decision
    # Direct insert because the decision block is in inbox (not yet active),
    # and do_connect() requires both endpoints to be active.
    from_id, to_id = Edge.canonical(mind_block_id, decision_result.block_id)
    await insert_agent_edge(
        conn,
        from_id=from_id,
        to_id=to_id,
        weight=0.70,
        relation_type="predicts",
        note=f"Prediction: {prediction[:80]}",
        current_active_hours=current_active_hours,
    )

    return MindPredictResult(
        mind_block_id=mind_block_id,
        decision_block_id=decision_result.block_id,
        prediction=prediction,
        verify_at=verify_at,
        edge_action="created",
    )


async def list_minds(conn: AsyncConnection) -> list[MindSummary]:
    """List all active mind blocks with prediction statistics."""
    mind_blocks = await queries.get_active_blocks_by_category(conn, "mind")
    summaries: list[MindSummary] = []

    for block in mind_blocks:
        block_id = block["id"]
        tags = await queries.get_tags(conn, block_id)
        subject = _extract_subject(tags)

        # Count predictions via predicts edges
        predicts_edges = await queries.get_edges_by_relation_type(
            conn, block_id, "predicts"
        )
        prediction_count = len(predicts_edges)

        # Count hits/misses by checking outcome evidence on linked decision blocks
        hit_count = 0
        miss_count = 0
        for edge in predicts_edges:
            other_id = edge["to_id"] if edge["from_id"] == block_id else edge["from_id"]
            decision_block = await queries.get_block(conn, other_id)
            if decision_block is None:
                continue
            evidence = float(decision_block.get("outcome_evidence") or 0.0)
            if evidence > 0:
                confidence = float(decision_block.get("confidence", 0.5))
                if confidence >= 0.5:
                    hit_count += 1
                else:
                    miss_count += 1

        summaries.append(MindSummary(
            block_id=block_id,
            subject=subject,
            confidence=float(block.get("confidence", 0.5)),
            prediction_count=prediction_count,
            hit_count=hit_count,
            miss_count=miss_count,
        ))

    return summaries


async def show_mind(conn: AsyncConnection, mind_block_id: str) -> MindShowResult:
    """Show a mind block with all linked predictions."""
    mind_block = await queries.get_block(conn, mind_block_id)
    if mind_block is None:
        raise ElfmemError(
            f"Block {mind_block_id[:8]}… not found.",
            recovery="Use mind_list() to find valid mind block IDs.",
        )

    tags = await queries.get_tags(conn, mind_block_id)
    subject = _extract_subject(tags)

    # Gather linked predictions
    predicts_edges = await queries.get_edges_by_relation_type(
        conn, mind_block_id, "predicts"
    )
    predictions: list[PredictionDetail] = []
    for edge in predicts_edges:
        other_id = (
            edge["to_id"] if edge["from_id"] == mind_block_id else edge["from_id"]
        )
        decision_block = await queries.get_block(conn, other_id)
        if decision_block is None:
            continue

        content = decision_block.get("content", "")
        verify_at = _extract_verify_at(content)
        evidence = float(decision_block.get("outcome_evidence") or 0.0)

        outcome: str | None = None
        if evidence > 0:
            confidence = float(decision_block.get("confidence", 0.5))
            outcome = "hit" if confidence >= 0.5 else "miss"

        predictions.append(PredictionDetail(
            block_id=other_id,
            content=content,
            confidence=float(decision_block.get("confidence", 0.5)),
            verify_at=verify_at,
            outcome=outcome,
        ))

    return MindShowResult(
        block_id=mind_block_id,
        subject=subject,
        content=mind_block.get("content", ""),
        confidence=float(mind_block.get("confidence", 0.5)),
        predictions=predictions,
    )


async def mind_outcome(
    conn: AsyncConnection,
    *,
    decision_block_id: str,
    hit: bool,
    reason: str,
    current_active_hours: float,
    prior_strength: float = 2.0,
    reinforce_threshold: float = 0.5,
    edge_reinforce_delta: float = 0.10,
    edge_degree_cap: int = 5,
) -> MindOutcomeResult:
    """Close a prediction: record hit/miss, update mind + decision confidence.

    1. Records outcome on the decision block (signal=0.9 for hit, 0.1 for miss).
    2. Finds the mind block via reverse ``predicts`` edge.
    3. Records outcome on the mind block (attenuated signal).
    4. Creates or reinforces a ``validates`` edge from decision to mind.
    """
    # Validate decision block
    decision_block = await queries.get_block(conn, decision_block_id)
    if decision_block is None or decision_block.get("status") != "active":
        raise BlockNotActiveError(decision_block_id)

    # Find linked mind block via predicts edge
    predicts_edges = await queries.get_edges_by_relation_type(
        conn, decision_block_id, "predicts"
    )
    if not predicts_edges:
        raise ElfmemError(
            f"No predicts edge found for decision {decision_block_id[:8]}…",
            recovery="This block may not be a prediction. Use outcome() for general blocks.",
        )

    edge = predicts_edges[0]
    mind_block_id = (
        edge["from_id"] if edge["to_id"] == decision_block_id else edge["to_id"]
    )

    mind_block = await queries.get_block(conn, mind_block_id)
    if mind_block is None or mind_block.get("status") != "active":
        raise ElfmemError(
            f"Mind block {mind_block_id[:8]}… is not active.",
            recovery="The linked mind block may have been archived.",
        )

    # Signal: 0.9 for hit, 0.1 for miss
    signal = 0.9 if hit else 0.1

    # 1. Record outcome on decision block
    decision_result = await record_outcome(
        conn,
        block_ids=[decision_block_id],
        signal=signal,
        weight=1.0,
        source=f"mind_outcome:{'hit' if hit else 'miss'}:{reason[:50]}",
        current_active_hours=current_active_hours,
        prior_strength=prior_strength,
        reinforce_threshold=reinforce_threshold,
        edge_reinforce_delta=edge_reinforce_delta,
    )

    # 2. Record attenuated outcome on mind block (signal scaled by 0.5)
    mind_signal = 0.5 + (signal - 0.5) * 0.5  # 0.7 for hit, 0.3 for miss
    mind_result = await record_outcome(
        conn,
        block_ids=[mind_block_id],
        signal=mind_signal,
        weight=0.5,  # Lower weight — one prediction doesn't define the whole model
        source=f"mind_calibration:{'hit' if hit else 'miss'}:{reason[:50]}",
        current_active_hours=current_active_hours,
        prior_strength=prior_strength,
        reinforce_threshold=reinforce_threshold,
        edge_reinforce_delta=edge_reinforce_delta,
    )

    # 3. Create validates edge: decision → mind
    validates_result = await do_connect(
        conn,
        source=decision_block_id,
        target=mind_block_id,
        relation="validates",
        weight=signal * 0.75,
        note=f"{'Hit' if hit else 'Miss'}: {reason[:80]}",
        if_exists="reinforce",
        edge_degree_cap=edge_degree_cap,
        edge_reinforce_delta=edge_reinforce_delta,
        current_active_hours=current_active_hours,
    )

    return MindOutcomeResult(
        mind_block_id=mind_block_id,
        decision_block_id=decision_block_id,
        hit=hit,
        reason=reason,
        mind_confidence_delta=mind_result.mean_confidence_delta,
        decision_confidence_delta=decision_result.mean_confidence_delta,
        validates_edge_action=validates_result.action,
    )
