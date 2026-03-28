# Logging Strategy for elfmem

## Executive Summary

Add best-practice, production-ready logging to elfmem that is:
- **Minimal by default** — zero overhead when not enabled
- **Structured** — machine-readable, JSON-serializable
- **Contextual** — operation IDs, session IDs, block ID prefixes
- **Hierarchical** — enable/disable by module or operation type
- **Agent-friendly** — parseable by downstream systems
- **Non-intrusive** — aligns with fail-fast, no-defensive-code principles

Target: agents deploying elfmem get observability without code changes; operators can tune verbosity per layer.

---

## Core Design Principles

### 1. Minimal by Default
- No logging output unless explicitly enabled
- `ELFMEM_LOG_LEVEL=INFO` or `--log-level INFO` to activate
- Logging config is **opt-in**, not opt-out
- Tests run silently (no log pollution)
- Zero performance cost when disabled (debug statements use lazy evaluation)

### 2. Structured Logging
All log events emit JSON-serializable structured data, not prose:
```python
{
    "timestamp": "2026-03-28T15:42:17.123456Z",
    "operation": "consolidate",
    "operation_id": "c-abc123def456",
    "session_id": "s-xyz789abc012",
    "level": "INFO",
    "event": "started",
    "inbox_count": 5,
    "elapsed_ms": 0
}
```

Not: `"Starting consolidation of 5 blocks"` (hard to parse, not programmatic).

### 3. Contextual Awareness
Every log event carries:
- **Timestamp** (ISO 8601 UTC with microseconds)
- **Operation type** (learn, recall, consolidate, curate, outcome, connect, frame)
- **Operation ID** (UUID-like string `o-<16-char-hex>` generated per operation)
- **Session ID** (set when session begins; stable across multiple operations)
- **Block IDs** (if relevant, shortened to 8 chars for readability)
- **Elapsed time** (ms for completed operations)

Enables correlation across logs and replay/debugging.

### 4. Hierarchical Control
```python
logging.getLogger("elfmem").setLevel(logging.INFO)
logging.getLogger("elfmem.operations.consolidate").setLevel(logging.DEBUG)
logging.getLogger("elfmem.adapters").setLevel(logging.WARNING)
```

Developers can focus on specific subsystems without noise from others.

### 5. Agent-First, Non-Intrusive Design
- **No exceptions in business logic** — logging errors don't raise; they emit logs and continue
- **No side effects** — logging doesn't modify state or affect operation outcomes
- **Idempotent** — duplicate events log twice (same operation ID), clients deduplicate if needed
- **Recoverable** — logger failure doesn't cascade; uses `sys.stderr` as final fallback

---

## Logging Architecture

### Layer Responsibilities

#### System Initialization (`elfmem.api`, `elfmem.config`)
**What to log:**
- Configuration loaded (values, source)
- LLM adapter initialized (model, base_url, not API keys)
- Embedding adapter initialized (model, dimensions)
- Database engine created (path, SQLite version, WAL enabled)
- Session created/destroyed (session_id, active_hours at destroy)

**Level:** INFO (startup diagnostics) + DEBUG (full config dump)

**Example:**
```json
{
    "timestamp": "2026-03-28T15:42:00Z",
    "source": "elfmem.api",
    "event": "system_initialized",
    "llm_model": "claude-haiku-4-5-20251001",
    "embedding_model": "text-embedding-3-small",
    "db_path": "/home/user/.elfmem/agent.db",
    "embedding_dimensions": 1536
}
```

#### Operation Entry/Exit (`elfmem.operations.*`, wrapped in `elfmem.api`)
**What to log per operation:**
- Operation started (operation_id, input summary)
- Operation completed (operation_id, elapsed_ms, result summary)
- Operation failed (operation_id, error type, recovery hint)

**Levels:**
- DEBUG: Full input/output payloads (content, embeddings, etc.)
- INFO: Operation outcome (N blocks learned, N promoted, etc.)
- WARNING: Recoverable anomalies (duplicate rejected, timeout retry)
- ERROR: Unrecoverable failure (DB write failed, LLM service down)

