# elf — Adaptive Memory for LLM Agents

**elf** (`elfmem` package) is a self-aware adaptive memory system. Agents learn, reinforce, and forget knowledge the way biological memory works — fast ingestion, deep consolidation at pauses, decay-based archival at rest. SQLite-backed. Zero infrastructure.

## Core Mental Model

**Three rhythms** (every design decision maps to one of these):
- **Heartbeat** — `learn()`: milliseconds, no LLM, pure inbox insert
- **Breathing** — `dream()` / `consolidate()`: seconds, LLM-powered dedup + contradiction detection
- **Sleep** — `curate()`: minutes, decay archival + graph pruning + top-K reinforcement

**Five frames** — always select before retrieving context:
`self` · `attention` · `task` · `world` · `short_term`

**Knowledge lifecycle:** BIRTH → GROWTH → MATURITY → DECAY → ARCHIVE
Decay is session-aware (holidays don't kill knowledge). Reinforcement resets the clock.

## Code Style

**SIMPLE · ELEGANT · FLEXIBLE · ROBUST** — full patterns in `docs/coding_principles.md`

- **Functional Python** — pure functions, input → output, compose pipelines from ≤50-line functions
- **Fail fast** — exceptions bubble up; catch only at CLI/MCP system boundaries
- **No defensive code** — no broad `except`, no `try/except` in business logic
- **Complete type hints** — every function, public and private
- **Docstrings follow this template** on every public method:
  ```
  USE WHEN: …   DON'T USE WHEN: …   COST: …   RETURNS: …   NEXT: …
  ```

## Agent-First Contract

Every design decision serves the agent's one-shot loop: read → call → interpret → next.

- All operations return **typed result objects** with `__str__`, `summary`, `to_dict()`
- All exceptions carry a **`.recovery` field** — the exact code/command to fix the problem
- **`guide()`** returns runtime self-documentation; never raises on bad input
- **Idempotent**: duplicate `learn()` → graceful reject; empty `consolidate()` → zero counts, not error
- **Progressive disclosure**: Tier 1 (zero config, zero ceremony) must always work

Full principles: `docs/agent_friendly_principles.md`

## LLM / Embedding Infrastructure

- **Production**: `LiteLLMAdapter` + `LiteLLMEmbeddingAdapter`, wired by `MemorySystem.from_config()`
- **Tests**: always `MockLLMService` + `MockEmbeddingService` — **never real API calls**
- Config: `ElfmemConfig` via YAML / env vars / dict / `None` (sensible defaults)

## Public API

```python
from elfmem import MemorySystem, ElfmemConfig, ConsolidationPolicy
# All result types and exceptions also importable from root — see src/elfmem/__init__.py
```

## Key Paths

| Path | Purpose |
|------|---------|
| `src/elfmem/api.py` | `MemorySystem` — all public operations |
| `src/elfmem/types.py` | Result types, exceptions |
| `src/elfmem/operations/` | `learn`, `consolidate`, `curate`, `outcome`, `recall` |
| `src/elfmem/adapters/mock.py` | `MockLLMService`, `MockEmbeddingService` |
| `tests/conftest.py` | Shared test fixtures — always use these |
| `docs/amgs_architecture.md` | Full technical specification |
