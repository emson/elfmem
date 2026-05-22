# Longitudinal evaluation harness

Evaluates elfmem's **trajectory** behaviour, not just snapshot correctness. Driven by Dmitry's production-feedback report on issue #50 (2026-05-17). See `docs/plans/plan_longitudinal_evaluation.md` for the full plan.

## Safety

**This harness must never touch the production DB at `~/.elfmem/databases/elfmem.db`.**

Three defences:
- Architectural: in-memory engine only (`create_test_engine()`); no file paths accepted
- Runtime: `safety.assert_in_memory_only(engine)` refuses anything that isn't `:memory:`
- Convention: never call `MemorySystem.from_config()` — that auto-discovers and opens the real DB

Verify after any run: `stat -f "%m" ~/.elfmem/databases/elfmem.db` mtime is unchanged.

## Run

```bash
# Layer 1 — closed-form derivations (no DB, ~1 second)
uv run python -m scripts.longitudinal_sim.closed_form

# Layer 2 — minimal one-day harness against in-memory MemorySystem (~10 seconds)
uv run python -m scripts.longitudinal_sim.harness
```

## What's here

| File | Purpose |
|------|---------|
| `closed_form.py` | Layer 1: analytical derivations (D1–D6). Confirms or refutes Dmitry's projections by math alone. |
| `safety.py` | Runtime guards: rejects any non-in-memory engine. |
| `topic.py` | Ground-truth semantic space (8-d unit vectors). |
| `mocks.py` | Topic-aware embedding + alignment LLM mocks. |
| `harness.py` | Minimal end-to-end: in-memory `MemorySystem` over one simulated day. |

## Phase 2 (not yet here)

- Time-compression machinery to advance `last_reinforced` across simulated weeks/months
- Eight-vital metric collector (hit rate, bedrock moat, ossification index, etc.)
- E1 experiment: v0.15.2 vs v0.15.3 over 365 simulated days
- E2 experiment: additive vs destructive rescore
- E3 experiment: constitutional review cycle

Phase 2 design is in the plan doc; implementation pending agreement on the closed-form findings.
