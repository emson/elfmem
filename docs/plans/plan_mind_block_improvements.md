# Mind Block Lifecycle: Inline Promotion for Structured Blocks

**Status:** PROPOSED  
**Date:** 2026-05-01  
**Author:** elf

---

## Context

The `elfmem mind outcome` operation cannot close a prediction without first
running `consolidate()` / `dream()`. The prediction block exists in the database,
`mind show` finds it via edge traversal, but `mind outcome` returns "Block not
found in active memory" because the block has `status="inbox"`.

This breaks the Theory of Mind calibration loop. An agent that makes predictions
but cannot close them programmatically is demonstrating theatre, not calibration.
For the planned demo (temporal calibration divergence), this is a blocker.

---

## Problem Analysis

### Root Cause: Status Is Overloaded

The `status` field conflates two orthogonal concerns:

1. **"Has been through LLM adjudication"** — dedup, contradiction detection,
   alignment scoring, embedding generation
2. **"Is a first-class participant in the knowledge graph"** — can receive
   outcome signals, can be connected via edges, subject to decay

For **free-form knowledge**, these are the same thing. Raw observations need LLM
processing to earn their place — is this a duplicate? Does it contradict existing
knowledge? Consolidation is how knowledge is validated. The inbox stage is
biologically correct: sensory buffer before long-term encoding.

For **structured blocks** (mind, decision), they are not. A mind block is
machine-generated from a template. A decision block is a falsifiable prediction
with a `verify_at` date. Dedup doesn't apply — each prediction is unique by
definition. Contradiction detection doesn't apply — predictions are about the
future, not competing truth claims.

### The Three Failure Points

**1. `mind_outcome()` rejects inbox decision blocks**

```python
# src/elfmem/operations/mind.py:320
decision_block = await queries.get_block(conn, decision_block_id)
if decision_block is None or decision_block.get("status") != "active":
    raise BlockNotActiveError(decision_block_id)  # ← The reported bug
```

**2. `predict()` rejects inbox mind blocks**

```python
# src/elfmem/operations/mind.py:153
mind_block = await queries.get_block(conn, mind_block_id)
if mind_block is None or mind_block.get("status") != "active":
    raise BlockNotActiveError(mind_block_id)
```

**3. `record_outcome()` silently skips inbox blocks**

```python
# src/elfmem/operations/outcome.py:103
if block is None or block["status"] != "active":
    continue  # silently skips — no error, but no update either
```

### Design Inconsistency

| Operation | Inbox blocks | Active blocks |
|-----------|-------------|--------------|
| `outcome()` (regular) | Silently skips | Processes |
| `mind_outcome()` | **Raises exception** | Processes |
| `do_connect()` | Raises exception | Processes |
| `predict()` | **Raises exception** | Processes |

The mind operations are stricter than the regular outcome path, but there's no
architectural reason for the difference.

### Why Tests Don't Catch It

Every existing test calls `await system.consolidate()` between `mind_predict()`
and `mind_outcome()`. The test suite was written to conform to the strict
requirement, masking the bug. No test attempts outcome closure on an
unconsolidated prediction.

Example from `tests/test_mind.py:292`:
```python
pred = await system.mind_predict(mind_id, "Will pay 49/mo", ...)
await system.consolidate()  # ← mandatory consolidation
result = await system.mind_outcome(pred.decision_block_id, hit=True, ...)
```

---

## Architectural Principle

**Deliberate acts are their own consolidation events for structured blocks.**

In biological memory, consolidation happens during rest — sensory experience is
processed, deduplicated, and integrated into long-term memory. But a prediction
is not sensory experience. It's a deliberate encoding — the agent explicitly
states "I believe X will happen by Y." You don't need to sleep on a prediction
before you can evaluate it.

The act of closing a prediction IS its consolidation. The outcome signal — hit
or miss — is the validation that free-form knowledge gets from LLM processing.

This maps to the three rhythms:
- `learn()` = Heartbeat (inbox insert, no LLM) — for free-form knowledge
- Mind lifecycle events = Heartbeat with inline promotion — for structured blocks
- `consolidate()` = Breathing (full LLM processing) — for knowledge that needs adjudication

---

## Options Evaluated

### Option 1: Fix only `mind_outcome()`

Promote inbox decision blocks to active in `mind_outcome()` only. Don't touch
`predict()` — mind blocks still require consolidation before predicting.

**Pros:**
- Minimal change (3 lines)
- Fixes Alv's reported bug directly
- No risk to mind block decay tiers

