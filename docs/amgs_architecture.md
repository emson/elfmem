# Adaptive Memory Graph System

## Architecture Specification

**Version 1.0 — March 2026**

*A graph-based, decay-aware, confidence-calibrated, context-assembling cognitive engine.*

---

## Table of Contents

1. [Intent and Philosophy](#1-intent-and-philosophy)
2. [Axioms and Foundational Truths](#2-axioms-and-foundational-truths)
3. [Primitives](#3-primitives)
4. [Context Frames: The Universal Abstraction](#4-context-frames-the-universal-abstraction)
5. [Storage Architecture](#5-storage-architecture)
6. [Retrieval Architecture](#6-retrieval-architecture)
7. [Mathematical Foundations](#7-mathematical-foundations)
8. [Memory Lifecycle](#8-memory-lifecycle)
9. [Agent Architecture](#9-agent-architecture)
10. [Self and Identity Assembly](#10-self-and-identity-assembly)
11. [Interface Architecture](#11-interface-architecture)
12. [Identity and Hashing Strategy](#12-identity-and-hashing-strategy)
13. [Calibration and Tuning](#13-calibration-and-tuning)
14. [Minimal Viable Product Scope](#14-minimal-viable-product-scope)
15. [Python Package Structure](#15-python-package-structure)
16. [Differentiation from Existing Systems](#16-differentiation-from-existing-systems)
17. [Implementation Roadmap](#17-implementation-roadmap)

---

## 1. Intent and Philosophy

This document specifies the architecture of the **Adaptive Memory Graph System** (AMGS): a Python library designed to function as a persistent, evolvable cognitive substrate for AI agents. AMGS is not a database, not a knowledge graph, and not a RAG pipeline, though it draws on techniques from all three. It is a system that models human-like memory dynamics: accumulation, consolidation, selective attention, temporal decay, confidence calibration, and emergent identity.

The system is designed to serve as the memory layer for autonomous and semi-autonomous AI agents, enabling them to build, maintain, and reason over an evolving knowledge base without requiring a human to curate or structure that knowledge manually.

### Design Principles

- **Memory is atomic**: all knowledge reduces to discrete, immutable content blocks with mutable metadata
- **Relationships create meaning**: edges are first-class entities that carry weight, confidence, and decay
- **Relevance is dynamic**: all memory decays unless reinforced through use or explicit action
- **Attention is selective**: only a subset of memory is active at any moment, selected by scoring
- **Context is constructed**: there is no permanent context; it is assembled on demand from memory
- **Confidence must be calibrated**: all classifications, links, and predictions carry measurable uncertainty
- **Identity is emergent**: the system's sense of self is continuously reconstructed from long-lived memory

---

## 2. Axioms and Foundational Truths

These axioms are the irreducible assumptions upon which the entire system is built. They are not configurable; they define the nature of the system.

| Axiom | Statement | Implication |
|-------|-----------|-------------|
| 1. Atomicity | All knowledge reduces to discrete memory blocks | Content is immutable once stored; metadata evolves independently |
| 2. Relational Meaning | Meaning emerges from relationships, not isolation | Edges are first-class entities with their own lifecycle |
| 3. Dynamic Relevance | All memory decays unless reinforced | Relevance is a function of time, usage, connectivity, and confidence |
| 4. Selective Attention | Only a subset of memory is active at any moment | Attention is a selection process, not a storage mechanism |
| 5. Constructed Context | There is no permanent context | Context is assembled dynamically from memory blocks via frames |
| 6. Calibrated Confidence | All predictions carry uncertainty | Confidence must be measured and refined over time using Brier scoring |
| 7. Emergent Identity | Self is not a static document | Identity is a continuously reconstructed context frame from long-lived memory |

These axioms have a direct consequence: the **Context Frame** becomes the universal abstraction through which all context assembly — including identity, attention, world knowledge, and task reasoning — is performed.

---

## 3. Primitives

The following are the minimal structural elements from which the entire system is composed.

### 3.1 Memory Block

The atomic unit of knowledge. Content is immutable; metadata evolves.

| Field | Type | Mutability | Description |
|-------|------|------------|-------------|
| `id` | VARCHAR | Immutable | SHA-256 hash of canonicalised content |
| `content` | TEXT | Immutable | Markdown text of the knowledge block |
| `created_at` | TIMESTAMP | Immutable | When the block was first ingested |
| `last_reinforced_at` | TIMESTAMP | Mutable | Last access or reinforcement event |
| `category` | VARCHAR | Mutable | Tag-based classification |
| `confidence` | FLOAT | Mutable | Calibrated confidence score (0.0 to 1.0) |
| `decay_lambda` | FLOAT | Mutable | Decay constant; higher = faster decay |
| `embedding` | FLOAT[] | Mutable | Vector embedding of content |
| `status` | VARCHAR | Mutable | One of: `raw`, `consolidated`, `archived`, `pruned` |
| `is_self_component` | BOOLEAN | Mutable | Flagged for Self frame inclusion |
| `meta` | JSON | Mutable | Extensible metadata dictionary |

### 3.2 Edge

Represents a weighted, typed relationship between two memory blocks. Edges decay independently of the nodes they connect.

| Field | Type | Description |
|-------|------|-------------|
| `source_id` | VARCHAR | Origin memory block ID |
| `target_id` | VARCHAR | Destination memory block ID |
| `relation_type` | VARCHAR | Semantic label (e.g. `supports`, `contradicts`, `elaborates`, `causes`) |
| `weight` | FLOAT | Strength of the relationship (0.0 to 1.0) |
| `confidence` | FLOAT | Calibrated confidence in this edge's validity |
| `created_at` | TIMESTAMP | When the edge was first created |
| `last_reinforced_at` | TIMESTAMP | Last reinforcement event |
| `decay_lambda` | FLOAT | Independent decay constant for this edge |

### 3.3 Score

A composite numerical function that drives all selection decisions in the system. The general form is:

```
Score = w1 × RecencyScore
      + w2 × CentralityScore
      + w3 × ConfidenceScore
      + w4 × QuerySimilarity
      + w5 × ReinforcementScore
```

Weights are tunable per context frame type, allowing the same scoring infrastructure to serve radically different selection purposes.

### 3.4 Decay Function

The temporal relevance function that governs memory fade:

```
Relevance(t) = e^(-λ × t)

Where:
  t = time since last reinforcement (hours)
  λ = decay constant (per block or edge)
```

Different **decay profiles** serve different memory types: ephemeral observations might have λ = 0.1 (half-life of ~7 hours), while core identity beliefs might have λ = 0.0001 (half-life of ~289 days).

---

## 4. Context Frames: The Universal Abstraction

This is the central architectural insight of AMGS. Previously, Self, Attention, World Model, and Short-Term Memory were treated as separate mechanisms. In this architecture, they are all instances of the same primitive: the **Context Frame**.

### 4.1 Definition

A Context Frame is a dynamically assembled, purpose-bound subset of memory blocks. It is **not** a storage type. It is a **selection mechanism**.

```
ContextFrame = SelectionRule + ScoringFunction + AssemblyStrategy
```

### 4.2 Frame Types

| Frame Type | Purpose | Selection Bias | Refresh Cadence |
|------------|---------|----------------|-----------------|
| **SELF** | Assemble long-lived identity and core beliefs | Low decay, high reinforcement, identity-tagged nodes | Periodic (hourly/daily) |
| **WORLD** | External knowledge model and domain understanding | Category-based clusters, semantic neighbourhood expansion | On consolidation |
| **ATTENTION** | Active reasoning window for current query | Query similarity, recency, centrality | Per query |
| **SHORT_TERM** | Recent events summary and working memory | Time-windowed, recent reinforcement | Periodic (minutes) |
| **TASK** | Solve a specific problem temporarily | Query similarity, task tags, graph traversal | Per task lifecycle |
| **INBOX** | View of unconsolidated raw memory | `status = raw`, recency-weighted | Continuous |

### 4.3 Inbox as a Context Frame

The Inbox is not a separate subsystem. It is a Context Frame whose selection rule filters for memory blocks with `status = raw`. Its scoring function weights recency heavily and ignores centrality and reinforcement (since these do not yet exist for unprocessed blocks). This means ingestion, consolidation monitoring, and inbox review all operate through the same frame interface.

This unification means any agent or CLI command that can assemble a frame can inspect the inbox. There is no special-case code path for raw memory.

### 4.4 Composite Context Frames

A critical design question: can a Context Frame contain other Context Frames? The answer is **yes**, and this creates a powerful compositional model.

A Composite Frame does not select directly from the memory block pool. Instead, it merges the outputs of child frames, applies its own deduplication and re-scoring, and produces a unified context. This creates a **directed acyclic graph (DAG)** of frame dependencies.

#### Composition Model

```
CompositeFrame = {
    children: [Frame, Frame, ...],
    merge_strategy: union | intersection | priority_chain,
    budget_allocation: { child_name: token_budget, ... },
    dedup_strategy: content_hash | semantic_threshold,
    rescore_weights: { ... }  // optional re-ranking after merge
}
```

#### Example Compositions

| Composite | Children | Merge Strategy | Use Case |
|-----------|----------|----------------|----------|
| **SESSION** | SELF + ATTENTION + SHORT_TERM | priority_chain | Full agent reasoning context |
| **REASONING** | WORLD + TASK + ATTENTION | union | Domain problem solving |
| **BRIEFING** | SELF + SHORT_TERM + INBOX | priority_chain | Daily cognitive digest |
| **DEEP_RECALL** | WORLD + SELF | union | Identity-grounded knowledge retrieval |

#### Circular Dependency Prevention

Composite frames form a DAG, not a cyclic graph. The system enforces this at frame registration time by performing a topological sort. If adding a child frame would create a cycle, the operation is rejected with a clear error. This is a hard constraint, not a runtime check.

#### Token Budget Allocation

When composing frames, the composite must allocate a finite token budget across its children. The budget allocation strategy can be:

- **Static**: fixed percentages per child
- **Dynamic**: proportional to child score mass
- **Priority-based**: fill highest-priority child first, then next with remainder

The default for SESSION is `priority_chain`: SELF gets its full allocation first, then ATTENTION fills the remaining budget, then SHORT_TERM gets whatever is left.

### 4.5 Frame Construction Pipeline

All frames — whether atomic or composite — follow the same four-step pipeline:

| Step | Operation | Mechanisms Used |
|------|-----------|-----------------|
| 1. Candidate Pool | Gather potential memory blocks | Category filter, graph traversal, vector similarity, time window, child frame outputs |
| 2. Scoring | Apply weighted composite score to each candidate | Decay weight, centrality, confidence, query similarity, reinforcement |
| 3. Selection | Choose the final set from scored candidates | Top-K, threshold cutoff, entropy balancing, token budget constraint |
| 4. Assembly | Produce the output context | Raw concatenation, LLM summarisation, structured formatting, markdown template |

The scoring weights differ per frame type. SELF weights confidence and reinforcement heavily. ATTENTION weights query similarity and recency. INBOX weights recency and ignores all graph-derived scores.

---

## 5. Storage Architecture

### 5.1 Primary Store: DuckDB

DuckDB is the primary storage engine. It provides OLAP-grade analytical queries, native array types for embeddings, JSON support for metadata, and zero-dependency embedded operation. No external database server is required.

### 5.2 Schema

| Table | Purpose | Key Columns |
|-------|---------|-------------|
| `memory_blocks` | All atomic knowledge units | id, content, status, category, confidence, decay_lambda, embedding, meta |
| `edges` | Relationships between blocks | source_id, target_id, relation_type, weight, confidence, decay_lambda |
| `frame_definitions` | Registered context frame configurations | name, frame_type, selection_rules, scoring_weights, assembly_strategy |
| `frame_compositions` | Parent-child relationships between frames | parent_frame, child_frame, merge_strategy, budget_allocation |
| `scores_cache` | Precomputed attention scores | block_id, frame_name, score, computed_at |
| `calibration_log` | Brier score history for confidence tuning | prediction_id, predicted, actual, brier_score, timestamp |
| `reinforcement_log` | Record of all reinforcement events | block_id, event_type, timestamp, source |

### 5.3 Graph Representation

The graph is stored as an edge list in DuckDB (the `edges` table) for persistence and analytical queries. At runtime, a NetworkX graph is materialised from this table for centrality computation, traversal, and neighbourhood expansion. The NetworkX graph is rebuilt on demand or cached with a TTL.

For systems exceeding approximately 100,000 nodes, a migration path to a dedicated graph engine (Neo4j, Memgraph) is available but not required for MVP.

### 5.4 Embedding Storage

Embeddings are stored as FLOAT arrays directly in the `memory_blocks` table. DuckDB's native array operations support cosine similarity computation without an external vector database. For systems exceeding 500,000 blocks, an external vector index (FAISS, pgvector, Qdrant) can be introduced as an acceleration layer without changing the data model.

---

## 6. Retrieval Architecture

The system uses a layered retrieval strategy designed to minimise cost while maximising recall. Each layer is progressively more expensive and operates on a progressively smaller candidate set.

| Layer | Mechanism | Cost | Purpose |
|-------|-----------|------|---------|
| 1. Pre-filter | SQL WHERE clauses on category, status, time window | Minimal | Eliminate obviously irrelevant blocks |
| 2. Keyword search | Full-text search on content column | Low | Exact and structural matching |
| 3. Fuzzy search | Trigram or Levenshtein distance matching | Low | Typo tolerance and near-match retrieval |
| 4. Vector search | Cosine similarity against query embedding | Medium | Semantic similarity retrieval |
| 5. Graph expansion | NetworkX neighbourhood traversal from seed nodes | Medium | Associative and structural recall |
| 6. Composite scoring | Weighted multi-signal ranking | Low (compute) | Final ranking for selection |

### 6.1 Hybrid Retrieval Flow

For a typical query, the system:

1. Applies **pre-filters** to reduce the candidate pool (typically by category and time window)
2. Runs **vector similarity** against the filtered set to find semantically relevant blocks
3. Expands via **graph traversal** to pull in structurally connected nodes the vector search may have missed
4. **Ranks** all candidates using the composite scoring function appropriate for the active context frame

This layered approach means that vector embeddings are never computed against the full corpus. The pre-filter stage typically reduces the search space by 80–95% before any embedding comparison occurs.

---

## 7. Mathematical Foundations

### 7.1 Half-Life Decay

```
decay_weight(t) = e^(-λ × t)

half_life = ln(2) / λ

Example profiles:
  Ephemeral  (λ = 0.1):    half-life ≈ 6.9 hours
  Standard   (λ = 0.01):   half-life ≈ 2.9 days
  Durable    (λ = 0.001):  half-life ≈ 28.9 days
  Core       (λ = 0.0001): half-life ≈ 289 days
```

### 7.2 Composite Attention Score

```
AttentionScore = w1 × RecencyScore
               + w2 × CentralityScore
               + w3 × ConfidenceScore
               + w4 × QuerySimilarity
               + w5 × ReinforcementScore

All component scores normalised to [0, 1]
Weights are per-frame-type, summing to 1.0
```

**Default weight profiles:**

| Frame Type | w1 Recency | w2 Centrality | w3 Confidence | w4 Similarity | w5 Reinforcement |
|------------|------------|---------------|---------------|---------------|-------------------|
| SELF | 0.05 | 0.25 | 0.30 | 0.10 | 0.30 |
| ATTENTION | 0.25 | 0.15 | 0.15 | 0.35 | 0.10 |
| SHORT_TERM | 0.50 | 0.05 | 0.10 | 0.20 | 0.15 |
| WORLD | 0.10 | 0.30 | 0.25 | 0.25 | 0.10 |
| TASK | 0.15 | 0.10 | 0.15 | 0.45 | 0.15 |
| INBOX | 0.60 | 0.00 | 0.10 | 0.20 | 0.10 |

### 7.3 Confidence Calibration (Brier Score)

```
BrierScore = (forecast_probability − actual_outcome)²

Perfect calibration: BS = 0
Random guessing:     BS = 0.25
Always wrong:        BS = 1.0
```

The Brier score is used to tune confidence weights over time. When the system predicts that a relationship has strength 0.8 but subsequent retrieval feedback shows it was irrelevant, the confidence on that edge is adjusted downward and the calibration log records the miss.

### 7.4 Graph Centrality Measures

| Measure | What It Captures | Use In AMGS |
|---------|------------------|-------------|
| Degree centrality | Number of direct connections | Basic importance signal |
| Eigenvector centrality | Connection to other important nodes | Influence propagation |
| PageRank | Importance via recursive link analysis | Knowledge hub identification |
| Betweenness centrality | Bridge between clusters | Conceptual connector detection |

### 7.5 Shannon Entropy

```
H = −Σ p(x) × log₂(p(x))
```

Used to measure category diversity within a context frame (high entropy means broadly distributed knowledge), uncertainty in classification decisions, and fragmentation of knowledge clusters. Entropy balancing during frame selection ensures that context frames do not become monocultural.

### 7.6 Monte Carlo Simulation (Post-MVP)

Monte Carlo methods will be used in the enhancement cycle to simulate the impact of adding or removing edges before committing changes, model future graph evolution under different pruning strategies, identify optimal node insertion points, and test graph stability under various decay scenarios. This is explicitly excluded from MVP scope.

---

## 8. Memory Lifecycle

### 8.1 Ingestion

Raw markdown content enters the system through the Inbox. At this stage, minimal processing occurs: a content hash is computed as the block ID, a `created_at` timestamp is set, status is marked as `raw`, and the block is written to the `memory_blocks` table. No embedding, categorisation, or linking happens at ingestion time. This keeps the ingestion path fast and cheap.

### 8.2 Consolidation

Consolidation transforms raw blocks into fully integrated memory. It is triggered either periodically (e.g. every 15 minutes) or when the inbox exceeds a configurable volume threshold. The consolidation pipeline performs the following steps in order:

1. **Categorise** the block using tag inference (LLM-assisted or rule-based)
2. **Compute and store** the embedding vector
3. **Extract** named entities and key concepts
4. **Create edges** to semantically and structurally related existing blocks
5. **Assign** a decay profile based on category
6. **Compute** initial attention scores
7. **Update status** to `consolidated`

LLM invocation during consolidation is selective. Categorisation and entity extraction may use an LLM, but embedding and scoring are purely computational. This keeps consolidation cost manageable.

### 8.3 Enhancement

The enhancement cycle operates as an offline background process. It evaluates the health of the graph by examining weak edges (low weight and confidence), redundant nodes (near-duplicate content), overlapping concepts that should be merged, and confidence mismatches between related nodes.

Enhancement may:

- **Merge** nodes (combining content and preserving the stronger ID)
- **Split** nodes (when a block covers multiple distinct concepts)
- **Strengthen** or **weaken** edges based on usage patterns
- **Prune** low-value fragments

Enhancement is the most expensive lifecycle phase and is typically scheduled weekly or monthly.

### 8.4 Reinforcement

When a memory block is accessed during frame assembly, query retrieval, or explicit user interaction, its `last_reinforced_at` timestamp is updated. This resets the decay clock for that block. Edges traversed during retrieval are also reinforced. Reinforcement is the mechanism by which frequently useful memories resist decay and maintain their position in the attention hierarchy.

### 8.5 Pruning

Blocks are candidates for pruning when their decay weight falls below a configurable threshold, they have low centrality in the graph, they have low reinforcement history, and their confidence is low.

Pruning sets the block status to `pruned` (soft delete) and cleans up orphaned edges. Hard deletion is a separate administrative operation. Graph integrity is maintained by ensuring no edge references a pruned node.

---

## 9. Agent Architecture

AMGS is designed to be operated by a set of specialised agents, each responsible for a distinct phase of the memory lifecycle. Agents interact with the system exclusively through the public API (described in Section 11). They do not access storage directly.

| Agent | Responsibility | Trigger | LLM Usage |
|-------|---------------|---------|-----------|
| **Ingestion Agent** | Receives raw content, writes to inbox | External input (API call, file watch, conversation) | None |
| **Consolidation Agent** | Processes inbox, categorises, embeds, links | Periodic or volume threshold | Selective (categorisation, entity extraction) |
| **Enhancement Agent** | Evaluates graph health, adjusts weights, merges/splits | Scheduled (weekly/monthly) | Moderate (relationship evaluation, merge decisions) |
| **Attention Agent** | Assembles context frames on demand | Per query or periodic refresh | Optional (summarisation during assembly) |
| **Curation Agent** | Monitors decay, triggers pruning, manages lifecycle | Periodic (daily) | Minimal (threshold checks) |

### 9.1 Agent Communication

Agents do not communicate directly with each other. They interact through the shared state of the memory graph. The Ingestion Agent writes raw blocks; the Consolidation Agent reads raw blocks and writes consolidated blocks; the Enhancement Agent reads the full graph and writes adjustments; the Attention Agent reads consolidated blocks and scores to assemble frames.

This shared-state model avoids complex inter-agent messaging while maintaining clear separation of concerns.

---

## 10. Self and Identity Assembly

The Self is not a static system prompt. It is a Context Frame of type `SELF`, assembled from memory blocks that satisfy the following criteria:

- They have the `is_self_component` flag set to `true`
- They have high confidence scores
- They have low `decay_lambda` values (long half-life)
- They have high reinforcement counts
- They have high graph centrality within the identity subgraph

### 10.1 Self Identification Strategy

The recommended approach uses a **hybrid of three mechanisms** working together:

1. **Explicit tag**: blocks can be flagged with `is_self_component = true` by the ingestion or consolidation agent when content appears to describe identity, values, or persistent preferences.

2. **Root anchor**: a dedicated root node in the graph serves as the identity anchor, and blocks within a configurable graph distance of this anchor are candidates for the Self frame.

3. **Scoring threshold**: even without explicit tagging, blocks that exceed a centrality and reinforcement threshold are considered for Self inclusion.

This three-layer approach ensures that Self is robust: explicitly tagged blocks are always included, structurally central blocks are discovered automatically, and the scoring threshold catches emergent identity patterns that neither tagging nor anchoring anticipated.

### 10.2 Self Refresh Cadence

The Self frame is not recalculated per query. It is assembled periodically (default: hourly) and cached. This reflects the human cognitive pattern where core identity is stable within a session but may shift gradually over longer timeframes. The cached Self is invalidated when new self-tagged blocks are consolidated or when a significant centrality shift is detected in the identity subgraph.

---

## 11. Interface Architecture

The system exposes its capabilities through a layered interface architecture. Each layer builds on the one below it, and external consumers can interact at whichever level is appropriate for their use case.

### 11.1 Layer Model

| Layer | Technology | Consumers | Description |
|-------|-----------|-----------|-------------|
| 1. Core Library | Python package (`amgs`) | Other Python code, agents, notebooks | Domain model, algorithms, storage. Direct function calls. |
| 2. Service Layer | Async Python (asyncio) | API layer, scheduler | Lifecycle management, consolidation scheduling, event loop |
| 3. REST API | FastAPI | CLI, web UI, external agents, webhooks | HTTP endpoints for all operations. OpenAPI schema. |
| 4. CLI | Typer (Python CLI framework) | Developers, operators, scripts | Command-line interface mapping to API operations |
| 5. Python SDK | Thin client over REST API | Agent code, notebooks, integrations | Ergonomic Python wrapper for the API |

### 11.2 REST API Specification

The API follows RESTful conventions with JSON request and response bodies. All endpoints are versioned under `/v1/`.

#### Memory Operations

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/v1/memory/ingest` | Add raw content to inbox. Accepts markdown string or batch array. |
| `GET` | `/v1/memory/{id}` | Retrieve a specific memory block by ID |
| `PATCH` | `/v1/memory/{id}` | Update mutable metadata (category, confidence, decay_lambda, meta) |
| `DELETE` | `/v1/memory/{id}` | Soft-delete (set status to pruned). Hard delete via `?hard=true` |
| `POST` | `/v1/memory/reinforce` | Explicitly reinforce one or more blocks by ID |
| `POST` | `/v1/memory/search` | Hybrid search with query, filters, and retrieval layer options |

#### Frame Operations

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/v1/frame/assemble` | Assemble a context frame by type. Returns ordered blocks. |
| `GET` | `/v1/frame/definitions` | List all registered frame definitions |
| `POST` | `/v1/frame/define` | Register a new frame definition with custom scoring weights |
| `PUT` | `/v1/frame/define/{name}` | Update an existing frame definition |
| `POST` | `/v1/frame/compose` | Define a composite frame from child frames |
| `GET` | `/v1/frame/{name}/cached` | Retrieve the most recently cached assembly for a frame |

#### Graph Operations

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/v1/graph/edge` | Create or update an edge between two blocks |
| `DELETE` | `/v1/graph/edge` | Remove an edge |
| `GET` | `/v1/graph/neighbours/{id}` | Get neighbouring nodes within N hops |
| `GET` | `/v1/graph/centrality/{id}` | Compute centrality scores for a node |
| `GET` | `/v1/graph/stats` | Graph-level statistics (node count, edge count, density, components) |

#### Lifecycle Operations

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/v1/lifecycle/consolidate` | Trigger consolidation of inbox blocks |
| `POST` | `/v1/lifecycle/enhance` | Trigger an enhancement cycle |
| `POST` | `/v1/lifecycle/prune` | Trigger pruning of decayed blocks |
| `POST` | `/v1/lifecycle/calibrate` | Run confidence calibration using Brier score log |
| `GET` | `/v1/lifecycle/status` | System health: inbox size, graph stats, last consolidation, decay summary |

### 11.3 CLI Specification

The CLI is built with Typer and maps directly to the REST API. It is the primary developer interface for operating and inspecting the system. All commands support `--format json` for machine-readable output.

#### Command Reference

| Command | Description | Example |
|---------|-------------|---------|
| `amgs ingest <text>` | Add content to inbox | `amgs ingest "Python supports duck typing"` |
| `amgs ingest --file <path>` | Ingest from markdown file | `amgs ingest --file notes.md` |
| `amgs ingest --batch <dir>` | Batch ingest all .md files in directory | `amgs ingest --batch ./knowledge/` |
| `amgs search <query>` | Hybrid search across all layers | `amgs search "memory decay models"` |
| `amgs search --layer vector` | Search using only a specific retrieval layer | `amgs search --layer keyword "DuckDB"` |
| `amgs frame assemble <type>` | Build and display a context frame | `amgs frame assemble SELF` |
| `amgs frame assemble --composite` | Assemble a composite frame | `amgs frame assemble SESSION` |
| `amgs frame list` | Show all registered frame definitions | `amgs frame list` |
| `amgs frame define <name>` | Register a new frame interactively | `amgs frame define RESEARCH` |
| `amgs graph inspect <id>` | Show a node and its neighbourhood | `amgs graph inspect abc123` |
| `amgs graph stats` | Display graph-level statistics | `amgs graph stats` |
| `amgs consolidate` | Run consolidation on inbox | `amgs consolidate` |
| `amgs consolidate --dry-run` | Preview consolidation without committing | `amgs consolidate --dry-run` |
| `amgs enhance` | Run enhancement cycle | `amgs enhance` |
| `amgs prune` | Run pruning of decayed blocks | `amgs prune --threshold 0.05` |
| `amgs calibrate` | Run confidence calibration | `amgs calibrate` |
| `amgs status` | System health overview | `amgs status` |
| `amgs export` | Export full graph as JSON or GraphML | `amgs export --format graphml` |
| `amgs shell` | Interactive REPL for exploration | `amgs shell` |

### 11.4 Python SDK

The SDK provides an ergonomic Python interface for agent code and notebooks:

```python
from amgs import MemoryClient

client = MemoryClient(base_url="http://localhost:8420")

# Ingest
block = client.ingest("Python's GIL prevents true thread parallelism")

# Search
results = client.search("concurrency in Python", top_k=5)

# Assemble a frame
self_frame = client.frame.assemble("SELF")
session = client.frame.assemble("SESSION")  # composite

# Graph inspection
neighbours = client.graph.neighbours(block.id, hops=2)

# Lifecycle
client.lifecycle.consolidate()
client.lifecycle.status()
```

---

## 12. Identity and Hashing Strategy

The block ID is a SHA-256 hash of the canonicalised content. Canonicalisation strips leading and trailing whitespace, normalises line endings to LF, and collapses multiple blank lines to a single blank line. This ensures that semantically identical content always produces the same ID regardless of formatting variations.

The ID contains **no encoded metadata**. Category prefixes, timestamps, and confidence bits were evaluated and rejected.

| Approach | Benefit | Risk | Decision |
|----------|---------|------|----------|
| Pure content hash | Identity integrity, deduplication | No structural information | **Adopted** |
| Category prefix | Fast filtering | Breaks on recategorisation | Rejected (use SQL filter) |
| LSH component | Similar nodes cluster by ID | Collision risk, mutable similarity | Rejected (use embeddings) |
| Timestamp component | Temporal sorting by ID | Volatile, misleading after reinforcement | Rejected (use `created_at` column) |
| Confidence bits | Quick trust estimation | Needs recalibration, stale quickly | Rejected (use `confidence` column) |

The rationale: IDs must be stable across the full lifecycle. Metadata changes (recategorisation, confidence adjustment) must never change a block's identity. **State belongs in metadata columns, not in identifiers.**

---

## 13. Calibration and Tuning

### 13.1 Success Metrics

| Metric | What It Measures | Target |
|--------|------------------|--------|
| Retrieval precision | Fraction of retrieved blocks that are relevant | > 0.80 |
| Retrieval recall | Fraction of relevant blocks that are retrieved | > 0.70 |
| Context coherence | Semantic consistency within assembled frames | Evaluated qualitatively |
| Graph stability | Rate of structural change between enhancement cycles | Low drift between cycles |
| Confidence calibration | Average Brier score across predictions | < 0.15 |
| Attention stability | Overlap between successive frame assemblies for same query | > 0.60 |

### 13.2 Parameter Tuning

The system supports multiple tuning methods:

- **Grid search** over scoring weight parameters for each frame type provides a baseline
- **Bayesian optimisation** can find optimal weight configurations more efficiently
- **A/B testing** of retrieval weight variants against held-out evaluation sets provides empirical validation
- **Simulation runs** using synthetic memory corpora test system behaviour under controlled conditions

### 13.3 Feedback Loops

The system becomes self-calibrating through several feedback mechanisms:

- The **Brier score log** tracks prediction accuracy over time and adjusts confidence weights accordingly
- **Centrality drift monitoring** detects when the graph's structural importance distribution shifts unexpectedly
- **Entropy monitoring** tracks the diversity of categories and concepts within frames
- **Attention stability tracking** measures whether successive assemblies for similar queries produce consistent results

---

## 14. Minimal Viable Product Scope

The MVP delivers a working system that demonstrates the core architectural principles without the full mathematical and agent infrastructure. The guiding principle is: **get the data model right, prove the frame abstraction works, and defer expensive optimisation.**

### MVP Includes

- DuckDB storage with `memory_blocks`, `edges`, and `frame_definitions` tables
- Content-hash ID generation with canonicalisation
- Basic ingestion pipeline (markdown content to inbox)
- Single-pass consolidation (categorise, embed, link, score)
- Simple half-life decay with configurable lambda per block
- Tag-based category system
- Basic composite attention scoring with configurable weights
- Two atomic frame types: SELF and ATTENTION
- One composite frame: SESSION (SELF + ATTENTION)
- INBOX as a context frame
- REST API via FastAPI with core endpoints
- CLI via Typer with ingest, search, frame assemble, consolidate, and status commands
- Python SDK with MemoryClient
- NetworkX for graph centrality computation
- Sentence-transformer embeddings (local, no API cost)

### MVP Excludes (Deferred to v1.1+)

- Monte Carlo simulation
- Shannon entropy balancing in frame selection
- Advanced calibration (Brier score feedback loops)
- Enhancement agent (graph health evaluation and repair)
- Curation agent (automated pruning)
- Bayesian parameter optimisation
- External graph database support
- External vector index support
- Multi-tenant isolation
- Web UI

---

## 15. Python Package Structure

```
amgs/
├── __init__.py
├── core/
│   ├── __init__.py
│   ├── models.py          # Pydantic models: MemoryBlock, Edge, Score, FrameDefinition
│   ├── storage.py         # DuckDB connection, table creation, CRUD
│   ├── hashing.py         # Content canonicalisation and SHA-256 ID generation
│   ├── decay.py           # Half-life decay computation
│   └── scoring.py         # Composite attention score calculation
├── graph/
│   ├── __init__.py
│   ├── manager.py         # NetworkX graph build, cache, rebuild
│   ├── centrality.py      # Degree, eigenvector, PageRank, betweenness
│   └── traversal.py       # Neighbourhood expansion, path finding
├── retrieval/
│   ├── __init__.py
│   ├── keyword.py         # Full-text and fuzzy search
│   ├── vector.py          # Embedding similarity search
│   └── hybrid.py          # Layered retrieval orchestration
├── frames/
│   ├── __init__.py
│   ├── registry.py        # Frame definition storage and DAG validation
│   ├── builder.py         # Frame construction pipeline (4 steps)
│   ├── composer.py        # Composite frame merging and budget allocation
│   └── assembler.py       # Output formatting (markdown, structured, summarised)
├── embeddings/
│   ├── __init__.py
│   └── provider.py        # Embedding generation (sentence-transformers, OpenAI, etc.)
├── lifecycle/
│   ├── __init__.py
│   ├── ingestion.py       # Inbox write logic
│   ├── consolidation.py   # Full consolidation pipeline
│   ├── enhancement.py     # Graph health evaluation and repair
│   ├── reinforcement.py   # Access tracking and decay reset
│   └── pruning.py         # Decay-based soft delete and edge cleanup
├── calibration/
│   ├── __init__.py
│   ├── brier.py           # Brier score computation and logging
│   └── tuning.py          # Weight optimisation (grid search, Bayesian)
├── api/
│   ├── __init__.py
│   ├── app.py             # FastAPI application factory
│   ├── routes/
│   │   ├── memory.py      # /v1/memory/* endpoints
│   │   ├── frames.py      # /v1/frame/* endpoints
│   │   ├── graph.py       # /v1/graph/* endpoints
│   │   └── lifecycle.py   # /v1/lifecycle/* endpoints
│   └── deps.py            # Dependency injection (storage, graph, embeddings)
├── cli/
│   ├── __init__.py
│   └── main.py            # Typer CLI application
├── sdk/
│   ├── __init__.py
│   └── client.py          # MemoryClient (httpx-based API client)
└── config.py              # Configuration (paths, defaults, decay profiles)
```

---

## 16. Differentiation from Existing Systems

Existing patterns that AMGS draws from include knowledge graphs, RAG pipelines, agent memory frameworks, GraphRAG-style systems, vector databases, and hybrid search systems. However, AMGS is architecturally distinct in several important ways.

| Existing Pattern | What It Does | What AMGS Adds |
|------------------|--------------|----------------|
| Vector database + RAG | Semantic retrieval over static documents | Temporal decay, confidence calibration, graph relationships, dynamic identity |
| Knowledge graph | Structured entity-relationship storage | Decay-aware relevance, attention scoring, frame-based context assembly |
| Agent memory (LangChain, etc.) | Conversation history and simple retrieval | Graph centrality, composite scoring, self-assembling identity, calibration |
| GraphRAG | Graph-enhanced retrieval augmented generation | Context frames as universal abstraction, compositional frames, lifecycle agents |

The fundamental difference is that AMGS treats memory as a **living system** with its own lifecycle, rather than as a static store that is queried. Memory decays, relationships evolve, confidence is calibrated, and identity emerges from the interaction of these processes over time.

---

## 17. Implementation Roadmap

| Phase | Scope | Duration | Key Deliverables |
|-------|-------|----------|------------------|
| Phase 1: Foundation | Core data model, storage, ingestion, basic search | 2–3 weeks | DuckDB schema, MemoryBlock CRUD, content hashing, keyword + vector search |
| Phase 2: Frames | Frame abstraction, SELF, ATTENTION, INBOX, composition | 2–3 weeks | Frame registry, builder pipeline, composite frames, SESSION assembly |
| Phase 3: Interface | REST API, CLI, Python SDK | 1–2 weeks | FastAPI app, Typer CLI, MemoryClient SDK |
| Phase 4: Lifecycle | Consolidation pipeline, reinforcement, basic pruning | 2 weeks | Consolidation agent, decay computation, reinforcement tracking |
| Phase 5: Graph Intelligence | Centrality computation, graph expansion, enhanced scoring | 2 weeks | NetworkX integration, centrality measures, graph-aware retrieval |
| Phase 6: Calibration | Brier scoring, confidence feedback, weight tuning | 2 weeks | Calibration log, feedback loops, grid search tuning |
| Phase 7: Enhancement | Graph health evaluation, merge/split, Monte Carlo | 3–4 weeks | Enhancement agent, simulation framework, automated repair |

---

## Conclusion

The Adaptive Memory Graph System is a graph-based, decay-aware, confidence-calibrated, context-assembling cognitive engine. Its central abstraction — the **Context Frame** — unifies all forms of context assembly (identity, attention, world knowledge, task reasoning, and even the raw inbox) under a single compositional model. The layered interface architecture ensures that the system is equally accessible to CLI operators, API consumers, agent code, and interactive notebooks.

This specification provides the complete reference for implementing AMGS. The MVP scope is deliberately constrained to prove the core abstractions, with a clear roadmap for incremental capability expansion.

---

*End of Specification*