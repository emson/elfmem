# Plan: Longitudinal evaluation of elfmem dynamics

**Status**: plan + Phase 1 implementation
**Driver**: Dmitry's production-feedback comment on issue #50 (2026-05-17) claiming ~70% → ~35% hit-rate degradation over 10 years under default usage, vs ~72% → ~80% with disciplined defenses
**Author**: elf, with Ben (2026-05-21)

---

## The reframe

Our existing test suite measures **snapshots**: "given state X, scoring produces Y". Dmitry's report measures **trajectories**: "given a year of real usage, does hit rate stay above 70%". These are different sciences. Snapshot correctness does not imply trajectory health.

elfmem has at least seven compounding processes, each with a sign and a time constant:

| # | Process | Sign | Time-constant | Where it shows up |
|---|---|---|---|---|
| 1 | Signal inflation (Beta-Binomial saturation) | Self-reinforcing | ~N events | `outcome()` updates |
| 2 | Prior ossification (confidence convergence) | Self-reinforcing | months | early outcomes lock the mean |
| 3 | Shadow hierarchy (centrality lock-in) | Preferential attachment | months | top-K capture by old blocks |
| 4 | Decay race (STANDARD tier vs reinforcement) | Adversarial | weeks | block archival |
| 5 | Cold-start gap | Snapshot, addressed in v0.15.3 | days | new-content retrieval |
| 6 | Constitutional ossification | Permanent | years | bedrock dominance |
| 7 | Contradiction-threshold drift | With content mass | months | false-positive rate |

Five of these only manifest after months of accumulated state. The current test suite cannot see them.

---

## Three-layer evaluation strategy

### Layer 1 — Closed-form derivations
**Goal**: where the math is tractable, derive the steady state analytically and confront Dmitry's projections with mathematics directly.

**Deliverable**: `scripts/longitudinal_sim/closed_form.py` — runnable, prints numerical answers.

**Derivations**:
- **D1** Beta-Binomial marginal sensitivity: `Δc ≈ w(s - c_N) / (N + p + w)`. Asymptotic inertness at N=100.
- **D2** Decay half-life by tier at Dmitry's usage rate (4h/day).
- **D3** Cold-start floor longevity (when does it extinguish?).
- **D4** Preferential-attachment top-K capture under elfmem's edge formation.
- **D5** Constitutional permanence horizon (PERMANENT tier half-life).
- **D6** Score-decomposition: maximum possible new-block score vs minimum constitutional score in ATTENTION.

**Cost**: 1 afternoon. **Value**: confirms or refutes Dmitry's projections without any simulation infrastructure.

### Layer 2 — Longitudinal simulation harness
**Goal**: where dynamics interact non-linearly (e.g. cold-start × decay × rescore), simulate a synthetic agent over compressed time and measure vitals.

**Deliverable**: `scripts/longitudinal_sim/harness.py` — drives the real `MemorySystem` over a workload, in-memory only, computes vitals per simulated week.

**Architecture**:
```
scripts/longitudinal_sim/
├── safety.py        ← asserts no production DB ever touched
├── topic.py         ← ground-truth semantic space (8-d unit vectors)
├── mocks.py         ← TopicEmbedding + AlignmentLLM mocks
├── workload.py      ← synthetic agent (learn/query/outcome generator)
├── vitals.py        ← 8 health metrics
├── harness.py       ← main loop
├── closed_form.py   ← Layer 1 derivations
└── e1_floor.py      ← first experiment
```

**Substrate**:
- **In-memory engine only** (`create_test_engine()`), never a file path
- **TopicEmbeddingService**: returns the block's topic vector (8-d unit) as embedding; cosine in topic space = "true semantic similarity"
- **AlignmentLLM**: deterministic alignment score from topic-to-self distance
- **Safety guard**: refuses to run if engine URL doesn't contain `:memory:`

**Time compression**: elfmem's recency is an "active hours" clock. The harness can either (a) drive the system through real operations and use `time.sleep` (too slow), or (b) advance `last_reinforced` timestamps directly between simulated days. We pick (b). This is the only place we touch internal state; it's the cost of compressed simulation.

**Vitals computed per simulated week**:
1. Hit rate on ground-truth-known queries
2. Recent-content reach (% of top-K from last 30 simulated days)
3. Bedrock moat (% of top-K from oldest 10% of blocks)
4. Confidence calibration error (Brier score)
5. Ossification index (75th-percentile `outcome_evidence`)
6. Edge density vs N (`E / N log N` ratio)
7. Decay churn (archive rate / learn rate)
8. Contradiction precision/recall on seeded contradictions

