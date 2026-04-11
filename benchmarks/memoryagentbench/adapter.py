"""elfmem adapter for MemoryAgentBench — document ingestion + retrieval.

Key difference from LoCoMo: MemoryAgentBench's Conflict Resolution competency
tests contradiction detection — elfmem's primary moat. For CR, we use FULL
consolidation (contradiction detection ON). For other competencies, we use
skip_llm for speed.

BM25 hybrid retrieval is handled natively by elfmem's retrieval pipeline
(stage 2b in hybrid_retrieve). No adapter-level BM25 or RRF needed.
"""

from __future__ import annotations

import logging
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import nltk

from benchmarks.memoryagentbench.config import MABenchConfig
from elfmem import ElfmemConfig, MemorySystem

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


def _context_budget_words(config: MABenchConfig) -> int:
    """Answer-context word budget derived from the model's context window.

    Subtracts fixed prompt overhead from context_window_tokens, then converts
    to words at a conservative 1.4 tokens/word (English prose skews high).

    Overhead breakdown:
      system_prompt: ~160 tokens
      QA template:    ~55 tokens
      question:       ~30 tokens
      answer_max:     config.answer_max_tokens
      safety margin:   50 tokens
    """
    overhead = 160 + 55 + 30 + config.answer_max_tokens + 50
    token_budget = config.context_window_tokens - overhead
    return max(100, int(token_budget / 1.4))


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
    skip_llm = not is_conflict_resolution

    elfmem_cfg = build_elfmem_config(config)

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    system = await MemorySystem.from_config(db_path, config=elfmem_cfg)
    try:
        # Phase 1: Ingest document via learn_document()
        start_ingest = time.monotonic()
        doc_result = await system.learn_document(
            context,
            chunk_size=config.chunk_size,
            chunker=nltk.sent_tokenize,
            source="memoryagentbench",
            skip_llm=skip_llm,
        )
        ingestion_time = time.monotonic() - start_ingest
        log.info(
            f"  Ingested {doc_result.chunks_total} chunks, "
            f"{doc_result.blocks_promoted} promoted in {ingestion_time:.1f}s"
        )

        # Phase 2: Answer each question
        qa_results: list[QAResult] = []
        for _q_idx, (question, answer_list) in enumerate(zip(questions, answers, strict=False)):
            q_start = time.monotonic()

            # elfmem's recall() includes BM25 (stage 2b) + graph expansion
            # + contradiction suppression natively.
            blocks = await system.recall(query=question, top_k=config.top_k)
            context_text = "\n\n".join(b.content for b in blocks)

            # Truncate context to fit the model's context window.
            max_context_words = _context_budget_words(config)
            words = context_text.split()
            if len(words) > max_context_words:
                context_text = " ".join(words[:max_context_words])

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
            chunks_ingested=doc_result.chunks_total,
            blocks_promoted=doc_result.blocks_promoted,
            qa_results=qa_results,
            ingestion_seconds=ingestion_time,
        )

    finally:
        await system.close()
        for suffix in ["", "-wal", "-shm"]:
            p = Path(db_path + suffix)
            if p.exists():
                p.unlink()
