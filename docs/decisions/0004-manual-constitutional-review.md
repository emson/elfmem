# 0004 — Manual constitutional review (v0.18)

**Status**: Accepted — implemented in v0.18.0
**Date**: 2026-05-23
**Deciders**: elf, Ben

## Context

[ADR 0003](0003-defer-constitutional-evolution.md) deferred constitutional
evolution because all four explored AUTOMATIC mechanisms — Architecture M,
Model C, Model D, and the self-architecting variant — were marginal-to-
negative against the disciplined baseline. The simulation evidence in that
ADR explicitly excluded any mechanism that *automatically* modifies
``self/constitutional`` content; the question of whether a *manual*
mechanism could pay rent was left open.

This ADR records the decision to ship a manual constitutional review
mechanism in v0.18 — a structurally different approach to the same
problem, one that ADR 0003 did not consider in scope.

## Alternatives considered

1. **Continue waiting per ADR 0003** — no constitutional evolution path at
   all. Acceptable, but leaves the agent's tagged constitutional content
   to drift from its operational reality indefinitely.
2. **Ship one of the deferred automatic mechanisms** — rejected by ADR 0003
   on simulation evidence; nothing in the intervening work changed that.
3. **Manual review cycle: surface drifted constitutional, require
   explicit accept** — this ADR. Read-only ``review_constitutional()``
   call exposes proposals; nothing is applied until ``accept_amendment()``
   is called with the block id and the chosen content.

## Decision

Ship the manual constitutional review cycle as v0.18.

## Why this doesn't conflict with ADR 0003

ADR 0003 specifically rejected AUTOMATIC modification of constitutional
content. This mechanism is the explicit non-automatic complement:

- ``review_constitutional()`` is **READ-ONLY** — it surfaces drift via an
  LLM-generated proposal; the block itself is never touched by this call.
- ``accept_amendment()`` requires an **EXPLICIT** invocation per block.
  There is no scheduler, no auto-trigger, no consolidate / curate / dream
  hook that ever applies an amendment without the agent (or user)
  deciding to.
- Every applied amendment is **audited** — pre-content and post-content
  are written into ``block_amendments`` alongside the drift score and
  rationale, with an ``acceptor`` field recording who decided.
- ``revert_amendment()`` provides one-step undo. Reverted amendments are
  flagged with ``reverted_at`` rather than deleted; history is preserved.
- A 90-day per-block cooldown (default) prevents oscillation: a block
  cannot be re-proposed for review while a recent amendment is still
  inside the cooldown window.

The earlier longitudinal Monte-Carlo simulation
(``scripts/longitudinal_sim/mc_constitutional_review.py`` in the research
compilation) measured **+9-14pp retrieval quality across the drifting
scenarios with zero stable-case tax** — the property all four automatic
mechanisms in ADR 0003 failed to deliver. The manual surface costs
nothing in the stable case because nothing happens unless someone calls
it; in the drifting case the gains accrue because the proposals are
proposed against the current operational centroid, not a fixed prior.

## Consequences

- New public API: ``review_constitutional``, ``accept_amendment``,
  ``revert_amendment``, ``list_amendments`` (exported from
  ``elfmem``, with matching AgentGuide entries and ``elfmem.guide()``
  documentation).
- New schema: ``block_amendments`` audit table (migration v4 → v5).
  Idempotent ALTER, no behaviour change for callers that never use the
  new API.
- New surfaces: ``elfmem review`` CLI subcommand group (interactive +
  ``--json``), four ``elfmem_*`` MCP tools (acceptor hard-coded to
  ``agent``).
- New ``ReviewConfig`` (nested under ``ElfmemConfig`` as ``review``) with
  nine tunables. Defaults are calibrated against the simulation; no
  YAML changes are required for v0.18 to work.
- Per-block 90-day cooldown (configurable) prevents oscillation.
- Frame cache for ``self`` is invalidated on every accept and revert so
  context retrieval reflects the new content immediately.

## References

- [ADR 0003 — Defer constitutional evolution](0003-defer-constitutional-evolution.md)
- [CHANGELOG: 0.18.0 — 2026-05-23](../../CHANGELOG.md)
- Research compilation: ``docs/research/long_term_evolution/`` (longitudinal
  Monte-Carlo + constitutional-review variants)
- Headline integration test:
  ``tests/test_amendment_apply.py::TestIntegration::test_review_accept_then_re_review_skips_cooled_block``