**Example:**
```json
{
    "timestamp": "2026-03-28T15:42:10Z",
    "operation": "learn",
    "operation_id": "o-abc123def456",
    "session_id": "s-xyz789abc012",
    "event": "started",
    "content_length": 245,
    "category": "knowledge",
    "tags": ["memory", "agent"]
}
```

```json
{
    "timestamp": "2026-03-28T15:42:10.050Z",
    "operation": "learn",
    "operation_id": "o-abc123def456",
    "session_id": "s-xyz789abc012",
    "event": "completed",
    "status": "created",
    "block_id": "abc123de",
    "elapsed_ms": 45
}
```

#### LLM Calls (`elfmem.adapters.anthropic`, `elfmem.adapters.openai`)
**What to log:**
- Model call started (model, prompt length, max_tokens)
- Model call completed (model, input_tokens, output_tokens, elapsed_ms)
- Model call failed (model, error type, retry count)
- Token usage summary per operation

**Level:** DEBUG (individual calls) + INFO (aggregate per operation)

**Example:**
```json
{
    "timestamp": "2026-03-28T15:42:15Z",
    "source": "elfmem.adapters.anthropic",
    "event": "llm_call_started",
    "operation_id": "o-abc123def456",
    "model": "claude-haiku-4-5-20251001",
    "prompt_tokens": 156,
    "max_tokens": 512
}
```

```json
{
    "timestamp": "2026-03-28T15:42:18.500Z",
    "source": "elfmem.adapters.anthropic",
    "event": "llm_call_completed",
    "operation_id": "o-abc123def456",
    "model": "claude-haiku-4-5-20251001",
    "input_tokens": 156,
    "output_tokens": 87,
    "elapsed_ms": 3500,
    "stop_reason": "end_turn"
}
```

#### Embedding Calls (`elfmem.adapters.openai`)
**What to log:**
- Batch embeddings started (batch_size)
- Batch embeddings completed (batch_size, model, elapsed_ms)
- Batch embeddings failed (batch_size, error)

**Level:** DEBUG (per batch) + summary in consolidate logs

**Example:**
```json
{
    "timestamp": "2026-03-28T15:42:20Z",
    "source": "elfmem.adapters.openai",
    "event": "embeddings_batch",
    "operation_id": "o-abc123def456",
    "batch_size": 5,
    "model": "text-embedding-3-small",
    "elapsed_ms": 1200
}
```

#### Decision Points (`elfmem.operations.consolidate`, `elfmem.operations.curate`)
**What to log:**
- Block promotion decisions (block_id, reason: promoted/rejected_exact/superseded)
- Edge creation (from_id → to_id, weight, reason)
- Archival decisions (block_id, reason: decayed/superseded/forgotten)
- Contradiction detected (block_a → block_b, confidence)
- Near-duplicate handling (block_id, action, similarity)

**Level:** DEBUG (per-decision) + INFO (summary statistics)

**Example:**
```json
{
    "timestamp": "2026-03-28T15:42:25Z",
    "source": "elfmem.operations.consolidate",
    "event": "block_decision",
    "operation_id": "o-con123def456",
    "block_id": "abc123de",
    "action": "promote",
    "confidence": 0.92,
    "summary": "Understanding of parameter scope in async contexts",
    "inferred_tags": ["async", "python"],
    "decay_tier": "standard"
}
```

```json
{
    "timestamp": "2026-03-28T15:42:26Z",
    "source": "elfmem.operations.consolidate",
    "event": "edge_created",
    "operation_id": "o-con123def456",
    "from_id": "abc123de",
    "to_id": "def456ab",
    "weight": 0.67,
    "reason": "semantic_similarity"
}
```

#### Database Operations (`elfmem.db.queries`, `elfmem.db.engine`)
**What to log:**
- Transaction boundaries (begin, commit, rollback)
- Slow query detection (query > 100ms logged as WARNING)
- Lock contention warnings (attempted write; had to wait > 50ms)
- Bulk operations (N rows inserted/updated/deleted)

