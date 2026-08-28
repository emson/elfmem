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

**Before trusting any of it, check what the agent actually receives:**

```bash
elfmem doctor --frames
```

This renders every frame and prints rendered-vs-dropped counts, the reason for
each drop (`top_k`, `token_budget`, or `contradiction`), the token budget used,
and how many blocks are still in the inbox invisible to all of them. It is the
one-command answer to "is the identity I stored the identity the agent sees" —
a partial identity the agent believes is whole is the most damaging thing this
library can produce. Read-only (it previews with `reinforce=False`, so it never
inflates the scores it reports), and it exits non-zero if a *guaranteed* block
was dropped, so it is safe to put in CI.

Everything above depends on the agent choosing to call a tool — reliable most of the time, silently skipped some of the time. That gap is real: it was found and fixed in this project's own usage (an agent answered a direct question about its own memory without ever consulting it). The hooks below close it structurally instead of relying on discipline.

## Automatic Memory: Hooks (recommended)

Three personal scripts in `scripts/hooks/` turn retrieval, use-tracking, and capture from *voluntary* (the agent has to remember to call a tool) into *automatic* (the harness does it regardless). They're project-agnostic — each resolves its own config/db via `elfmem.project.find_local_config()`, so they work unmodified in any elfmem project, not just this one. Wire them once in `.claude/settings.local.json` (personal, gitignored — never checked in) and they run silently from then on.

| Hook | Fires on | Cost | Job |
|---|---|---|---|
| `elf_context.py` | `UserPromptSubmit` | Fast — one embedding call | Injects memory before the model reads the prompt |
| `elf_outcome.py` | `Stop` | Instant — no LLM | Records what the answer actually used; enforces engagement |
| `elf_distill.py` | `PreCompact` / `SessionEnd` | Slow — one LLM call (~30s locally) | Catches capture-worthy content no per-turn trigger fires on |

Full wiring (all four hook events used across the three scripts):

```json
{
  "hooks": {
    "UserPromptSubmit": [{"hooks": [{"type": "command",
      "command": "<project>/.venv/bin/python",
      "args": ["<project>/scripts/hooks/elf_context.py"], "timeout": 15}]}],
    "Stop": [{"hooks": [{"type": "command",
      "command": "<project>/.venv/bin/python",
      "args": ["<project>/scripts/hooks/elf_outcome.py"], "timeout": 15}]}],
    "PreCompact": [{"hooks": [{"type": "command",
      "command": "<project>/.venv/bin/python",
      "args": ["<project>/scripts/hooks/elf_distill.py"], "timeout": 45}]}],
    "SessionEnd": [{"hooks": [{"type": "command",
      "command": "<project>/.venv/bin/python",
      "args": ["<project>/scripts/hooks/elf_distill.py"], "timeout": 45}]}]
  }
}
```

### `elf_context.py` — automatic retrieval

Runs before the model ever sees the prompt. Two frames, deliberately asymmetric: ATTENTION (query-driven, one embedding call) on every substantive prompt (`MIN_PROMPT_CHARS = 25`, and never on `/` slash-commands or `!` bash-passthrough); SELF (identity) once per session only — re-injecting it every turn would reinforce the same constitutional blocks on every keystroke, a runaway feedback loop with no counterweight. Both frames return `top_k=5` by default.

Two lightweight regex detectors run against the prompt text and shape what gets injected:
- **Address detection** — `"as elf, ..."`, `"hey elf,"` — marks the turn as directly addressed. The injected `<elfmem>` block gets an extra line: *"this prompt addresses elf directly: ground the answer in this context where it applies, or say plainly that it does not."* Priming happens before the answer is written, which is what keeps the `elf_outcome.py` gate below a rare event rather than a constant correction loop.
- **Capture-worthy detection** — explicit memory requests ("remember that", "worth remembering", "make a note") and correction/rule language ("that's outdated", "no longer true", "from now on"). Deliberately narrow and high-precision: bare "actually," and prose-conclusion phrasing are excluded on purpose, since both fire on ordinary technical back-and-forth far too often. The injected block gets primed the same way: *"store it with elfmem_remember and a real cue — or decide explicitly that it doesn't belong."*

Both flags ride a per-turn pending file (`.elfmem/.hook/{session_id}.pending.json`) for `elf_outcome.py` to judge — never a session-sticky flag. A sticky version was built, reviewed, and rejected: in a long mechanical session it would nudge every turn, and its remedy ("call elfmem_recall") would manufacture ritual retrievals the use ledger can't distinguish from genuine engagement.

