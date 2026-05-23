# elfmem reflect — metacognitive contradiction detection

**Status:** draft · **Owner:** elf · **Target release:** v0.14.0

## Intent

elfmem detects contradictions between *new* blocks and existing ones during
`dream()` (Breathing). It does not periodically re-examine the SELF frame
for *internal* inconsistency, nor compare SELF claims against the
current code surface. The result is silent constitutional drift: stale
SELF blocks coexist with newer ones that contradict them, and nothing
flags it.

The 2026-05-08 reflection found two concurrent "fourth rhythm" claims in
elf's own memory — one from `mind_predict` (older), one from `rescore`
(newer). dream() never noticed because both blocks were promoted at
different times against different evolving SELFs.

The principle this serves:

> **My identity evolves — it is the living summary of what works.**

If identity evolves, the constitution must be inspectable for drift.
`reflect()` is the operation that does that inspection.

The principle this enforces:

> **Self-knowledge is observable and proposable, never silently
> mutated.** `reflect()` proposes; the agent decides; the existing
> `remember --category self` commits.

## Problem

Three concrete failures, each a real instance from the current DB:

1. **Stale rule, live code.** SELF block: *"three rhythms — every new
   feature must map to one."* Code: four rhythms (rescore exists).
   Nothing flagged the contradiction; Ben caught it manually during a
   reflective conversation.
2. **Two truths, both stored.** Attention block calls `mind_predict`
   "the fourth rhythm." A newer SELF block calls `rescore` the fourth.
   Both active. dream() compares incoming to active; it never compares
   active to active across time.
3. **Constitution claims a capability that doesn't exist (yet).** SELF
   blocks reference operations or behaviors. If a refactor renames or
   removes one, the SELF block becomes a lie. There is no test for
   this.

## Design

### Rhythm classification

`reflect()` is **Breathing** — seconds, LLM-powered, like dream().
Pointed inward at the constitution rather than outward at the inbox.
This is deliberate: dream() integrates new knowledge; reflect()
integrates *self-knowledge over time*.

Not Heartbeat (LLM call required). Not Sleep (semantic work, not
housekeeping). Not Deep Sleep (proposes revision, not reweighting).

### Three phases

```
survey  →  analyze (LLM)  →  report
```

**Survey** — pure, no LLM:

- Load all `frame=self`, `status=active` blocks.
- Load operation registry (single source of truth — see
  *Prerequisite* below).
- Load reality signals: recent contradiction events from consolidation
  logs (last 30 days), low-confidence outcome events (`signal < 0.3`),
  archived blocks that were once SELF.

**Analyze** — one LLM call with constrained JSON output:

```
Input:
  self_blocks: [{id, content, created_at, last_scored_at, tags}]
  operations:  [{name, exists, signature}]
  signals:     [{kind, ref, summary, observed_at}]

Output (JSON):
  contradictions: [
    {block_a_id, block_b_id, conflict, severity}     # within SELF
  ]
  drifts: [
    {block_id, claim, reality, evidence_refs}        # SELF vs code/signals
  ]
  proposals: [
    {replaces: [block_ids], new_content, rationale, confidence}
  ]
```

The prompt is explicit: "Do not invent contradictions. If two blocks
say compatible things in different language, say so. Confidence must
be calibrated."

**Report** — return typed `ReflectionResult`; never auto-apply.

### Why never auto-apply

The constitution is sacred. An LLM hallucination that silently
overwrites a SELF block is unrecoverable in the worst case. The agent
(or user) must accept each proposal explicitly.

`reflect --apply <proposal_id>` and `reflect --apply-all` are the
explicit-commit paths. They route through the existing
`remember --category self` plumbing so the audit trail is uniform.

### Idempotency

Reflect on unchanged state must produce the same proposals (same JSON,
ignoring timestamps). This is the contract. Achieved by:

- Deterministic input ordering (block IDs sorted).
- LLM temperature 0 for the analyze call.
- Proposals keyed by content hash, not generation order.

If reflect produces different proposals on identical input, that is a
bug — surface it via a `reflect --diagnose` mode that runs twice and
diffs.

### Prerequisite: live operation registry

reflect() needs to know which operations actually exist to detect
"SELF claims X; X is gone" drifts. The current state — operations
listed by hand in `guide.py` and described in CLAUDE.md / README —
cannot be trusted as ground truth.

Therefore **a small prerequisite ships first**:

```python
# src/elfmem/registry.py
@dataclass(frozen=True)
class OperationSpec:
    name: str
    rhythm: Rhythm                    # heartbeat | breathing | sleep | deep_sleep
    cost: Cost                        # instant | fast | llm
    signature: str
    description: str

def all_operations() -> list[OperationSpec]: ...
```

`elfmem guide` derives its operation table from `all_operations()`.
`reflect()` uses the same source. The "three rhythms" footer becomes
"four rhythms" automatically because rhythms come from the registry,
not from a string. This eliminates the class of drift that produced
the original problem.

### Public API

```python
class MemorySystem:
    def reflect(
        self,
        frame: Frame = "self",
        *,
        signal_window_days: int = 30,
        min_confidence: float = 0.6,
    ) -> ReflectionResult: ...

    def reflect_apply(
        self,
        proposal_id: str,
    ) -> RememberResult: ...
```

### Result types

```python
@dataclass(frozen=True)
class Contradiction:
    block_a_id: str
    block_b_id: str
    conflict: str
    severity: Literal["high", "medium", "low"]

@dataclass(frozen=True)
class Drift:
    block_id: str
    claim: str
    reality: str
    evidence_refs: list[str]

@dataclass(frozen=True)
class Proposal:
    id: str                           # content-hash; stable across runs
    replaces: list[str]               # block_ids
    new_content: str
    rationale: str
    confidence: float

@dataclass(frozen=True)
class ReflectionResult:
    contradictions: list[Contradiction]
    drifts: list[Drift]
    proposals: list[Proposal]
    surveyed_block_count: int
    elapsed_ms: int

    def __str__(self) -> str: ...
    @property
    def summary(self) -> str: ...
    def to_dict(self) -> dict: ...
```

`ReflectionResult.summary`:

```
reflect (frame=self): 12 blocks surveyed
  · 1 contradiction (high)
  · 2 drifts
  · 3 proposals (avg confidence 0.78)
next: elfmem reflect --apply prop_a3f1   # accept top proposal
```

### CLI

```
elfmem reflect                       # survey + analyze + print report
elfmem reflect --json                # machine-readable for MCP
elfmem reflect --apply <prop_id>     # commit one proposal
elfmem reflect --apply-all           # commit every proposal above min_confidence
elfmem reflect --frame attention     # later: extend to non-SELF frames
elfmem reflect --diagnose            # run twice, diff output (idempotency check)
```

### Exclusions (avoid reflexive infinite loops)

Blocks tagged `system/reflexive` are excluded from reflect's input.
This prevents reflect from detecting "elfmem has no reflect operation"
when reflect itself is the operation under construction. Also excludes
proposals that target blocks tagged `system/immutable`.

### Cost and budgets

- One LLM call per `reflect()` invocation. ~2k–10k tokens depending on
  SELF size.
- `signal_window_days=30` bounds the reality-signal payload.
- For DBs with >200 SELF blocks (unlikely but possible), the survey
  pages by oldest-first and reflects on a window. This mirrors
  rescore's progressive coverage.

### Doctor integration

```
elfmem doctor

  ...
  reflection: 1 stale SELF block, 1 unresolved drift
    suggestion: elfmem reflect
```

Doctor counts: blocks where `last_reflected_at` is older than
`reflection.min_age_days` (default 7), or NULL.

A new column `last_reflected_at` is added to `blocks` (schema v3 → v4)
in the same migration that adds the registry. Backfill: NULL.

## Implementation steps

Each step is a separate PR. Steps 1–2 ship as **v0.14.0-prereq**;
steps 3–6 ship as **v0.14.0**.

### Step 1 — Operation registry (prerequisite)

- [ ] Add `src/elfmem/registry.py` with `OperationSpec` + `all_operations()`.
- [ ] Annotate each operation in `api.py` with its `Rhythm` and `Cost`.
- [ ] Refactor `guide.py` to derive operation table from registry.
- [ ] Refactor "Three rhythms" footer into a function over the registry.
- [ ] Tests: `test_registry.py` — every public method on `MemorySystem`
      has exactly one `OperationSpec`; rhythms are well-formed.
- [ ] AgentGuide unchanged (it auto-derives now).
- [ ] CHANGELOG: Added — operation registry; Changed — guide derivation.

### Step 2 — `last_reflected_at` schema migration (prerequisite)

