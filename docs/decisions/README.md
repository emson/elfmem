# Decision Records

Architecture Decision Records (ADRs) for elfmem. Each file documents a significant decision: the context, the alternatives considered, the choice made, and why.

ADRs are **append-only**. We don't edit old decisions; if a decision is superseded, we write a new ADR that references it.

## Format

```
# NNNN — Short title

**Status**: Accepted | Superseded by NNNN | Deprecated
**Date**: YYYY-MM-DD
**Deciders**: who reasoned about this

## Context
What was the situation that called for a decision?

## Alternatives considered
What options were on the table? With brief notes on each.

## Decision
What did we choose? Why?

## Consequences
What does this commit us to? What does it preclude?

## References
Links to plans, research, issues, simulation results.
```

## Current ADRs

| # | Title | Status |
|---|---|---|
| [0001](0001-power-law-decay-rejected.md) | Power-law decay rejected | Accepted |
| [0002](0002-v017-scope.md) | v0.17 scope: bundle four scoring fixes | Accepted |
| [0003](0003-defer-constitutional-evolution.md) | Defer constitutional evolution mechanisms | Accepted |
| [0004](0004-manual-constitutional-review.md) | Manual constitutional review (v0.18) | Accepted |
| [0005](0005-peer-protocol-hardening.md) | Peer-protocol hardening (v0.19) | Accepted |
| [0006](0006-defer-multi-parameter-self-tuning.md) | Defer multi-parameter self-tuning (issue #73) | Accepted |
| [0007](0007-bound-and-checkpoint-consolidation.md) | Bound and checkpoint consolidation for slow LLM adapters | Accepted |
| [0008](0008-mcp-entry-default.md) | MCP entry generation default, drift detection, and migration | Accepted |
