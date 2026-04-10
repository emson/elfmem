"""elfmem adapter for MemoryAgentBench — chunk ingestion + retrieval.

Key difference from LoCoMo: MemoryAgentBench's Conflict Resolution competency
tests contradiction detection — elfmem's primary moat. For CR, we use FULL
consolidation (contradiction detection ON). For other competencies, we use
skip_contradictions for speed.
"""

from __future__ import annotations

import logging
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import nltk

from rank_bm25 import BM25Okapi

from elfmem import ElfmemConfig, MemorySystem

from benchmarks.memoryagentbench.config import MABenchConfig

nltk.download("punkt_tab", quiet=True)

log = logging.getLogger(__name__)


@dataclass
class QAResult:
    """Per-question retrieval result."""

    question: str
    ground_truths: list[str]
    retrieved_context: str
    query_seconds: float


@dataclass
class ExampleResult:
    """Result from processing one MABench example."""

    source: str
    competency: str
    chunks_ingested: int
    blocks_promoted: int
    qa_results: list[QAResult]
    ingestion_seconds: float


class _BM25Index:
    """BM25 index over ingested chunks for keyword retrieval."""

    def __init__(self) -> None:
        self._contents: list[str] = []
        self._bm25: BM25Okapi | None = None

    def add(self, content: str) -> None:
        self._contents.append(content)

    def build(self) -> None:
        if self._contents:
            tokenized = [c.lower().split() for c in self._contents]
            self._bm25 = BM25Okapi(tokenized)

    def search(self, query: str, top_k: int = 10) -> list[tuple[str, float]]:
        if self._bm25 is None:
            return []
        scores = self._bm25.get_scores(query.lower().split())
        ranked = sorted(
            zip(self._contents, scores), key=lambda x: x[1], reverse=True,
        )
        return ranked[:top_k]


def chunk_text(text: str, chunk_size: int = 1024) -> list[str]:
    """Split text into sentence-aligned chunks of ~chunk_size words.

    Uses NLTK sentence tokenization for clean boundaries.
    """
    sentences = nltk.sent_tokenize(text)
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for sentence in sentences:
        sent_len = len(sentence.split())
        if current_len + sent_len > chunk_size and current:
            chunks.append(" ".join(current))
            current = []
            current_len = 0
        current.append(sentence)
        current_len += sent_len

    if current:
        chunks.append(" ".join(current))
    return chunks


def build_elfmem_config(config: MABenchConfig) -> ElfmemConfig:
    """Build ElfmemConfig from benchmark config."""
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


def _rrf_merge(
    vector_blocks: list, bm25_results: list[tuple[str, float]], top_k: int, k: int = 60,
) -> tuple[list, str]:
    """Merge vector search and BM25 results via Reciprocal Rank Fusion."""
    block_scores: dict[str, float] = {}
    block_map: dict[str, object] = {}
    for rank, block in enumerate(vector_blocks):
        block_scores[block.id] = 1.0 / (k + rank)
        block_map[block.id] = block

    supplementary: list[str] = []
    for rank, (content, _score) in enumerate(bm25_results):
        bm25_rrf = 1.0 / (k + rank)
        # Check if any vector block contains this BM25 content (approximate match)
        matched = False
        for bid, block in block_map.items():
            if content[:50] in block.content:
                block_scores[bid] += bm25_rrf
                matched = True
                break
        if not matched and len(supplementary) < 5:
            supplementary.append(content)

    ranked = sorted(block_map.values(), key=lambda b: block_scores[b.id], reverse=True)
    trimmed = ranked[:top_k]
    context_lines = [b.content for b in trimmed]
    context_lines.extend(supplementary)
    return trimmed, "\n\n".join(context_lines)


async def process_example(
    example: dict[str, Any],
    competency: str,
    config: MABenchConfig,
) -> ExampleResult:
    """Ingest one MABench example and answer all its questions.

    For Conflict Resolution: uses full consolidation (contradiction detection ON).
    For other competencies: uses skip_llm for speed.
    """
    context = example["context"]
    questions = example["questions"]
    answers = example["answers"]
    metadata = example.get("metadata", {})
    source = metadata.get("source", "unknown") if isinstance(metadata, dict) else "unknown"

    # Conflict Resolution needs contradiction detection — elfmem's moat
    is_conflict_resolution = competency == "Conflict_Resolution"

    elfmem_cfg = build_elfmem_config(config)

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    system = await MemorySystem.from_config(db_path, config=elfmem_cfg)
    try:
        # Phase 1: Chunk and ingest context
        start_ingest = time.monotonic()
        chunks = chunk_text(context, config.chunk_size)
        bm25_index = _BM25Index()
        total_promoted = 0

        await system.begin_session(task_type="ingestion")
        for i, chunk in enumerate(chunks):
            await system.learn(
                content=chunk,
                tags=[f"chunk:{i}"],
                category="knowledge",
                source="memoryagentbench",
            )
            bm25_index.add(chunk)

            # Consolidate periodically
            if (i + 1) % config.consolidate_every_n_chunks == 0:
                await system.end_session()
                await system.begin_session(task_type="consolidation")
                if is_conflict_resolution:
                    result = await system.consolidate()  # FULL — contradiction detection ON
                else:
                    result = await system.consolidate(skip_llm=True)
                total_promoted += result.promoted
                await system.end_session()
                await system.begin_session(task_type="ingestion")
        await system.end_session()

        # Final consolidation for remaining chunks
        await system.begin_session(task_type="consolidation")
        if is_conflict_resolution:
            result = await system.consolidate()
        else:
            result = await system.consolidate(skip_llm=True)
        total_promoted += result.promoted
        await system.end_session()

        bm25_index.build()
        ingestion_time = time.monotonic() - start_ingest
        log.info(f"  Ingested {len(chunks)} chunks, {total_promoted} promoted in {ingestion_time:.1f}s")

        # Phase 2: Answer each question
        qa_results: list[QAResult] = []
        for q_idx, (question, answer_list) in enumerate(zip(questions, answers)):
            q_start = time.monotonic()

            await system.begin_session(task_type="retrieval")
            frame_result = await system.frame("attention", query=question, top_k=config.top_k)
            await system.end_session()

            # BM25 hybrid merge
            blocks = frame_result.blocks
            context_text = frame_result.text
            bm25_hits = bm25_index.search(question, top_k=config.top_k)
            if bm25_hits:
                blocks, context_text = _rrf_merge(blocks, bm25_hits, config.top_k)

            # Ensure answer_list is a list of strings
            if isinstance(answer_list, str):
                gt_list = [answer_list]
            elif isinstance(answer_list, list):
                gt_list = [str(a) for a in answer_list]
            else:
                gt_list = [str(answer_list)]

            qa_results.append(QAResult(
                question=question,
                ground_truths=gt_list,
                retrieved_context=context_text,
                query_seconds=time.monotonic() - q_start,
            ))

        return ExampleResult(
            source=source,
            competency=competency,
            chunks_ingested=len(chunks),
            blocks_promoted=total_promoted,
            qa_results=qa_results,
            ingestion_seconds=ingestion_time,
        )

    finally:
        await system.close()
        for suffix in ["", "-wal", "-shm"]:
            p = Path(db_path + suffix)
            if p.exists():
                p.unlink()
