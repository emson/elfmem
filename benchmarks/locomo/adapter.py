"""elfmem adapter for LoCoMo — session replay + per-question retrieval."""

from __future__ import annotations

import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from rank_bm25 import BM25Okapi

from elfmem import ElfmemConfig, MemorySystem

from benchmarks.locomo.config import LoCoMoConfig
from benchmarks.locomo.data import Conversation, QAPair


@dataclass
class QAResult:
    """Per-question retrieval result — no scoring, no answer generation yet.

    USE WHEN: Capturing what elfmem retrieved for a single question.
    DON'T USE WHEN: You need scored/answered results — that's the runner's job.
    COST: Zero (dataclass).
    RETURNS: Retrieval context + ground truth for downstream scoring.
    NEXT: Pass to answerer for prediction, then to report for scoring.
    """

    question: str
    category: int
    evidence_ids: list[str]
    retrieved_dia_ids: list[str]
    frame_context: str
    adversarial_answer: str | None
    ground_truth: str | None
    query_seconds: float


@dataclass
class ConversationResult:
    """Return type from process_conversation — what runner needs.

    USE WHEN: Collecting results from a full conversation replay.
    DON'T USE WHEN: Single-question retrieval — use QAResult directly.
    COST: Zero (dataclass).
    RETURNS: Ingestion stats + per-question retrieval results.
    NEXT: Pass qa_results to answerer, then to report builder.
    """

    sample_id: str
    sessions_ingested: int
    turns_ingested: int
    blocks_consolidated: int
    qa_results: list[QAResult]


def build_elfmem_config(config: LoCoMoConfig) -> ElfmemConfig:
    """Build ElfmemConfig from benchmark config.

    USE WHEN: Creating a fresh MemorySystem for a conversation.
    DON'T USE WHEN: You already have an ElfmemConfig instance.
    COST: Zero.
    RETURNS: ElfmemConfig ready for MemorySystem.from_config().
    NEXT: Pass to MemorySystem.from_config().
    """
    return ElfmemConfig.model_validate({
        "llm": {
            "model": config.elfmem_llm_model,
            "base_url": config.lm_studio_base_url,
            "temperature": 0.0,
            "max_tokens": 512,
            "timeout": 120,
        },
        "embeddings": {
            "model": config.elfmem_embedding_model,
            "base_url": config.lm_studio_base_url,
            "dimensions": config.elfmem_embedding_dimensions,
            "timeout": 60,
        },
        "memory": {
            "inbox_threshold": config.inbox_threshold,
            "top_k": config.top_k,
            "search_window_hours": config.search_window_hours,
            "curate_interval_hours": 1000.0,
            "contradiction_similarity_prefilter": config.contradiction_similarity_prefilter,
        },
    })


def _extract_dia_ids(blocks: list) -> list[str]:
    """Extract dialog IDs from ScoredBlock tags."""
    dia_ids: list[str] = []
    for block in blocks:
        for tag in block.tags:
            if tag.startswith("dia:"):
                dia_ids.append(tag[4:])
    return dia_ids


async def _ingest_conversation(
    system: MemorySystem,
    conversation: Conversation,
) -> tuple[int, int, int, _BM25Index]:
    """Replay all sessions via learn() + consolidate().

    Returns (sessions_ingested, turns_ingested, blocks_consolidated, bm25_index).
    The BM25 index is built alongside elfmem's vector index for hybrid retrieval.
    """
    total_turns = 0
    total_consolidated = 0
    bm25_index = _BM25Index()

    for session in conversation.sessions:
        await system.begin_session(task_type="ingestion")
        for turn in session.turns:
            content = f"[{session.date_time}] {turn.speaker}: {turn.text}"
            await system.learn(
                content=content,
                tags=[
                    f"dia:{turn.dia_id}",
                    f"session:{session.session_num}",
                    f"speaker:{turn.speaker}",
                ],
                category="conversation",
                source="locomo",
            )
            # Index third-person observation for BM25 (resolves pronoun mismatch)
            observation = _to_observation(turn.speaker, turn.text, session.date_time)
            bm25_index.add(observation, turn.dia_id)
            total_turns += 1
        await system.end_session()

        await system.begin_session(task_type="consolidation")
        result = await system.consolidate(skip_llm=True)
        total_consolidated += result.promoted
        await system.end_session()

    bm25_index.build()
    return len(conversation.sessions), total_turns, total_consolidated, bm25_index


