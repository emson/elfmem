# Constitution

**Version:** C1 · **Classification:** initial · **Hash:** `<computed by validate_plan.py --write-lock>`

> This document is **promoted, never edited**. Every agent working on this build
> receives this file **inlined verbatim and byte-identical**. Do not paraphrase,
> summarise, or regenerate it per agent.

---

## 1. Invariants

*These hold everywhere, regardless of which unit you are building. A unit that
violates one is wrong even when its own tests pass. If your assignment appears to
require violating an invariant, stop and report rather than proceeding.*

1. **The index is the map; the files are the territory.** `.elfmem/index.db`
   never holds information that cannot be reconstructed from
   `.elfmem/memory/**.md` by `elfmem index` with zero LLM calls. Anything that
   can't be rebuilt this way is a bug, not a feature.
2. **`self.md` is never a block-table row.** It is read directly into the `self`
   frame; it is never superseded, never decayed, never subject to
   `consolidate()`. No supersession path may reach it, structurally — not by guard.
3. **A block's `id:` is permanent and content-independent**, assigned once at
   creation. `sha256(content)` is a secondary field, used only to decide whether
   re-embedding is needed. Reinforcement count, decay clock, and edges key off
   `id:`, not content hash.
4. **No LLM call mutates memory directly.** Every change to
   `.elfmem/memory/**.md` is either (a) a deterministic operation — `learn`
   (append), `edit`, `forget`, `promote` — or (b) a human-approved `--apply` of a
   proposal file an LLM only *drafted*. Reasoning proposes; a human or
   deterministic code applies.
5. **A pinned block (`pinned: true` in frontmatter) is never proposed for
   removal and is always included in its frame.**
6. **Reconciliation of duplicate/re-sent evidence happens exactly once, at
   rebuild or review time — never scattered across write paths.** This is what
   keeps merge arithmetic (Bayesian α/β, trust-weighted or otherwise) safe from
   double-counting. If your unit is tempted to merge evidence at write/import
   time, stop — that is very likely the bug this plan exists to close (see
   `model.md`'s resolved peer-bundle defect for the concrete precedent).

## 2. Core concepts

*The shared vocabulary. Use these terms exactly; do not coin synonyms.*

### Stable block identity

A permanent `id:` in frontmatter, assigned once at block creation, independent
of content hash. Editing a block's content never changes its `id:`. This is
what makes "the files are hand-editable" survive a second edit — without it,
every edit orphans the block's reinforcement/decay/edge history.

### L1/L2 authority split

L1 (`.elfmem/memory/**.md`) is truth. L2 (`.elfmem/index.db`) is derived,
disposable, and rebuildable with zero LLM calls. Nothing may write to L2 except
the `elfmem index` rebuild path (Invariant 1). `frame()`/`recall()` only read L2.

### Corpus-level, proposal-only review engine

One mechanism — Discover (read the index) → Judge (LLM call, one per cycle, not
per block) → Worksheet (proposal file with checkboxes) → Apply (human-approved
mutation through the deterministic mutation API) — with a **pluggable reasoning
source**: either the host agent invoking the `elf-review` skill, or elfmem's own
optional gateway for headless/no-host-agent operation (scheduled review, cron).
This is **one engine, not two** — `elfmem review` (CLI/gateway) and `elf-review`
(skill) are the same pipeline with different Judge-step LLM bindings. Do not
build two proposal-file parsers, two worksheet UIs, or two Apply implementations.

### Reasoning-ownership seam

The review engine's Judge step is reasoning-source-agnostic by design. A unit
implementing or calling into the Judge step must not assume which source is
bound — it receives an answer, not a specific adapter.

## 3. Process rules

These are absolute and override any local convenience or apparent time saving.

1. **Never commit.** A human performs every commit and every merge.
2. **Never merge**, and never modify a branch you do not own.
3. **Never write to plan documents.** `model.md`, `build-plan.md`,
   `constitution.md` and `plan.lock.yaml` are read-only to you.
4. **Never touch a file outside your unit's `Owns` set.** If your work appears
   to require it, that is a deferred-wiring item: report it, do not perform it.
5. **Never fabricate a missing asset.** If a document, path or command
   referenced in your packet does not exist, **stop and report**. Do not
   reconstruct it, do not infer its contents, and do not proceed on assumption.
6. **Never claim completion you have not verified.** Run the `Verified by`
   command and record its actual output.
7. **Follow this project's own engineering conventions**, not generic
   defaults: functional Python, fail-fast (no broad `except`, no
   `try/except` in business logic), complete type hints on every function,
   the `USE WHEN / DON'T USE WHEN / COST / RETURNS / NEXT` docstring
   template on every public method (`CLAUDE.md`, repo root).
8. **Every new public `MemorySystem` method needs a corresponding
   `AgentGuide` entry** in `src/elfmem/guide.py`'s `GUIDES` dict before your
   unit is done — this is a project rule, not optional polish.
9. **Update `CHANGELOG.md`'s `[Unreleased]` section** for any user-facing
   behaviour change (CLI, config schema, MCP tools). One bullet per logical
   change, leading with the affected symbol or command. Never edit a released
   version section.
10. **Never edit `docs/decisions/*.md` (ADRs).** They are append-only. If your
    unit's work supersedes or grounds-out an existing ADR (this plan
    references ADR 0003, 0005, 0009, 0010), report that it needs a new ADR —
    do not write one yourself as part of a unit's packet.

## 4. Stop and report

When blocked: do not improvise. Write the blockage to `results/<unit-id>.md`
with what you expected, what you found, and what you need. Then stop.

Blocking conditions include: a missing or unreadable asset; a base commit that
does not match the pin in your packet; a `Verified by` command that does not
exist; an assignment that appears to require violating an invariant or touching
an unowned file; a `Depth: named` or `sketched` unit being rendered into a
packet before it has been promoted to `full` (see `build-plan.md`'s depth
notes — Waves 4–8 are not yet ready to render).

## 5. Reporting missing context

If you needed information you were not given, record it in your results file
under **Missing context**, naming what you needed and what you did instead.
This is a first-class signal, not a complaint: repeated reports mean the
concept model is wrong and will be revised (see `model.md`'s Model drift log).

## 6. Where results go

Write to `results/<unit-id>.md`, following `assets/result-template.md`'s
structure (copied into this plan directory alongside the validator):

- The constitution **version and hash** from your packet header, echoed back
- The `Verified by` command and its **actual, unedited output**, under a
  `## Verification` heading
- Files created or modified
- Missing context, if any
- Blockages, if any

These files are the build's ground truth. Unit status, gate status and
provenance are folded out of them on demand — there is no separate status file.
**Put the real output under `## Verification`.** A result that claims
completion with nothing recorded there is reported as *claimed*, not built.

---

## Amendment history

*Full diffs live in git. Recorded here only for orientation.*

| Version | Date | Classification | Summary |
|---|---|---|---|
| C1 | 2026-08-09 | initial | First promotion — invariants and core concepts from `model.md`, process rules from repo-root `CLAUDE.md` plus specbuild's own template |
