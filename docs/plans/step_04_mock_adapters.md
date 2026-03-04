# Step 4: Mock Adapters — Implementation Plan

## Overview

Build mock implementations of the `LLMService` and `EmbeddingService` protocols
for deterministic testing. These mocks enable all downstream tests (Steps 5-7)
to run without external API calls.

**Key design decisions (locked):**
- Mocks satisfy the existing Protocol contracts in `ports/services.py`
- Embeddings are hash-seeded and deterministic (same input = same vector)
- Similarity overrides allow controlled cosine similarity in tests
- LLM mock returns configurable alignment scores, tags, and contradiction scores
- Factory functions for concise test setup
- All mocks are async (matching the protocol signatures)

---

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `src/elfmem/adapters/__init__.py` | Create | Package init |
| `src/elfmem/adapters/mock.py` | Create | MockLLMService + MockEmbeddingService |
| `tests/conftest.py` | Modify | Add shared pytest fixtures |

---

## Module Design

### 1. `src/elfmem/adapters/__init__.py`

```python
"""elfmem adapters — concrete implementations of port protocols."""
```

---

### 2. `src/elfmem/adapters/mock.py`

**Purpose:** Deterministic mock implementations of LLMService and EmbeddingService
for testing. No external calls, no randomness — fully reproducible.

**Imports:**
```python
from __future__ import annotations

import hashlib
import struct

import numpy as np

from elfmem.ports.services import EmbeddingService, LLMService
```

**Class: `MockLLMService`**

```python
class MockLLMService:
    """Deterministic mock LLM service for testing.

    All methods are async to match the LLMService protocol.
    Returns configurable scores and tags without making any API calls.

    Args:
        default_alignment: Default self-alignment score for any block.
        alignment_overrides: Dict mapping content substrings to alignment scores.
            If block content contains the substring, that score is returned.
        default_tags: Default tags returned by infer_self_tags.
        tag_overrides: Dict mapping content substrings to tag lists.
        default_contradiction: Default contradiction score for any block pair.
        contradiction_overrides: Dict mapping (content_a_substring, content_b_substring)
            tuples to contradiction scores.
    """

    def __init__(
        self,
        *,
        default_alignment: float = 0.5,
        alignment_overrides: dict[str, float] | None = None,
        default_tags: list[str] | None = None,
        tag_overrides: dict[str, list[str]] | None = None,
        default_contradiction: float = 0.1,
        contradiction_overrides: dict[tuple[str, str], float] | None = None,
    ) -> None:
```

**Key implementation notes for `MockLLMService`:**

- `alignment_overrides` is checked by iterating keys and testing
  `if substring in block` — first match wins. This lets tests control
  alignment for specific content without exact-matching entire blocks.
- `tag_overrides` works the same way: `if substring in block` → return
  those tags.
- `contradiction_overrides` iterates `(sub_a, sub_b)` keys and checks
  `if sub_a in block_a and sub_b in block_b` — first match wins.
- Track call counts for assertions: `self.alignment_calls: int = 0`,
  `self.tag_calls: int = 0`, `self.contradiction_calls: int = 0`.
  Increment on each method call.
- All methods must be `async def` to satisfy the Protocol.
- Must pass `isinstance(mock, LLMService)` since LLMService is
  `@runtime_checkable`.

**Method signatures:**

```python
async def score_self_alignment(self, block: str, self_context: str) -> float:
    """Return alignment score. Checks overrides first, then default."""

async def infer_self_tags(self, block: str, self_context: str) -> list[str]:
    """Return inferred tags. Checks overrides first, then default."""

async def detect_contradiction(self, block_a: str, block_b: str) -> float:
    """Return contradiction score. Checks overrides first, then default."""
```

---

**Class: `MockEmbeddingService`**