- [ ] Migration v3 → v4: `ALTER TABLE blocks ADD COLUMN last_reflected_at TEXT;`
- [ ] Backfill: NULL (eligible-on-first-run).
- [ ] Doctor reads the column; warns when stale.
- [ ] Tests: `test_migrate_v4.py`.

### Step 3 — Survey phase (pure)

- [ ] `src/elfmem/operations/reflect.py` — `survey(system, frame, window) -> SurveyResult`.
- [ ] Pure function. No LLM. Loads SELF blocks + registry + signals.
- [ ] Tests: `test_reflect_survey.py` — deterministic ordering,
      exclusion rules, window filtering.

### Step 4 — Analyze phase (LLM)

- [ ] `analyze(survey, llm) -> AnalysisResult`. One LLM call, JSON
      output with `Contradiction`, `Drift`, `Proposal`.
- [ ] Prompt template in `src/elfmem/prompts/reflect.txt`.
- [ ] MockLLMService fixture for tests; never real API calls in CI.
- [ ] Tests: golden-output test — same survey input → same proposals.
- [ ] Tests: idempotency — two consecutive runs produce identical
      proposal IDs.

### Step 5 — Public API + CLI

- [ ] `MemorySystem.reflect()` and `MemorySystem.reflect_apply()`.
- [ ] AgentGuide entry in `guide.py GUIDES` (mandatory — see
      CLAUDE.md).
- [ ] `elfmem reflect` CLI command with the flag set above.
- [ ] MCP tool `mcp__elfmem__elfmem_reflect`.
- [ ] Tests: end-to-end via CLI; MCP roundtrip; result-types
      `__str__`/`summary`/`to_dict`.

### Step 6 — Doctor integration + docs

- [ ] Doctor counts and surfaces stale-reflection state.
- [ ] Update `docs/note_2026_05_08_reflection.md` to mark reflect as
      shipped.
- [ ] Update CHANGELOG entry.
- [ ] Update README "rhythms" section to derive from registry.

## Testing

Per `docs/testing_principles.md`:

- **No real API calls.** MockLLMService for the analyze phase. Golden
  fixtures for prompt input/output.
- **Property tests** for survey: any subset of SELF blocks → ordering
  is stable; exclusion rules are total.
- **Idempotency test:** `reflect()` twice on a frozen DB → identical
  `to_dict()` modulo `elapsed_ms` and timestamps.
- **Drift detection test:** seed DB with a SELF block claiming
  `operation_x` exists; registry has no `operation_x`; assert one
  drift returned with `evidence_refs` including the registry.
- **Apply round-trip:** `reflect_apply()` of a proposal results in
  the old block(s) archived and a new SELF block stored. Subsequent
  `reflect()` shows zero contradictions for that pair.

## Principles served

- **Agent-first contract** — typed result, `summary`, `to_dict`,
  `.recovery` on failure (e.g., LLM unavailable → recovery suggests
  `reflect --json --dry-run`).
- **Functional Python** — survey is pure; analyze is a single
  function that takes a survey and an LLM and returns a result;
  report is a `__str__`.
- **Fail fast** — LLM errors propagate; no silent fallback that
  silently overwrites the constitution.
- **Idempotent** — same input → same proposals.
- **Progressive disclosure** — `elfmem reflect` works with zero
  config; advanced flags optional.
- **No defensive code in business logic** — survey trusts the DB
  schema; analyze trusts the LLM contract; CLI catches at the
  boundary.

## Open design questions

1. **Should reflect detect drift in the *attention* frame too?** Yes,
   eventually. Ship `frame=self` first; extend after one release of
   field experience.
2. **Should reflect run automatically?** No, not in v1. After we have
   a calibration baseline (how many false positives does reflect
   surface?), we can consider a "reflect when stale_self_blocks > N"
   nudge in `should_dream`.
3. **Should peer SELF blocks be reflected on?** No. Peer perspectives
   are exempt from rescoring (per `plan_deep_sleep_rescoring.md`)
   and are exempt here too. Reflect operates only on `source_peer IS
   NULL` blocks.
4. **Confidence threshold default?** Start at `0.6`. Tune from
   real-world false-positive rate.

## Non-goals (explicit)

- Reflect does **not** generate new SELF principles from scratch. It
  only revises or retires existing ones. New principles come from
  lived experience via `remember --category self`.
- Reflect does **not** replace dream(). They serve different rhythms
  on different inputs.
- Reflect is **not** a chat interface. It is a structured operation
  with structured output.
