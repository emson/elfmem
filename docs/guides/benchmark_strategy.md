# elfmem Benchmark Strategy

Which benchmarks actually measure what makes elfmem different?

**Standard methodology:** All benchmarks produce reports conforming to the
[Benchmark Report Spec](benchmark_report_spec.md), enabling cross-benchmark
comparison via the normalised effectiveness score.

---

## The Problem with LongMemEval Alone

LongMemEval tests **conversational memory recall** — retrieving facts from past
conversations. That's necessary but not sufficient. It treats memory as a retrieval
problem (find the right passage, generate an answer). This ignores elfmem's most
distinctive capabilities:

| elfmem Capability | LongMemEval Tests It? |
|---|---|
| Conversational recall | Yes |
| Temporal reasoning | Partially (dates in content, not native decay) |
| Knowledge updates | Yes (78 questions) |
| Contradiction detection | No (just "use latest info") |
| Decay-based lifecycle | No (static retrieval) |
| Graph-aware retrieval | No (flat vector search baseline) |
| Outcome-based learning | No |
| Frame-based context assembly | No |
| Identity persistence | No |
| Knowledge consolidation quality | No |

LongMemEval gives us a number we can put on a leaderboard, but it doesn't tell us
whether elfmem's unique features actually help.

---

## Recommended Benchmark Suite

Three benchmarks, each testing a different aspect of elfmem's moat. Ordered by
priority — start with #1, add others as capacity allows.

### 1. MemoryAgentBench (Primary — Tests the Moat)

**Why this is the best fit for elfmem:**

MemoryAgentBench (ICLR 2026) tests exactly what elfmem was built for. It evaluates
four competencies that map directly to elfmem operations:

| MemoryAgentBench Competency | elfmem Feature |
|---|---|
| Accurate Retrieval | `frame("attention", query)` — 5-stage hybrid pipeline |
| Test-Time Learning | `learn()` → `consolidate()` cycle |
| Long-Range Understanding | Graph expansion (1-hop), multi-session edges |
| **Selective Forgetting / Conflict Resolution** | `curate()` decay + `consolidate()` contradiction detection |

The landmark finding: existing memory systems (Cognee, Letta/MemGPT, Mem0) drop to
**7% accuracy** on multi-hop contradiction scenarios. This is precisely where elfmem's
contradiction detection, decay-based archival, and knowledge graph should excel.

**Dataset:** Two purpose-built datasets:
- **EventQA** — multi-turn event sequences with causal dependencies
- **FactConsolidation** — facts that get updated/contradicted over time

**Format:** Dialogue chunks fed incrementally (matches elfmem's `learn()` → `consolidate()`
rhythm). Already includes implementations for Cognee, Letta, and Mem0 as baselines.

**Evaluation:** GPT-4o as judge, per-competency accuracy scores.