**Level:** DEBUG (all operations) + WARNING (slow/contention)

**Example:**
```json
{
    "timestamp": "2026-03-28T15:42:27Z",
    "source": "elfmem.db.queries",
    "event": "transaction_begin",
    "session_id": "s-xyz789abc012",
    "operation": "consolidate"
}
```

```json
{
    "timestamp": "2026-03-28T15:42:28.150Z",
    "source": "elfmem.db.queries",
    "event": "slow_query",
    "operation_id": "o-con123def456",
    "query_type": "update_block_status",
    "elapsed_ms": 152,
    "threshold_ms": 100,
    "row_count": 1
}
```

---

## Configuration

### Environment Variables (No Code Change Needed)

```bash
# Enable logging at specified level (DEBUG, INFO, WARNING, ERROR)
export ELFMEM_LOG_LEVEL=INFO

# Choose output format (text, json, compact)
export ELFMEM_LOG_FORMAT=json

# Route logs to file instead of stderr
export ELFMEM_LOG_FILE=/tmp/elfmem.log

# Per-module overrides (colon-separated module:level pairs)
export ELFMEM_LOG_MODULES=elfmem.operations.consolidate:DEBUG,elfmem.adapters:WARNING

# Disable logging in tests (always set in test suite)
export ELFMEM_LOG_LEVEL=CRITICAL
```

### Config File Integration (`.elfmem/config.yaml`)

```yaml
# Optional logging section
logging:
  level: INFO  # global level
  format: json  # text | json | compact
  file: ~/.elfmem/elfmem.log  # null = stderr
  modules:
    elfmem.operations.consolidate: DEBUG  # per-module overrides
    elfmem.adapters: WARNING

  # Advanced tuning
  slow_query_threshold_ms: 100
  lock_contention_threshold_ms: 50

  # For high-volume systems: sample logs
  # (reduces I/O but keep 1:1 for errors)
  sample_rate: 1.0  # 1.0 = all, 0.1 = 10%
```

### Programmatic Control (`MemorySystem` API)

```python
from elfmem import MemorySystem, ElfmemConfig
from elfmem.logging_config import configure_logging

# Option 1: Set via environment before import
import os
os.environ["ELFMEM_LOG_LEVEL"] = "DEBUG"

# Option 2: Configure explicitly
configure_logging(level="DEBUG", format="json", file="/tmp/elf.log")

# Option 3: Set per-module (fine-grained)
configure_logging(
    level="INFO",
    module_overrides={
        "elfmem.operations.consolidate": "DEBUG",
        "elfmem.adapters": "WARNING"
    }
)

system = await MemorySystem.from_config("agent.db")
```

---

## Edge Cases & Mitigation

### 1. Performance Impact (Critical)

**Problem:** Logging overhead breaks latency budget for `learn()` (should be <10ms).

**Mitigation:**
- All logging is **asynchronous** (queued, never blocks)
- Debug-level logs use **lazy evaluation**: `logger.debug("result=%s", expensive_fn())` only calls `expensive_fn()` if DEBUG is enabled
- LLM/embedding calls already exceed 100ms, so their logs are negligible
- Logging is **disabled by default** — zero overhead in production without env var
- Per-operation sampling via `sample_rate` config

**Implementation:**
```python
# ❌ Blocks on slow operation if log disabled
import logging
logger = logging.getLogger(__name__)

async def learn(...):
    logger.info(f"Learning {content[:100]}...")  # slow!
    ...

# ✅ Lazy; only evaluates if DEBUG enabled
logger.debug("Learning %s", content[:100])

# ✅ Async; never blocks
import logging.handlers
handler = logging.handlers.QueueHandler(queue)
handler.target = logging.handlers.TimedRotatingFileHandler(...)
```

### 2. Test Isolation (Critical)

**Problem:** Logs in tests pollute stdout/stderr, make test output hard to read.

**Mitigation:**
- `conftest.py` sets `ELFMEM_LOG_LEVEL=CRITICAL` (suppresses all elfmem logs)
- Tests can opt-in to logs via `@pytest.mark.log_level("DEBUG")`
- Fixture provides in-memory log capture for assertions

