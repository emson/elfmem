# Evaluating elfmem with MemoryAgentBench

A step-by-step guide to benchmarking elfmem against
[MemoryAgentBench](https://github.com/HUST-AI-HYZ/MemoryAgentBench) (ICLR 2026).

**Why this benchmark?** MemoryAgentBench tests the capabilities that are elfmem's
competitive moat: contradiction detection, knowledge consolidation, selective
forgetting, and test-time learning. Existing systems (Cognee, Letta, Mem0) drop
to **7% accuracy** on multi-hop contradictions — precisely the gap elfmem was
designed to fill.

**Report format:** Output conforms to the
[Benchmark Report Spec](benchmark_report_spec.md) for cross-benchmark comparison.
Includes mandatory baselines and retrieval tracking.

---

## Table of Contents

1. [What MemoryAgentBench Tests](#1-what-memoryagentbench-tests)
2. [How elfmem Maps to the Four Competencies](#2-how-elfmem-maps-to-the-four-competencies)
3. [Setup](#3-setup)
4. [Data Format](#4-data-format)
5. [Architecture: Integration Approach](#5-architecture-integration-approach)
6. [Implementation](#6-implementation)
7. [Running the Evaluation](#7-running-the-evaluation)
8. [Configuration Tuning](#8-configuration-tuning)
9. [Performance & Time Estimates](#9-performance--time-estimates)
10. [Edge Cases & Mitigations](#10-edge-cases--mitigations)
11. [Interpreting Results](#11-interpreting-results)

---

## 1. What MemoryAgentBench Tests

Four core competencies, each with purpose-built datasets:

| Competency | Datasets | What It Tests |
|---|---|---|
| **Accurate Retrieval (AR)** | EventQA, LongMemEval, Ruler QA | Find specific facts in 64K-421K token contexts |
| **Test-Time Learning (TTL)** | ICL (banking77, clinic150, etc.), Recsys | Learn patterns from examples, apply to new cases |
| **Long-Range Understanding (LRU)** | DetectiveQA, InfBench Summarization | Synthesize information across very long documents |
| **Conflict Resolution (CR)** | FactConsolidation (single/multi-hop, 6K-262K) | Resolve contradictory facts — newer fact wins |

**Total:** 146 examples across all splits, but each example has a long context
(6K-800K tokens) with multiple questions. The evaluation is substantial.

**Evaluation:** F1, exact match, substring match, ROUGE-L. Results saved per-query
with aggregated metrics.

### Why Conflict Resolution Matters Most for elfmem

The FactConsolidation dataset assigns **serial numbers** to facts. Later facts with
higher serial numbers contradict earlier ones. The system must prefer the newer fact.

This maps directly to elfmem's:
- `consolidate()` — contradiction detection via LLM
- `curate()` — decay-based archival (old contradicted facts decay faster)
- `outcome()` — penalty signals accelerate decay of wrong knowledge

Other systems score ~7% on multi-hop contradiction scenarios. elfmem should do
significantly better because contradiction handling is a first-class feature.

---

## 2. How elfmem Maps to the Four Competencies

| Competency | elfmem Feature | Expected Advantage |
|---|---|---|
| Accurate Retrieval | 5-stage hybrid pipeline + MMR diversity | Graph expansion finds structurally related context |
| Test-Time Learning | `learn()` → `consolidate()` cycle | Tag inference + confidence calibration |
| Long-Range Understanding | Frame assembly + token budget rendering | Composable context with diversity |
| **Conflict Resolution** | **Contradiction detection + decay + penalty** | **The moat — no other system does this natively** |

---

## 3. Setup

### 3.1 Clone MemoryAgentBench

```bash
cd /Users/emson/Dropbox/devel/projects/ai
git clone https://github.com/HUST-AI-HYZ/MemoryAgentBench.git
cd MemoryAgentBench
```

### 3.2 Dependencies

MemoryAgentBench has heavy dependencies (PyTorch, CUDA, transformers, etc.) designed
for GPU servers. We do NOT need most of these — elfmem replaces the retrieval and
memory systems, and we use local Gemma via LM Studio for generation.

**Minimal dependencies** (just what we need for data loading + evaluation):

```bash
# From the elfmem project root
cd /Users/emson/Dropbox/devel/projects/ai/elf0_mem_sim

pip install tiktoken rouge-score nltk tqdm datasets
```

We will **not** install the full `requirements.txt` — it pulls in CUDA packages,
faiss-gpu, and vendored copies of cognee/letta/mem0 that we don't need.

### 3.3 Download Data

The dataset is on HuggingFace: `ai-hyz/MemoryAgentBench`

```bash
python3 -c "
from datasets import load_dataset
ds = load_dataset('ai-hyz/MemoryAgentBench')
print('Splits:', list(ds.keys()))
for split, data in ds.items():
    print(f'  {split}: {len(data)} examples')
"
```

Expected output:
```
Splits: ['Accurate_Retrieval', 'Test_Time_Learning', 'Long_Range_Understanding', 'Conflict_Resolution']
  Accurate_Retrieval: 22 examples
  Test_Time_Learning: 6 examples
  Long_Range_Understanding: 110 examples
  Conflict_Resolution: 8 examples
```

### 3.4 LM Studio

Ensure LM Studio is running with both models loaded:
- `google/gemma-4-26b-a4b` (LLM for consolidation + answer generation)
- `text-embedding-nomic-embed-text-v1.5` (768d embeddings)

```bash
# Verify
curl -s http://localhost:1234/v1/models | python3 -m json.tool
```

### 3.5 API Keys

```bash
# Only needed for LLM-based evaluation (optional — F1/EM don't need it)
export OPENAI_API_KEY="sk-..."   # For GPT-4o judge on LongMemEval subset
```

---

## 4. Data Format

Each example in the dataset has this structure:

```json
{
  "context": "A very long text (6K-800K tokens)...",
  "questions": ["What happened after X?", "When did Y occur?", ...],
  "answers": [["answer1a", "answer1b"], ["answer2"], ...],
  "metadata": {
    "source": "factconsolidation_mh_32k",
    "qa_pair_ids": ["id1", "id2", ...],
    "question_dates": ["2023/04/10", ...],
    "question_types": ["temporal", ...],
    "question_ids": ["q1", "q2", ...],
    "previous_events": ["...", ...],
    "keypoints": ["...", ...],
    "demo": "few-shot example text"
  }
}
```

**Key design:** "inject once, query multiple times." Each example has ONE long
`context` that gets chunked and fed incrementally to the memory system, then ALL
`questions` are asked sequentially.

### FactConsolidation Format (Conflict Resolution)

The context contains facts with serial numbers:

```
Fact #1: The capital of France is Paris.
Fact #2: The capital of France is Lyon.
```

Questions ask about the latest fact. The system must return "Lyon" (Fact #2 supersedes
Fact #1). Multi-hop variants chain contradictions across related facts.

Sizes: 6K, 32K, 64K, 262K tokens — from trivial to stress-test.

---

## 5. Architecture: Integration Approach

### Two Integration Paths

**Path A: Standalone adapter (recommended)**

Write our own runner script that:
1. Loads the HuggingFace dataset directly
2. Chunks context and feeds to elfmem (`learn()` → `consolidate()`)
3. Queries elfmem (`frame("attention", question)`) per question
4. Generates answers via local Gemma
5. Computes metrics (F1, EM) ourselves
6. Outputs results in MemoryAgentBench's format for comparison

This avoids the heavyweight dependencies and GPU requirements of the original repo.

**Path B: Integrate into MemoryAgentBench's `agent.py`**

Modify their codebase to add elfmem as a new agent type. This gives direct
comparison against their baselines but requires dealing with their dependency
hell and the fact that the codebase is not async.

**We choose Path A** — standalone adapter with compatible output format.

### Flow

```
┌─────────────────────────────────────────────────────────────┐
│  For each example in dataset:                               │
│                                                             │
│  1. Create fresh elfmem DB                                  │
│  2. Chunk context into sentences (NLTK, ~4096 tokens each)  │
│  3. Feed chunks sequentially: learn() each chunk            │
│  4. Consolidate after each batch of chunks                  │
│  5. For each question:                                      │
│     a. frame("attention", question) → retrieved context     │
│     b. Gemma generates answer from context + question       │
│     c. Compute F1, EM against ground truth                  │
│  6. Write results JSON                                      │
│                                                             │
│  Output: results JSON per dataset (compatible format)       │
└─────────────────────────────────────────────────────────────┘
```

---

## 6. Implementation

### 6.1 Project Structure

```
elf0_mem_sim/
├── benchmarks/
│   ├── longmemeval/          # (existing)
│   └── memoryagentbench/
│       ├── __init__.py
│       ├── runner.py          # Main benchmark runner
│       ├── adapter.py         # elfmem ↔ MemoryAgentBench adapter
│       ├── answerer.py        # LLM answer generation (reuse from longmemeval)
│       ├── metrics.py         # F1, EM, ROUGE computation
│       ├── config.py          # Benchmark configuration
│       └── results/
│           └── .gitkeep
```

### 6.2 Metrics (`metrics.py`)

Matching MemoryAgentBench's evaluation exactly:

```python
"""Metrics matching MemoryAgentBench's evaluation (eval_other_utils.py)."""

from __future__ import annotations

import re
import string
from collections import Counter

from rouge_score import rouge_scorer


def normalize_answer(s: str) -> str:
    """Lower, remove punctuation/articles/whitespace."""
    s = s.lower()
    s = re.sub(r"\b(a|an|the|and)\b", " ", s)
    exclude = set(string.punctuation)
    s = "".join(ch for ch in s if ch not in exclude)
    return " ".join(s.split())


def f1_score(prediction: str, ground_truth: str) -> float:
    """Token-level F1 with normalization."""
    pred_tokens = normalize_answer(prediction).split()
    gt_tokens = normalize_answer(ground_truth).split()
    if not pred_tokens or not gt_tokens:
        return float(pred_tokens == gt_tokens)
    common = Counter(pred_tokens) & Counter(gt_tokens)
    num_same = sum(common.values())
    if num_same == 0:
        return 0.0
    precision = num_same / len(pred_tokens)
    recall = num_same / len(gt_tokens)
    return (2 * precision * recall) / (precision + recall)


def exact_match(prediction: str, ground_truth: str) -> float:
    """Normalized exact match."""
    return float(normalize_answer(prediction) == normalize_answer(ground_truth))


def substring_exact_match(prediction: str, ground_truth: str) -> float:
    """Ground truth appears as substring in prediction."""
    return float(normalize_answer(ground_truth) in normalize_answer(prediction))


def rouge_l(prediction: str, ground_truth: str) -> dict[str, float]:
    """ROUGE-L F1 and recall."""
    scorer = rouge_scorer.RougeScorer(["rougeL", "rougeLsum"], use_stemmer=True)
    scores = scorer.score(ground_truth, prediction)
    return {
        "rougeL_f1": scores["rougeL"].fmeasure,
        "rougeL_recall": scores["rougeL"].recall,
        "rougeLsum_f1": scores["rougeLsum"].fmeasure,
        "rougeLsum_recall": scores["rougeLsum"].recall,
    }


def compute_all_metrics(prediction: str, ground_truths: list[str]) -> dict[str, float]:
    """Compute all metrics, taking best score across multiple ground truths."""
    best = {
        "f1": 0.0, "exact_match": 0.0, "substring_exact_match": 0.0,
        "rougeL_f1": 0.0, "rougeL_recall": 0.0,
        "rougeLsum_f1": 0.0, "rougeLsum_recall": 0.0,
    }
    for gt in ground_truths:
        best["f1"] = max(best["f1"], f1_score(prediction, gt))
        best["exact_match"] = max(best["exact_match"], exact_match(prediction, gt))
        best["substring_exact_match"] = max(
            best["substring_exact_match"], substring_exact_match(prediction, gt)
        )
        r = rouge_l(prediction, gt)
        for k, v in r.items():
            best[k] = max(best[k], v)
    return best
```

### 6.3 Configuration (`config.py`)

```python
"""Benchmark configuration for MemoryAgentBench."""

from dataclasses import dataclass
from pathlib import Path


@dataclass
class MABenchConfig:
    """Configuration for the MemoryAgentBench benchmark run."""

    # Output
    output_dir: Path = Path("benchmarks/memoryagentbench/results")

    # Which competencies to run (None = all)
    competencies: list[str] | None = None  # e.g. ["Conflict_Resolution"]

    # Which sub-datasets to run (None = all in selected competencies)
    sources: list[str] | None = None  # e.g. ["factconsolidation_mh_32k"]

    # LM Studio settings
    lm_studio_base_url: str = "http://localhost:1234/v1"

    # elfmem settings
    elfmem_llm_model: str = "google/gemma-4-26b-a4b"
    elfmem_embedding_model: str = "text-embedding-nomic-embed-text-v1.5"
    elfmem_embedding_dimensions: int = 768
    chunk_size: int = 4096          # tokens per chunk (matches MABench default)
    top_k: int = 10                 # retrieval top-k
    inbox_threshold: int = 50
    consolidate_after_n_chunks: int = 10  # consolidate every N chunks
    search_window_hours: float = 10000.0
    contradiction_similarity_prefilter: float = 0.65

    # Answer generation
    answer_model: str = "google/gemma-4-26b-a4b"
    answer_max_tokens: int = 300

    # Execution
    max_examples: int | None = None  # None = all; set small for testing
    verbose: bool = False
```

### 6.4 The Adapter (`adapter.py`)

```python
"""elfmem adapter for MemoryAgentBench — chunk ingestion + retrieval per example."""

from __future__ import annotations

import tempfile
import time
from pathlib import Path
from typing import Any

import nltk

from elfmem import ElfmemConfig, MemorySystem
from elfmem.types import FrameResult

nltk.download("punkt_tab", quiet=True)


def chunk_text(text: str, chunk_size: int = 4096) -> list[str]:
    """Split text into sentence-aligned chunks of approximately chunk_size tokens.

    Matches MemoryAgentBench's chunking strategy (NLTK sentence tokenization,
    grouped by approximate token count).
    """
    sentences = nltk.sent_tokenize(text)
    chunks: list[str] = []
    current_chunk: list[str] = []
    current_len = 0

    for sentence in sentences:
        # Approximate token count (words ≈ 0.75 tokens, but close enough)
        sent_len = len(sentence.split())
        if current_len + sent_len > chunk_size and current_chunk:
            chunks.append(" ".join(current_chunk))
            current_chunk = []
            current_len = 0
        current_chunk.append(sentence)
        current_len += sent_len

    if current_chunk:
        chunks.append(" ".join(current_chunk))

    return chunks


async def process_example(
    example: dict[str, Any],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    """Ingest context chunks and answer all questions for one example.

    Args:
        example: One MemoryAgentBench example with context, questions, answers.
        config: Benchmark configuration dict.

    Returns:
        List of per-question result dicts with predictions and timing.
    """
    context = example["context"]
    questions = example["questions"]
    answers = example["answers"]
    metadata = example.get("metadata", {})

    base_url = config.get("lm_studio_base_url", "http://localhost:1234/v1")

    # --- Build elfmem config ---
    elfmem_cfg = ElfmemConfig.model_validate({
        "llm": {
            "model": config.get("elfmem_llm_model", "google/gemma-4-26b-a4b"),
            "base_url": base_url,
            "temperature": 0.0,
            "max_tokens": 512,
            "timeout": 120,
        },
        "embeddings": {
            "model": config.get("elfmem_embedding_model", "text-embedding-nomic-embed-text-v1.5"),
            "base_url": base_url,
            "dimensions": config.get("elfmem_embedding_dimensions", 768),
            "timeout": 60,
        },
        "memory": {
            "inbox_threshold": config.get("inbox_threshold", 50),
            "curate_interval_hours": 1000.0,
            "top_k": config.get("top_k", 10),
            "search_window_hours": config.get("search_window_hours", 10000.0),
            "contradiction_similarity_prefilter": config.get(
                "contradiction_similarity_prefilter", 0.65
            ),
        },
    })

    # --- Temp DB per example ---
    with tempfile.NamedTemporaryFile(suffix=".db", delete=True) as tmp:
        db_path = tmp.name

    system = await MemorySystem.from_config(db_path, config=elfmem_cfg)

    try:
        # --- Phase 1: Chunk and ingest context ---
        chunk_size = config.get("chunk_size", 4096)
        chunks = chunk_text(context, chunk_size)
        consolidate_every = config.get("consolidate_after_n_chunks", 10)

        memorize_start = time.monotonic()

        await system.begin_session(task_type="ingestion")

        for i, chunk_text_content in enumerate(chunks):
            await system.learn(
                content=chunk_text_content,
                tags=[f"chunk:{i}"],
                category="knowledge",
                source="memoryagentbench",
            )

            # Consolidate periodically
            if (i + 1) % consolidate_every == 0:
                await system.consolidate()

        # Final consolidation for remaining inbox
        await system.consolidate()
        await system.end_session()

        memorize_time = time.monotonic() - memorize_start

        # --- Phase 2: Answer each question ---
        results: list[dict[str, Any]] = []

        for q_idx, (question, answer_list) in enumerate(zip(questions, answers)):
            query_start = time.monotonic()

            await system.begin_session(task_type="retrieval")
            frame_result = await system.frame("attention", query=question)
            await system.end_session()

            query_time = time.monotonic() - query_start

            results.append({
                "query_id": q_idx,
                "question": question,
                "answers": answer_list,
                "frame_result": frame_result,
                "memory_construction_time": memorize_time,
                "query_time_len": query_time,
                "qa_pair_id": metadata.get("qa_pair_ids", [None])[q_idx]
                    if q_idx < len(metadata.get("qa_pair_ids", []))
                    else None,
            })

        return results

    finally:
        await system.close()
        db_file = Path(db_path)
        if db_file.exists():
            db_file.unlink()
        for suffix in ["-wal", "-shm"]:
            wal = Path(db_path + suffix)
            if wal.exists():
                wal.unlink()
```

### 6.5 The Runner (`runner.py`)

```python
"""Main MemoryAgentBench runner — processes examples and computes metrics."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from pathlib import Path

from datasets import load_dataset

from benchmarks.memoryagentbench.adapter import process_example
from benchmarks.memoryagentbench.answerer import generate_answer
from benchmarks.memoryagentbench.config import MABenchConfig
from benchmarks.memoryagentbench.metrics import compute_all_metrics

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# Competency → split mapping
COMPETENCY_SPLITS = {
    "AR": "Accurate_Retrieval",
    "TTL": "Test_Time_Learning",
    "LRU": "Long_Range_Understanding",
    "CR": "Conflict_Resolution",
}


async def run_benchmark(cfg: MABenchConfig) -> dict[str, Path]:
    """Run MemoryAgentBench evaluation and return paths to result files."""

    # --- Load dataset ---
    ds = load_dataset("ai-hyz/MemoryAgentBench")
    log.info(f"Loaded MemoryAgentBench: {', '.join(f'{k}={len(v)}' for k, v in ds.items())}")

    # --- Select competencies ---
    if cfg.competencies:
        splits = [COMPETENCY_SPLITS.get(c, c) for c in cfg.competencies]
    else:
        splits = list(ds.keys())

    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    output_paths: dict[str, Path] = {}

    # --- Build adapter config ---
    adapter_config = {
        "lm_studio_base_url": cfg.lm_studio_base_url,
        "elfmem_llm_model": cfg.elfmem_llm_model,
        "elfmem_embedding_model": cfg.elfmem_embedding_model,
        "elfmem_embedding_dimensions": cfg.elfmem_embedding_dimensions,
        "chunk_size": cfg.chunk_size,
        "top_k": cfg.top_k,
        "inbox_threshold": cfg.inbox_threshold,
        "consolidate_after_n_chunks": cfg.consolidate_after_n_chunks,
        "search_window_hours": cfg.search_window_hours,
        "contradiction_similarity_prefilter": cfg.contradiction_similarity_prefilter,
    }

    for split_name in splits:
        if split_name not in ds:
            log.warning(f"Split {split_name} not found, skipping")
            continue

        split_data = ds[split_name]
        log.info(f"\n{'='*60}\nRunning {split_name} ({len(split_data)} examples)\n{'='*60}")

        # Filter by source if specified
        examples = list(split_data)
        if cfg.sources:
            examples = [
                ex for ex in examples
                if ex.get("metadata", {}).get("source") in cfg.sources
            ]
            log.info(f"Filtered to {len(examples)} examples matching sources: {cfg.sources}")

        if cfg.max_examples is not None:
            examples = examples[:cfg.max_examples]

        all_results: list[dict] = []
        all_metrics: dict[str, list[float]] = {
            "f1": [], "exact_match": [], "substring_exact_match": [],
            "rougeL_f1": [], "rougeL_recall": [],
            "memory_construction_time": [], "query_time_len": [],
        }

        for ex_idx, example in enumerate(examples):
            source = example.get("metadata", {}).get("source", "unknown")
            n_questions = len(example["questions"])
            log.info(f"  [{ex_idx+1}/{len(examples)}] {source}: {n_questions} questions")

            start = time.monotonic()

            try:
                # Ingest + retrieve
                qa_results = await process_example(example, adapter_config)

                # Generate answers and compute metrics
                for qr in qa_results:
                    hypothesis = await generate_answer(
                        frame_result=qr["frame_result"],
                        question=qr["question"],
                        question_date="",
                        model=cfg.answer_model,
                        max_tokens=cfg.answer_max_tokens,
                        base_url=cfg.lm_studio_base_url,
                    )

                    metrics = compute_all_metrics(hypothesis, qr["answers"])

                    result_entry = {
                        "output": hypothesis,
                        "answer": qr["answers"],
                        "query": qr["question"],
                        "query_id": qr["query_id"],
                        "qa_pair_id": qr["qa_pair_id"],
                        "source": source,
                        "memory_construction_time": qr["memory_construction_time"],
                        "query_time_len": qr["query_time_len"],
                        **metrics,
                    }
                    all_results.append(result_entry)

                    for k in all_metrics:
                        if k in result_entry:
                            all_metrics[k].append(result_entry[k])

                elapsed = time.monotonic() - start
                log.info(f"    {elapsed:.1f}s | {n_questions} Qs answered")

            except Exception as e:
                log.error(f"    ERROR: {e}")
                # Write fallback results
                for q_idx, (q, a) in enumerate(
                    zip(example["questions"], example["answers"])
                ):
                    all_results.append({
                        "output": "I don't know.",
                        "answer": a,
                        "query": q,
                        "query_id": q_idx,
                        "source": source,
                        "f1": 0.0,
                        "exact_match": 0.0,
                        "error": str(e),
                    })

        # --- Write results ---
        output_path = cfg.output_dir / f"{split_name}_elfmem_results.json"

        averaged = {}
        for k, vals in all_metrics.items():
            if vals:
                avg = sum(vals) / len(vals)
                # Match MABench format: multiply by 100 for accuracy-like metrics
                if k not in ("memory_construction_time", "query_time_len"):
                    averaged[k] = round(avg * 100, 2)
                else:
                    averaged[k] = round(avg, 2)

        output = {
            "benchmark": "MemoryAgentBench",
            "split": split_name,
            "agent": "elfmem",
            "data": all_results,
            "averaged_metrics": averaged,
            "total_examples": len(examples),
            "total_questions": len(all_results),
        }

        with open(output_path, "w") as f:
            json.dump(output, f, indent=2, default=str)

        output_paths[split_name] = output_path

        log.info(f"\n  Results: {output_path}")
        log.info(f"  Averaged: {averaged}")

    return output_paths


def main():
    """CLI entry point."""
    cfg = MABenchConfig()

    args = sys.argv[1:]

    # Quick competency selection
    if "--cr" in args or "--conflict" in args:
        cfg.competencies = ["Conflict_Resolution"]
    elif "--ar" in args or "--retrieval" in args:
        cfg.competencies = ["Accurate_Retrieval"]
    elif "--ttl" in args or "--learning" in args:
        cfg.competencies = ["Test_Time_Learning"]
    elif "--lru" in args or "--understanding" in args:
        cfg.competencies = ["Long_Range_Understanding"]

    if "--test" in args:
        cfg.max_examples = 1
        cfg.verbose = True

    for arg in args:
        if arg.startswith("--top-k="):
            cfg.top_k = int(arg.split("=")[1])
        if arg.startswith("--max="):
            cfg.max_examples = int(arg.split("=")[1])
        if arg.startswith("--source="):
            cfg.sources = [arg.split("=")[1]]

    output_paths = asyncio.run(run_benchmark(cfg))

    print("\nResults:")
    for split, path in output_paths.items():
        print(f"  {split}: {path}")


if __name__ == "__main__":
    main()
```

### 6.6 Shared Answerer

Reuse the same answerer from the LongMemEval guide. Create a symlink or shared
module at `benchmarks/shared/answerer.py`, or copy the answerer from
`benchmarks/longmemeval/answerer.py`. The interface is identical:

```python
from benchmarks.longmemeval.answerer import generate_answer
# or copy the file to benchmarks/memoryagentbench/answerer.py
```

---

## 7. Running the Evaluation

### 7.1 Step-by-Step

**Step 1: Smoke test (1 example, Conflict Resolution)**

```bash
cd /Users/emson/Dropbox/devel/projects/ai/elf0_mem_sim

python -m benchmarks.memoryagentbench.runner --conflict --test
```

This processes 1 FactConsolidation example. Check that:
- Chunks are ingested without timeout
- Consolidation completes (watch LM Studio for activity)
- Answers reference the latest facts (not contradicted older ones)

**Step 2: Full Conflict Resolution (the moat test)**

```bash
python -m benchmarks.memoryagentbench.runner --conflict
```

8 examples (single-hop and multi-hop, various sizes). This is where elfmem
should shine vs other systems.

**Step 3: Accurate Retrieval**

```bash
python -m benchmarks.memoryagentbench.runner --retrieval
```

22 examples. Tests basic retrieval quality.

**Step 4: All competencies**

```bash
python -m benchmarks.memoryagentbench.runner
```

### 7.2 Output Format

Results conform to the [Benchmark Report Spec](benchmark_report_spec.md). Saved to
`benchmarks/memoryagentbench/results/{timestamp}_memoryagentbench_elfmem.json`:

```json
{
  "meta": {
    "benchmark": "memoryagentbench",
    "version": "1.0",
    "timestamp": "2026-04-08T14:30:00Z",
    "duration_seconds": 3600,
    "elfmem_version": "0.5.1",
    "models": {
      "consolidation_llm": "google/gemma-4-26b-a4b",
      "embedding": "text-embedding-nomic-embed-text-v1.5",
      "embedding_dimensions": 768,
      "answer_llm": "google/gemma-4-26b-a4b",
      "judge": null
    },
    "elfmem_config": { "top_k": 10, "contradiction_similarity_prefilter": 0.50 },
    "lm_studio_base_url": "http://localhost:1234/v1"
  },
  "scores": {
    "overall": 45.0,
    "by_category": {
      "factconsolidation_sh": {"score": 55.0, "count": 4},
      "factconsolidation_mh": {"score": 35.0, "count": 4}
    }
  },
  "baselines": {
    "no_retrieval":      {"overall": 2.0, "by_category": {}},
    "perfect_retrieval": {"overall": 68.0, "by_category": {}}
  },
  "retrieval": {
    "overall_recall": null
  },
  "efficiency": {
    "total_memorization_seconds": 2400,
    "total_query_seconds": 120,
    "avg_query_seconds": 2.5,
    "total_blocks_learned": 800,
    "total_blocks_active": 720
  },
  "questions": [
    {
      "id": "factconsolidation_mh_32k_q0",
      "category": "factconsolidation_mh",
      "question": "What is the capital of France?",
      "ground_truth": "Lyon",
      "prediction": "The capital of France is Lyon.",
      "score": 1.0,
      "metric": "f1",
      "retrieval_recall": null,
      "evidence_ids": [],
      "retrieved_ids": [],
      "query_seconds": 3.1
    }
  ]
}
```

**Note:** `retrieval_recall` is `null` for MemoryAgentBench because the dataset
does not provide evidence annotations at the chunk level. The baselines serve
as the primary diagnostic instead.

---

## 8. Configuration Tuning

### 8.1 Key Parameters for Conflict Resolution

| Parameter | Default | Tuning Notes |
|---|---|---|
| `top_k` | 10 | Increase to 15-20 for multi-hop (need multiple related facts) |
| `consolidate_after_n_chunks` | 10 | Lower = better contradiction detection (more frequent checks) but slower |
| `contradiction_similarity_prefilter` | 0.65 | Lower = more contradiction checks (important for FactConsolidation!) |
| `chunk_size` | 4096 | Smaller chunks = finer granularity but more consolidation calls |

**For FactConsolidation specifically**, consider lowering `contradiction_similarity_prefilter`
to 0.50 — contradictory facts may have moderate (not high) cosine similarity, and we
want elfmem to detect them.

### 8.2 Per-Competency Tuning

```python
COMPETENCY_CONFIGS = {
    "Conflict_Resolution": {
        "top_k": 15,
        "contradiction_similarity_prefilter": 0.50,  # more aggressive detection
        "consolidate_after_n_chunks": 5,              # frequent contradiction checks
    },
    "Accurate_Retrieval": {
        "top_k": 10,
        "consolidate_after_n_chunks": 20,             # batch for speed
    },
    "Long_Range_Understanding": {
        "top_k": 20,                                  # wide context needed
        "consolidate_after_n_chunks": 15,
    },
    "Test_Time_Learning": {
        "top_k": 10,
        "consolidate_after_n_chunks": 10,
    },
}
```

---

## 9. Performance & Time Estimates

All elfmem operations run locally via LM Studio ($0 API cost).

### Per-Competency Estimates

| Competency | Examples | Context Size | Est. Chunks | Est. Time |
|---|---|---|---|---|
| Conflict Resolution | 8 | 6K-262K | 2-64 each | 30 min - 4 hours |
| Accurate Retrieval | 22 | 64K-421K | 16-103 each | 4-16 hours |
| Long-Range Understanding | 110 | varies | varies | 1-3 days |
| Test-Time Learning | 6 | varies | varies | 2-8 hours |

**Start with Conflict Resolution** — smallest dataset, most relevant to elfmem's
moat, fastest feedback loop.

### Bottleneck

The bottleneck is `consolidate()` — one LLM call per block via local Gemma.
At ~20-40 tokens/sec, each consolidation call takes ~5-15 seconds. With 64 chunks
at the 262K context size, that's ~5-15 minutes of consolidation per example.

---

## 10. Edge Cases & Mitigations

### 10.1 Very Large Contexts (262K+)

The FactConsolidation 262K variant produces ~64 chunks. With `consolidate_after_n_chunks=10`,
that's 7 consolidation rounds plus a final one. Total consolidation: potentially thousands
of LLM calls (each chunk may split into multiple blocks).

**Mitigation:** Start with the 6K and 32K variants. Only attempt 262K after
validating the pipeline.

### 10.2 Contradiction Detection False Positives

elfmem's contradiction detection may flag facts as contradictory when they're
actually complementary (e.g., "Paris is in France" vs "Lyon is in France").

**Mitigation:** The `contradiction_similarity_prefilter` at 0.65 reduces false
positives. Monitor the debug output for contradiction counts.

### 10.3 Multi-Hop Chains

Multi-hop FactConsolidation requires the system to chain: fact A supersedes B,
fact C references A, question asks about C. elfmem's graph expansion (1-hop)
helps here — if A and C are connected via edges, retrieving C also retrieves A.

**Mitigation:** Use higher `top_k` (15-20) for multi-hop to increase the chance
of retrieving all chain links.

### 10.4 Chunk Boundary Splitting

NLTK sentence tokenization may split a fact across two chunks if it spans
a sentence boundary. This could cause elfmem to learn incomplete facts.

**Mitigation:** The 4096-token chunk size is large enough that most facts fit
within a single chunk. Monitor for anomalies in debug output.

---

## 11. Interpreting Results

### 11.1 Baseline Comparisons (from the MemoryAgentBench Paper)

| System | AR | TTL | LRU | CR (single-hop) | CR (multi-hop) |
|---|---|---|---|---|---|
| Mem0 | ~40% | ~30% | ~25% | ~35% | ~7% |
| Cognee | ~38% | ~28% | ~22% | ~30% | ~7% |
| Letta (MemGPT) | ~42% | ~35% | ~30% | ~38% | ~7% |
| GPT-4o long-context | ~55% | ~45% | ~40% | ~50% | ~25% |

> Note: These are approximate from the paper. Check Table 2 for exact numbers.
> The 7% multi-hop CR number is the headline finding.

### 11.2 What "Good" Looks Like for elfmem

**Conflict Resolution (the headline metric):**
- Single-hop: Target >50% (beating all memory systems)
- **Multi-hop: Target >15%** (doubling the 7% baseline would be significant)
- Any score >25% on multi-hop CR demonstrates elfmem's contradiction detection
  provides real value

**Accurate Retrieval:**
- Target >45% (competitive with memory systems, below long-context baselines)
- elfmem's graph expansion should help with EventQA

**Overall:**
- If elfmem beats all memory systems on CR while matching them on AR, that's
  a strong result — it proves the moat (contradiction handling) without sacrificing
  general retrieval quality

### 11.3 Reporting Results

Present results in this order:
1. **CR multi-hop** first (the headline — elfmem vs 7% baseline)
2. CR single-hop (validates the finding)
3. AR (shows general retrieval isn't sacrificed)
4. TTL and LRU (nice-to-have, not elfmem's focus)

---

## Quick Reference

```bash
# === SETUP ===
git clone https://github.com/HUST-AI-HYZ/MemoryAgentBench.git ../MemoryAgentBench
pip install tiktoken rouge-score nltk tqdm datasets

# === RUN ===
cd /Users/emson/Dropbox/devel/projects/ai/elf0_mem_sim

# Smoke test (1 example, Conflict Resolution)
python -m benchmarks.memoryagentbench.runner --conflict --test

# Full Conflict Resolution with baselines (recommended)
python -m benchmarks.memoryagentbench.runner --conflict --baselines

# Full Conflict Resolution without baselines (faster)
python -m benchmarks.memoryagentbench.runner --conflict

# Specific sub-dataset
python -m benchmarks.memoryagentbench.runner --conflict --source=factconsolidation_mh_32k

# All competencies
python -m benchmarks.memoryagentbench.runner --baselines

# Custom top-k
python -m benchmarks.memoryagentbench.runner --conflict --top-k=20

# Resume after crash
python -m benchmarks.memoryagentbench.runner --resume
```