**Repo:** [github.com/HUST-AI-HYZ/MemoryAgentBench](https://github.com/HUST-AI-HYZ/MemoryAgentBench)
(287 stars, ICLR 2026)

**Integration effort:** Medium. Write an elfmem adapter following their Cognee/Letta
patterns. The incremental dialogue chunk format maps naturally to elfmem sessions.

**What it proves:** Whether elfmem's contradiction detection and decay-based archival
actually improve knowledge management over simpler memory systems. If elfmem scores
significantly above 7% on multi-hop contradictions, that's the clearest proof of value.

---

### 2. LoCoMo (Secondary — The Standard)

**Why it matters:**

LoCoMo (ACL 2024) is the most-cited long-term memory benchmark (725 stars). It's the
de facto standard that everyone compares against. Results here are credible and
comparable.

It tests five question types that map to elfmem capabilities:

| LoCoMo Question Type | elfmem Feature |
|---|---|
| Single-hop factual | Basic `recall()` |
| Multi-hop reasoning | Graph expansion (1-hop neighbours) |
| Temporal reasoning | Decay-aware scoring + dates in content |
| Open-ended summarization | Frame assembly + rendering |
| Adversarial questions | Abstention via low retrieval scores |

**Dataset:** 10 long conversations (~300 turns each, ~9K tokens, up to 35 sessions).
Much smaller than LongMemEval but with richer question types including summarization.

**Key baseline:** Human ceiling is 87.9 F1. GPT-4 achieves 32.1 F1. RAG approaches
reach ~53 F1. Letta (MemGPT) claims 74.0 F1 using file-based memory.

**Evaluation:** F1 score (QA), BLEU/ROUGE (summarization).

**Repo:** [github.com/snap-research/locomo](https://github.com/snap-research/locomo)
(725 stars, ACL 2024)

**Integration effort:** Low-Medium. Similar to LongMemEval — replay conversations
through `learn()`, consolidate, then answer questions. 10 conversations (not 500)
makes it fast to iterate.

**What it proves:** Whether elfmem is competitive with state-of-the-art memory
systems on the standard benchmark. This is table-stakes credibility.

---

### 3. LongMemEval (Tertiary — Scale + Knowledge Updates)

**Why it's still useful:**

We already have the integration guide. LongMemEval complements the other two with:
- **Scale testing** (500 sessions in the medium set — tests decay at scale)
- **Abstention** (30 questions testing "I don't know" — unique to this benchmark)
- **Knowledge updates** (78 questions — tests contradiction handling end-to-end)

Keep this as the third benchmark, run after MemoryAgentBench and LoCoMo validate
the core pipeline.

---

## Benchmarks Considered but Deprioritised

### Evo-Memory (Google DeepMind)
Tests whether agents improve over time via "test-time learning." Highly relevant to
elfmem's outcome signals and reinforcement. **Deprioritised** because: no public
implementation yet, 10 different task datasets to integrate, and the test-time
learning concept is harder to isolate from the base model's capabilities.

**Revisit when:** elfmem has outcome signal integration in production and we want
to prove that feedback loops improve agent performance over time.

### LMEB (Long-horizon Memory Embedding Benchmark)
Tests whether your embedding model is suited for memory retrieval (vs passage
retrieval). Key finding: MTEB scores don't predict memory retrieval quality.
**Deprioritised** because: it tests the embedding model (Nomic), not elfmem's
memory system. Useful for validating our choice of `text-embedding-nomic-embed-text-v1.5`.

**Revisit when:** we want to compare embedding models for elfmem (Nomic vs
OpenAI vs Cohere vs local alternatives).

### NoLiMa (ICML 2025)
Tests retrieval when query and target have no lexical overlap. elfmem's graph
expansion should help here. **Deprioritised** because: it's a retrieval benchmark,
not a memory benchmark. Tests embedding quality more than memory system design.

### AMemGym (ICLR 2026)
Interactive, on-policy memory evaluation. Generates live multi-turn interactions.
**Deprioritised** because: requires a live agent loop (not just replay), making
integration significantly more complex. The "Reuse Bias" finding is important
but doesn't change our benchmark choice.

### MSC / PersonaChat
Legacy benchmarks. LoCoMo explicitly supersedes MSC (16x longer conversations).
PersonaChat has no multi-session, no temporal reasoning.

### MemoryBench (Supermemory)
Unified harness running LoCoMo + LongMemEval + ConvoMem. Created by a commercial
vendor — results have been disputed. **Consider using** the harness framework
but running our own evaluation rather than trusting their numbers.

---

## What No Benchmark Tests (elfmem's Untested Moat)

Some of elfmem's most distinctive features have **no existing benchmark**:

| Feature | Why It's Hard to Benchmark |
|---|---|
| **Frame-based context assembly** | No benchmark tests "use SELF frame for identity, ATTENTION for query, TASK for goals" — requires multi-mode agent evaluation |
| **Identity persistence** | Requires 1000s of agent steps + human evaluation of identity coherence |
| **Outcome-based learning** | Requires tasks with measurable outcomes (success/failure), not just retrieval |
| **Decay calibration** | Requires months of data to assess whether decay profiles match real memory patterns |
| **Bridge protection** | Requires graph metrics (connectivity, centrality distribution) during archival |
| **Constitutional block preservation** | Requires adversarial evaluation — can the system be tricked into forgetting core values? |

**Recommendation:** For these, build custom evaluation suites as separate projects.
Start with a **frame-switching evaluation**: give an agent alternating identity and
task queries, measure whether SELF and ATTENTION frames produce appropriate (different)
contexts. This is the lowest-hanging unique evaluation.

---

## Implementation Priority

```
Phase 1: MemoryAgentBench  ←  Tests contradiction + consolidation (the moat)
         ~1-2 weeks integration, runs locally with Gemma

Phase 2: LoCoMo            ←  Standard benchmark (credibility)
         ~1 week integration, 10 conversations (fast)

Phase 3: LongMemEval       ←  Scale test (already have guide)
         Integration guide exists, oracle → small → medium

Phase 4: Custom evaluations ←  Frame-switching, identity coherence
         Design and build from scratch
```

### Cost & Time (Local Gemma via LM Studio)

| Benchmark | Questions | Est. Time | API Cost |
|---|---|---|---|
| MemoryAgentBench | ~200-500 | ~1-2 days | $0 (local) + ~$2 eval |
| LoCoMo | ~400 (across 10 conversations) | ~4-8 hours | $0 (local) + ~$2 eval |
| LongMemEval oracle | 500 | ~8-16 hours | $0 (local) + ~$3 eval |

All elfmem operations run locally. Only the GPT-4o evaluation judge costs money.

---

## Summary

**LongMemEval is a fine benchmark, but it tests elfmem at its weakest** — flat
conversational recall where elfmem's graph, decay, and contradiction features
don't shine.

**MemoryAgentBench tests elfmem at its strongest** — contradiction detection,
knowledge consolidation, selective forgetting. The 7% multi-hop accuracy cliff
that other systems hit is precisely the gap elfmem was designed to fill.

**LoCoMo provides credibility** — it's the standard, and strong results there
prove elfmem works as a general-purpose memory system, not just a niche tool.

Run all three. Lead with MemoryAgentBench results in any write-up.
