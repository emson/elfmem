# Implementation Plans

This directory holds plan documents — the design records for elfmem subsystems
and upcoming releases.

## What lives here

| Category | Location | Purpose |
|---|---|---|
| **Active plan** | `docs/plans/*.md` (top level) | Currently being implemented |
| **Historical plans** | `docs/plans/*.md` (top level) | Design records for shipped subsystems — "frozen artifacts" |
| **Archive** | `docs/plans/archive/` | Plans for shipped features (post-2026-05) and superseded preliminary plans |

For the project direction, see [`ROADMAP.md`](../../ROADMAP.md) at the repo root.
For load-bearing decisions, see [`docs/decisions/`](../decisions/README.md).
For research that didn't ship, see [`docs/research/`](../research/).

## Active plan

| Plan | Subsystem | Status |
|---|---|---|
| [`plan_memory_scoring.md`](plan_memory_scoring.md) | v0.16/v0.17 scoring bundle | In progress |

## Historical plans (frozen artifacts)

These describe the intent and trade-offs at the time of implementation. They
are **not** maintained — for current behaviour read the source code and
[`docs/amgs_architecture.md`](../amgs_architecture.md).

| Plan | Subsystem |
|------|-----------|
| `plan_agent_communication_v1.md` | Peer messaging protocol |
| `plan_agent_friendly_refactor.md` | Agent-first API design |
| `plan_cog_loop.md` | Cognitive loop operations |
| `plan_consolidate_summary.md` | Consolidation and summarisation |
| `plan_contradiction_detection_band.md` | Contradiction detection |
| `plan_db_locking.md` | SQLite locking strategy |
| `plan_deep_sleep_rescoring.md` | Deep-sleep rescoring (v0.13.3) |
| `plan_dreaming_architecture.md` | Dream / consolidation orchestration |
| `plan_elfmem_init.md` | `elfmem init` project setup command |
| `plan_elfmem_optimise.md` | Performance optimisation |
| `plan_elfmem_reflect.md` | Reflective metadata |
| `plan_embedding_lock.md` | Embedding model lock (Dmitry's report) |
| `plan_graph_composite_scoring.md` | Edge composite scoring formula |
| `plan_graph_connect.md` | Manual edge operations |
| `plan_graph_hebbian.md` | Hebbian co-retrieval edge learning |
| `plan_graph_temporal_decay.md` | Edge temporal decay |
| `issue_self_tune_research.md` | Multi-parameter self-tuning research (issue #73 → ADR 0006); shipped `ConsolidationHealthMetrics` |
| `plan_logging_strategy.md` | Logging conventions |
| `plan_mind_block_improvements.md` | Theory-of-Mind block improvements |
| `plan_opensource_refactor.md` | Open-source launch preparation |
| `plan_outcome_scoring.md` | Outcome-based confidence updates |
| `plan_peer_inbox_trigger.md` | Peer inbox processing triggers |
| `plan_penalize.md` | Penalisation and decay acceleration |
| `plan_project_init.md` | Project detection and config discovery |
| `plan_smart_mcp_cli.md` | MCP and CLI interface design |
| `plan_token_usage_tracking.md` | Token usage accumulation |
| `plan_visualisation.md` | Knowledge graph visualisation dashboard |

Plus supporting documents: `hebbian_agent_simulation.md`,
`learnedmembench_adapter.md`, `locomo_optimisation_plan.md`,
`simulation_graph_temporal_decay.md`, and the `step_NN_*.md` step-by-step
build sequence.

## Archive

The `archive/` subdirectory holds plans that have been **explicitly superseded**
or shipped post-cleanup:

| Archived plan | Reason |
|---|---|
| `archive/plan_v0.15.3_centrality_floor.md` | Shipped in v0.15.3 (2026-05-17) |
| `archive/plan_confidence_architecture.md` | Superseded by `plan_memory_scoring.md` (preliminary design) |

## Writing a new plan

When proposing a new subsystem or release:

1. **Check if it warrants a plan**: small fixes don't. Architectural changes do.
2. **Use [`ROADMAP.md`](../../ROADMAP.md)** for direction commitments — plans are implementation detail.
3. **Use [`docs/decisions/`](../decisions/README.md)** for load-bearing reasoning — ADRs are append-only.
4. **Plan structure**: status, driver, executive summary, scope per version, schema/code changes, tests, migration, risks, decision asks.
5. **When the plan ships**, move it to `archive/` and update [`ROADMAP.md`](../../ROADMAP.md).