**Cons:**
- Agent must still consolidate after `mind_create()` before `mind_predict()`
- Treats the symptom at one failure point, not the design gap
- Leaves the create→predict friction for future users

**Verdict:** Safe but incomplete. A patch, not a design fix.

### Option 2: Fix both, with correct decay tier assignment (CHOSEN)

Promote inbox blocks to active in both `predict()` (for mind blocks) and
`mind_outcome()` (for decision blocks). When promoting mind blocks, also set
the correct decay tier (DURABLE, λ=0.001) that consolidation would normally
assign.

**Pros:**
- Fixes the full mind lifecycle: create → predict → outcome with zero mandatory
  consolidation
- Correct decay tier assignment — mind blocks get DURABLE as intended
- No change to `learn()`, `consolidate()`, `outcome()`, or `do_connect()` contracts
- Biologically sound: lifecycle events are the validation for structured blocks

**Cons:**
- Promoted blocks lack embeddings (see Trade-offs section)
- Promoted blocks lack alignment scores (acceptable — structured blocks don't
  benefit from LLM alignment scoring)

**Verdict:** Right balance of correctness and minimality.

### Option 3: Fix both, plus lazy embedding mechanism

Same as Option 2, plus modify `consolidate()` to also generate embeddings for
active blocks that have `embedding=NULL`.

**Pros:**
- Self-heals the embedding gap at the next dream cycle
- Everything eventually reaches full quality

**Cons:**
- Changes consolidation's query scope (`get_inbox_blocks` → also includes
  active blocks with NULL embeddings)
- Risk of processing blocks that intentionally lack embeddings
- Broader blast radius — should be a separate PR

**Verdict:** Ideal future state, but should follow as a separate change.

---

## Chosen Solution: Option 2

### What Changes

**File 1: `src/elfmem/operations/mind.py`**

In `predict()` (line 152-154), replace:
```python
if mind_block is None or mind_block.get("status") != "active":
    raise BlockNotActiveError(mind_block_id)
```

With:
```python
if mind_block is None:
    raise BlockNotActiveError(mind_block_id)
if mind_block.get("status") == "inbox":
    # Deliberate act of predicting validates the mind model — promote inline
    tier = determine_decay_tier(
        await queries.get_tags(conn, mind_block_id),
        mind_block.get("category", ""),
    )
    lam = decay_lambda_for_tier(tier)
    await queries.update_block_scoring(conn, mind_block_id, decay_lambda=lam)
    await queries.update_block_status(conn, mind_block_id, "active")
elif mind_block.get("status") != "active":
    raise BlockNotActiveError(mind_block_id)  # archived blocks still rejected
```

In `mind_outcome()` (line 319-321), replace:
```python
if decision_block is None or decision_block.get("status") != "active":
    raise BlockNotActiveError(decision_block_id)
```

With:
```python
if decision_block is None:
    raise BlockNotActiveError(decision_block_id)
if decision_block.get("status") == "inbox":
    # Outcome closure IS the consolidation event for predictions
    await queries.update_block_status(conn, decision_block_id, "active")
elif decision_block.get("status") != "active":
    raise BlockNotActiveError(decision_block_id)  # archived blocks still rejected
```

Note: Decision blocks don't need decay tier adjustment — STANDARD (λ=0.01) is
the correct tier, and it matches the `insert_block` default.

**File 2: `tests/test_mind.py`**

Add new test class `TestInlinePromotion`:
- `test_predict_promotes_inbox_mind_block` — mind_create → mind_predict without
  consolidation
- `test_outcome_promotes_inbox_decision_block` — mind_predict → mind_outcome
  without consolidation
- `test_full_lifecycle_without_consolidation` — create → predict → outcome in
  one flow
- `test_promoted_mind_block_has_durable_decay` — verify λ=0.001 after promotion
- `test_archived_mind_block_still_rejected` — archived blocks cannot be promoted
- `test_archived_decision_block_still_rejected` — same for decisions

**File 3: `CHANGELOG.md`**

Add under `[Unreleased] ### Fixed`:
```
- `mind_outcome` no longer requires `consolidate()` before closing a prediction.
  Decision blocks are promoted to active inline when their outcome is recorded.
- `mind_predict` no longer requires `consolidate()` after `mind_create()`. Mind
  blocks are promoted to active inline (with correct DURABLE decay tier) when a
  prediction is made against them.
```

**File 4: `src/elfmem/guide.py`**

Update `mind_predict` and `mind_outcome` guide entries to remove any mention of
requiring consolidation first.