**Implementation:**
```python
# conftest.py
import os
os.environ["ELFMEM_LOG_LEVEL"] = "CRITICAL"

@pytest.fixture
def log_capture():
    """Capture logs emitted during test."""
    handler = logging.handlers.MemoryHandler(capacity=1000)
    logging.getLogger("elfmem").addHandler(handler)
    yield handler.buffer
    logging.getLogger("elfmem").removeHandler(handler)

# test_consolidate.py
async def test_consolidate_promotes_blocks(system, log_capture):
    # Logs are captured but not printed
    await system.consolidate()
    # Can assert on logs if needed
    assert any(e.getMessage() == "block_decision" for e in log_capture)
```

### 3. Large-Scale Systems (100k+ blocks)

**Problem:** Logging every edge creation = millions of events per consolidate run.

**Mitigation:**
- Summary logging: log counts, not individual events
- Configurable sampling (e.g., `sample_rate=0.1` logs 10% of events)
- Metrics mode: counters/gauges for monitoring systems instead of individual events

**Example config:**
```yaml
logging:
  level: INFO
  sample_rate: 0.1  # Log 10% of DEBUG events
  # INFO and above always logged (never sampled)

  metrics:
    enabled: true
    format: prometheus  # /metrics endpoint for scraping
```

### 4. Sensitive Data Leakage

**Problem:** Block content, prompts, embeddings could leak into logs.

**Mitigation:**
- **Never log content** — only metadata (length, hash, ID)
- **Never log full prompts** — only length and template name
- **Never log embeddings** — only dimensions and norm statistics
- Content redaction: if DEBUG logs something sensitive, wrap in `[REDACTED]`

**Pattern:**
```python
# ❌ Leaks content
logger.debug("Block content: %s", block.content)

# ✅ Safe
logger.debug("Block stored", block_id=block.id, content_len=len(block.content))
```

### 5. Circular Logging (Adapter Failures)

**Problem:** If LLM adapter fails, logging may try to call the adapter again.

**Mitigation:**
- Logging infrastructure is **independent** of LLM/embedding services
- Only `stderr` is fallback (no API calls)
- Adapters log **before** attempting calls (not during error handling)

### 6. Clock Skew & Timestamp Consistency

**Problem:** Timestamps across multiple operations should be sortable/consistent.

**Mitigation:**
- Use **UTC+microseconds** (ISO 8601)
- All timestamps generated by logger, not callers
- Clock drift handled by client (sort by operation_id + timestamp if needed)

---

## Logging Flows

### learn() — Heartbeat
```
[started] content_length=245, tags=[x, y]
    → hash block
    → check for duplicate
[duplicate_rejected] or [created] → block_id, elapsed_ms
```

### consolidate() — Breathing
```
[started] inbox_count=5
    → embed inbox blocks [embeddings_batch] batch_size=5, elapsed_ms=1200
    → process each block [block_decision] action=promote|reject_exact|supersede
    → detect contradictions [contradiction_detected] or [no_contradiction]
    → create edges [edge_created] x N
    → write to DB [transaction_begin] → [N rows updated] → [transaction_commit]
[completed] processed=5, promoted=3, deduplicated=1, edges_created=4, elapsed_ms=5000
```

### curate() — Sleep
```
[started] active_count=42, current_active_hours=1200.5
    → archive decayed [block_archived] reason=decayed, block_id, decay_lambda
    → prune edges [edges_pruned] count=7, reason=weight_below_threshold
    → reinforce top-N [block_reinforced] block_id, new_confidence
[completed] archived=2, edges_pruned=7, reinforced=5, elapsed_ms=800
```

### recall() — Retrieval
```
[started] query="error handling", frame=attention, top_k=5
    → retrieve candidates [vector_search] results_count=12, elapsed_ms=45
    → score candidates [scoring] mean_score=0.67, std_dev=0.15
    → co-retrieval staging [co_retrieval_edge_promoted] from_id, to_id (if threshold met)
[completed] returned=5, cached=false, edges_promoted=1, elapsed_ms=150
```

