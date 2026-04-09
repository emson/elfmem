# Benchmark Report Specification

Standard output format, baselines, and methodology for all elfmem benchmarks.
Every benchmark runner produces a report conforming to this spec, enabling
cross-benchmark comparison.

---

## 1. Standard Report Format

Every benchmark produces a single JSON file at
`benchmarks/{name}/results/{timestamp}_{benchmark}_elfmem.json`.

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
      "single-hop": {"score": 52.3, "count": 841},
      "multi-hop":  {"score": 45.2, "count": 282}
    }
  },

  "baselines": {
    "no_retrieval":      {"overall": 3.2, "by_category": {}},
    "perfect_retrieval": {"overall": 72.1, "by_category": {}}
  },

  "retrieval": {
    "overall_recall": 59.4,
    "by_category": {
      "single-hop": {"recall": 71.2, "count": 841}
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

### Field Definitions

**meta** — Reproducibility envelope. Every field required.

| Field | Type | Description |
|---|---|---|
| `benchmark` | string | Benchmark name: `"locomo"`, `"memoryagentbench"`, `"longmemeval"` |
| `version` | string | Report spec version (currently `"1.0"`) |
| `timestamp` | string | ISO 8601 UTC timestamp of run start |
| `duration_seconds` | float | Total wall-clock time |
| `elfmem_version` | string | From `elfmem.__version__` or `pyproject.toml` |
| `models.*` | string/null | Model identifiers. `judge` is null unless external judge used |
| `elfmem_config` | object | Subset of `MemoryConfig` values that affect results |
| `lm_studio_base_url` | string | Endpoint URL |

**scores** — Primary results. Scores are **percentages** (0-100).

| Field | Type | Description |
|---|---|---|
| `overall` | float | Primary aggregate metric (F1 or accuracy, benchmark-dependent) |
| `by_category.*` | object | `{score, count}` per category |

**baselines** — Floor and ceiling for context. Same structure as `scores`.

| Baseline | What It Measures |
|---|---|
| `no_retrieval` | LLM answers with zero context (the floor) |
| `perfect_retrieval` | LLM answers with ground-truth evidence (the ceiling) |

The gap `perfect_retrieval - elfmem` is **retrieval loss**. The gap
`elfmem - no_retrieval` is **memory value added**.

**retrieval** — How well elfmem found the right blocks.

| Field | Type | Description |
|---|---|---|
| `overall_recall` | float | Percentage of evidence items found in retrieved blocks |
| `by_category.*` | object | `{recall, count}` per category |

**efficiency** — Resource usage.

**questions** — Per-question raw data. All benchmarks use these standardised
field names:

| Field | Type | Description |
|---|---|---|
| `id` | string | Unique question identifier |
| `category` | string | Category/type name |
| `question` | string | The question text |
| `ground_truth` | string | Expected answer |
| `prediction` | string | elfmem + LLM answer |
| `score` | float | Primary score (0.0-1.0, NOT percentage) |
| `metric` | string | Which metric computed this score |
| `retrieval_recall` | float | Evidence recall for this question (0.0-1.0) |
| `evidence_ids` | list[str] | Ground-truth evidence identifiers |
| `retrieved_ids` | list[str] | IDs of blocks elfmem retrieved |
| `query_seconds` | float | Time for retrieval + answer generation |

---

## 2. Mandatory Baselines

Every benchmark run MUST include two baselines to contextualise the scores.

### No-Retrieval Baseline

Ask the answer LLM the question with **no context at all**. This measures what
the model already knows (should score near zero for personal conversations,
potentially higher for factual knowledge).

```python
async def no_retrieval_baseline(question: str, model: str, base_url: str) -> str:
    """Answer with zero context — establishes the floor."""
    client = AsyncOpenAI(base_url=base_url, api_key="lm-studio")
    response = await client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": question}],
        max_tokens=300, temperature=0.0,
    )
    return response.choices[0].message.content or ""
```

### Perfect-Retrieval Baseline

Stuff the **ground-truth evidence** directly into the prompt. This measures the
answer LLM's ceiling given perfect context — any gap between this and elfmem's
score is retrieval loss.

For benchmarks with evidence annotations (LoCoMo `evidence` field, LongMemEval
`has_answer` field), extract the exact evidence turns/blocks. For benchmarks
without evidence annotations (some MemoryAgentBench datasets), use the full
context (which is the "long-context baseline").

### Why Baselines Matter

Without baselines, a score of "45% F1" is uninterpretable. With baselines:

```
No retrieval:        3%   (floor — the model knows nothing)
elfmem:             45%   (our system)
Perfect retrieval:  72%   (ceiling — perfect context)

Memory value added:  42 points (45 - 3)
Retrieval loss:      27 points (72 - 45)
```

This tells us elfmem adds significant value but has room to improve retrieval.

---

## 3. Retrieval Recall

Every benchmark must track what elfmem retrieved versus what it should have.
This separates "did we find the right blocks?" from "did the LLM answer well?"

### How to Track

During ingestion, tag each block with a source identifier:
- LoCoMo: `dia:{dia_id}` (e.g., `dia:D1:3`)
- LongMemEval: `turn:{session_idx}:{turn_idx}` (e.g., `turn:2:3`)
- MemoryAgentBench: `chunk:{chunk_idx}` (e.g., `chunk:7`)

After retrieval, extract these tags from `ScoredBlock.tags` and compare against
the benchmark's evidence annotations.

```python
def compute_retrieval_recall(
    retrieved_blocks: list[ScoredBlock],
    evidence_ids: list[str],
    tag_prefix: str,
) -> float:
    """Compute fraction of evidence IDs found in retrieved blocks."""
    if not evidence_ids:
        return 1.0
    retrieved_ids = set()
    for block in retrieved_blocks:
        for tag in block.tags:
            if tag.startswith(tag_prefix):
                retrieved_ids.add(tag[len(tag_prefix):])
    hits = sum(1 for eid in evidence_ids if eid in retrieved_ids)
    return hits / len(evidence_ids)
```

---

## 4. Consistent elfmem Configuration

All benchmarks MUST use identical elfmem settings so results are comparable:

```yaml
llm:
  model: "google/gemma-4-26b-a4b"
  base_url: "http://localhost:1234/v1"
  temperature: 0.0
  max_tokens: 512
  timeout: 120

embeddings:
  model: "text-embedding-nomic-embed-text-v1.5"
  base_url: "http://localhost:1234/v1"
  dimensions: 768
  timeout: 60

memory:
  inbox_threshold: 50
  top_k: 10
  search_window_hours: 10000.0
  curate_interval_hours: 1000.0
  contradiction_similarity_prefilter: 0.65
```

These are saved in `meta.elfmem_config` in every report, so any deviation
is visible and reproducible.

### Benchmark-Specific Overrides

Some benchmarks warrant different settings (e.g., lower contradiction prefilter
for MemoryAgentBench Conflict Resolution). Document any overrides in the report:

```json
{
  "meta": {
    "elfmem_config": {
      "top_k": 10,
      "contradiction_similarity_prefilter": 0.50,
      "_overrides": {
        "contradiction_similarity_prefilter": "lowered from 0.65 for conflict resolution testing"
      }
    }
  }
}
```

---

## 5. Metrics

Each benchmark uses its own native metric (to match published baselines), but
the report normalises them into the `scores` envelope.

| Benchmark | Primary Metric | How Computed | Stemming |
|---|---|---|---|
| LoCoMo | Token F1 | Porter-stemmed, normalised | Yes (Porter) |
| MemoryAgentBench | Token F1 | Normalised, no stemming | No |
| LongMemEval | LLM-judge accuracy | GPT-4o binary yes/no | N/A |

**These metrics are NOT directly comparable across benchmarks.** The `scores.overall`
in each report uses the benchmark's native metric. Cross-benchmark comparison
should use the `baselines` section: the **relative position** between floor and
ceiling is comparable even when absolute metrics differ.

### Normalised Effectiveness Score

For cross-benchmark comparison, compute:

```
effectiveness = (elfmem_score - no_retrieval_score) / (perfect_retrieval_score - no_retrieval_score)
```

This gives a 0.0-1.0 score representing how much of the possible retrieval
value elfmem captures, independent of the metric scale. An effectiveness
of 0.60 means elfmem captures 60% of the value that perfect retrieval would
provide.

---

## 6. Standard CLI Flags

All benchmark runners should support these common flags:

| Flag | Description | Example |
|---|---|---|
| `--test` | Minimal smoke test (1 unit, ~5 questions) | `--test` |
| `--max=N` | Limit to N evaluation units | `--max=3` |
| `--top-k=N` | Override retrieval top-k | `--top-k=20` |
| `--baselines` | Include no-retrieval + perfect-retrieval baselines | `--baselines` |
| `--resume` | Skip already-completed units | `--resume` |
| `--output=PATH` | Custom output path | `--output=results/run1.json` |

Benchmark-specific flags (category filters, dataset selection) are additive.

---

## 7. Quality Checklist

Before considering any benchmark run valid:

- [ ] Report JSON passes schema validation
- [ ] `meta.elfmem_version` matches installed version
- [ ] `meta.models` matches what LM Studio is serving
- [ ] `meta.elfmem_config` matches actual config used
- [ ] Baselines included and sensible (no-retrieval << elfmem << perfect-retrieval)
- [ ] All questions have a `prediction` (no nulls)
- [ ] Score distribution is reasonable (not all 0.0 or all 1.0)
- [ ] Retrieval recall is computed (evidence_ids present)
- [ ] Duration is plausible for the dataset size
- [ ] Results file is crash-safe (written incrementally)
