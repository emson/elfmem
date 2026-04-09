# Evaluating elfmem with LoCoMo

A step-by-step guide to benchmarking elfmem against
[LoCoMo](https://github.com/snap-research/locomo) (ACL 2024) — the most-cited
long-term conversational memory benchmark.

**Why this benchmark?** LoCoMo is the standard. Results here are credible and
comparable across the field. It tests multi-hop reasoning (elfmem's graph
expansion), temporal reasoning (decay-aware scoring), and adversarial questions
(abstention). 725 GitHub stars, widely cited.

**Report format:** Output conforms to the
[Benchmark Report Spec](benchmark_report_spec.md) for cross-benchmark comparison.
Includes mandatory baselines (no-retrieval floor, perfect-retrieval ceiling) and
retrieval recall tracking.

---

## Table of Contents

1. [What LoCoMo Tests](#1-what-locomo-tests)
2. [How elfmem Maps to the Five Categories](#2-how-elfmem-maps-to-the-five-categories)
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

## 1. What LoCoMo Tests

10 long conversations between two speakers, each spanning 19-32 sessions with
369-689 turns. Total: **1,986 QA pairs** across five categories.

| Category | Name | Count | What It Tests |
|----------|------|-------|---------------|
| 1 | **Multi-hop** | 282 | Combine facts from multiple turns/sessions |
| 2 | **Temporal** | 321 | Reason about when events happened |
| 3 | **Open-ended** | 96 | Provide explanations and summaries |
| 4 | **Single-hop** | 841 | Retrieve a specific fact from one turn |
| 5 | **Adversarial** | 446 | Correctly say "not mentioned" for things never discussed |

### Key Metrics

- **Token-level F1** (categories 1-4): normalized, stemmed token overlap
- **Binary accuracy** (category 5): must output "not mentioned" or "no information available"
- **Retrieval recall**: fraction of evidence dialog IDs retrieved (when using RAG)

### Baselines (from the paper)

| System | Overall F1 |
|--------|-----------|
| Human ceiling | 87.9 |
| GPT-4 (full context) | 32.1 |
| RAG (Dragon retriever + GPT-4) | ~53 |
| Letta (file-based memory) | 74.0 (claimed) |

---

## 2. How elfmem Maps to the Five Categories

| Category | elfmem Feature | Expected Advantage |
|---|---|---|
| Multi-hop | Graph expansion (1-hop) + edge-based co-retrieval | Related facts connected via edges get retrieved together |
| Temporal | Dates in content + decay-aware recency scoring | Recent events score higher naturally |
| Open-ended | Frame assembly with token budget rendering | Diverse context via MMR for richer answers |
| Single-hop | 5-stage hybrid retrieval | Solid baseline — vector + graph + MMR |
| Adversarial | Low retrieval scores → abstention | Score threshold signals "nothing relevant found" |

---

## 3. Setup

### 3.1 Clone LoCoMo

```bash
cd /Users/emson/Dropbox/devel/projects/ai
git clone https://github.com/snap-research/locomo.git
cd locomo
```

### 3.2 Dependencies

LoCoMo's `requirements.txt` is a conda export with GPU-heavy packages. We only
need the evaluation metrics:

```bash
# From elfmem project root
cd /Users/emson/Dropbox/devel/projects/ai/elf0_mem_sim

pip install nltk rouge-score tqdm
```

The `bert-score` package is optional (used in their evaluation but we'll use F1
as the primary metric, matching the paper's main results).

### 3.3 Dataset

The dataset is included in the repo:

```bash
ls /Users/emson/Dropbox/devel/projects/ai/locomo/data/locomo10.json
```

Verify:
```bash
python3 -c "
import json
data = json.load(open('/Users/emson/Dropbox/devel/projects/ai/locomo/data/locomo10.json'))
print(f'{len(data)} conversations')
total_qa = sum(len(d['qa']) for d in data)
print(f'{total_qa} total QA pairs')
for d in data:
    sess_count = sum(1 for k in d['conversation'] if k.startswith('session_') and not k.endswith('_date_time'))
    print(f'  {d[\"sample_id\"]}: {sess_count} sessions, {len(d[\"qa\"])} QA pairs')
"
```

Expected: 10 conversations, 1,986 total QA pairs.

### 3.4 LM Studio

Ensure both models are loaded:
- `google/gemma-4-26b-a4b`
- `text-embedding-nomic-embed-text-v1.5`

```bash
curl -s http://localhost:1234/v1/models | python3 -m json.tool
```

### 3.5 API Keys

```bash
# No API keys needed for elfmem operations (all local).
# Only if you want to compare against their GPT baselines:
export OPENAI_API_KEY="sk-..."
```

---

## 4. Data Format

Each of the 10 conversations in `locomo10.json`:

```json
{
  "sample_id": "conv-26",
  "conversation": {
    "speaker_a": "Caroline",
    "speaker_b": "Melanie",
    "session_1_date_time": "1:56 pm on 8 May, 2023",
    "session_1": [
      {
        "speaker": "Caroline",
        "dia_id": "D1:1",
        "text": "Hey Mel! Good to see you! How have you been?"
      },
      {
        "speaker": "Melanie",
        "dia_id": "D1:2",
        "text": "Hey Caroline! Good to see you! ..."
      }
    ],
    "session_2_date_time": "3:30 pm on 15 May, 2023",
    "session_2": [...]
  },
  "observation": {
    "session_1_observation": {
      "Caroline": [
        ["Caroline attended an LGBTQ support group recently.", "D1:3"]
      ],
      "Melanie": [...]
    }
  },
  "session_summary": {
    "session_1_summary": "Caroline and Melanie had a conversation on 8 May 2023..."
  },
  "qa": [
    {
      "question": "When did Caroline go to the LGBTQ support group?",
      "answer": "7 May 2023",
      "evidence": ["D1:3"],
      "category": 2
    },
    {
      "question": "What did Caroline realize after her charity race?",
      "evidence": ["D2:3"],
      "category": 5,
      "adversarial_answer": "self-care is important"
    }
  ]
}
```

### Key Details

- **Dialog IDs** follow `D{session}:{turn}` format (e.g., `D1:3` = session 1, turn 3)
- **Sessions** are numbered `session_1`, `session_2`, etc. with parallel `session_N_date_time`
- **Evidence** lists which dialog IDs contain the answer (for retrieval recall)
- **Category 5** (adversarial) questions have `adversarial_answer` but often no `answer`
  field — the correct response is "not mentioned in the conversation"
- **Observations** are pre-extracted facts per session per speaker (optional retrieval units)
- **Session summaries** are pre-generated (optional retrieval units)

---

## 5. Architecture: Integration Approach

### Three Retrieval Modes in LoCoMo

LoCoMo supports three retrieval granularities:
1. **dialog** — individual dialog turns as retrieval units
2. **observation** — LLM-extracted facts per session
3. **summary** — session-level summaries

For elfmem, we use **dialog mode** — each turn becomes a block. This maps most
naturally to elfmem's `learn()` operation and preserves the dialog IDs needed
for retrieval recall computation.

### Flow

```
┌──────────────────────────────────────────────────────────┐
│  For each of 10 conversations:                           │
│                                                          │
│  1. Create fresh elfmem DB                               │
│  2. Replay all sessions chronologically                  │
│     └─ learn() each turn with date + speaker + dia_id    │
│     └─ consolidate() after each session                  │
│  3. For each QA pair:                                    │
│     a. frame("attention", question) → context            │
│     b. Gemma generates answer from context               │
│     c. Extract retrieved dia_ids for recall metric       │
│     d. Compute F1 / binary accuracy                      │
│  4. Write results                                        │
│                                                          │
│  Output: results JSON with per-category F1 + recall      │
└──────────────────────────────────────────────────────────┘
```

### Why Track Dialog IDs?

LoCoMo evaluates both **answer quality** (F1) and **retrieval quality** (recall of
evidence dialog IDs). To compute retrieval recall, we need to know which dialog
IDs elfmem retrieved — not just the text.

We embed the dialog ID in each block's tags: `tags=["dia:D1:3"]`. After retrieval,
we extract these tags from the returned `ScoredBlock` objects.

---

## 6. Implementation

### 6.1 Project Structure

```
elf0_mem_sim/
├── benchmarks/
│   ├── longmemeval/          # (existing)
│   ├── memoryagentbench/     # (existing)
│   └── locomo/
│       ├── __init__.py
│       ├── runner.py          # Main benchmark runner
│       ├── adapter.py         # elfmem ↔ LoCoMo adapter
│       ├── metrics.py         # F1, adversarial accuracy, retrieval recall
│       ├── config.py          # Benchmark configuration
│       └── results/
│           └── .gitkeep
```

### 6.2 Metrics (`metrics.py`)

Matching LoCoMo's evaluation exactly (from `task_eval/evaluation.py`):

```python
"""Metrics matching LoCoMo's evaluation (evaluation.py)."""

from __future__ import annotations

import re
import string
from collections import Counter

from nltk.stem import PorterStemmer

_ps = PorterStemmer()

# Category 5 abstention phrases (from LoCoMo's gpt_utils.py)
ABSTENTION_PHRASES = ["no information available", "not mentioned"]


def normalize_answer(s: str) -> str:
    """Lower, remove articles/punctuation/whitespace (matches LoCoMo)."""
    s = s.lower()
    s = re.sub(r"\b(a|an|the|and)\b", " ", s)
    exclude = set(string.punctuation)
    s = "".join(ch for ch in s if ch not in exclude)
    return " ".join(s.split())


def f1_score(prediction: str, ground_truth: str) -> float:
    """Token-level F1 with Porter stemming (matches LoCoMo exactly)."""
    pred_tokens = [_ps.stem(w) for w in normalize_answer(prediction).split()]
    gt_tokens = [_ps.stem(w) for w in normalize_answer(ground_truth).split()]
    if not pred_tokens and not gt_tokens:
        return 1.0
    if not pred_tokens or not gt_tokens:
        return 0.0
    common = Counter(pred_tokens) & Counter(gt_tokens)
    num_same = sum(common.values())
    if num_same == 0:
        return 0.0
    precision = num_same / len(pred_tokens)
    recall = num_same / len(gt_tokens)
    return (2 * precision * recall) / (precision + recall)


def multihop_f1(prediction: str, ground_truth: str) -> float:
    """Multi-hop F1: split answer by comma, compute partial F1 for each sub-answer.

    Matches LoCoMo's evaluation.py::f1() function for category 1.
    """
    gt_parts = [part.strip() for part in ground_truth.split(",")]
    if len(gt_parts) <= 1:
        return f1_score(prediction, ground_truth)

    scores = []
    for gt_part in gt_parts:
        scores.append(f1_score(prediction, gt_part))
    return sum(scores) / len(scores) if scores else 0.0


def adversarial_score(prediction: str) -> float:
    """Category 5: binary accuracy — does the prediction abstain?"""
    pred_lower = prediction.lower()
    for phrase in ABSTENTION_PHRASES:
        if phrase in pred_lower:
            return 1.0
    return 0.0


def retrieval_recall(retrieved_dia_ids: list[str], evidence_dia_ids: list[str]) -> float:
    """Fraction of evidence dialog IDs that were retrieved."""
    if not evidence_dia_ids:
        return 1.0  # no evidence needed
    retrieved_set = set(retrieved_dia_ids)
    hits = sum(1 for eid in evidence_dia_ids if eid in retrieved_set)
    return hits / len(evidence_dia_ids)


def score_question(
    prediction: str,
    qa: dict,
) -> dict[str, float]:
    """Score a single question based on its category."""
    category = qa["category"]

    if category == 5:
        return {"score": adversarial_score(prediction), "metric": "adversarial_accuracy"}

    answer = qa.get("answer", "")

    if category == 1:
        return {"score": multihop_f1(prediction, answer), "metric": "multihop_f1"}

    if category == 3:
        # Open-ended: take first alternative (split by semicolon)
        answer = answer.split(";")[0].strip()

    return {"score": f1_score(prediction, answer), "metric": "f1"}
```

### 6.3 Configuration (`config.py`)

```python
"""Benchmark configuration for LoCoMo."""

from dataclasses import dataclass
from pathlib import Path


@dataclass
class LoCoMoConfig:
    """Configuration for the LoCoMo benchmark run."""

    # Data
    data_file: Path = Path("../locomo/data/locomo10.json")
    output_dir: Path = Path("benchmarks/locomo/results")

    # LM Studio settings
    lm_studio_base_url: str = "http://localhost:1234/v1"

    # elfmem settings
    elfmem_llm_model: str = "google/gemma-4-26b-a4b"
    elfmem_embedding_model: str = "text-embedding-nomic-embed-text-v1.5"
    elfmem_embedding_dimensions: int = 768
    top_k: int = 10
    inbox_threshold: int = 50
    search_window_hours: float = 10000.0
    contradiction_similarity_prefilter: float = 0.65

    # Answer generation
    answer_model: str = "google/gemma-4-26b-a4b"
    answer_max_tokens: int = 300

    # Execution
    max_conversations: int | None = None  # None = all 10
    max_qa_per_conversation: int | None = None  # None = all
    categories: list[int] | None = None  # None = all [1,2,3,4,5]
    verbose: bool = False
```

### 6.4 The Adapter (`adapter.py`)

```python
"""elfmem adapter for LoCoMo — session replay + per-question retrieval."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from elfmem import ElfmemConfig, MemorySystem
from elfmem.types import FrameResult


def extract_sessions(conversation: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract ordered sessions from a LoCoMo conversation dict.

    Returns list of dicts with keys: session_num, date_time, turns.
    Each turn has: speaker, dia_id, text.
    """
    sessions = []
    for i in range(1, 50):  # LoCoMo scans range(1, 50) for sessions
        key = f"session_{i}"
        date_key = f"session_{i}_date_time"
        if key not in conversation:
            break
        sessions.append({
            "session_num": i,
            "date_time": conversation.get(date_key, f"Session {i}"),
            "turns": conversation[key],
        })
    return sessions


async def ingest_conversation(
    system: MemorySystem,
    conversation: dict[str, Any],
) -> dict[str, Any]:
    """Replay a full LoCoMo conversation into elfmem.

    Learns each turn with embedded date, speaker, and dia_id.
    Consolidates after each session.

    Returns debug info dict.
    """
    sessions = extract_sessions(conversation)
    total_turns = 0
    total_consolidated = 0

    for session in sessions:
        date_time = session["date_time"]
        session_num = session["session_num"]

        await system.begin_session(task_type="ingestion")

        for turn in session["turns"]:
            speaker = turn["speaker"]
            dia_id = turn["dia_id"]
            text = turn["text"]

            # Embed date, speaker, and dia_id in content for retrieval
            content = f"[{date_time}] {speaker}: {text}"

            await system.learn(
                content=content,
                tags=[f"dia:{dia_id}", f"session:{session_num}", f"speaker:{speaker}"],
                category="conversation",
                source="locomo",
            )
            total_turns += 1

        await system.end_session()

        # Consolidate after each session
        await system.begin_session(task_type="consolidation")
        result = await system.consolidate()
        total_consolidated += result.processed
        await system.end_session()

    return {
        "sessions": len(sessions),
        "turns": total_turns,
        "consolidated": total_consolidated,
    }


def extract_dia_ids_from_blocks(blocks: list) -> list[str]:
    """Extract dialog IDs from retrieved ScoredBlock tags.

    Blocks are tagged with "dia:D1:3" format during ingestion.
    """
    dia_ids = []
    for block in blocks:
        if hasattr(block, "tags") and block.tags:
            for tag in block.tags:
                if tag.startswith("dia:"):
                    dia_ids.append(tag[4:])  # strip "dia:" prefix
    return dia_ids


async def process_conversation(
    conversation_data: dict[str, Any],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    """Ingest one LoCoMo conversation and answer all its QA pairs.

    Args:
        conversation_data: One entry from locomo10.json
        config: Benchmark configuration dict

    Returns:
        List of per-question result dicts
    """
    conversation = conversation_data["conversation"]
    qa_pairs = conversation_data["qa"]

    base_url = config.get("lm_studio_base_url", "http://localhost:1234/v1")

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

    with tempfile.NamedTemporaryFile(suffix=".db", delete=True) as tmp:
        db_path = tmp.name

    system = await MemorySystem.from_config(db_path, config=elfmem_cfg)

    try:
        # Phase 1: Ingest entire conversation
        ingest_info = await ingest_conversation(system, conversation)

        # Phase 2: Answer each question
        results: list[dict[str, Any]] = []

        # Filter categories if specified
        categories = config.get("categories")
        max_qa = config.get("max_qa_per_conversation")

        filtered_qa = qa_pairs
        if categories:
            filtered_qa = [qa for qa in filtered_qa if qa["category"] in categories]
        if max_qa:
            filtered_qa = filtered_qa[:max_qa]

        for qa in filtered_qa:
            question = qa["question"]
            category = qa["category"]

            # Build query — include adversarial framing for category 5
            if category == 5:
                adversarial_answer = qa.get("adversarial_answer", "")
                query = question
                # The answer prompt will handle the multiple-choice framing
            else:
                query = question

            await system.begin_session(task_type="retrieval")
            frame_result = await system.frame("attention", query=query)
            await system.end_session()

            # Extract retrieved dialog IDs for recall computation
            retrieved_dia_ids = []
            if frame_result and frame_result.blocks:
                retrieved_dia_ids = extract_dia_ids_from_blocks(frame_result.blocks)

            results.append({
                "question": question,
                "category": category,
                "qa": qa,
                "frame_result": frame_result,
                "retrieved_dia_ids": retrieved_dia_ids,
                "ingest_info": ingest_info,
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

### 6.5 The Answer Generator

For LoCoMo, the answerer needs category-specific prompting. Category 5 (adversarial)
requires a specific "not mentioned" response format.

```python
"""Answer generation for LoCoMo — category-aware prompting."""

from __future__ import annotations

import random

from openai import AsyncOpenAI

from elfmem.types import FrameResult

DEFAULT_BASE_URL = "http://localhost:1234/v1"

SYSTEM_PROMPT = """You are a helpful assistant answering questions about past conversations
between two people. Answer based ONLY on the provided conversation context.

Rules:
- Be concise and specific
- For temporal questions, cite specific dates
- For multi-part questions, address each part
- If the information is not in the context, say "no information available"
- Include names, dates, and details when available"""

QA_TEMPLATE = """## Conversation Context

{context}

## Question

{question}

## Answer

Provide a concise, direct answer based on the conversation context above."""

# Category 5: adversarial multiple-choice
ADVERSARIAL_TEMPLATE = """## Conversation Context

{context}

## Question

{question}

Choose the correct answer:
{choices}

## Answer

State which option is correct and explain briefly."""


async def generate_locomo_answer(
    frame_result: FrameResult | None,
    qa: dict,
    model: str = "google/gemma-4-26b-a4b",
    max_tokens: int = 300,
    base_url: str = DEFAULT_BASE_URL,
) -> str:
    """Generate an answer to a LoCoMo question.

    Category 5 questions get adversarial multiple-choice framing.
    """
    if frame_result is None or not frame_result.blocks:
        return "no information available"

    context = frame_result.text
    question = qa["question"]
    category = qa["category"]

    if category == 5:
        # Adversarial: present as multiple choice (matching LoCoMo's format)
        adversarial_answer = qa.get("adversarial_answer", "unknown")
        options = [
            ("Not mentioned in the conversation", True),
            (adversarial_answer, False),
        ]
        # Randomise order (matching LoCoMo)
        random.shuffle(options)
        labels = ["a", "b"]
        choices = "\n".join(f"({labels[i]}) {opt[0]}" for i, opt in enumerate(options))

        prompt = ADVERSARIAL_TEMPLATE.format(
            context=context, question=question, choices=choices
        )
    else:
        prompt = QA_TEMPLATE.format(context=context, question=question)

    client = AsyncOpenAI(base_url=base_url, api_key="lm-studio")

    response = await client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        temperature=0.0,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    )

    return response.choices[0].message.content or "no information available"
```

### 6.6 The Runner (`runner.py`)

```python
"""Main LoCoMo runner — processes conversations and computes metrics."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from collections import defaultdict
from pathlib import Path

from benchmarks.locomo.adapter import process_conversation
from benchmarks.locomo.answerer import generate_locomo_answer
from benchmarks.locomo.config import LoCoMoConfig
from benchmarks.locomo.metrics import retrieval_recall, score_question

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

CATEGORY_NAMES = {
    1: "multi-hop",
    2: "temporal",
    3: "open-ended",
    4: "single-hop",
    5: "adversarial",
}


async def run_benchmark(cfg: LoCoMoConfig) -> Path:
    """Run the full LoCoMo benchmark."""

    data_path = cfg.data_file.resolve()
    if not data_path.exists():
        raise FileNotFoundError(f"Data file not found: {data_path}")

    with open(data_path) as f:
        data = json.load(f)

    log.info(f"Loaded {len(data)} conversations from {data_path.name}")

    if cfg.max_conversations:
        data = data[:cfg.max_conversations]

    cfg.output_dir.mkdir(parents=True, exist_ok=True)

    adapter_config = {
        "lm_studio_base_url": cfg.lm_studio_base_url,
        "elfmem_llm_model": cfg.elfmem_llm_model,
        "elfmem_embedding_model": cfg.elfmem_embedding_model,
        "elfmem_embedding_dimensions": cfg.elfmem_embedding_dimensions,
        "top_k": cfg.top_k,
        "inbox_threshold": cfg.inbox_threshold,
        "search_window_hours": cfg.search_window_hours,
        "contradiction_similarity_prefilter": cfg.contradiction_similarity_prefilter,
        "categories": cfg.categories,
        "max_qa_per_conversation": cfg.max_qa_per_conversation,
    }

    all_results: list[dict] = []
    category_scores: dict[int, list[float]] = defaultdict(list)
    category_recalls: dict[int, list[float]] = defaultdict(list)
    total_time = 0.0

    output_path = cfg.output_dir / "locomo_elfmem_results.json"

    for conv_idx, conv_data in enumerate(data):
        sample_id = conv_data["sample_id"]
        n_qa = len(conv_data["qa"])
        start = time.monotonic()

        log.info(f"\n[{conv_idx+1}/{len(data)}] {sample_id}: {n_qa} QA pairs")

        try:
            qa_results = await process_conversation(conv_data, adapter_config)

            ingest_info = qa_results[0]["ingest_info"] if qa_results else {}
            log.info(
                f"  Ingested: {ingest_info.get('sessions', 0)} sessions, "
                f"{ingest_info.get('turns', 0)} turns, "
                f"{ingest_info.get('consolidated', 0)} consolidated"
            )

            for qr in qa_results:
                # Generate answer
                hypothesis = await generate_locomo_answer(
                    frame_result=qr["frame_result"],
                    qa=qr["qa"],
                    model=cfg.answer_model,
                    max_tokens=cfg.answer_max_tokens,
                    base_url=cfg.lm_studio_base_url,
                )

                # Score
                score_info = score_question(hypothesis, qr["qa"])
                category = qr["category"]

                # Retrieval recall
                evidence = qr["qa"].get("evidence", [])
                recall = retrieval_recall(qr["retrieved_dia_ids"], evidence)

                result_entry = {
                    "sample_id": sample_id,
                    "question": qr["question"],
                    "category": category,
                    "category_name": CATEGORY_NAMES.get(category, "unknown"),
                    "prediction": hypothesis,
                    "answer": qr["qa"].get("answer", ""),
                    "evidence": evidence,
                    "retrieved_dia_ids": qr["retrieved_dia_ids"],
                    "score": score_info["score"],
                    "metric": score_info["metric"],
                    "retrieval_recall": recall,
                }
                all_results.append(result_entry)
                category_scores[category].append(score_info["score"])
                if category != 5:  # recall not meaningful for adversarial
                    category_recalls[category].append(recall)

            elapsed = time.monotonic() - start
            total_time += elapsed
            log.info(f"  {elapsed:.1f}s | {len(qa_results)} Qs answered")

        except Exception as e:
            log.error(f"  ERROR on {sample_id}: {e}")
            elapsed = time.monotonic() - start
            total_time += elapsed

    # --- Aggregate metrics ---
    summary = {}
    all_scores = []
    for cat in sorted(category_scores.keys()):
        scores = category_scores[cat]
        avg = sum(scores) / len(scores) if scores else 0.0
        name = CATEGORY_NAMES.get(cat, f"cat_{cat}")
        summary[name] = {
            "score": round(avg * 100, 1),
            "count": len(scores),
        }
        all_scores.extend(scores)

        recalls = category_recalls.get(cat, [])
        if recalls:
            summary[name]["retrieval_recall"] = round(
                sum(recalls) / len(recalls) * 100, 1
            )

    overall = sum(all_scores) / len(all_scores) if all_scores else 0.0
    summary["overall"] = {"score": round(overall * 100, 1), "count": len(all_scores)}

    # Overall retrieval recall
    all_recalls = [r for recalls in category_recalls.values() for r in recalls]
    if all_recalls:
        summary["overall"]["retrieval_recall"] = round(
            sum(all_recalls) / len(all_recalls) * 100, 1
        )

    output = {
        "benchmark": "LoCoMo",
        "agent": "elfmem",
        "summary": summary,
        "total_time_seconds": round(total_time, 1),
        "data": all_results,
    }

    with open(output_path, "w") as f:
        json.dump(output, f, indent=2, default=str)

    # Print summary
    log.info(f"\n{'='*60}")
    log.info("LoCoMo Results (elfmem)")
    log.info(f"{'='*60}")
    for name, info in summary.items():
        recall_str = f"  recall={info['retrieval_recall']}%" if "retrieval_recall" in info else ""
        log.info(f"  {name:15s}: {info['score']:5.1f}%  (n={info['count']}){recall_str}")
    log.info(f"\n  Total time: {total_time:.0f}s")
    log.info(f"  Results: {output_path}")

    return output_path


def main():
    """CLI entry point."""
    cfg = LoCoMoConfig()

    args = sys.argv[1:]

    if "--test" in args:
        cfg.max_conversations = 1
        cfg.max_qa_per_conversation = 5
        cfg.verbose = True

    # Category filters
    if "--multihop" in args:
        cfg.categories = [1]
    elif "--temporal" in args:
        cfg.categories = [2]
    elif "--openended" in args:
        cfg.categories = [3]
    elif "--singlehop" in args:
        cfg.categories = [4]
    elif "--adversarial" in args:
        cfg.categories = [5]

    for arg in args:
        if arg.startswith("--top-k="):
            cfg.top_k = int(arg.split("=")[1])
        if arg.startswith("--max-conv="):
            cfg.max_conversations = int(arg.split("=")[1])
        if arg.startswith("--max-qa="):
            cfg.max_qa_per_conversation = int(arg.split("=")[1])

    output_path = asyncio.run(run_benchmark(cfg))
    print(f"\nResults: {output_path}")


if __name__ == "__main__":
    main()
```

---

## 7. Running the Evaluation

### 7.1 Step-by-Step

**Step 1: Smoke test (1 conversation, 5 questions)**

```bash
cd /Users/emson/Dropbox/devel/projects/ai/elf0_mem_sim

python -m benchmarks.locomo.runner --test
```

This ingests 1 conversation (~20-30 sessions, ~400 turns) and answers 5 questions.
Expect ~10-20 minutes with local Gemma (consolidation is the bottleneck).

**Step 2: Single-hop only (easiest category)**

```bash
python -m benchmarks.locomo.runner --singlehop --max-conv=3
```

Tests basic retrieval across 3 conversations.

**Step 3: Full run (all 10 conversations, all categories)**

```bash
python -m benchmarks.locomo.runner
```

### 7.2 Output Format

Conforms to the [Benchmark Report Spec](benchmark_report_spec.md). Saved to
`benchmarks/locomo/results/{timestamp}_locomo_elfmem.json`:

```json
{
  "meta": {
    "benchmark": "locomo",
    "version": "1.0",
    "timestamp": "2026-04-08T14:30:00Z",
    "duration_seconds": 12345,
    "elfmem_version": "0.5.1",
    "models": {
      "consolidation_llm": "google/gemma-4-26b-a4b",
      "embedding": "text-embedding-nomic-embed-text-v1.5",
      "embedding_dimensions": 768,
      "answer_llm": "google/gemma-4-26b-a4b",
      "judge": null
    },
    "elfmem_config": {
      "top_k": 10,
      "inbox_threshold": 50,
      "search_window_hours": 10000.0,
      "contradiction_similarity_prefilter": 0.65,
      "curate_interval_hours": 1000.0
    },
    "lm_studio_base_url": "http://localhost:1234/v1"
  },
  "scores": {
    "overall": 47.3,
    "by_category": {
      "single-hop":   {"score": 52.3, "count": 841},
      "multi-hop":    {"score": 45.2, "count": 282},
      "temporal":     {"score": 38.7, "count": 321},
      "open-ended":   {"score": 32.1, "count": 96},
      "adversarial":  {"score": 68.4, "count": 446}
    }
  },
  "baselines": {
    "no_retrieval":      {"overall": 3.2, "by_category": {}},
    "perfect_retrieval": {"overall": 72.1, "by_category": {}}
  },
  "retrieval": {
    "overall_recall": 59.4,
    "by_category": {
      "single-hop": {"recall": 71.2, "count": 841},
      "multi-hop":  {"recall": 62.3, "count": 282},
      "temporal":   {"recall": 55.1, "count": 321},
      "open-ended": {"recall": 48.9, "count": 96}
    }
  },
  "efficiency": {
    "total_memorization_seconds": 12000,
    "total_query_seconds": 450,
    "avg_query_seconds": 3.2,
    "total_blocks_learned": 5000,
    "total_blocks_active": 4200
  },
  "questions": [
    {
      "id": "conv-26_q0",
      "category": "temporal",
      "question": "When did Caroline go to the LGBTQ support group?",
      "ground_truth": "7 May 2023",
      "prediction": "May 7, 2023",
      "score": 0.85,
      "metric": "f1",
      "retrieval_recall": 1.0,
      "evidence_ids": ["D1:3"],
      "retrieved_ids": ["D1:3", "D1:5", "D2:1"],
      "query_seconds": 3.2
    }
  ]
}
```

**Key fields:**
- `baselines` — no-retrieval (floor) and perfect-retrieval (ceiling) establish
  context for scores. See [Benchmark Report Spec](benchmark_report_spec.md) for
  the normalised effectiveness formula.
- `retrieval` — evidence recall, separate from answer quality
- `questions[].prediction` — standardised field name (not `hypothesis` or `output`)

---

## 8. Configuration Tuning

### 8.1 Key Parameters

| Parameter | Default | Effect |
|---|---|---|
| `top_k` | 10 | Blocks retrieved per query |
| `search_window_hours` | 10000 | Wide = search everything |
| `contradiction_similarity_prefilter` | 0.65 | Higher = fewer (faster) contradiction checks |

### 8.2 Per-Category Tuning

| Category | Recommended `top_k` | Notes |
|---|---|---|
| Single-hop (4) | 5 | Answer in one turn — fewer blocks = less noise |
| Multi-hop (1) | 15-20 | Need facts from multiple sessions |
| Temporal (2) | 10-15 | Need dated context to reason about time |
| Open-ended (3) | 15-20 | Diverse context for richer answers |
| Adversarial (5) | 5 | Low scores → abstention; fewer blocks → cleaner signal |

---

## 9. Performance & Time Estimates

All local via LM Studio — $0 API cost.

| Scope | Turns | Consolidation | Questions | Est. Time |
|---|---|---|---|---|
| 1 conversation | ~400 | ~400 LLM calls | ~200 | 2-6 hours |
| 3 conversations | ~1200 | ~1200 LLM calls | ~600 | 6-18 hours |
| All 10 | ~5000 | ~5000 LLM calls | ~1986 | 2-5 days |

**Bottleneck:** `consolidate()` — one Gemma call per turn. At ~10s per call
with local Gemma, 400 turns = ~67 minutes of pure consolidation per conversation.

### Speed Optimisation

1. **Run conversations in parallel** (if LM Studio handles concurrent requests)
2. **Increase `inbox_threshold`** to batch consolidation (fewer but larger batches)
3. **Start with fewer conversations** (`--max-conv=3`) to validate quickly

---

## 10. Edge Cases & Mitigations

### 10.1 Category 5 Scoring Sensitivity

The adversarial score checks if the output contains "not mentioned" or
"no information available" as substrings. If Gemma phrases its abstention
differently (e.g., "I cannot find this in the conversation"), it scores 0.

**Mitigation:** The system prompt explicitly instructs the LLM to use
"no information available" phrasing. Verify with the smoke test.

### 10.2 Dialog ID Tracking

Retrieval recall requires knowing which `dia_id` tags are on retrieved blocks.
If elfmem's consolidation merges or deduplicates blocks, tags may be lost.

**Mitigation:** Monitor retrieval recall separately from F1. If recall is
low but F1 is reasonable, the content is being retrieved but dia_id tracking
is broken — check tag preservation through consolidation.

### 10.3 Long Conversations (30+ Sessions)

Some conversations have 30+ sessions with 600+ turns. This means 600+
consolidation LLM calls per conversation.

**Mitigation:** Start with shorter conversations. Use `--max-conv=1` and
pick a conversation with fewer sessions for initial validation.

### 10.4 Multi-Hop Requires Graph

Multi-hop questions need facts from different sessions connected by reasoning.
elfmem's 1-hop graph expansion should help — if session-crossing edges are
created during consolidation.

**Mitigation:** Monitor `edges_created` in consolidation results. If few
edges are created, the graph expansion won't help multi-hop. Consider
increasing `edge_score_threshold` sensitivity.

### 10.5 Open-Ended Scoring

Category 3 uses F1 on just the first semicolon-separated answer alternative.
Gemma may generate verbose answers that dilute F1 (low precision).

**Mitigation:** Instruct the LLM to be concise. Consider post-processing
to extract the core answer.

---

## 11. Interpreting Results

### 11.1 Baseline Comparison

| System | Cat 1 (MH) | Cat 2 (T) | Cat 3 (OE) | Cat 4 (SH) | Cat 5 (Adv) | Overall |
|---|---|---|---|---|---|---|
| Human | — | — | — | — | — | 87.9 |
| GPT-4 full context | — | — | — | — | — | 32.1 |
| RAG (Dragon+GPT-4) | — | — | — | — | — | ~53 |
| Letta (claimed) | — | — | — | — | — | 74.0 |

> Note: Per-category baselines vary by paper. Overall F1 is the primary metric.

### 11.2 What "Good" Looks Like

- **Overall F1 > 40%**: elfmem is competitive as a memory system
- **Overall F1 > 53%**: elfmem beats RAG baselines
- **Category 1 (multi-hop) significantly above baseline**: graph expansion is working
- **Category 5 > 70%**: abstention via score thresholds is working
- **Retrieval recall > 60%**: the hybrid pipeline is finding evidence turns

### 11.3 Reporting

Present results as a table with per-category breakdown. The per-category split
shows where elfmem's features help most (multi-hop = graph, temporal = decay,
adversarial = abstention).

---

## Quick Reference

```bash
# === SETUP ===
git clone https://github.com/snap-research/locomo.git ../locomo
pip install nltk rouge-score tqdm

# === RUN ===
cd /Users/emson/Dropbox/devel/projects/ai/elf0_mem_sim

# Smoke test (1 conversation, 5 questions)
python -m benchmarks.locomo.runner --test

# Single-hop only (3 conversations)
python -m benchmarks.locomo.runner --singlehop --max-conv=3

# Full benchmark with baselines (recommended)
python -m benchmarks.locomo.runner --baselines

# Full benchmark without baselines (faster)
python -m benchmarks.locomo.runner

# Category-specific
python -m benchmarks.locomo.runner --multihop
python -m benchmarks.locomo.runner --temporal
python -m benchmarks.locomo.runner --adversarial

# Custom top-k
python -m benchmarks.locomo.runner --top-k=20

# Resume after crash
python -m benchmarks.locomo.runner --resume
```