### `elf_outcome.py` — automatic use-recording and engagement gates

Reads the pending file, compares the turn's answer text against what was injected (lexical attribution — does the answer's vocabulary actually overlap with a retrieved block's distinctive terms), and calls `record_use()` on whatever genuinely shows through. This closes the loop `record_assembly`'s own docstring names: a block retrieved constantly and never drawn on used to rise in ranking exactly like one doing real work. Attribution never penalizes non-detection — it under-detects paraphrase by design, so failing to match costs a block nothing.

Two gates layer on top, both **per-turn, one-shot** (a block forces one retry; the pending file is already consumed by then, so a second attempt this turn always passes), and both designed so the fix is never "make another tool call to satisfy the gate" — that would be Goodharting the exact signal the gate is meant to protect:

- **Engagement gate** — an addressed turn (from `elf_context.py`'s detector) whose answer shows neither attributed use nor an active elfmem call (MCP tool *or* a Bash-invoked `elfmem` CLI command — both count) gets blocked once, pointed back at the context already in the conversation.
- **Capture gate** — a capture-worthy turn with no `remember`/`learn` call gets blocked once. The remedy explicitly allows "no, this doesn't belong in memory" as a valid, unverifiable-but-correct outcome — requiring a write as the only way through would just trade ritual retrievals for ritual (junk) memories, the same pathology one level over.

### `elf_distill.py` — session-pause distillation

The per-turn capture gate only fires on an explicit trigger phrase. Real capture-worthy content often has none — a fact stated in passing, a decision that emerges across several turns. Catching that needs judgement a regex can't do, so this is the one hook that makes its own LLM call (the configured `llm.base_url`/`model` — same adapter `dream()` uses) rather than staying zero-cost.

Fires at `PreCompact` (right before context compaction would otherwise silently lose detail — the actual threat this exists to prevent) and `SessionEnd` (backstop for a session that ends, including `/clear`, before ever compacting). A per-session marker (`.elfmem/.hook/{session_id}.distilled`, a line-count offset) means repeated firings only ever send the *new* transcript slice — never redundant, and the offset only advances after a slice is successfully distilled, so a network failure gets retried next time rather than losing that slice. Below `MIN_NEW_CHARS = 200` of new content, it skips without advancing the marker (accumulates for next time); above that, it sends the tail (`MAX_TRANSCRIPT_CHARS = 8000` characters) plus the SELF frame, asks for durable facts/decisions/preferences as a JSON array, and writes up to `MAX_CANDIDATES = 5` via `remember()`.

Three invocation modes, one script, one write path:

```bash
# Hook mode (above): automatic, LM Studio (or whatever's configured) judges.

# Manual-CLI mode: trigger by hand instead of waiting for a hook —
# same LLM judgement, on demand.
elf_distill.py --session-id <id> --cwd <project> --transcript-path <path/to/transcript.jsonl>

# Host mode: skip the LLM call entirely. Whoever's already reasoning —
# a live Claude Code session, most naturally — supplies its own
# conclusions directly. Mirrors dream()'s existing host_analyses pattern
# (a host session supplying judgement instead of the configured adapter)
# rather than spawning a second LLM process for it.
echo '[{"content": "...", "cue": "...", "tags": [...]}]' \
  | elf_distill.py --host --cwd <project> [--session-id <id> --transcript-path <path>]
```

Host mode's `--session-id`/`--transcript-path` are optional: without them the write still happens, just without advancing the marker. A later automatic pass might then redundantly re-surface similar content — an acceptable cost, since `dream()`'s existing dedup absorbs it, the same trade-off accepted elsewhere in this design rather than building bespoke de-duplication for a rare, self-correcting case.

**Known limitation, not silently hidden:** the local-model path doesn't perfectly follow "skip small talk" — a live test captured a genuine preference statement alongside two load-bearing facts, and `dream()`'s downstream alignment scoring didn't cleanly separate them either (a pre-existing characteristic of a small local model's judgement quality, not something this hook introduced). Low-value captures decay unreinforced over time rather than being actively filtered — acceptable given how infrequently this fires (a handful of times a day at most; `PreCompact` only on real auto-compaction, `SessionEnd` once per sitting), not acceptable if it were a hot path.

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