---

## Implementation Roadmap

### Phase 1: Infrastructure (Week 1)
- [ ] Create `elfmem/logging_config.py` with logger factory
- [ ] Create `elfmem/logging_formatters.py` (JSON, text, compact)
- [ ] Update `ElfmemConfig` to include logging section
- [ ] Environment variable parsing in `config.py`
- [ ] Test infrastructure (log capture fixture)

**Files to create:**
- `src/elfmem/logging_config.py` (200 lines)
- `src/elfmem/logging_formatters.py` (150 lines)
- `tests/test_logging.py` (200 lines)

**Changes to existing files:**
- `src/elfmem/config.py` (add `LoggingConfig` class)
- `src/elfmem/api.py` (call `configure_logging()` in `from_config()`)
- `tests/conftest.py` (set `ELFMEM_LOG_LEVEL=CRITICAL` by default)

### Phase 2: Operation-Level Logging (Week 2)
- [ ] Wrap each operation in context manager for entry/exit
- [ ] Add operation_id and session_id context vars
- [ ] Log operation start/end with elapsed time
- [ ] Log result summary (created/promoted/etc.)

**Changes:**
- `src/elfmem/operations/*.py` (wrap each function)
- `src/elfmem/api.py` (ensure operation context propagates)

### Phase 3: LLM & Embedding Calls (Week 3)
- [ ] Log in `ElmthropicLLMAdapter`, `OpenAILLMAdapter`
- [ ] Log token usage (input, output, total)
- [ ] Log timings for slow calls (>5s warning)
- [ ] Log retries and failures

**Changes:**
- `src/elfmem/adapters/anthropic.py`
- `src/elfmem/adapters/openai.py`
- `src/elfmem/adapters/mock.py` (no-op logging for tests)

### Phase 4: Database & Decision Logging (Week 4)
- [ ] Log database transactions
- [ ] Log block promotion/archival decisions
- [ ] Log edge creation
- [ ] Log contradiction detection

**Changes:**
- `src/elfmem/db/queries.py` (wrap bulk operations)
- `src/elfmem/operations/consolidate.py` (decision points)
- `src/elfmem/operations/curate.py` (archival decisions)

### Phase 5: Documentation & Tuning (Week 5)
- [ ] Docs: how to enable logging (`docs/logging_guide.md`)
- [ ] Docs: log reference (all event types, fields)
- [ ] Docs: sampling and performance tuning
- [ ] Update CHANGELOG.md
- [ ] CLI: add `--log-level` flag to all commands

**Files to create:**
- `docs/logging_guide.md` (detailed examples)
- `docs/log_reference.md` (all event types)

**Changes:**
- `src/elfmem/cli.py` (add `--log-level` flag)
- CHANGELOG.md (new feature)
- README.md (reference logging guide)

### Phase 6: Testing & Hardening (Week 6)
- [ ] Performance testing (measure overhead with/without logging)
- [ ] Large-scale testing (consolidate 1k blocks with logging)
- [ ] Error path testing (logging when LLM fails, etc.)
- [ ] Test noise verification (ensure tests run silently by default)

---

## API Examples

### User: Enable Logging
```bash
# CLI: one-line enable
ELFMEM_LOG_LEVEL=INFO elfmem recall "my query"

# CLI: detailed tuning
ELFMEM_LOG_LEVEL=DEBUG ELFMEM_LOG_FORMAT=json ELFMEM_LOG_FILE=/tmp/elf.log elfmem serve

# Config file: persistent
# .elfmem/config.yaml
logging:
  level: INFO
  format: json
```

### User: Debug Specific Operation
```python
from elfmem.logging_config import configure_logging
from elfmem import MemorySystem

configure_logging(
    level="INFO",
    module_overrides={"elfmem.operations.consolidate": "DEBUG"}
)

system = await MemorySystem.from_config("agent.db")
await system.consolidate()  # Detailed logs only for consolidate
```

