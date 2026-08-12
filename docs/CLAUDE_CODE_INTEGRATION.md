# Claude Code + elf Integration Guide

## Overview

Claude Code gets persistent, evolving memory via elfmem's MCP server. Claude can:
- Query elf's identity before major decisions (`frame="self"`)
- Learn new concepts by remembering them
- Access the full knowledge base built up across sessions
- Make decisions guided by elf's constitutional principles

## Setup

Setup and MCP configuration are covered in full in [`docs/SETUP_AND_CONFIG.md`](SETUP_AND_CONFIG.md) and the README's [MCP section](../README.md#mcp-for-ai-agents-with-mcp-support). In short:

```bash
uv sync --extra mcp --extra cli   # never `uv add elfmem[mcp]` — self-dependency, fails
elfmem init                       # writes .elfmem/config.yaml and the elfmem section in CLAUDE.md/AGENTS.md
```

`elfmem init` prints the exact MCP JSON snippet to paste into `~/.claude.json` (Claude Code's per-project MCP config) — including an absolute path to the `elfmem` executable and, if a `.env` exists at the project root, an `--env-file` flag so the spawned server process gets your API keys ([ADR 0008](decisions/0008-mcp-entry-default.md)). `elfmem doctor --migrate-mcp` detects drift in an existing entry; `elfmem migrate apply` fixes it.

## How It Works

Ask Claude to query elf's identity before a design decision:

```
elfmem_recall(query="consolidation timing principles and heuristics", frame="self", top_k=5)
```

elf responds with whatever's actually been learned and reinforced in that project's memory — not a fixed prompt. Query examples:

```
elfmem_recall("identity principles for distributed caching", frame="self")
elfmem_recall("optimization strategy for retrieval at scale", frame="self")
elfmem_recall("how should I decide when to curate", frame="self")
```

Use `frame="self"` for identity/principles/values, `frame="attention"` for specific problems and patterns, `frame="task"` for goal-directed work. See the README's [Four frames](../README.md#four-frames-retrieval-shaped-by-intent) section for the full scoring model.

You can also inspect elf's identity directly from the shell:

```bash
elfmem recall "my identity and values" --frame self
elfmem status
elfmem remember "New principle: X" --tags self/principle
```

## Agent Discipline: Self-Calibrating Memory

Static prompts tell agents what to do. **Agent discipline** teaches agents to improve their own memory through use — the full `RECALL → EXPECT → ACT → OBSERVE → CALIBRATE → ENCODE` loop, at three tiers (2 / 6 / 12 instructions), is documented in the README's [Building agents with elfmem](../README.md#building-agents-with-elfmem) section and `examples/agent_discipline.md`. `scripts/seed_team_memory.py` seeds elfmem with project conventions for a team agent.

The critical step most agents skip is **calibration** — telling elfmem which recalled blocks actually helped (`outcome(signal=0.85)`), which were noise (`signal=0.45`), and which misled (`signal=0.15`). Without it, all knowledge decays equally and memory never improves.

## Simulation-Based Calibration (`simulate` frame)

`simulate` is a **frame**, not a rhythm — a Theory-of-Mind retrieval mode that blends the SELF constitution with `mind/*` blocks to reason about other agents, users, or hypothetical scenarios (see the README's [Theory of Mind](../README.md#theory-of-mind-modelling-other-agents) section for the shipped `mind_create`/`mind_predict`/`mind_outcome` mechanics).

Beyond what's shipped, `examples/simulation_calibration.md` explores a deeper calibration design for high-stakes or novel-domain decisions:
- **Brier scores** to track prediction accuracy over time
- **Fragility scores** to reveal when predictions rest on too few blocks
- **Adversarial scenarios** to prevent echo chambers
- **Wildcard tracking** to detect when the simulation framework is too narrow
- **Tiered simulation** matching depth to decision stakes (1-10 LLM calls)

This is exploratory design material, not a description of shipped behaviour — check `ROADMAP.md` for what's actually implemented versus proposed.