```python
class MockEmbeddingService:
    """Deterministic mock embedding service for testing.

    Generates reproducible embeddings from content hashes. Same input always
    produces the same vector. Supports similarity overrides for controlled
    cosine similarity between specific content pairs.

    Args:
        dimensions: Embedding vector dimensionality. Default: 64 (small for tests).
        similarity_overrides: Dict mapping frozenset({content_a, content_b}) to
            desired cosine similarity. When both contents have been embedded,
            the second vector is adjusted to achieve the target similarity.
    """

    def __init__(
        self,
        *,
        dimensions: int = 64,
        similarity_overrides: dict[frozenset[str], float] | None = None,
    ) -> None:
```

**Key implementation notes for `MockEmbeddingService`:**

- **Hash-seeded deterministic embeddings:** Use `hashlib.sha256(text.encode()).digest()`
  to seed a deterministic vector. Convert the hash bytes to floats and pad/truncate
  to `dimensions`. Then L2-normalise so cosine similarity works correctly.
- **Implementation approach for deterministic vector from hash:**
  ```python
  digest = hashlib.sha256(text.encode("utf-8")).digest()
  # Extend digest to fill dimensions (repeat hash if needed)
  needed_bytes = dimensions * 4  # 4 bytes per float32
  extended = digest * (needed_bytes // len(digest) + 1)
  raw = np.frombuffer(extended[:needed_bytes], dtype=np.float32)
  # Normalise to unit vector
  norm = np.linalg.norm(raw)
  if norm > 0:
      raw = raw / norm
  return raw
  ```
- **Similarity overrides:** Store a cache `self._cache: dict[str, np.ndarray]`
  of previously generated embeddings. When `embed(text)` is called:
  1. Check if `text` has a similarity override with any previously cached text
  2. If yes, generate a vector that achieves the target cosine similarity
     with the cached vector
  3. If no, generate the default hash-seeded vector
  4. Cache the result
- **Generating a vector with target cosine similarity:** Given a unit vector `a`
  and target similarity `s`, construct `b = s*a + sqrt(1-s²)*orthogonal` where
  `orthogonal` is a deterministic unit vector orthogonal to `a` (derived from
  the hash of the new text).
- Track call count: `self.embed_calls: int = 0`
- Must pass `isinstance(mock, EmbeddingService)` since EmbeddingService is
  `@runtime_checkable`.

**Method signature:**

```python
async def embed(self, text: str) -> np.ndarray:
    """Return a deterministic normalised float32 embedding vector.

    Same text always produces the same vector. If similarity_overrides
    are configured, adjusts the vector to achieve the target cosine
    similarity with previously embedded texts.
    """
```

---

**Factory functions:**

```python
def make_mock_llm(**kwargs) -> MockLLMService:
    """Create a MockLLMService with optional overrides.

    Convenience wrapper — all kwargs are passed to MockLLMService.__init__.

    Examples:
        make_mock_llm()  # all defaults
        make_mock_llm(default_alignment=0.9)
        make_mock_llm(alignment_overrides={"identity": 0.95})
    """
    return MockLLMService(**kwargs)


def make_mock_embedding(**kwargs) -> MockEmbeddingService:
    """Create a MockEmbeddingService with optional overrides.

    Convenience wrapper — all kwargs are passed to MockEmbeddingService.__init__.

    Examples:
        make_mock_embedding()  # 64-dim, no overrides
        make_mock_embedding(dimensions=128)
        make_mock_embedding(similarity_overrides={
            frozenset({"cats are great", "dogs are great"}): 0.85,
        })
    """
    return MockEmbeddingService(**kwargs)
```

---

### 3. `tests/conftest.py` — Modification

**Purpose:** Add shared pytest fixtures that are used across all test modules
(Steps 3-7+). These fixtures provide pre-configured mocks and an in-memory
database engine.

**Imports to add:**
```python
import pytest

from elfmem.adapters.mock import (
    MockLLMService,
    MockEmbeddingService,
    make_mock_llm,
    make_mock_embedding,
)
from elfmem.db.engine import create_test_engine
from elfmem.db.queries import seed_builtin_data
```

