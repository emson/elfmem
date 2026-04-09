# LoCoMo Benchmark Optimisation Plan

## Current State (2026-04-09)

**Score: 39.9% F1** on conv-26 (199 questions), up from 4.7% baseline.

| Category | Score | Count | Weight |
|----------|-------|-------|--------|
| Adversarial | 95.7% | 47 | 24% |
| Single-hop | 37.5% | 70 | 35% |
| Open-ended | 10.6% | 32 | 16% |
| Multi-hop | 10.7% | 37 | 19% |
| Temporal | 5.2% | 13 | 7% |
| **Overall** | **39.9%** | **199** | **100%** |

**Retrieval recall: 12.6%** — still the primary bottleneck.

---

## What's Been Done (Completed Iterations)

| Iteration | Score | Delta | Key Change |
|-----------|-------|-------|------------|
| Baseline | 4.7% | — | Generic prompt, top_k=10, no BM25 |
| +prompt +top_k=30 | 6.5% | +1.8% | Concise extraction prompt, more candidates |
| +adversarial fix | 29.0% | +22.5% | Fixed cat 5 scoring (MCQ→direct, 0%→95.7%) |
| +BM25 hybrid | 36.8% | +7.8% | Keyword matching via BM25+RRF (+23% single-hop) |
| +adversarial BM25 skip | 37.8% | +1.0% | Skip BM25 supplementary for cat 5 |
| +observation transform | 39.9% | +2.1% | Pronoun resolution for BM25 index |

### Code Changes Made

1. **`src/elfmem/adapters/openai.py`** — `_extract_json()` strips markdown fences from LLM responses
2. **`src/elfmem/operations/consolidate.py`** — `skip_llm` flag bypasses LLM calls during consolidation
3. **`src/elfmem/api.py`** — `skip_llm` parameter on `MemorySystem.consolidate()`
4. **`src/elfmem/memory/retrieval.py`** — Tags loaded into ScoredBlock during retrieval (was hardcoded `[]`)
5. **`benchmarks/shared/answerer.py`** — Concise extraction prompt, adversarial direct-answer format
6. **`benchmarks/locomo/adapter.py`** — BM25 hybrid search, observation transform, RRF merge
7. **`benchmarks/locomo/metrics.py`** — `str()` coercion for int answers, fixed `multihop_f1`
8. **`benchmarks/locomo/baselines.py`** — Fixed category name mapping
9. **`benchmarks/locomo/config.py`** — `answer_max_tokens: 100`

---

## Target: 70% F1

### Why 70% Is Achievable

- LoCoMo human ceiling: 87.9%
- MemMachine v0.2 (SOTA): 91.7% (OpenAI embeddings + Cohere reranker)
- Letta (MemGPT): 74.0% (file-based memory + GPT-4)
- RAG with observations + Dragon retriever: ~40%+ (from LoCoMo paper)

We're at 39.9% with a local model (Gemma-26b + nomic-embed 768d). With better retrieval, 70% is realistic.

### Gap Analysis

To reach 70%, need ~30 more points. Retrieval recall (12.6%) is the bottleneck — 87% of evidence blocks aren't found.

**Three compounding failures** (identified via research):

1. **No lexical matching in vector search** — cosine similarity misses exact keyword overlap. BM25 partially addresses this but only for the BM25 index, not elfmem's vector pipeline.

2. **Pronoun mismatch** — Dialog says "I went to..." but questions ask "When did Caroline go to...". Observation transform partially addresses this for BM25 but NOT for vector embeddings.

3. **Diluted composite scoring** — Recency (0.25 weight) and reinforcement (0.10) push recent/popular blocks above semantically relevant ones. Later session blocks dominate over earlier evidence.

---

## Phase 1: Retrieval Quality (Target: 39.9% → 55%)

### 1.1 Cross-Encoder Reranking
**Impact: HIGH | Effort: LOW | Speed: +150ms per query**

After initial retrieval returns top-50 candidates, rerank with `cross-encoder/ms-marco-MiniLM-L-6-v2` (80MB, CPU). Cross-encoders jointly attend to both query and candidate — much more accurate than bi-encoder cosine similarity.

```python
from sentence_transformers import CrossEncoder
reranker = CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')
scores = reranker.predict([(query, block.content) for block in candidates])
# Reorder by cross-encoder score, keep top_k
```

**Install:** `uv pip install sentence-transformers`
**Expected:** +10-15% on single-hop and multi-hop (better ranking of existing candidates)

### 1.2 Observation-Based Vector Embeddings
**Impact: HIGH | Effort: MEDIUM | Speed: No overhead**

Currently elfmem embeds raw dialog (`"[date] Caroline: I went to..."`). The observation transform only helps BM25. Extend it to vector embeddings:

- During learn(), store raw content (for rendering)
- During consolidate(), embed the observation (for retrieval)
- Modify `_fallback_analysis()` to accept an observation string as summary

```python
# In adapter: learn with observation as a tag or custom field
# In consolidate: use observation for embedding instead of raw content
```

**Expected:** +5-10% across all categories (fixes pronoun mismatch for vector search)

### 1.3 Increase LM Studio Context Window
**Impact: MEDIUM | Effort: NONE (config change)**

Increase n_ctx in LM Studio from 4096 to 8192 for Gemma-4-26b. This allows top_k=50 without context overflow, retrieving more candidates.

**Expected:** +3-5% (more evidence blocks in context)

---

## Phase 2: Answer Quality (Target: 55% → 65%)