---

## Trade-offs

### Embedding Gap

Blocks promoted inline have `embedding=NULL`. They are invisible to semantic
search via `recall()` but are found by all primary access patterns:

| Access pattern | Uses embeddings? | Works? |
|---------------|-----------------|--------|
| `mind_list()` | No (category query) | Yes |
| `mind_show(id)` | No (edge traversal) | Yes |
| `mind_outcome(id)` | No (direct lookup) | Yes |
| `frame("simulate")` guaranteed slots | No (tag matching) | Yes |
| `frame("simulate")` query ranking | Yes | **No** |
| `recall("prediction about X")` | Yes | **No** |

**Mitigation:** If the agent runs `dream()` for other reasons (inbox threshold
reached), the mind/decision blocks will already be active and skipped by
consolidation. A future change (Option 3) can add lazy embedding generation for
active blocks with NULL embeddings.

### Alignment Score

Promoted blocks have `alignment_score=0.0` and `confidence=0.50` (the insert
default) rather than an LLM-assessed value. For structured blocks this is
acceptable:
- Mind blocks: confidence is calibrated by prediction outcomes, not LLM scoring
- Decision blocks: confidence represents prediction accuracy, not content quality

### No Change to `consolidate()`

If `consolidate()` runs after inline promotion, it skips the already-active
blocks (`get_inbox_blocks` returns inbox blocks only). This is correct — the
blocks have already been validated by their lifecycle events.

---

## Verification Plan

1. `uv run pytest tests/test_mind.py -x` — all tests pass including new
   inline promotion tests
2. `uv run pytest -x` — full suite passes, no regressions
3. `uv run ruff check src/elfmem/operations/mind.py`
4. `uv run mypy src/elfmem/operations/mind.py`
5. Manual end-to-end:
   ```bash
   elfmem mind create alv-test --goal "Test goal"
   # No consolidation needed
   elfmem mind predict <mind_id> "Test prediction" --verify-at 2026-05-02
   # No consolidation needed
   elfmem mind outcome <decision_id> --hit --reason "Confirmed"
   # Should succeed without error
   ```

---

## Future Work

- **Option 3 (lazy embedding):** Modify consolidation to embed active blocks
  with NULL embeddings. This self-heals the embedding gap. Separate PR.
- **decision decay tier:** Consider whether predictions should be DURABLE
  (currently STANDARD). Predictions that proved correct might warrant longer
  retention.
- **Structured block category handling in consolidation:** Messages already skip
  dedup/contradiction. Consider whether `mind` and `decision` categories should
  have similar skip logic, reducing wasted LLM calls if they do enter the inbox
  path.

---

## Simulation Traces

### Trace 1: Current broken flow (Alv's bug)

```
mind_create("alv")      → M.status="inbox"
consolidate()           → M.status="active", M.embedding=vec, M.decay_lambda=0.001
mind_predict(M, "...")  → D.status="inbox", D.embedding=NULL
mind_outcome(D, hit)    → get_block(D) → status="inbox" ≠ "active" → RAISES ✗
```

### Trace 2: Option 2 — full zero-consolidation flow

```
mind_create("alv")      → M.status="inbox", M.decay_lambda=0.01
mind_predict(M, "...")  → promote M: status="active", decay_lambda=0.001 (DURABLE)
                        → D.status="inbox", D.decay_lambda=0.01
                        → edge M↔D (predicts) ✓
mind_outcome(D, hit)    → promote D: status="active"
                        → record_outcome([D], signal=0.9) → Bayesian update ✓
                        → record_outcome([M], signal=0.7) → Bayesian update ✓
                        → do_connect(D→M, "validates") → both active ✓
                        → MindOutcomeResult ✓
```

### Trace 3: Consolidation runs after inline promotion

```
mind_create("alv")      → M.status="inbox"
mind_predict(M, "...")  → M promoted to "active"
dream()                 → get_inbox_blocks() → M is NOT inbox → skipped
                        → D is inbox → D gets embedding, dedup, promoted
                        → D.embedding=vec ✓ (self-heals if dream runs before outcome)
mind_outcome(D, hit)    → D already active → proceeds normally ✓
```

### Trace 4: Archived block rejection

```
mind_create("alv")      → M.status="inbox"
consolidate()           → M.status="active"
curate()                → M.status="archived" (if decayed)
mind_predict(M, "...")  → M.status="archived" ≠ "inbox" and ≠ "active"
                        → RAISES BlockNotActiveError ✓ (correct — model was retired)
```