def _to_observation(speaker: str, text: str, date_time: str) -> str:
    """Convert a first-person dialog turn into a third-person observation.

    Resolves the pronoun mismatch that kills both BM25 and vector retrieval:
    Q: "When did Caroline go to the LGBTQ support group?"
    Raw: "I went to an LGBTQ support group recently"  ← no "Caroline"
    Obs: "Caroline went to an LGBTQ support group recently"  ← matches!

    Simple rule-based transform — no LLM needed.
    """
    obs = text
    # Replace first-person pronouns with speaker name (case-insensitive, word boundary)
    import re
    obs = re.sub(r"\bI\b", speaker, obs)
    obs = re.sub(r"\bI'm\b", f"{speaker} is", obs, flags=re.IGNORECASE)
    obs = re.sub(r"\bI've\b", f"{speaker} has", obs, flags=re.IGNORECASE)
    obs = re.sub(r"\bI'd\b", f"{speaker} would", obs, flags=re.IGNORECASE)
    obs = re.sub(r"\bI'll\b", f"{speaker} will", obs, flags=re.IGNORECASE)
    obs = re.sub(r"\bmy\b", f"{speaker}'s", obs, flags=re.IGNORECASE)
    obs = re.sub(r"\bmyself\b", f"{speaker}", obs, flags=re.IGNORECASE)
    obs = re.sub(r"\bme\b", speaker, obs, flags=re.IGNORECASE)
    return f"{obs} ({date_time})"


class _BM25Index:
    """Lightweight BM25 index over conversation observations for keyword retrieval.

    Indexes third-person OBSERVATIONS (not raw dialog) to resolve the pronoun
    mismatch between questions ("Caroline") and dialog ("I"). Also addresses
    the lexical matching gap for exact keyword overlap.
    """

    def __init__(self) -> None:
        self._contents: list[str] = []
        self._dia_ids: list[str] = []
        self._bm25: BM25Okapi | None = None

    def add(self, content: str, dia_id: str) -> None:
        self._contents.append(content)
        self._dia_ids.append(dia_id)

    def build(self) -> None:
        tokenized = [c.lower().split() for c in self._contents]
        self._bm25 = BM25Okapi(tokenized)

    def search(self, query: str, top_k: int = 30) -> list[tuple[str, str, float]]:
        """Search for turns matching the query.

        Returns list of (dia_id, content, score) sorted by score descending.
        """
        if self._bm25 is None:
            return []
        scores = self._bm25.get_scores(query.lower().split())
        ranked = sorted(
            zip(self._dia_ids, self._contents, scores),
            key=lambda x: x[2],
            reverse=True,
        )
        return ranked[:top_k]


def _rrf_merge(
    vector_blocks: list,
    bm25_results: list[tuple[str, str, float]],
    top_k: int,
    k: int = 60,
) -> tuple[list, str]:
    """Merge vector search and BM25 results using Reciprocal Rank Fusion.

    Strategy: vector results form the primary candidate set. BM25 results
    BOOST blocks already in the set (reranking) and ADD a limited number
    of new blocks as supplementary context (appended after vector results).
    This prevents common keywords (e.g., speaker names) from overwhelming
    semantically relevant results.

    Returns (merged_blocks, rendered_context).
    """
    # Build RRF scores — vector results start with their rank score
    block_scores: dict[str, float] = {}
    block_map: dict[str, object] = {}
    for rank, block in enumerate(vector_blocks):
        block_scores[block.id] = 1.0 / (k + rank)
        block_map[block.id] = block

    # Build dia_id → block_id mapping from vector results
    dia_to_block: dict[str, str] = {}
    for block in vector_blocks:
        for tag in block.tags:
            if tag.startswith("dia:"):
                dia_to_block[tag[4:]] = block.id

    # BM25 boosts existing blocks; new BM25-only blocks go to supplementary list
    supplementary: list[str] = []
    for rank, (dia_id, content, _score) in enumerate(bm25_results):
        bm25_rrf = 1.0 / (k + rank)
        block_id = dia_to_block.get(dia_id)
        if block_id and block_id in block_scores:
            # Found by both — boost the vector result
            block_scores[block_id] += bm25_rrf
        elif len(supplementary) < 5:
            # BM25-only — add as supplementary context (limited to top 5)
            supplementary.append(content)

    # Sort vector blocks by boosted RRF score
    ranked_blocks = sorted(block_map.values(), key=lambda b: block_scores[b.id], reverse=True)
    trimmed = ranked_blocks[:top_k]

    # Build context: reranked vector blocks + BM25 supplementary
    context_lines = [b.content for b in trimmed]
    context_lines.extend(supplementary)

    return trimmed, "\n\n".join(context_lines)