### User: Performance Tuning (High-Volume Agent)
```yaml
# 100k blocks, optimize I/O
logging:
  level: WARNING  # errors and slow queries only
  sample_rate: 0.01  # log 1% of DEBUG events
  slow_query_threshold_ms: 200  # adjust for your disk speed
  lock_contention_threshold_ms: 100
```

### Agent: Parse Logs Programmatically
```python
import json
import subprocess

result = subprocess.run(
    ["elfmem", "recall", "error handling"],
    env={**os.environ, "ELFMEM_LOG_LEVEL": "INFO", "ELFMEM_LOG_FORMAT": "json"},
    capture_output=True,
    text=True
)

# Parse stderr as JSON stream
for line in result.stderr.split("\n"):
    if line:
        event = json.loads(line)
        if event.get("event") == "completed":
            print(f"Recall returned {len(event['results'])} blocks")
```

---

## Metrics & Observability

### Prometheus-Compatible Metrics
```python
# Optional metrics (enabled with logging)
from elfmem.metrics import get_metrics

metrics = get_metrics()
# Counters
metrics.operation_count["learn"] → count of learn() calls
metrics.operation_count["consolidate"] → count of consolidate() calls
metrics.block_promoted_total → total blocks promoted
metrics.block_archived_total → total blocks archived

# Gauges
metrics.inbox_size → current inbox block count
metrics.active_size → current active block count
metrics.tokens_used_total → cumulative LLM tokens

# Histograms (bucket distributions)
metrics.operation_latency_seconds["consolidate"] → p50, p95, p99
metrics.llm_latency_seconds → distribution of LLM call times
```

**Scrape endpoint (if serving):**
```bash
GET /metrics → Prometheus format
# TYPE elfmem_operation_count counter
elfmem_operation_count{operation="learn"} 42
elfmem_operation_count{operation="consolidate"} 5
elfmem_block_promoted_total 27
elfmem_inbox_size 3
```

---

## Backwards Compatibility

**Zero breaking changes:**
- Logging is opt-in (no output unless configured)
- Existing code works unchanged
- New `logging` config section is optional
- Tests unaffected (disabled by default in test suite)

**Deprecation:**
- No logging features are deprecated in Phase 1

---

## Documentation Outline

### User Documentation

1. **`docs/logging_guide.md`** (for humans)
   - Quick start: "How to enable logging"
   - Config examples (env vars, config file, code)
   - Per-operation examples (consolidate debug, recall trace)
   - Performance tuning for large systems
   - Troubleshooting (logs not appearing, too noisy, etc.)

2. **`docs/log_reference.md`** (for reference)
   - All event types (table: operation, event, fields, level)
   - Field definitions (operation_id format, block_id, elapsed_ms)
   - Log format specifications (JSON schema for programmatic parsing)

3. **`docs/logging_architecture.md`** (for developers)
   - Design rationale (why structured + contextual)
   - Extension points (add custom handlers, metrics)
   - Testing with logs (capture fixture, assertions)

### Code Documentation

- Docstrings: operation entry/exit, context managers
- Inline comments: decision logging rationale
- Examples: code snippets in docstrings

---

## Summary: Why This Design

| Goal | How Achieved |
|------|--------------|
| **Minimal by default** | Disabled unless env var set; zero overhead when off |
| **Structured** | JSON events with typed fields, not prose messages |
| **Contextual** | operation_id, session_id, elapsed_ms on every event |
| **Hierarchical** | Per-module config (consolidate:DEBUG, adapters:WARNING) |
| **Agent-friendly** | Parseable output, predictable field names, exit codes |
| **Non-intrusive** | No exceptions in business logic; fail-fast preserved |
| **Performant** | Async logging, lazy debug, disabled in tests |
| **Secure** | No content/prompts/embeddings logged; metadata only |
| **Testable** | Log capture fixture, deterministic event format |

---

## Next Steps

1. **Review & Approve**: Feedback on scope, priorities, design choices?
2. **Kick Off Phase 1**: Create logging infrastructure
3. **Integrate**: Wrap operations one at a time (consolidate first, highest value)
4. **Document**: Reference guide + user guide
5. **Ship**: Release in next minor version (0.5.0)
