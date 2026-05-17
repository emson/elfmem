# Plan: contradiction detection band is too narrow for textual contradictions

**Status**: design question — no PR yet
**Discovered while**: implementing PR #55 (surfacing `contradictions_detected` on `ConsolidateResult`)
**Related**: issue #50 item 1 (the observability fix), but distinct from it

---

## The observation

`consolidate.py` runs contradiction detection only when two blocks fall into a
specific cosine-similarity band:

```
similarity ≥ CONTRADICTION_SIMILARITY_PREFILTER (0.40)   # too low: skip detection
similarity < NEAR_DUP_NEAR_THRESHOLD (0.90)              # too high: treat as near-dup
                                                          # and supersede instead
```

If similarity is `≥ 0.90`, the second block is marked `action="supersede"` and
the existing block is archived. **Contradiction detection never runs.** The
supersession path lives at `consolidate.py:309-311`; the contradiction loop at
`consolidate.py:374-390` only iterates `evolving_vecs`, from which the
superseded block has just been removed.

## Why this matters

Textual contradictions in real-world agent memory often share most of their
surface form and differ in one fact:

| Contradicting wording | Tokens shared | Likely cosine similarity |
|---|---|---|
| "Dima's birthday is January 15th" / "Dima's birthday is July 20th" | 4 of 5 | high |
| "The meeting is at 2pm" / "The meeting is at 4pm" | 4 of 5 | high |
| "Use sync calls" / "Use async calls" | 2 of 3 | high |
| "Redis is acceptable" / "Redis is forbidden" | 2 of 3 | high |

These are exactly the kinds of contradictions a memory system should catch —
and exactly the kinds the current band misses. The two blocks land above the
near-dup threshold, the second supersedes the first, and the system silently
loses the older fact rather than flagging the disagreement.

**Dmitry's literal reproduction in #50** (two birthday dates) is likely one of
these cases. PR #55 surfaces `contradictions_detected` so the user can *see*
that detection is or isn't firing — but on Dmitry's exact wording, the new
field will likely still read `0`, because detection doesn't run.

## Options

### Option A — lower `NEAR_DUP_NEAR_THRESHOLD`

Wider contradiction band by raising the ceiling of "definitely a duplicate."

- **Pro**: smallest change. One constant.
- **Con**: changes near-dup behaviour globally. Real near-duplicates (paraphrases of the same fact) might start triggering contradiction LLM calls — extra cost, possibly false positives.
- **Verdict**: fragile. Tuning one threshold to fix a categorical confusion is the wrong tool.

### Option B — run contradiction detection on near-dup candidates *before* superseding

When `best_sim ≥ NEAR_DUP_NEAR_THRESHOLD`, instead of immediately marking for
supersession:

1. Run the contradiction LLM on the pair.
2. If `c_score < contradiction_threshold` → treat as near-dup, supersede (current behaviour).
3. If `c_score ≥ contradiction_threshold` → **don't supersede**; promote the new block normally AND record the contradiction.

**The principle this captures**: *two blocks that share most words but
disagree on a key fact are not duplicates, they are contradictions.*
Contradiction takes precedence over deduplication.

- **Pro**: cleanly resolves the categorical confusion. Treats contradictions
  as first-class semantic events, not as accidents of phrasing.
- **Pro**: zero behavioural change when LLM scores below threshold (so quiet
  paraphrases still get deduplicated as today).
- **Con**: adds an LLM call to every near-dup candidate (currently those skip
  LLM entirely). For high-similarity batches this could be expensive.
- **Mitigation**: gate the additional LLM call behind a config flag
  `contradiction_check_on_near_dup: bool` (default True, can disable for
  cost-sensitive workloads).

### Option C — keep both: dedupe + contradict in parallel

When similarity is high AND LLM says contradiction, store both:
- Archive the older block as superseded.
- Insert a contradiction row between the two (even though one is now archived).

- **Pro**: preserves the historical record of disagreement.
- **Con**: confused semantics. Why is there a contradiction between an active
  and an archived block? Querying contradictions becomes harder.
- **Verdict**: not the cleanest.

### Option D — two-stage threshold with explicit categories

Introduce `IDENTICAL_DUPLICATE` (≥ 0.97) vs `NEAR_DUPLICATE` (0.90 - 0.97).
Above 0.97 → silently supersede (genuine paraphrases). Between 0.90 and 0.97
→ run contradiction LLM, branch as in Option B.

- **Pro**: keeps the fast path for genuine duplicates.
- **Con**: two more constants to tune. More complex mental model.

## Recommendation

**Option B with the config gate.** It's the principled fix — contradictions
take precedence over deduplication. The added LLM cost is bounded by the
existing `CONTRADICTION_THRESHOLD` (above-threshold pairs were already going
to get LLM-evaluated; this just moves the evaluation earlier in the
pipeline). The config gate lets cost-sensitive users opt out.

Implementation sketch:

```python
# consolidate.py, in _collect_decisions, replacing the current
# "if best_sim >= near_dup_near_threshold: supersede" branch

if not is_message and best_active is not None and best_sim >= near_dup_near_threshold:
    if contradiction_check_on_near_dup and not (skip_llm or skip_contradictions):
        # Check before superseding — contradiction takes precedence
        try:
            c_score = await asyncio.wait_for(
                llm.detect_contradiction(content, best_active["content"]),
                timeout=_LLM_CONTRADICT_TIMEOUT,
            )
            if c_score >= contradiction_threshold:
                # Don't supersede — record contradiction, promote normally
                a_id = min(block_id, best_active["id"])
                b_id = max(block_id, best_active["id"])
                contradiction_decisions.append(
                    _ContradictionDecision(block_a_id=a_id, block_b_id=b_id, score=c_score)
                )
                # Fall through to normal promotion (no supersedes_id)
                # The existing contradiction loop later in the function will
                # not re-detect because best_active is still in evolving_vecs.
                supersedes_id = None
            else:
                supersedes_id = best_active["id"]
                evolving_vecs.pop(best_active["content"].strip().lower(), None)
        except TimeoutError:
            # On timeout, default to today's behaviour (supersede)
            supersedes_id = best_active["id"]
            evolving_vecs.pop(best_active["content"].strip().lower(), None)
    else:
        supersedes_id = best_active["id"]
        evolving_vecs.pop(best_active["content"].strip().lower(), None)
```

## Test scenarios

- Two near-identical paraphrases (no contradiction) → supersede, count = 0
- Two high-similarity contradicting (e.g. birthdays) → both promoted, count = 1
- `skip_contradictions=True` → revert to current supersede-only behaviour
- `skip_llm=True` → revert to current supersede-only behaviour
- LLM timeout on the near-dup contradiction check → fallback to supersede

## Out of scope for this plan

- Changing `CONTRADICTION_THRESHOLD` itself. Tune it separately if false-positive
  rate on the new path is too high.
- Reviving archived blocks if a later contradiction is detected against them.
  One-way: archive is forward. Re-promotion is a separate request.
- Surfacing supersession events in `ConsolidateResult` (already counted in
  `deduplicated`).