async def _retrieve_for_qa(
    system: MemorySystem,
    qa: QAPair,
    top_k: int,
    bm25_index: _BM25Index | None = None,
) -> QAResult:
    """Run hybrid retrieval: vector search (elfmem) + BM25 (keyword), merged via RRF.

    This addresses the core retrieval gap: questions and evidence blocks share
    exact keywords that pure vector search misses, while vector search catches
    semantic matches that keywords miss. RRF fusion combines both signals.
    """
    start = time.monotonic()

    await system.begin_session(task_type="retrieval")
    frame_result = await system.frame("attention", query=qa.question, top_k=top_k)
    await system.end_session()

    # Hybrid merge with BM25 if index available.
    # Skip BM25 supplementary for adversarial (cat 5) — adding context makes
    # the model hallucinate answers for things NOT in the conversation.
    context = frame_result.text
    blocks = frame_result.blocks
    if bm25_index is not None and qa.category != 5:
        bm25_hits = bm25_index.search(qa.question, top_k=top_k)
        blocks, context = _rrf_merge(frame_result.blocks, bm25_hits, top_k)

    elapsed = time.monotonic() - start
    retrieved_dia_ids = _extract_dia_ids(blocks)

    return QAResult(
        question=qa.question,
        category=qa.category,
        evidence_ids=qa.evidence,
        retrieved_dia_ids=retrieved_dia_ids,
        frame_context=context,
        adversarial_answer=qa.adversarial_answer,
        ground_truth=qa.answer,
        query_seconds=elapsed,
    )


async def process_conversation(
    conversation: Conversation,
    config: LoCoMoConfig,
) -> ConversationResult:
    """Ingest a full conversation into elfmem and retrieve for all QA pairs.

    USE WHEN: Processing one LoCoMo conversation end-to-end.
    DON'T USE WHEN: You only need retrieval (call _retrieve_for_qa directly).
    COST: LLM calls for consolidation + embedding calls for retrieval.
    RETURNS: ConversationResult with ingestion stats and per-question results.
    NEXT: Pass to runner for answer generation and scoring.
    """
    elfmem_cfg = build_elfmem_config(config)

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    system = await MemorySystem.from_config(db_path, config=elfmem_cfg)
    try:
        sessions_ingested, turns_ingested, blocks_consolidated, bm25_index = (
            await _ingest_conversation(system, conversation)
        )

        qa_pairs = conversation.qa_pairs
        if config.categories:
            qa_pairs = [q for q in qa_pairs if q.category in config.categories]
        if config.max_qa_per_conversation:
            qa_pairs = qa_pairs[: config.max_qa_per_conversation]

        qa_results = [
            await _retrieve_for_qa(system, qa, config.top_k_retrieval, bm25_index)
            for qa in qa_pairs
        ]

        return ConversationResult(
            sample_id=conversation.sample_id,
            sessions_ingested=sessions_ingested,
            turns_ingested=turns_ingested,
            blocks_consolidated=blocks_consolidated,
            qa_results=qa_results,
        )
    finally:
        await system.close()
        for suffix in ["", "-wal", "-shm"]:
            p = Path(db_path + suffix)
            if p.exists():
                p.unlink()
