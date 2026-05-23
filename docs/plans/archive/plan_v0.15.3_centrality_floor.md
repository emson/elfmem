# Plan: v0.15.3 — Cold-start centrality floor

**Status**: design — ready to implement after approval
**Driver**: [issue #50](https://github.com/emson/elfmem/issues/50) — Dmitry's "I just told it something important and it ignored that next turn"
**Target**: v0.15.3 (patch release)
**Parent plan**: `docs/plans/plan_memory_scoring.md` (the three-version programme: v0.15.3 → v0.16.0 → v0.17.0)
**Author**: elf

---

## 1. Summary

A 10-line change in retrieval scoring that protects fresh blocks from losing top-K to bedrock blocks on centrality alone. This is **Dmitry's actual fix** — the v0.15.2 cliff removal addressed a real but small defect (±0.03 score impact); the centrality cold-start gap is the dominant term (±0.105 impact, 3.5× larger).

**Scope**: one new pure function `effective_centrality()` in `scoring.py`, one call-site change in `memory/retrieval.py`, ~10 LOC, no schema change, no new config, no migration.

**Why this works**: the simulation `/tmp/confidence_sim.py` decomposed the score gap and showed centrality is the killer term. A fresh block at `similarity=0.74, conf=0.85, recency=1.0, centrality=0.10, reinforcement=0` loses to bedrock at `similarity=0.62, conf=0.95, recency=0.70, centrality=0.80, reinforcement=1.0` by Δ=-0.018. The 0.70 centrality gap dominates the 0.12 similarity gap when multiplied by frame weights.

---

## 2. Background: where the bug actually lives

### 2.1 The retrieval call path

```
MemorySystem.recall(query) / .frame(name, query)
   ↓ src/elfmem/api.py
do_recall / do_frame
   ↓ src/elfmem/operations/recall.py (delegates)
   ↓ src/elfmem/memory/retrieval.py
_stage_1_candidate_selection  (top-K by cosine + 1-hop expansion)
_stage_2_filter               (status, min_confidence, frame-specific)
_stage_3_compute_centrality   (calls memory.graph.compute_centrality)
   ↓ centralities: dict[block_id, float] in [0.0, 1.0]
_stage_4_compute_scores       ← where centrality enters the score formula
   ↓ for each block:
     centrality = centralities.get(block_id, 0.0)   ← raw, no floor today
     score = compute_score(sim, conf, recency, centrality, reinf, weights)
_stage_5_mmr_diversity        (diversity selection)
_stage_6_contradiction_suppression
```

The floor enters at `_stage_4_compute_scores`, line ~335 of `memory/retrieval.py`. Single insertion point.

### 2.2 Why centrality starts at 0.0 for fresh blocks

`memory/graph.py:compute_centrality()` computes `weighted_degree / max_weighted_degree` across the candidate set. A block with zero edges has weighted_degree=0 → centrality=0.0. Even after consolidation creates a few similarity edges, a brand-new block has 1-3 edges versus bedrock blocks with 10+. Normalised centrality remains very low (<0.10) for at least a week of agent usage.

This is correct behaviour for the *meaning* of centrality (the block isn't yet central to the knowledge graph). It's incorrect behaviour for *retrieval ranking* of fresh blocks the agent genuinely cares about.

### 2.3 Why we don't simply re-weight centrality

The "obvious" fix is to lower ATTENTION's centrality weight from 0.15. But:

- That penalises bedrock equally and forever (not just during cold-start)
- It treats a symptom rather than the cause (centrality is fine; it's the *initial value* that's stale)
- Other frames (SELF=0.25, TASK=0.20, SIMULATE=0.20) would need separate retuning
- The change affects all queries, even those where bedrock dominance is desirable

A *floor* targeted at the cold-start window is surgical: it only intervenes when both conditions hold (centrality is low AND block is fresh), and it decays naturally as the block ages.

---

## 3. The fix

### 3.1 New pure function in `scoring.py`

```python
# Cold-start floor parameters — defensible constants, not config knobs.
# See docs/plans/plan_v0.15.3_centrality_floor.md for rationale.
_COLD_START_RECENCY_THRESHOLD: float = 0.70   # ≈ 35h on STANDARD tier
_COLD_START_CENTRALITY_THRESHOLD: float = 0.10  # ≤ ~10% of max weighted degree
_COLD_START_FLOOR_STRENGTH: float = 0.50      # peak floor at recency=1.0


def effective_centrality(
    *,
    raw_centrality: float,
    recency: float,
) -> float:
    """Apply cold-start centrality floor for fresh, low-edge blocks.

    USE WHEN:  Called by retrieval (_stage_4_compute_scores) before feeding
               centrality into compute_score(). Protects fresh blocks from
               losing top-K to bedrock on graph centrality alone.
    DON'T USE: For curation, archival, or any non-retrieval decision —
               those should see the raw graph-derived centrality.
    COST:      O(1) per block. Pure arithmetic.
    RETURNS:   float in [0.0, 1.0]. Equal to raw_centrality when the block
               has either established edges (raw_centrality ≥ threshold) or
               is no longer fresh (recency ≤ threshold). Otherwise applies
               a recency-scaled floor.

    The floor decays automatically as the block ages — once recency falls
    below threshold, the floor stops applying and the block competes on
    its actual graph position. This makes the cold-start protection
    self-extinguishing.
    """
    if raw_centrality < _COLD_START_CENTRALITY_THRESHOLD and recency > _COLD_START_RECENCY_THRESHOLD:
        return max(raw_centrality, _COLD_START_FLOOR_STRENGTH * recency)
    return raw_centrality
```

Properties of this function:

- **Pure**: no I/O, no state, deterministic
- **Monotonic in raw_centrality**: never *lowers* the value (always `max(raw, floor)` or `raw` directly)
- **Self-extinguishing**: as recency falls, the floor falls; once recency ≤ 0.70, no floor at all
- **Self-limiting**: as the block gains edges (raw_centrality rises past 0.10), no floor at all
- **Idempotent**: applying twice gives same result as applying once
- **No discontinuity**: at the boundary (recency=0.70+ε), the floor is 0.50·0.70 = 0.35 — well above the centrality_threshold=0.10, so no jump in scores at the boundary
- **Continuous in recency** in the active region: floor decays linearly with recency

### 3.2 Call-site change in `memory/retrieval.py`

```python
# In _stage_4_compute_scores, around line 335:
recency = math.exp(-decay_lam * hours_since)

# OLD:
centrality = centralities.get(block_id, 0.0)

# NEW:
raw_centrality = centralities.get(block_id, 0.0)
centrality = effective_centrality(
    raw_centrality=raw_centrality,
    recency=recency,
)
```

Note: we apply the floor to the centrality that feeds `compute_score()`, but the `ScoredBlock.centrality` field (returned to callers) should report the *effective* centrality, not the raw — because that's what determined the score. Consumers comparing `ScoredBlock.centrality` to score components should see consistent values.

### 3.3 Why this is a `scoring.py` helper, not a `compute_score()` modification

`scoring.py:4` documents that `compute_score()` is *frozen* — its formula is part of the stable contract. Changing it would be a breaking version change. The cold-start floor is therefore implemented as a separate helper that adjusts the *input* before calling `compute_score()`. The formula itself remains unchanged.

This preserves the existing contract while adding the new behaviour at the right architectural layer.

---

## 4. Why these constants

### 4.1 `_COLD_START_RECENCY_THRESHOLD = 0.70`

Per tier:
- PERMANENT (λ=0.00001): recency 0.70 at ~35000 hours (~4 years) — effectively always fresh, but PERMANENT tier blocks are constitutional bedrock and already have high centrality, so the floor doesn't activate
- DURABLE (λ=0.001): recency 0.70 at ~357 hours (~15 days)
- STANDARD (λ=0.010): recency 0.70 at ~35.7 hours (~1.5 days)
- EPHEMERAL (λ=0.050): recency 0.70 at ~7.1 hours

The cold-start window naturally scales with the block's decay tier. EPHEMERAL blocks get a short protection window; DURABLE blocks get weeks. This matches semantic intuition: a more important block (DURABLE/PERMANENT tier) deserves longer protection from being out-ranked by bedrock.

### 4.2 `_COLD_START_CENTRALITY_THRESHOLD = 0.10`

`weighted_degree` is the sum of edge weights touching the block. Default edge weights for new connections are 0.65 (similar), 0.75 (supports), etc. A block with one default edge in a graph where the top block has degree 6.5 has normalised centrality = 0.10. So `< 0.10` corresponds roughly to "0-1 edges of default weight in a typical graph."

This threshold is the boundary between "no graph position yet" and "starting to establish position."

### 4.3 `_COLD_START_FLOOR_STRENGTH = 0.50`

Peak floor at recency=1.0 is 0.50 — half of the maximum possible centrality. In ATTENTION frame (centrality weight 0.15), this contributes 0.15 × 0.50 = 0.075 to the score for a perfectly-fresh block with no edges, vs bedrock at 0.15 × 0.80 = 0.12. Gap reduces from 0.12 to 0.045 — enough that a semantically-relevant fresh block (similarity advantage of 0.10+) can win.

The choice of 0.50 (not 0.70 or 0.30) trades off two concerns:
- Higher floor → fresh blocks more competitive, but irrelevant fresh blocks might also surface
- Lower floor → safer against irrelevant blocks, but doesn't fully fix Dmitry's symptom

0.50 is the round-number middle ground. It gives fresh-and-relevant blocks a competitive shot without making irrelevant fresh blocks dominant.

### 4.4 Why not config knobs

These are not exposed as `ElfmemConfig` fields because:
- Each adds API surface that must be supported forever
- Users have no way to know what "good" values are without empirical measurement
- The plan's principle (per `plan_memory_scoring.md`) is to hardcode defensible defaults and add knobs only when users request tuning
- If v0.16.0 evidence shows the constants are wrong, this is a single-file change to ship in v0.16.1

---

## 5. Call-path consequences

### 5.1 What this changes

| Path | Touches centrality? | Affected by floor? | Why |
|---|---|---|---|
| `recall(query)` | Yes (via `_stage_4`) | Yes | This is the target path |
| `frame(name, query)` | Yes (via `_stage_4`) | Yes | Same target path |
| `frame(name)` (queryless) | Yes — uses `renormalized_without_similarity()` | Yes | Centrality weight is *higher* without similarity (rescaled). Floor still applies. |
| `connect_by_query(s, t)` | Yes (calls `recall` internally) | Yes | Inherited |
| `curate()` | Yes (different code in `curate.py:256`) | **No** | Curation centrality is computed separately; archival decisions should see raw graph state |
| `viz/data.py` | Yes (visualization) | **No** | Visualization shows actual graph; floor is a retrieval-only concept |
| `mind_show()` / `mind_list()` | No (uses confidence) | N/A | |

### 5.2 What the floor does NOT do

- **Does not change centrality stored anywhere** — `compute_centrality()` in `memory/graph.py` is unchanged; the floor is applied locally at retrieval-time only
- **Does not affect curation/archival** — `curate.py` uses its own centrality computation and is untouched. Fresh blocks are protected from archival by their decay tier and recency, not by this floor.
- **Does not affect graph visualization** — the viz layer should show the agent's actual graph state
- **Does not affect contradiction suppression** — that uses confidence, not centrality
- **Does not affect MMR diversity** — MMR operates on scored blocks; whether a block makes it to MMR is what the floor changes, and that's the intended effect

---

## 6. Edge cases

### 6.1 Bedrock blocks (high centrality, may be fresh)

A constitutional PERMANENT block might be high-centrality (0.80) and high-recency (~1.0 always). The `centrality < 0.10` gate prevents the floor from applying. Existing behaviour preserved. ✓

### 6.2 Old blocks (low recency)

A 100-day-old STANDARD block has recency ≈ 0.09. The `recency > 0.70` gate prevents the floor from applying. Existing behaviour preserved. ✓

### 6.3 Recently-archived blocks re-activated

If a block was archived and then re-learned (creating a new block, per current semantics), it's a new block — fresh + few edges → floor applies. This is the desired behaviour (we want re-learned content to compete).

### 6.4 Empty graph (no edges anywhere)

`compute_centrality()` returns 0.0 for all blocks when `max_weighted_degree == 0`. The `centrality < 0.10` gate is satisfied for everyone. The floor applies to every fresh block. They compete on similarity + recency + the floor (all equal). This is fine — the floor doesn't break anything; it just gives all fresh blocks equal cold-start treatment.

### 6.5 Single edge counts as "established"?

A block with one default-weight (0.65) edge in a small graph might have centrality 0.30, which is above the 0.10 threshold. The floor doesn't apply. Is this correct?

Yes. Once a block has even one edge in a sparse graph (centrality > 0.10), it's no longer "cold-start." The agent has connected it to something. The protection is for the truly unconnected case.

### 6.6 Recency exactly at 0.70

The condition is `recency > 0.70`, strict inequality. At exactly 0.70, no floor applies. This is intentional — at the boundary, we transition smoothly to no protection. (The floor itself at recency=0.70+ε is 0.50·0.70 = 0.35, well above the centrality_threshold=0.10, so there's no jump-up at the boundary either.)

### 6.7 Centrality exactly at 0.10

The condition is `raw_centrality < 0.10`, strict inequality. At exactly 0.10, no floor applies. Consistent with the recency case.

### 6.8 Float precision

All comparisons use `<` and `>`, no `<=` or `==`. No floating-point equality concerns. The thresholds are at 0.10 and 0.70, well-representable in float.

### 6.9 Concurrent retrieval calls

`effective_centrality()` is pure and stateless. Safe under concurrency. The `centralities` dict is computed per-query and not shared across calls.

### 6.10 Frame-specific behaviour

Each frame has different centrality weight (SELF=0.25, ATTENTION=0.15, TASK=0.20, SIMULATE=0.20). The floor applies uniformly to centrality; its effect on score is then scaled by each frame's weight. SELF frame sees the largest absolute effect, but SELF retrieves identity content where fresh content (a just-learned principle about self) genuinely should surface.

### 6.11 Block aging across the boundary

A block at recency=0.71 has the floor applied (recency above threshold). One hour later, recency=0.70 → no floor. The score component changes from `0.5·recency·weight` to `raw_centrality·weight`. If raw_centrality is 0, score drops by `0.5·0.70·weight = 0.0525` (in ATTENTION). This is a discrete step but small — comparable to the natural recency decay over the same period (a few % score change per hour at STANDARD tier).

We could smooth this with a soft transition. We won't — the discontinuity is small and adds complexity. Documented and acceptable.

### 6.12 Block gaining its 2nd edge across the boundary

A block at raw_centrality=0.09 (just below threshold) jumps to 0.30 when consolidation adds an edge. Floor was applied (max(0.09, 0.5)=0.5); now no floor (0.30). The block's score *decreases* from `0.5·weight` to `0.30·weight` — for ATTENTION, a drop of 0.030.

This is acceptable because: (a) the block's score before the floor was 0.09·0.15=0.0135; the floor lifted it to 0.075; the post-floor real value is 0.045 — net result of acquiring an edge is the block went from 0.075 to 0.045 score. (b) But the block also gained the edge's effects elsewhere (it can be reached by spreading activation, by 1-hop expansion). Net retrieval probability likely *increased* even if this one score term dropped. (c) The block is no longer cold-start-protected because it's no longer cold-start — by design.

If this proves to be an observed problem, we revisit in v0.16+.

---

## 7. Tests

### 7.1 Unit tests for `effective_centrality()` (new in `tests/test_scoring.py`)

```python
class TestEffectiveCentrality:
    """Cold-start centrality floor for fresh blocks with few edges."""

    def test_fresh_block_with_low_centrality_gets_floor(self):
        # Brand-new block: centrality=0.0, recency=1.0 → floor=0.5
        assert effective_centrality(raw_centrality=0.0, recency=1.0) == 0.5

    def test_floor_decays_with_recency(self):
        # Aging block: recency 0.85 → floor 0.425
        assert effective_centrality(raw_centrality=0.0, recency=0.85) == 0.425

    def test_floor_stops_at_recency_boundary(self):
        # At exactly the boundary, no floor
        assert effective_centrality(raw_centrality=0.0, recency=0.70) == 0.0

    def test_floor_stops_above_centrality_threshold(self):
        # Block with established edges: not protected
        assert effective_centrality(raw_centrality=0.5, recency=1.0) == 0.5

    def test_block_at_centrality_boundary_unchanged(self):
        # At exactly the centrality threshold, no floor
        assert effective_centrality(raw_centrality=0.10, recency=1.0) == 0.10

    def test_never_lowers_centrality(self):
        # Floor only raises; never lowers
        assert effective_centrality(raw_centrality=0.05, recency=0.95) >= 0.05
        assert effective_centrality(raw_centrality=0.95, recency=1.0) == 0.95

    def test_idempotent(self):
        # Applying twice yields same result
        once = effective_centrality(raw_centrality=0.0, recency=1.0)
        twice = effective_centrality(raw_centrality=once, recency=1.0)
        assert once == twice

    def test_old_block_unaffected(self):
        # Low recency: floor doesn't apply regardless of centrality
        assert effective_centrality(raw_centrality=0.0, recency=0.30) == 0.0
        assert effective_centrality(raw_centrality=0.50, recency=0.30) == 0.50
```

### 7.2 Integration test in `tests/test_recall.py` or similar

```python
async def test_cold_start_block_surfaces_top_k_in_attention(self, system):
    """v0.15.3: fresh block with good semantic match beats bedrock on retrieval.

    Reproduces Dmitry's symptom: 'I just told it something important and it
    ignored that next turn.' Without the centrality floor, the fresh block
    loses to bedrock on the centrality term. With the floor, it wins.
    """
    # Set up bedrock — many edges, established centrality
    bedrock_ids = []
    for i in range(5):
        r = await system.learn(f"Constitutional principle {i}")
        bedrock_ids.append(r.block_id)
    await system.dream()
    # Make bedrock central by connecting them
    for i in range(len(bedrock_ids) - 1):
        await system.connect(bedrock_ids[i], bedrock_ids[i+1])

    # Add many positive outcomes to give bedrock high confidence + reinforcement
    for bid in bedrock_ids:
        for _ in range(5):
            await system.outcome([bid], signal=1.0)

    # Learn a fresh block with content the agent should care about
    fresh = await system.learn("This is an important new principle about Y")
    await system.dream()  # promote to active

    # Query for the fresh content
    results = await system.recall("important new principle Y")

    # Without floor: fresh block likely loses top-K to bedrock
    # With floor: fresh block surfaces in top-K
    assert any(r.id == fresh.block_id for r in results[:3]), (
        "Fresh, semantically-relevant block should surface in top-3 "
        "via cold-start centrality floor"
    )
```

### 7.3 No-regression tests

- Existing `tests/test_scoring.py` (ScoringWeights validation, frame weights) — must pass unchanged
- Existing `tests/test_recall.py` retrieval tests — must pass unchanged (the floor only *raises* centrality, so ranking only shifts in favour of fresh blocks)
- Existing `tests/test_curate.py` (archival decisions) — must pass unchanged (curate uses different centrality computation)

### 7.4 Test count and runtime impact

Estimated +8 unit tests, +1 integration test. Total runtime impact: ~50ms.

---

## 8. Documentation updates

### 8.1 CHANGELOG entry

```markdown
## [0.15.3] — 2026-05-18

Second milestone of the memory-scoring architecture work driven by
[issue #50](https://github.com/emson/elfmem/issues/50). v0.15.2 removed the
confidence cliff (which contributed ±0.03 to the retrieval gap). This release
addresses the *dominant* term in Dmitry's symptom: centrality, which
contributed ±0.105.

### Fixed

- Fresh blocks no longer lose top-K retrieval to bedrock on graph centrality
  alone. A cold-start centrality floor lifts blocks with few edges and high
  recency to a recency-scaled floor value (peak 0.50 at recency=1.0).
  The floor self-extinguishes as the block either ages (recency drops below
  0.70) or builds edges (centrality rises above 0.10). Affects retrieval
  scoring only; curation and archival decisions are unchanged. See
  `docs/plans/plan_v0.15.3_centrality_floor.md` for the full design.
```

### 8.2 No AgentGuide update needed

`recall()` and `frame()` AgentGuide entries currently describe what they do, not the internal scoring formula. The floor is an implementation detail that improves their behaviour without changing the contract. No agent-visible API surface changes.

### 8.3 Internal documentation

The `effective_centrality()` function's docstring (in §3.1 above) is the canonical reference. The plan document itself (this file) lives in `docs/plans/` for future maintainers.

### 8.4 No CLAUDE.md update needed

CLAUDE.md describes the four rhythms and four frames at a conceptual level. The centrality floor doesn't change any of those concepts.

---

## 9. Migration and backwards compatibility

### 9.1 No schema migration

The change is in-memory scoring only. No new columns, no new tables, no data migration.

### 9.2 No data migration

Existing block data is unaffected. The floor is applied at retrieval-time from raw centrality values that are computed each call.

### 9.3 Behaviour change is monotonic-friendly

The floor can only *raise* a block's centrality term. It can never lower it. Therefore:
- Block rankings can only shift in favour of fresh blocks
- No block that previously made top-K can fall out due to this change alone
- The change can only *add* fresh blocks to top-K; it cannot remove other blocks

This makes regression testing simpler and reduces upgrade risk.

### 9.4 Rollback path

If the floor causes unexpected behaviour, rollback is trivial:
- Revert the call-site change in `memory/retrieval.py` (1 line)
- The new `effective_centrality()` function can be left in place (unused) or removed
- No data to revert

Estimated rollback effort: 5 minutes.

---

## 10. Risks

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| Floor constants are wrong | medium | medium | Constants chosen with simulation evidence; can be tuned in v0.16+ |
| Fresh-but-irrelevant blocks dominate top-K | low | medium | Floor scales with recency only, not similarity-aware. But blocks already pass similarity-based candidate selection in stage 1, so irrelevant blocks aren't candidates. |
| Breaks an existing scoring test | low | low | The floor is monotonic-friendly; existing rankings only shift in favour of fresh blocks. Will discover any regressions in CI. |
| Floor activates too widely (affects more than intended) | low | low | Two gates (centrality AND recency) ensure narrow activation. Curation/viz paths explicitly untouched. |
| Performance impact | very low | very low | One `if` and one `max()` per scored block. ~50ns per block. Negligible. |
| Confuses users debugging scoring | low | low | `ScoredBlock.centrality` reports the *effective* (post-floor) value. Users who want to understand the boost can check `recency` and infer. Could add an `effective_centrality_boosted: bool` field later if needed. |

Overall risk: **very low**. This is the smallest possible architectural change that addresses the largest empirical defect.

---

## 11. Open questions (deferred to v0.16+)

1. **Should the constants be config knobs?** Per `plan_memory_scoring.md` principle: not yet. Revisit if users request tuning.

2. **Should the floor also apply to curation centrality?** No — archival should see the graph as it is. But this could be revisited if cold-start blocks are observed to be aggressively archived.

3. **Is `_COLD_START_CENTRALITY_THRESHOLD=0.10` the right boundary?** Empirically tuned to "1 edge in a 10× graph." If observation shows blocks sit at 0.05-0.10 for extended periods, we might raise to 0.20 in v0.16+.

4. **Should `effective_centrality()` accept the frame as a parameter?** Currently frame-agnostic. SELF frame might want a higher floor (identity matters more). Defer until evidence justifies frame-specific behaviour.

5. **Should we add an `effective_centrality_boosted` field to `ScoredBlock`?** Useful for debugging but adds API surface. Defer until users ask.

6. **Should the floor strength be a function of frame weight?** E.g., higher floor when centrality weight is lower, to keep the effective centrality contribution roughly constant. Mathematically tempting but harder to reason about. Defer.

---

## 12. Implementation checklist

- [ ] Branch from `main`: `fix-centrality-cold-start-v0.15.3`
- [ ] Add `effective_centrality()` function to `src/elfmem/scoring.py` with docstring per §3.1
- [ ] Add module constants `_COLD_START_RECENCY_THRESHOLD`, `_COLD_START_CENTRALITY_THRESHOLD`, `_COLD_START_FLOOR_STRENGTH`
- [ ] Modify `_stage_4_compute_scores` in `src/elfmem/memory/retrieval.py` to call `effective_centrality()`
- [ ] Add unit tests to `tests/test_scoring.py` per §7.1 (8 tests)
- [ ] Add integration test to `tests/test_recall.py` per §7.2 (1 test)
- [ ] Run `uv run ruff check src tests`
- [ ] Run `uv run mypy src`
- [ ] Run `uv run pytest -q` — confirm all tests pass (existing + new)
- [ ] Bump `pyproject.toml` from `0.15.2` → `0.15.3`
- [ ] Update `CHANGELOG.md` with the entry from §8.1
- [ ] Commit with descriptive message linking to `plan_memory_scoring.md` and this plan
- [ ] Push branch and open PR
- [ ] After PR merge: tag `v0.15.3` and push → triggers PyPI publish

Estimated end-to-end implementation time: **1 day** (mostly tests and review).

---

## 13. Decision asks

1. **Approve the design** — three named constants, one pure helper function, one call-site change in retrieval. Yes or revisions?
2. **Approve the constants** — `0.10` centrality threshold, `0.70` recency threshold, `0.50` floor strength. Or different values?
3. **Approve the testing strategy** — 8 unit tests + 1 integration test. Sufficient or want more coverage?
4. **Confirm scope discipline** — no AgentGuide changes, no schema changes, no new config. This is purely an internal scoring refinement.

If all four are yes, I implement immediately.

---

## 14. References

- `docs/research/memory_scoring_survey.md` — the parent research paper (untracked)
- `docs/plans/plan_memory_scoring.md` — the parent three-version programme (untracked)
- `/tmp/confidence_sim.py` — simulation that quantified the centrality vs confidence gap
- `src/elfmem/scoring.py` — module being extended
- `src/elfmem/memory/retrieval.py` — call-site for the new helper
- `src/elfmem/memory/graph.py:compute_centrality()` — unchanged; provides raw centrality input
- `src/elfmem/operations/curate.py:256` — separate centrality computation; explicitly unaffected
- v0.15.2 PR #60 — the cliff fix that preceded this work