### Layer 3 — Ground-truth calibration
**Goal**: when Dmitry shares an anonymised DB snapshot via the private repo channel, fit the workload model's parameters so the first simulated month reproduces his observed metrics, then trust the year-12 projection.

**Deliverable**: `scripts/longitudinal_sim/calibrate.py` — fits Poisson rates, topic-drift parameter, query mix from a real DB.

**Status**: blocked on Dmitry sharing data. Scaffold only.

---

## The non-negotiable safety constraint

**This evaluation must not alter elf's current memory** (`~/.elfmem/databases/elfmem.db`).

Three defences, all on:

1. **Architectural**: the harness exclusively uses `create_test_engine()`, which returns a `sqlite+aiosqlite:///:memory:` engine with `StaticPool`. There is no code path that accepts a file URL.
2. **Runtime assertion**: `safety.assert_in_memory_only(engine)` is called before any operation. It refuses to proceed if the engine URL is anything other than `:memory:`, or if any of these paths appear in any config: `~/.elfmem/databases`, `/Users/emson/.elfmem`.
3. **No `from_config()`**: never call it. `from_config()` auto-discovers `.elfmem/config.yaml` and opens the production DB. We construct `MemorySystem` directly with `engine=test_engine, llm_service=mock, embedding_service=mock`.

Belt + braces + suspenders. Verified by a unit test at top of the harness module.

---

## Phase 1 implementation (this PR)

In scope:
- `docs/plans/plan_longitudinal_evaluation.md` (this doc)
- `scripts/longitudinal_sim/safety.py` — runtime assertions
- `scripts/longitudinal_sim/closed_form.py` — Layer 1 derivations, runnable
- `scripts/longitudinal_sim/README.md` — explains the package
- `scripts/longitudinal_sim/__init__.py` — module marker
- Layer 2 scaffold (topic, mocks, vitals, harness) — runnable minimum proof that exercises one simulated day end-to-end against the real `MemorySystem` with in-memory engine

Out of scope (Phase 2):
- Full year-long compressed simulation (needs time-advance machinery)
- E1 (v0.15.2 vs v0.15.3 comparison) — requires running the harness against two scoring code versions
- E2 (additive vs destructive rescore) — depends on v0.16 implementation
- E3 (constitutional review cycle) — depends on v0.17/v0.18 design
- Layer 3 calibration

---

## Acceptance criteria for Phase 1

- `uv run python -m scripts.longitudinal_sim.closed_form` runs in <5 seconds and prints six labelled derivations with numerical answers.
- `uv run python -m scripts.longitudinal_sim.harness` runs in <60 seconds, exercises one simulated day, prints vitals.
- `pytest scripts/longitudinal_sim/` passes; safety assertion verified.
- No file under `~/.elfmem/databases/` is created, modified, or read by any test or harness run.

Final verification: `ls -la ~/.elfmem/databases/elfmem.db` mtime is unchanged before vs after the full Phase 1 run.

---

## What Phase 1 will tell us

After running the closed-form derivations, we will have answered:

- Is signal inflation mathematically real at Dmitry's usage rate? (Predicted: yes, by N=100 events the system is asymptotically inert.)
- Is constitutional dominance mathematically inevitable under current ATTENTION weights? (Predicted: yes, baseline non-similarity score is 0.65 × {conf+rec+cent+reinf} for bedrock vs max 0.35 for similarity advantage of any new block.)
- Does the cold-start floor extinguish before blocks have time to accumulate edges at Dmitry's usage rate? (Predicted: probably — floor lasts ~9 active days; co-retrieval rate at 4h/day may be insufficient.)

If derivations confirm the projections, Layer 2 is justified. If they contradict, we save a week and have a precise counter-question for Dmitry.

---

## References
- `docs/plans/plan_memory_scoring.md` — v0.15.x → v0.18+ plan, with the post-ship verification finding from 2026-05-18
- Issue #50 + Dmitry's 2026-05-17 production-feedback comment
- `tests/CLAUDE.md` — test infrastructure rules (in-memory only, mock services)
- `src/elfmem/scoring.py`, `src/elfmem/operations/outcome.py` — formulas under test