**Fixtures to add:**

```python
@pytest.fixture
def mock_llm() -> MockLLMService:
    """A MockLLMService with sensible defaults for general testing."""
    return make_mock_llm()


@pytest.fixture
def mock_embedding() -> MockEmbeddingService:
    """A MockEmbeddingService with 64-dim vectors, no overrides."""
    return make_mock_embedding()


@pytest.fixture
async def test_engine():
    """An in-memory async SQLite engine with all tables created and seeded.

    Yields the engine; disposes after the test.
    """
    engine = await create_test_engine()
    async with engine.begin() as conn:
        await seed_builtin_data(conn)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_conn(test_engine):
    """An async connection to the test database within a transaction.

    The transaction is rolled back after each test for isolation.
    """
    async with test_engine.begin() as conn:
        yield conn
        # Transaction is rolled back automatically when context exits
        # without commit — provides test isolation
```

**Key implementation notes for conftest.py:**
- `pytest-asyncio` is configured with `asyncio_mode = "auto"` in pyproject.toml,
  so async fixtures and tests work without `@pytest.mark.asyncio`
- `test_engine` creates tables and seeds data; `db_conn` provides an isolated
  transaction per test
- The `db_conn` fixture uses `engine.begin()` — the transaction is automatically
  rolled back when the `async with` block exits without explicit commit, giving
  each test a clean slate
- Fixtures are session/function-scoped as appropriate — `test_engine` can be
  function-scoped for isolation (each test gets a fresh DB)

---

## Key Invariants

1. **Protocol compliance** — `isinstance(MockLLMService(), LLMService)` is True;
   same for `MockEmbeddingService` / `EmbeddingService`
2. **Determinism** — same input to `embed()` always returns the same vector;
   same content always gets the same alignment score
3. **Normalised vectors** — all mock embeddings have L2 norm ≈ 1.0
4. **No external calls** — mocks never import litellm, openai, or any provider SDK
5. **Call tracking** — all mocks track call counts for test assertions

## Security Considerations

1. **No real API keys** — mocks don't read environment variables or connect
   to external services
2. **No secrets in test fixtures** — conftest.py contains no credentials
3. **Deterministic hashing** — uses sha256 for reproducibility, not for
   security (test-only)

## Edge Cases

1. **Empty content** — `embed("")` should still produce a valid unit vector
   (hash of empty string is deterministic)
2. **Very long content** — hash-based approach handles any length
3. **Similarity override for unseen text** — override only activates when
   both texts in the pair have been embedded; first text returns default vector
4. **Similarity = 1.0 override** — should return identical vectors
5. **Similarity = 0.0 override** — should return orthogonal vectors
6. **Multiple overrides matching** — first match wins (iterate dict order)
7. **No overrides configured** — all methods return defaults

## Dependencies

- `numpy` (already in pyproject.toml) — for embedding vectors
- `elfmem.ports.services` — Protocol definitions
- `elfmem.db.engine` + `elfmem.db.queries` — for conftest fixtures (Step 3 must
  be complete before conftest.py fixtures involving the DB can be used)

## Done Criteria

1. `from elfmem.adapters.mock import MockLLMService, MockEmbeddingService, make_mock_llm, make_mock_embedding` — all importable
2. `isinstance(MockLLMService(), LLMService)` is True
3. `isinstance(MockEmbeddingService(), EmbeddingService)` is True
4. `await mock.embed("hello")` returns a float32 ndarray of correct dimensions
5. `await mock.embed("hello")` called twice returns identical vectors
6. Similarity overrides produce vectors with the target cosine similarity (±0.01)
7. `await mock.score_self_alignment(block, ctx)` returns configured score
8. `await mock.infer_self_tags(block, ctx)` returns configured tags
9. `await mock.detect_contradiction(a, b)` returns configured score
10. All fixtures in conftest.py work with pytest-asyncio
11. `mypy --strict` passes on `adapters/mock.py`