### 2.1 Category-Specific Prompts
**Impact: MEDIUM | Effort: LOW**

Different categories need different answer strategies:

| Category | Current Prompt | Optimised Prompt |
|----------|---------------|-----------------|
| Single-hop | Generic extraction | "State the specific fact mentioned" |
| Multi-hop | Generic extraction | "Combine information from multiple parts of the conversation" |
| Temporal | Generic extraction | "State the specific date or time period" |
| Open-ended | Generic extraction | "Provide a brief explanation using details from the conversation" |

### 2.2 Answer Post-Processing
**Impact: LOW-MEDIUM | Effort: LOW**

Clean LLM answers before scoring:
- Strip "Based on the conversation..." preambles
- Extract dates from verbose responses ("yesterday (7 May 2023)" → "7 May 2023")
- Remove qualifying phrases ("It seems like..." → direct answer)

---

## Phase 3: Advanced Retrieval (Target: 65% → 70%+)

### 3.1 Hybrid BM25 in elfmem Core
**Impact: HIGH | Effort: MEDIUM**

Port the benchmark adapter's BM25 hybrid search into elfmem's retrieval pipeline as a core feature:

- Add `BM25Index` that builds alongside vector index during consolidation
- Add Stage 2b (BM25 search) parallel to Stage 2 (vector search)
- Merge via RRF before Stage 4 (composite scoring)
- Configurable via `MemoryConfig.bm25_weight`

### 3.2 Multi-Query Retrieval with Query Decomposition
**Impact: MEDIUM | Effort: MEDIUM**

For multi-hop questions, decompose into sub-queries:
- "When did Caroline first mention therapy after her race?" →
  - "Caroline therapy"
  - "Caroline race"
- Run retrieval for each, merge with RRF

### 3.3 HyDE (Hypothetical Document Embeddings)
**Impact: MEDIUM | Effort: MEDIUM**

Generate a hypothetical answer, embed it, use for vector search. Converts question→document matching to document→document matching.

Only use adaptively — when initial retrieval returns low-confidence results.

### 3.4 Knowledge Graph Enhancement
**Impact: MEDIUM | Effort: HIGH**

Build entity-relationship graph during ingestion:
- Extract entities (people, places, dates, topics) from observations
- Create edges between entities mentioned in the same turn
- During retrieval, traverse entity graph to find related blocks

---

## Phase 4: Scale to All 10 Conversations (Target: validate 70%+)

### 4.1 Run Full LoCoMo (1,986 QA pairs)
- All 10 conversations
- All 5 categories
- With baselines (no-retrieval + perfect-retrieval)
- Estimated time: ~4 hours with current setup

### 4.2 Per-Conversation Analysis
- Identify worst-performing conversations
- Category-specific tuning
- Error analysis on failure cases

### 4.3 Compare Against Published Baselines
- GPT-4 full context: 32.1% ← we've surpassed this
- RAG (Dragon + GPT-4): ~53% ← target for Phase 2
- Letta (MemGPT + GPT-4): 74.0% ← target for Phase 3
- Human ceiling: 87.9%

---

## Implementation Priority

```
Phase 1.1: Cross-encoder reranking        ← Highest impact, lowest effort
Phase 1.2: Observation-based embeddings   ← Fixes pronoun mismatch for vectors
Phase 2.1: Category-specific prompts      ← Quick win for answer quality
Phase 1.3: Increase context window        ← Config change only
Phase 2.2: Answer post-processing         ← Clean up LLM output
Phase 3.1: BM25 in elfmem core            ← Production feature
Phase 3.2: Multi-query decomposition      ← Multi-hop improvement
Phase 3.3: HyDE                           ← Adaptive retrieval fallback
Phase 3.4: Knowledge graph                ← Long-term architectural
Phase 4:   Full benchmark validation      ← Final validation
```

---

## Success Metrics

| Milestone | Score | Key Indicator |
|-----------|-------|--------------|
| ✅ Baseline | 4.7% | Harness working |
| ✅ Quick wins | 39.9% | Prompt + BM25 + adversarial |
| Phase 1 complete | ~55% | Retrieval recall > 30% |
| Phase 2 complete | ~65% | Answer F1 > 50% on single-hop |
| **Phase 3 complete** | **~70%** | **Surpass RAG baselines** |
| Stretch | 75%+ | Competitive with Letta |

---

## Technical Notes

### Embedding Model
- Current: `text-embedding-nomic-embed-text-v1.5` (768d, local via LM Studio)
- Consider: `BGE-M3` (1024d, unified dense+sparse+ColBERT) — eliminates need for separate BM25
- Consider: larger nomic model at 1536d

### LLM Model
- Current: `google/gemma-4-26b-a4b` (local via LM Studio, ~1.5s per call)
- Extended thinking MUST be disabled (was causing 8s+ per call)
- `_extract_json()` fix handles markdown-wrapped JSON responses

### Consolidation
- `skip_llm=True` reduces ingestion from 5+ hours to 41 seconds
- Blocks promoted with neutral confidence (0.50), no LLM-generated summaries
- Edges still created via cosine+jaccard+temporal scoring
- Tags preserved from learn() through consolidation to retrieval

### BM25 Implementation
- `rank_bm25` library (pure Python, no infrastructure)
- Index built during ingestion alongside elfmem's vector index
- Observations indexed (third-person) for pronoun resolution
- RRF merge: vector blocks reranked + limited BM25 supplementary (5 max)
- BM25 skipped for adversarial (cat 5) to prevent false context injection
