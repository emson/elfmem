# Evaluating elfmem with LongMemEval

A step-by-step guide to benchmarking elfmem's long-term memory against the
[LongMemEval](https://github.com/xiaowu0162/LongMemEval) benchmark (ICLR 2025).

**Report format:** Output conforms to the
[Benchmark Report Spec](benchmark_report_spec.md) for cross-benchmark comparison.
Produces both the JSONL hypothesis file (for LongMemEval's GPT-4o judge) and a
standard report JSON with baselines and retrieval tracking.

---

## Table of Contents

1. [What is LongMemEval?](#1-what-is-longmemeval)
2. [How elfmem Maps to the Benchmark](#2-how-elfmem-maps-to-the-benchmark)
3. [Setup](#3-setup)
4. [Data Format Deep Dive](#4-data-format-deep-dive)
5. [Architecture: The Adapter Script](#5-architecture-the-adapter-script)
6. [Implementation: Step by Step](#6-implementation-step-by-step)
7. [Running the Evaluation](#7-running-the-evaluation)
8. [Configuration Tuning](#8-configuration-tuning)
9. [Cost Estimates & Optimisation](#9-cost-estimates--optimisation)
10. [Edge Cases & Mitigations](#10-edge-cases--mitigations)
11. [Interpreting Results](#11-interpreting-results)
12. [Advanced: Ablation Studies](#12-advanced-ablation-studies)

---

## 1. What is LongMemEval?

LongMemEval tests whether a chat assistant can **remember and reason over long
conversation histories**. It provides:

- **500 questions** across 6 types testing 5 memory abilities
- **Three dataset sizes** (oracle / small / medium) with increasing noise
- **LLM-as-judge evaluation** using GPT-4o for binary accuracy scoring

### Question Types

| Type | Count | What It Tests |
|------|-------|---------------|
| `single-session-user` | 70 | Recall facts from user messages |
| `single-session-assistant` | 56 | Recall facts from assistant responses |
| `single-session-preference` | 30 | Recall user preferences |
| `multi-session` | 133 | Reason across multiple conversations |
| `temporal-reasoning` | 133 | Reason about when things happened |
| `knowledge-update` | 78 | Track updated/contradicted info |
| (subset: `*_abs`) | 30 | Correctly abstain on unanswerable Qs |

### Dataset Sizes

| File | Sessions | Tokens | Purpose |
|------|----------|--------|---------|
| `longmemeval_oracle.json` | Evidence only | ~5K | Upper-bound test (perfect retrieval) |
| `longmemeval_s_cleaned.json` | ~40 | ~115K | Realistic test (fits 128K context) |
| `longmemeval_m_cleaned.json` | ~500 | ~1M+ | Stress test (requires retrieval) |

### Evaluation Flow

```
Your system → hypothesis.jsonl → evaluate_qa.py (GPT-4o judge) → accuracy scores
```

---

## 2. How elfmem Maps to the Benchmark

### The Challenge

LongMemEval gives you **timestamped conversation sessions** and asks questions.
elfmem is a **memory system**, not a QA system. We need a wrapper:

```
[Conversation History] → elfmem (learn + consolidate) → elfmem (recall) → Gemma (answer) → hypothesis
                         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                         All local via LM Studio — zero API cost
```

### Capability Mapping

| LongMemEval Ability | elfmem Feature | Notes |
|---------------------|---------------|-------|
| Information Extraction | `learn()` + `frame("attention", query)` | Direct mapping |
| Multi-Session Reasoning | Knowledge graph edges + graph expansion | Edges link blocks across sessions |
| Temporal Reasoning | Timestamps embedded in content + recency scoring | No native date index — encode dates in content |
| Knowledge Updates | Contradiction detection in `consolidate()` | elfmem detects & handles contradictions |
| Abstention | Low retrieval scores → "I don't know" | Threshold on `ScoredBlock.score` |

### What Makes elfmem Different from Baseline RAG

| Baseline RAG | elfmem |
|--------------|--------|
| Flat vector search | 5-stage hybrid pipeline (pre-filter → vector → graph expand → score → MMR) |
| No deduplication | Dedup + near-duplicate superseding |
| No contradiction handling | LLM-powered contradiction detection |
| Static embeddings | Decay-based lifecycle (BIRTH → ARCHIVE) |
| No reinforcement | Retrieval reinforces blocks (Hebbian) |
| No graph | Knowledge graph with semantic edges |

---

## 3. Setup

### 3.1 Clone LongMemEval

```bash
cd /Users/emson/Dropbox/devel/projects/ai
git clone https://github.com/xiaowu0162/LongMemEval.git
cd LongMemEval
```

### 3.2 Create Environment

We'll use elfmem's existing environment and add LongMemEval's evaluation deps:

```bash
# From the elfmem project root
cd /Users/emson/Dropbox/devel/projects/ai/elf0_mem_sim

# Install LongMemEval's evaluation dependencies (minimal)
pip install openai==1.35.1 tqdm backoff numpy nltk
```

> **Note**: We only need `requirements-lite.txt` deps for evaluation. We do NOT
> need their retrieval/generation code — elfmem replaces that.

### 3.2.1 LM Studio Setup

elfmem uses a **local Gemma model** via LM Studio for all LLM + embedding operations.
Make sure LM Studio is running with these models loaded:

| Role | Model | Notes |
|------|-------|-------|
| LLM (consolidation + answers) | `google/gemma-4-26b-a4b` | Used by elfmem consolidate() and answer generation |
| Embeddings | `text-embedding-nomic-embed-text-v1.5` | 768-dimensional embeddings |

Both are served at `http://localhost:1234/v1` (LM Studio's default OpenAI-compatible endpoint).

**Verify LM Studio is running:**

```bash
# Quick health check — should return a model list
curl http://localhost:1234/v1/models

# Test completion
curl http://localhost:1234/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "google/gemma-4-26b-a4b", "messages": [{"role": "user", "content": "hello"}], "max_tokens": 10}'

# Test embeddings
curl http://localhost:1234/v1/embeddings \
  -H "Content-Type: application/json" \
  -d '{"model": "text-embedding-nomic-embed-text-v1.5", "input": "test"}'
```

### 3.3 Download Data

```bash
mkdir -p /Users/emson/Dropbox/devel/projects/ai/LongMemEval/data
cd /Users/emson/Dropbox/devel/projects/ai/LongMemEval/data

# Download all three datasets
wget https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/longmemeval_oracle.json
wget https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/longmemeval_s_cleaned.json
wget https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/longmemeval_m_cleaned.json
```

### 3.4 Verify Setup

```bash
# Check data files exist and are valid JSON
python3 -c "
import json
for f in ['longmemeval_oracle.json', 'longmemeval_s_cleaned.json', 'longmemeval_m_cleaned.json']:
    data = json.load(open(f'/Users/emson/Dropbox/devel/projects/ai/LongMemEval/data/{f}'))
    print(f'{f}: {len(data)} questions')
"
# Expected: 500 questions each
```

### 3.5 Required API Keys

```bash
# elfmem uses LOCAL models via LM Studio — no API keys needed for ingestion/retrieval/answering!
# The only API key needed is for the evaluation judge (GPT-4o):
export OPENAI_API_KEY="sk-..."            # OpenAI for LongMemEval's evaluate_qa.py (GPT-4o judge)
```

> **Key advantage of local models**: The entire elfmem pipeline (learn, consolidate,
> embed, retrieve, answer) runs locally via LM Studio at zero API cost. Only the
> final evaluation step (GPT-4o as judge) requires a cloud API key.

---

## 4. Data Format Deep Dive

Each of the 500 entries in the JSON file looks like this:

```json
{
  "question_id": "gpt4_2655b836",
  "question_type": "temporal-reasoning",
  "question": "What was the first issue I reported about my new car?",
  "answer": "GPS system not functioning correctly",
  "question_date": "2023/04/10 (Mon) 23:07",
  "haystack_dates": [
    "2023/03/15 (Wed) 10:22",
    "2023/03/20 (Mon) 14:30",
    "2023/04/01 (Sat) 09:15"
  ],
  "haystack_session_ids": ["session_abc_1", "session_abc_2", "session_abc_3"],
  "haystack_sessions": [
    [
      {"role": "user", "content": "I just bought a new car!", "has_answer": false},
      {"role": "assistant", "content": "Congrats! What kind?", "has_answer": false}
    ],
    [
      {"role": "user", "content": "The GPS in my new car isn't working...", "has_answer": true},
      {"role": "assistant", "content": "That's frustrating. Have you tried...", "has_answer": false}
    ],
    [
      {"role": "user", "content": "Now the AC is broken too!", "has_answer": false},
      {"role": "assistant", "content": "Sounds like you should take it back.", "has_answer": false}
    ]
  ],
  "answer_session_ids": ["session_abc_2"]
}
```

**Key observations:**

1. **`haystack_sessions`** — ordered list of conversation sessions. Each session is
   a list of turn dicts with `role` and `content`.
2. **`haystack_dates`** — one timestamp per session (parallel array with `haystack_sessions`).
3. **`has_answer`** — marks which turns contain evidence (for debugging, not for input).
4. **`question_date`** — when the question is asked (always after all sessions).
5. **Abstention questions** — `question_id` ends with `"_abs"`. The correct answer
   is "I don't know" / "unanswerable".

---

## 5. Architecture: The Adapter Script

### High-Level Flow

```
┌─────────────────────────────────────────────────┐
│  For each of 500 questions:                     │
│                                                 │
│  1. Create fresh elfmem DB (in-memory)          │
│  2. Replay haystack_sessions chronologically    │
│     └─ learn() each turn with date metadata     │
│     └─ consolidate() after each session         │
│  3. Query: frame("attention", question)         │
│  4. Answer: LLM generates answer from context   │
│  5. Write {"question_id", "hypothesis"} to JSONL│
│                                                 │
│  Output: hypothesis.jsonl                       │
│  Evaluate: evaluate_qa.py → accuracy            │
└─────────────────────────────────────────────────┘
```

### Why Per-Question Isolation?

Each question has its own `haystack_sessions` — a different set of conversations.
We create a **fresh elfmem database per question** so there's no cross-contamination.
This mirrors how the benchmark is designed: each question is independent.

### Why Consolidate After Each Session?

elfmem's retrieval only searches **active** blocks (promoted from inbox via
`consolidate()`). Without consolidation, `learn()`'d blocks sit in the inbox
and are invisible to `frame()` / `recall()`.

Consolidating after each session simulates a natural agent rhythm: the agent
has a conversation, then processes what it learned during a pause.

### Content Formatting Strategy

We embed the **date** and **role** into each learned block's content so elfmem's
vector search can match temporal and speaker queries:

```python
content = f"[{date}] {role}: {turn_content}"
# Example: "[2023/03/20 (Mon) 14:30] user: The GPS in my new car isn't working..."
```

**Why include dates in content?**
- elfmem has no native date index — it tracks `active_hours` (session time), not calendar dates
- LongMemEval's temporal reasoning questions ask about calendar dates
- Embedding the date in the text lets vector search pick up temporal cues
- The answer LLM can read dates directly from retrieved context

**Why include role?**
- The benchmark tests recall from both user and assistant messages
- Knowing who said what helps the answer LLM reason correctly

---

## 6. Implementation: Step by Step

### 6.1 Project Structure

```
elf0_mem_sim/
├── benchmarks/
│   └── longmemeval/
│       ├── __init__.py
│       ├── runner.py          # Main benchmark runner
│       ├── adapter.py         # elfmem ↔ LongMemEval adapter
│       ├── answerer.py        # LLM answer generation
│       ├── config.py          # Benchmark configuration
│       └── results/           # Output directory
│           └── .gitkeep
```

### 6.2 Configuration (`config.py`)

```python
"""Benchmark configuration — all tunables in one place."""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class BenchmarkConfig:
    """Configuration for the LongMemEval benchmark run."""

    # Paths
    data_file: Path = Path("../../LongMemEval/data/longmemeval_oracle.json")
    output_dir: Path = Path("benchmarks/longmemeval/results")

    # elfmem settings
    consolidate_after_n_sessions: int = 1       # consolidate every N sessions
    curate_interval_hours: float = 1000.0       # effectively disable auto-curate
    inbox_threshold: int = 50                   # allow larger inbox before forced consolidate
    top_k: int = 10                             # retrieval top-k
    frame_name: str = "attention"               # which frame to use for retrieval
    search_window_hours: float = 10000.0        # wide window — don't filter by recency

    # LM Studio settings (local models)
    lm_studio_base_url: str = "http://localhost:1234/v1"

    # LLM settings for answer generation (local Gemma via LM Studio)
    answer_model: str = "google/gemma-4-26b-a4b"
    answer_max_tokens: int = 300

    # elfmem LLM settings (consolidation — same local models)
    elfmem_llm_model: str = "google/gemma-4-26b-a4b"
    elfmem_embedding_model: str = "text-embedding-nomic-embed-text-v1.5"
    elfmem_embedding_dimensions: int = 768

    # Execution
    max_questions: int | None = None            # None = all 500; set to 5 for testing
    resume_from: int = 0                        # skip first N questions (for resuming)
    concurrency: int = 1                        # sequential by default (safe)

    # Debug
    verbose: bool = False
    save_retrieval_debug: bool = False          # save retrieved blocks per question
```

### 6.3 The Adapter (`adapter.py`)

This is the core integration — it manages elfmem's lifecycle for each question.

```python
"""elfmem adapter for LongMemEval — manages learn/consolidate/recall per question."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any

from elfmem import ElfmemConfig, MemorySystem
from elfmem.types import FrameResult


async def process_question(
    entry: dict[str, Any],
    config: dict[str, Any],
) -> tuple[FrameResult | None, list[dict[str, Any]]]:
    """Ingest conversation history and retrieve context for one question.

    Args:
        entry: One LongMemEval question entry (with haystack_sessions, etc.)
        config: elfmem and benchmark configuration dict

    Returns:
        Tuple of (FrameResult or None, debug_info list)
    """
    sessions = entry["haystack_sessions"]
    dates = entry["haystack_dates"]
    question = entry["question"]
    question_date = entry.get("question_date", "")

    debug_info: list[dict[str, Any]] = []

    # --- Build elfmem config (local models via LM Studio) ---
    base_url = config.get("lm_studio_base_url", "http://localhost:1234/v1")

    elfmem_cfg = ElfmemConfig.model_validate({
        "llm": {
            "model": config.get("elfmem_llm_model", "google/gemma-4-26b-a4b"),
            "base_url": base_url,
            "temperature": 0.0,
            "max_tokens": 512,
            "timeout": 120,         # local models can be slower
        },
        "embeddings": {
            "model": config.get("elfmem_embedding_model", "text-embedding-nomic-embed-text-v1.5"),
            "base_url": base_url,
            "dimensions": config.get("elfmem_embedding_dimensions", 768),
            "timeout": 60,
        },
        "memory": {
            "inbox_threshold": config.get("inbox_threshold", 50),
            "curate_interval_hours": config.get("curate_interval_hours", 1000.0),
            "top_k": config.get("top_k", 10),
            "search_window_hours": config.get("search_window_hours", 10000.0),
            "contradiction_similarity_prefilter": 0.65,  # match project config — reduces LLM calls
        },
    })

    # --- Use a temporary file DB (auto-cleaned) ---
    with tempfile.NamedTemporaryFile(suffix=".db", delete=True) as tmp:
        db_path = tmp.name

    # MemorySystem needs a path; the temp file is deleted but path is reusable
    system = await MemorySystem.from_config(db_path, config=elfmem_cfg)

    try:
        # --- Phase 1: Ingest all sessions ---
        session_count = 0
        total_blocks = 0

        for session_idx, (session, date) in enumerate(zip(sessions, dates)):
            await system.begin_session(task_type="ingestion")

            for turn in session:
                role = turn["role"]
                content = turn["content"]

                # Format: embed date and role for temporal + speaker retrieval
                formatted = f"[{date}] {role}: {content}"

                result = await system.learn(
                    content=formatted,
                    tags=[f"session:{session_idx}", f"role:{role}"],
                    category="conversation",
                    source="longmemeval",
                )
                if result.status == "created":
                    total_blocks += 1

            await system.end_session()
            session_count += 1

            # Consolidate periodically
            consolidate_every = config.get("consolidate_after_n_sessions", 1)
            if session_count % consolidate_every == 0:
                await system.begin_session(task_type="consolidation")
                cons_result = await system.consolidate()
                await system.end_session()

                debug_info.append({
                    "session_idx": session_idx,
                    "consolidated": cons_result.processed,
                    "promoted": cons_result.promoted,
                })

        # Final consolidation for any remaining inbox blocks
        await system.begin_session(task_type="consolidation")
        final_cons = await system.consolidate()
        await system.end_session()

        # --- Phase 2: Retrieve context for the question ---
        await system.begin_session(task_type="retrieval")

        # Include question date in the query for temporal context
        query = f"[Asked on {question_date}] {question}"
        frame_name = config.get("frame_name", "attention")
        frame_result = await system.frame(frame_name, query=query)

        await system.end_session()

        # --- Collect debug info ---
        status = await system.status()
        debug_info.append({
            "total_blocks_learned": total_blocks,
            "active_blocks": status.active_count,
            "archived_blocks": status.archived_count,
            "retrieved_blocks": len(frame_result.blocks) if frame_result else 0,
            "top_score": frame_result.blocks[0].score if frame_result and frame_result.blocks else 0,
        })

        return frame_result, debug_info

    finally:
        await system.close()
        # Clean up temp DB
        db_file = Path(db_path)
        if db_file.exists():
            db_file.unlink()
        # Also clean up WAL/SHM files
        for suffix in ["-wal", "-shm"]:
            wal = Path(db_path + suffix)
            if wal.exists():
                wal.unlink()
```

### 6.4 The Answer Generator (`answerer.py`)

This wraps an LLM to turn retrieved context into a natural language answer.

```python
"""Generate answers from retrieved elfmem context using a local LLM via LM Studio."""

from __future__ import annotations

from openai import AsyncOpenAI

from elfmem.types import FrameResult

# Default LM Studio endpoint
DEFAULT_BASE_URL = "http://localhost:1234/v1"

# System prompt for the answer LLM
ANSWER_SYSTEM_PROMPT = """You are a helpful assistant answering questions about past conversations.

You will be given:
1. Retrieved memory context — excerpts from past conversation sessions with timestamps
2. A question about those conversations

Rules:
- Answer ONLY based on the provided memory context
- If the context does not contain enough information to answer, say "I don't have enough information to answer this question"
- Be concise and direct — give the specific answer, not a summary of the context
- For temporal questions, pay attention to dates in the context
- If information was updated or corrected in later sessions, use the LATEST information
- Include specific details (names, numbers, dates) when available
"""

ANSWER_USER_TEMPLATE = """## Retrieved Memory Context

{context}

## Question (asked on {question_date})

{question}

## Your Answer

Provide a concise, direct answer based on the memory context above."""


async def generate_answer(
    frame_result: FrameResult | None,
    question: str,
    question_date: str,
    model: str = "google/gemma-4-26b-a4b",
    max_tokens: int = 300,
    base_url: str = DEFAULT_BASE_URL,
) -> str:
    """Generate an answer to a LongMemEval question using retrieved context.

    Uses local Gemma model via LM Studio's OpenAI-compatible API.

    Args:
        frame_result: Retrieved context from elfmem frame() call
        question: The question to answer
        question_date: When the question was asked
        model: Model ID served by LM Studio
        max_tokens: Max tokens for the answer
        base_url: LM Studio endpoint URL

    Returns:
        The generated answer string
    """
    if frame_result is None or not frame_result.blocks:
        return "I don't have enough information to answer this question."

    # Build context from retrieved blocks
    context = frame_result.text

    prompt = ANSWER_USER_TEMPLATE.format(
        context=context,
        question=question,
        question_date=question_date,
    )

    # LM Studio serves an OpenAI-compatible API — no API key needed
    client = AsyncOpenAI(base_url=base_url, api_key="lm-studio")

    response = await client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        temperature=0.0,
        messages=[
            {"role": "system", "content": ANSWER_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    )

    return response.choices[0].message.content or "I don't know."
```

### 6.5 The Runner (`runner.py`)

The main script that ties everything together.

```python
"""Main benchmark runner — processes all questions and writes hypothesis JSONL."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from pathlib import Path

from benchmarks.longmemeval.adapter import process_question
from benchmarks.longmemeval.answerer import generate_answer
from benchmarks.longmemeval.config import BenchmarkConfig

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)


async def run_benchmark(cfg: BenchmarkConfig) -> Path:
    """Run the full LongMemEval benchmark and return path to output JSONL.

    Steps:
        1. Load data
        2. For each question: ingest → retrieve → answer
        3. Write hypothesis JSONL
        4. Print summary stats
    """
    # --- Load data ---
    data_path = cfg.data_file.resolve()
    if not data_path.exists():
        raise FileNotFoundError(f"Data file not found: {data_path}")

    with open(data_path) as f:
        data = json.load(f)

    log.info(f"Loaded {len(data)} questions from {data_path.name}")

    # --- Slice if needed ---
    if cfg.resume_from > 0:
        data = data[cfg.resume_from:]
        log.info(f"Resuming from question {cfg.resume_from}, {len(data)} remaining")

    if cfg.max_questions is not None:
        data = data[:cfg.max_questions]
        log.info(f"Limited to {len(data)} questions")

    # --- Prepare output ---
    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    dataset_name = data_path.stem  # e.g. "longmemeval_oracle"
    output_path = cfg.output_dir / f"{dataset_name}_elfmem_hypotheses.jsonl"
    debug_path = cfg.output_dir / f"{dataset_name}_elfmem_debug.jsonl"

    # --- Build config dict for adapter ---
    adapter_config = {
        "lm_studio_base_url": cfg.lm_studio_base_url,
        "elfmem_llm_model": cfg.elfmem_llm_model,
        "elfmem_embedding_model": cfg.elfmem_embedding_model,
        "elfmem_embedding_dimensions": cfg.elfmem_embedding_dimensions,
        "inbox_threshold": cfg.inbox_threshold,
        "curate_interval_hours": cfg.curate_interval_hours,
        "top_k": cfg.top_k,
        "search_window_hours": cfg.search_window_hours,
        "frame_name": cfg.frame_name,
        "consolidate_after_n_sessions": cfg.consolidate_after_n_sessions,
    }

    # --- Process questions ---
    results: list[dict] = []
    errors: list[dict] = []
    total_time = 0.0

    with open(output_path, "w") as out_f, open(debug_path, "w") as dbg_f:
        for i, entry in enumerate(data):
            qid = entry["question_id"]
            qtype = entry["question_type"]
            start = time.monotonic()

            try:
                log.info(
                    f"[{i+1}/{len(data)}] {qtype}: {qid} "
                    f"({len(entry['haystack_sessions'])} sessions)"
                )

                # Phase 1 & 2: Ingest + Retrieve
                frame_result, debug_info = await process_question(entry, adapter_config)

                # Phase 3: Generate answer (local Gemma via LM Studio)
                hypothesis = await generate_answer(
                    frame_result=frame_result,
                    question=entry["question"],
                    question_date=entry.get("question_date", ""),
                    model=cfg.answer_model,
                    max_tokens=cfg.answer_max_tokens,
                    base_url=cfg.lm_studio_base_url,
                )

                elapsed = time.monotonic() - start
                total_time += elapsed

                result = {"question_id": qid, "hypothesis": hypothesis}
                results.append(result)

                # Write immediately (crash-safe)
                out_f.write(json.dumps(result) + "\n")
                out_f.flush()

                if cfg.save_retrieval_debug:
                    dbg_entry = {
                        "question_id": qid,
                        "question_type": qtype,
                        "question": entry["question"],
                        "answer": entry["answer"],
                        "hypothesis": hypothesis,
                        "debug": debug_info,
                        "elapsed_seconds": round(elapsed, 2),
                    }
                    dbg_f.write(json.dumps(dbg_entry) + "\n")
                    dbg_f.flush()

                log.info(f"  -> {elapsed:.1f}s | answer: {hypothesis[:80]}...")

            except Exception as e:
                elapsed = time.monotonic() - start
                log.error(f"  !! ERROR on {qid}: {e}")
                errors.append({"question_id": qid, "error": str(e)})

                # Write a fallback so evaluation doesn't skip this question
                fallback = {"question_id": qid, "hypothesis": "I don't know."}
                out_f.write(json.dumps(fallback) + "\n")
                out_f.flush()

    # --- Summary ---
    log.info("=" * 60)
    log.info(f"Completed: {len(results)}/{len(data)} questions")
    log.info(f"Errors: {len(errors)}")
    log.info(f"Total time: {total_time:.1f}s ({total_time/max(len(results),1):.1f}s/question)")
    log.info(f"Output: {output_path}")

    if errors:
        error_path = cfg.output_dir / f"{dataset_name}_errors.json"
        with open(error_path, "w") as f:
            json.dump(errors, f, indent=2)
        log.info(f"Errors saved: {error_path}")

    return output_path


def main():
    """CLI entry point."""
    cfg = BenchmarkConfig()

    # Quick CLI overrides
    args = sys.argv[1:]
    if "--oracle" in args:
        cfg.data_file = Path("../../LongMemEval/data/longmemeval_oracle.json")
    elif "--small" in args:
        cfg.data_file = Path("../../LongMemEval/data/longmemeval_s_cleaned.json")
    elif "--medium" in args:
        cfg.data_file = Path("../../LongMemEval/data/longmemeval_m_cleaned.json")

    if "--test" in args:
        cfg.max_questions = 5
        cfg.verbose = True
        cfg.save_retrieval_debug = True

    if "--debug" in args:
        cfg.save_retrieval_debug = True

    for arg in args:
        if arg.startswith("--top-k="):
            cfg.top_k = int(arg.split("=")[1])
        if arg.startswith("--max="):
            cfg.max_questions = int(arg.split("=")[1])
        if arg.startswith("--resume="):
            cfg.resume_from = int(arg.split("=")[1])

    output_path = asyncio.run(run_benchmark(cfg))
    print(f"\nHypothesis file: {output_path}")
    print(f"Next step: run evaluation (see guide Section 7)")


if __name__ == "__main__":
    main()
```

---

## 7. Running the Evaluation

### 7.1 Step-by-Step Execution

**Step 1: Smoke test (5 questions, oracle data)**

```bash
cd /Users/emson/Dropbox/devel/projects/ai/elf0_mem_sim

python -m benchmarks.longmemeval.runner --oracle --test
```

This processes 5 questions with debug output (~5-10 min with local Gemma). Check:
- LM Studio stays responsive (watch for queue buildup)
- No crashes or timeouts
- Hypotheses look reasonable
- Debug JSONL shows blocks being retrieved

**Step 2: Full oracle run (500 questions, evidence-only sessions)**

```bash
python -m benchmarks.longmemeval.runner --oracle
```

**Step 3: Run LongMemEval's evaluation**

```bash
cd /Users/emson/Dropbox/devel/projects/ai/LongMemEval/src/evaluation

# Evaluate
python3 evaluate_qa.py gpt-4o \
    ../../data/../elf0_mem_sim/benchmarks/longmemeval/results/longmemeval_oracle_elfmem_hypotheses.jsonl \
    ../../data/longmemeval_oracle.json

# Print metrics
python3 print_qa_metrics.py \
    ../../data/../elf0_mem_sim/benchmarks/longmemeval/results/longmemeval_oracle_elfmem_hypotheses.jsonl.eval-results-gpt-4o \
    ../../data/longmemeval_oracle.json
```

**Step 4: Progress to small dataset**

```bash
cd /Users/emson/Dropbox/devel/projects/ai/elf0_mem_sim
python -m benchmarks.longmemeval.runner --small
# Then evaluate as above with longmemeval_s_cleaned.json
```

**Step 5: Medium dataset (full stress test)**

```bash
python -m benchmarks.longmemeval.runner --medium
# This will take significantly longer — ~500 sessions per question
```

### 7.2 Expected Output Format

The runner produces **two output files**:

**1. Hypothesis JSONL** (for LongMemEval's GPT-4o judge):

```jsonl
{"question_id": "gpt4_2655b836", "hypothesis": "The GPS system was not functioning correctly."}
{"question_id": "89527b6b", "hypothesis": "A 10K race in Portland on July 4th."}
```

**2. Standard report JSON** (conforms to [Benchmark Report Spec](benchmark_report_spec.md)):

```json
{
  "meta": {
    "benchmark": "longmemeval",
    "version": "1.0",
    "timestamp": "2026-04-08T14:30:00Z",
    "duration_seconds": 36000,
    "elfmem_version": "0.5.1",
    "models": {
      "consolidation_llm": "google/gemma-4-26b-a4b",
      "embedding": "text-embedding-nomic-embed-text-v1.5",
      "embedding_dimensions": 768,
      "answer_llm": "google/gemma-4-26b-a4b",
      "judge": "gpt-4o"
    },
    "elfmem_config": { "top_k": 10, "search_window_hours": 10000.0 },
    "lm_studio_base_url": "http://localhost:1234/v1"
  },
  "scores": {
    "overall": 59.4,
    "by_category": {
      "single-session-user":       {"score": 72.9, "count": 70},
      "single-session-assistant":  {"score": 67.9, "count": 56},
      "single-session-preference": {"score": 73.3, "count": 30},
      "multi-session":             {"score": 58.6, "count": 133},
      "temporal-reasoning":        {"score": 45.1, "count": 133},
      "knowledge-update":          {"score": 61.5, "count": 78}
    }
  },
  "baselines": {
    "no_retrieval":      {"overall": 4.0, "by_category": {}},
    "perfect_retrieval": {"overall": 78.0, "by_category": {}}
  },
  "retrieval": {
    "overall_recall": 55.0,
    "by_category": {}
  },
  "efficiency": { "..." : "..." },
  "questions": [
    {
      "id": "gpt4_2655b836",
      "category": "temporal-reasoning",
      "question": "What was the first issue I reported about my new car?",
      "ground_truth": "GPS system not functioning correctly",
      "prediction": "The GPS system was not functioning correctly.",
      "score": 0.85,
      "metric": "f1",
      "retrieval_recall": 1.0,
      "evidence_ids": ["session_abc_2"],
      "retrieved_ids": ["session_abc_2", "session_abc_1"],
      "query_seconds": 5.2
    }
  ]
}
```

The GPT-4o evaluation produces per-type and overall accuracy:

```
single-session-user:        72.9% (51/70)
single-session-assistant:   67.9% (38/56)
single-session-preference:  73.3% (22/30)
multi-session:              58.6% (78/133)
temporal-reasoning:         45.1% (60/133)
knowledge-update:           61.5% (48/78)
---
Task-averaged:              63.2%
Overall:                    59.4%
Abstention:                 56.7% (17/30)
```

---

## 8. Configuration Tuning

### 8.1 Key Knobs

| Parameter | Default | Effect | Recommendation |
|-----------|---------|--------|---------------|
| `top_k` | 10 | Blocks retrieved per query | Start 10, try 15-20 for multi-session |
| `frame_name` | "attention" | Retrieval strategy | "attention" is best for QA |
| `consolidate_after_n_sessions` | 1 | Consolidation frequency | 1 = best quality, N > 1 = faster |
| `inbox_threshold` | 50 | Auto-consolidation trigger | Higher = larger batches |
| `elfmem_llm_model` | gemma-4-26b | Consolidation model | Local, zero cost |
| `answer_model` | gemma-4-26b | Answer generation model | Same local model |
| `search_window_hours` | 10000 | Retrieval time window | Large = search everything |

### 8.2 Tuning for Each Question Type

**Single-session (user/assistant/preference):**
- `top_k=5` is sufficient (answer in one session)
- Standard attention frame works well

**Multi-session:**
- Increase `top_k=15-20` (need blocks from multiple sessions)
- Graph expansion helps — elfmem naturally does this via 1-hop expand

**Temporal reasoning:**
- Dates embedded in content are critical
- Consider increasing `top_k=15` to get more temporal context
- The answer LLM needs to see multiple dated blocks to reason about order

**Knowledge update:**
- elfmem's contradiction detection is a key advantage here
- `consolidate()` detects contradictions and marks them
- The answer LLM should prefer the latest information

**Abstention:**
- When `frame()` returns low-scoring blocks (all scores < 0.3), the system should
  answer "I don't know"
- Add a score threshold check in the answerer

### 8.3 Adaptive Config per Question Type

For maximum performance, you can dispatch different configs per question type:

```python
TYPE_CONFIGS = {
    "single-session-user":       {"top_k": 5,  "frame_name": "attention"},
    "single-session-assistant":  {"top_k": 5,  "frame_name": "attention"},
    "single-session-preference": {"top_k": 5,  "frame_name": "attention"},
    "multi-session":             {"top_k": 20, "frame_name": "attention"},
    "temporal-reasoning":        {"top_k": 15, "frame_name": "attention"},
    "knowledge-update":          {"top_k": 10, "frame_name": "attention"},
}
```

---

## 9. Cost & Performance (Local Models)

### 9.1 API Cost

Since all elfmem operations run **locally via LM Studio**, the only API cost is
the final GPT-4o evaluation judge:

| Component | Cost |
|-----------|------|
| elfmem (learn, consolidate, embed, retrieve, answer) | **$0.00** (local) |
| GPT-4o evaluation judge (500 questions) | **~$2-5** |
| **Total for full benchmark run** | **~$2-5** |

This is a major advantage — you can iterate freely without cost concerns.

### 9.2 Time Estimates (Local Gemma on Apple Silicon)

Local inference is slower than cloud APIs. Gemma 26B on an M-series Mac
typically runs at ~20-40 tokens/sec. The bottleneck is `consolidate()` which
makes one LLM call per block.

| Dataset | Blocks/Q | Consolidation | Answer | Per Question | Total (500) |
|---------|----------|---------------|--------|-------------|-------------|
| Oracle | ~15 | ~30-60s | ~5s | ~1-2 min | ~8-16 hours |
| Small | ~200 | ~7-15 min | ~5s | ~8-15 min | ~3-5 days |
| Medium | ~2500 | ~1.5-3 hrs | ~5s | ~2-3 hrs | ~6-8 weeks |

> **The medium dataset is impractical with local models.** Focus on oracle and small.

### 9.3 Speed Optimisation Strategies

1. **Start with oracle** — validate the pipeline first (manageable at ~8-16 hours)
2. **Batch consolidation** — set `consolidate_after_n_sessions: 5` to reduce
   overhead (fewer session start/stop cycles, same number of LLM calls)
3. **Subsample first** — run on 5-10 questions (`--max=5`) before committing to 500
4. **Increase timeout** — local models can be slow on large blocks;
   the config uses `timeout: 120` for LLM and `60` for embeddings
5. **Monitor LM Studio** — watch GPU utilisation and queue depth. If LM Studio
   queues requests, consolidation will slow dramatically
6. **Consider batch embedding** — LM Studio may support batch embedding requests
   which reduces per-call overhead
7. **Raised contradiction prefilter** — the project config uses `0.65` (vs `0.40`
   default) to skip contradiction LLM calls for dissimilar blocks — critical
   for local model speed

---

## 10. Edge Cases & Mitigations

### 10.1 Empty Retrieval

**Problem**: `frame()` returns no blocks or very low-scoring blocks.
**Mitigation**: Check `len(frame_result.blocks)` and top score. If empty or all
scores below a threshold (e.g., 0.2), return "I don't know."

```python
if not frame_result or not frame_result.blocks:
    return "I don't have enough information to answer this question."

if frame_result.blocks[0].score < 0.15:
    return "I don't have enough information to answer this question."
```

### 10.2 Consolidation Failures

**Problem**: LM Studio timeouts or OOM during consolidation (large blocks, GPU memory).
**Mitigation**: elfmem has built-in retry logic (3 retries by default). The adapter
config uses `timeout: 120` for LLM calls. The runner catches exceptions per
question and writes a fallback "I don't know" answer. If LM Studio crashes under
load, consider reducing `max_tokens` or processing fewer blocks per batch.

### 10.3 Near-Duplicate Content

**Problem**: Similar conversations across sessions get deduplicated by elfmem,
losing distinct instances that matter for counting/temporal questions.
**Mitigation**: Including the session date and index in the content makes
"similar" messages distinct:
```
[2023/03/15] user: I went running → different from
[2023/04/20] user: I went running
```

### 10.4 Token Budget Overflow

**Problem**: elfmem's attention frame has a 2000-token budget. With many relevant
blocks, some may be truncated.
**Mitigation**: Increase the token budget via config or use higher `top_k` with
a custom rendering that prioritises high-scoring blocks.

### 10.5 Abstention Questions

**Problem**: Questions ending in `_abs` are unanswerable — the correct response is
to say "I don't know."
**Mitigation**: The answer system prompt already instructs the LLM to abstain when
context is insufficient. Additionally, low retrieval scores naturally signal
irrelevance. Consider adding explicit abstention logic:

```python
is_abstention_candidate = (
    not frame_result
    or not frame_result.blocks
    or frame_result.blocks[0].score < 0.25
)
```

### 10.6 Very Long Sessions

**Problem**: Some sessions in the medium dataset may have 20+ turns, making
individual `learn()` calls slow in aggregate.
**Mitigation**: Consider learning session summaries instead of individual turns
for very long sessions (>15 turns). Summarise the session first, then learn the
summary. This reduces block count but may lose detail.

### 10.7 Temporal Precision

**Problem**: elfmem's recency scoring uses `active_hours`, not calendar dates.
Questions like "What did I do on March 15th?" rely on dates in content, not
elfmem's native recency model.
**Mitigation**: Dates are embedded in learned content (see Section 5). The
vector search can match date strings, and the answer LLM can reason about them.
For better temporal retrieval, consider adding date-based tags:

```python
tags = [f"date:{date.split()[0]}", f"month:{date.split('/')[1]}"]
```

### 10.8 LM Studio Stability

**Problem**: LM Studio may become unstable during long runs (memory leaks, GPU
thermal throttling, model unloading).
**Mitigation**:
- Monitor LM Studio's system tray / logs during runs
- The runner writes results incrementally (crash-safe) — use `--resume=N` to
  continue after a restart
- Consider restarting LM Studio between dataset runs
- If the embedding model gets unloaded, LM Studio may need manual re-loading
- Keep both models loaded in LM Studio before starting (check the model list)

---

## 11. Interpreting Results

### 11.1 Baseline Comparisons (from the LongMemEval paper)

| System | Oracle | Small | Medium |
|--------|--------|-------|--------|
| GPT-4o (full context) | — | 53.3% | N/A |
| GPT-4o + BM25 RAG | — | 49.2% | 44.8% |
| GPT-4o + Stella RAG | — | 52.1% | 49.3% |
| Claude 3.5 (full context) | — | 51.8% | N/A |

> Note: These are approximate numbers from the paper. Exact figures may vary.
> Check the paper's Table 2 for authoritative numbers.

### 11.2 What "Good" Looks Like for elfmem

**Oracle dataset**: This is the upper bound — only evidence sessions are provided.
If elfmem can't perform well here, the retrieval pipeline needs work.
- **Target**: >55% overall (competitive with full-context baselines)
- **Expectation**: elfmem's graph expansion + dedup should help multi-session Qs

**Small dataset**: The realistic test — 40 sessions of noise.
- **Target**: >50% overall (matching or beating dense RAG baselines)
- **Key advantage**: Contradiction detection should help knowledge-update Qs

**Medium dataset**: The stress test — 500 sessions.
- **Target**: >45% overall (beating RAG baselines that degrade with noise)
- **Key advantage**: Decay model should filter old noise naturally

### 11.3 Per-Type Analysis

After evaluation, look at per-type breakdowns to identify strengths/weaknesses:

- **High single-session scores**: Basic retrieval works
- **Low multi-session scores**: Graph expansion may need tuning
- **Low temporal scores**: Date encoding in content may be insufficient
- **High knowledge-update scores**: Contradiction detection is working
- **Low abstention scores**: Score thresholds need adjustment

---

## 12. Advanced: Ablation Studies

To understand which elfmem features contribute most, run ablations:

### 12.1 Ablation Matrix

| Experiment | Change | Tests |
|------------|--------|-------|
| **No graph expansion** | Use `recall()` instead of `frame()` | Value of knowledge graph |
| **No consolidation** | Skip consolidate, use raw embeddings | Value of LLM processing |
| **No contradiction detection** | Disable in config | Value for knowledge-update Qs |
| **Flat retrieval** | `top_k=10`, no MMR | Value of MMR diversity |
| **Large top_k** | `top_k=30` vs default 10 | Retrieval breadth |
| **Per-type config** | Adaptive config per question type | Specialisation benefit |
| **Session summaries** | Learn summaries not turns | Compression vs detail |

### 12.2 Running Ablations

```bash
# Baseline
python -m benchmarks.longmemeval.runner --oracle

# No graph (use recall instead of frame)
python -m benchmarks.longmemeval.runner --oracle --top-k=10
# (modify adapter to use system.recall() instead of system.frame())

# Large top-k
python -m benchmarks.longmemeval.runner --oracle --top-k=30

# Per-type adaptive
# (modify runner to use TYPE_CONFIGS from Section 8.3)
```

### 12.3 Statistical Significance

With 500 questions, a 2-3% accuracy difference is likely noise. Use McNemar's
test to check significance:

```python
from scipy.stats import chi2

def mcnemar_test(correct_a, correct_b, n):
    """Compare two systems on the same questions."""
    # b = A correct, B wrong; c = A wrong, B correct
    b = sum(a and not b for a, b in zip(correct_a, correct_b))
    c = sum(not a and b for a, b in zip(correct_a, correct_b))
    if b + c == 0:
        return 1.0  # No difference
    stat = (abs(b - c) - 1) ** 2 / (b + c)
    return 1 - chi2.cdf(stat, df=1)
```

---

## Quick Reference: Command Cheat Sheet

```bash
# === SETUP ===
git clone https://github.com/xiaowu0162/LongMemEval.git ../LongMemEval
cd ../LongMemEval/data && wget <3 data URLs above>

# === RUN ===
cd /Users/emson/Dropbox/devel/projects/ai/elf0_mem_sim

# Smoke test (5 questions)
python -m benchmarks.longmemeval.runner --oracle --test

# Full oracle run with baselines (recommended)
python -m benchmarks.longmemeval.runner --oracle --baselines

# Full oracle run without baselines (faster)
python -m benchmarks.longmemeval.runner --oracle

# Small dataset
python -m benchmarks.longmemeval.runner --small --baselines

# Resume from question 200
python -m benchmarks.longmemeval.runner --small --resume=200

# === EVALUATE ===
cd ../LongMemEval/src/evaluation

python3 evaluate_qa.py gpt-4o \
    PATH_TO_HYPOTHESES.jsonl \
    ../../data/longmemeval_oracle.json

python3 print_qa_metrics.py \
    PATH_TO_HYPOTHESES.jsonl.eval-results-gpt-4o \
    ../../data/longmemeval_oracle.json
```

---

## Next Steps

1. **Ensure LM Studio is running** with both `google/gemma-4-26b-a4b` and
   `text-embedding-nomic-embed-text-v1.5` loaded
2. **Create the `benchmarks/longmemeval/` directory** and implement the files above
3. **Smoke test** with `--oracle --test` (5 questions, ~5-10 min, $0.00)
4. **Full oracle run** (500 questions, ~8-16 hours, $0.00)
5. **Evaluate** with GPT-4o judge (~$2-5) and compare against paper baselines
6. **Tune** based on per-type accuracy breakdown
7. **Progress** to small dataset (oracle must work first)
8. **Run ablations** to identify which elfmem features matter most
