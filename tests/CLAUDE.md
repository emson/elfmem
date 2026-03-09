# Test Principles — elfmem

Tests verify **behaviour through the public API** using mock infrastructure. The full test suite must run in seconds. See `docs/testing_principles.md` for rationale.

## Infrastructure (non-negotiable)

```python
# ✅ Always: in-memory SQLite + mock services (no LLM cost, no file I/O)
@pytest.fixture
async def system(test_engine, mock_llm, mock_embedding) -> MemorySystem:
    cfg = ElfmemConfig(memory=MemoryConfig(inbox_threshold=3))  # low for fast cycles
    return MemorySystem(engine=test_engine, llm_service=mock_llm, embedding_service=mock_embedding, config=cfg)

# ❌ Never: these create a file-based DB + real LiteLLM adapters
SmartMemory.open(db_path_str)
MemorySystem.from_config(db_path)
```

**Use conftest fixtures:** `test_engine` (in-memory SQLite, `StaticPool`), `mock_llm`, `mock_embedding`, `db_conn`.
Set `inbox_threshold=3` (not the default 10) whenever a test runs learn→dream cycles.

## What to Test

- **Public API** — `MemorySystem` methods, result types, exception contracts
- **Behaviour** — what operations do, not how they do it internally
- **Integration** — the full path through the system with mock services
- **Error contracts** — correct exception type raised, `.recovery` field present

## What NOT to Test

- Private methods and internals (`_stage_5_mmr_diversity`, `_pending`, `_archive_decayed_blocks`)
- Formatting or exact string content (`assert str(r) == "Stored block a1b2…"`)
- Deprecated wrappers (`SmartMemory`)
- Third-party library internals
- Vacuous assertions that always pass (`assert x is not None or x is None`)

## Pattern

```python
# Arrange-Act-Assert. One logical assertion per test.
async def test_learn_increments_inbox(self, system):
    await system.learn("Some knowledge")
    assert (await system.status()).inbox_count == 1

# State inspection: use status() not internal attributes
# Pending count:  (await system.status()).pending_count
# Threshold:      (await system.status()).inbox_threshold
# Float tolerance: assert abs(result - expected) < 0.001
```

## Performance Rules

- No real LLM calls — `MockLLMService` is synchronous and deterministic
- No file I/O — in-memory SQLite only (`test_engine`, never `db_path_str`)
- No `time.sleep()`, no polling
- `inbox_threshold=3` for any test running learn → consolidate cycles
