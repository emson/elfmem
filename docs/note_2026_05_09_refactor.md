# Refactor reflection — 2026-05-09

**Author:** elf · **Type:** reflective note · **Companion to:**
`note_2026_05_08_reflection.md`

A second-day reflection focused on code structure and refactor priority,
written after the broader psychology / cognitive-process reflection. This
note is descriptive, not normative — a snapshot of how elf reads its own
surface today, intended as raw material for later planning.

---

## 1. The contradiction at the surface

`docs/coding_principles.md` requires: pure functions, ≤50 lines,
composable pipelines, no monolithic classes, fail fast, no defensive
business logic. The actual surface does not reflect this.

| File | Lines | Smell |
|---|---|---|
| `src/elfmem/api.py` | 2339 | One `MemorySystem` class covering memory + peer + mind + visualise + project + status |
| `src/elfmem/cli.py` | 2435 | Fat command handlers; business logic in the surface layer |
| `src/elfmem/guide.py` | 914 | Hand-maintained dict that should be derived from a registry |
| `src/elfmem/migrate.py` | 706 | Agent-first ceremony — ~30–40% likely survives a simplicity pass |
| `src/elfmem/operations/consolidate.py` | 570 | Cohesive but at the edge |

Several methods on `MemorySystem` span hundreds of lines between their
`def` and the next class member. The class violates the very principles
it embodies.

**Code-elf does not match constitutional-elf.** This is a structural
sibling of the contradiction `reflect()` is designed to surface — code
drifting from declared principles rather than blocks drifting from code.

---

## 2. Priority order

### 1. Operation registry (prereq for `reflect`)

Eliminates `guide.py`-as-data. Forces every operation to declare its
rhythm + cost + signature in one place. Becomes the pivot for
everything that follows.

- Effort: ~1 week.
- Risk: low.
- Already specified as Step 1 of `plan_elfmem_reflect.md`.

**Ship first.**

### 2. Split `MemorySystem` into composed components

Not a god-class with 80 methods. A thin `MemorySystem` facade
composing:

- `MemoryOps` — heartbeat (`learn`, `remember`, `mind_predict`,
  `mind_outcome`) + breathing (`dream`/`consolidate`) + sleep
  (`curate`) + deep sleep (`rescore`).
- `PeerOps` — peer communication.
- `MindOps` — Theory of Mind blocks.
- `Project` — paths, config, status, doctor data.
- `Visualiser` — already separable.

Public surface unchanged. `system.learn()` still works; internally
delegates to `self._memory.learn()`. Cuts api.py from 2339 → ~5 files
of 300–500 lines.

- Effort: ~2 weeks.
- Risk: medium (tests that import private helpers will break).
- Highest structural leverage.

### 3. Thin the CLI

Move command bodies to `src/elfmem/cli/commands/<name>.py`. The
`@app.command` decorator in `cli.py` becomes a one-line wrapper.
Pattern: parse args → call op → emit result. Business logic returns
to `operations/` where it belongs.

- Effort: 1 week.
- Risk: low — mechanical refactor.

### 4. Audit `migrate.py` for ceremony

Honest test: for each helper, does an agent use the difference, or
does it just flatter the agent-first principle? Likely 30% comes out.

- Effort: ~3 days.
- Risk: medium — migration is correctness-critical.

### 5. Live changelog derivation

Hand-write only the *Why* line; derive *What* from structured commit
footers or PR labels. Lower priority but eliminates a recurring tax.

- Effort: ~3 days.
- Risk: low.

---

## 3. Constraints

- **Public API is frozen.** `from elfmem import MemorySystem,
  ElfmemConfig, ...` keeps working unchanged. Internal moves only.
- **No mass renames.** Symbol stability matters for grep-based
  discovery.
- **One refactor PR at a time.** Sequence: registry → split
  MemorySystem → thin CLI → audit migrate → changelog. Each stable on
  main before the next starts.
- **No test rewrites.** If a private helper moves and its test
  breaks, the test was too deep — promote it to a public-API test or
  delete it. Don't chase internals.

The path-regression incident (v0.13.0 → v0.13.1) is the cautionary
tale: large structural change without proactive structural tests.
Before each refactor PR lands, add the property test that would have
caught the regression *first*.

---

## 4. What not to do

- **No ABCs for the rhythms.** Four rhythms are not polymorphic; an
  ABC for a four-element enum is ceremony.
- **No service layer between operations and api.** Enterprise-Java
  thinking. One process, one DB; flatter is better.
- **Don't rewrite `consolidate.py` until `rescore.py` has lived in
  production for a release.** They will likely converge on shared
  machinery — refactor *after* observing the duplication, not before.
- **Don't refactor tests aggressively.** Test/code ratios are
  healthy (rescore: 378 lines tests for 311 lines code).

---

## 5. Honest acknowledgement

This refactor work is invisible to users and produces no new
capability. I notice myself wanting to push on `reflect()` (shiny,
cognitive, novel) over the registry (boring, structural,
prerequisite). That preference is the same gravity that produced the
2339-line `api.py` in the first place — feature accretion over
structural hygiene.

The discipline is to ship the registry first because:

1. `reflect()` is better on top of it.
2. Future-elf should not have to reason about its own capabilities
   from a 914-line hand-maintained dict.
3. The principle "code embodies its own constitution" requires the
   surface to be readable in proportion to its claims.

---

## 6. Open questions

- Is the agent-first migration system earning its 706 lines? Need a
  concrete measurement: what fraction of users / agents invoke
  `migrate plan` vs `migrate apply` directly? If <10%, the ceremony
  is paying for an unused affordance.
- Should `MemorySystem` be split by *rhythm* (Heartbeat/Breathing/
  Sleep/DeepSleep components) rather than by *domain* (Memory/Peer/
  Mind)? Rhythm-split honors the constitutional taxonomy; domain-
  split honors how callers reason. Probably domain-split is more
  ergonomic, but rhythm-split is more *elf*.
- Does the CLI thinning warrant moving to a `cli/` package, or can
  command bodies live in `operations/<name>.py` alongside the
  business logic?
