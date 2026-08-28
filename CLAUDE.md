# elf — Adaptive Memory for LLM Agents

**elf** (`elfmem` package) is a self-aware adaptive memory system. Agents learn, reinforce, and forget knowledge the way biological memory works — fast ingestion, deep consolidation at pauses, decay-based archival at rest. SQLite-backed. Zero infrastructure.

## Core Mental Model

**Four rhythms** (every design decision maps to one of these):
- **Heartbeat** — `learn()`: milliseconds, no LLM, pure inbox insert
- **Breathing** — `dream()` / `consolidate()`: seconds, LLM-powered dedup + contradiction detection
- **Sleep** — `curate()`: minutes, decay archival + graph pruning + top-K reinforcement
- **Deep Sleep** — `dream(rescore=True)` / `rescore()`: re-evaluates aged active blocks against the *current* SELF; keeps alignment / summary / tags fresh as the agent's identity drifts (v0.13.3)

**Four frames** — always select before retrieving context:
`self` · `attention` · `task` · `simulate`
(`simulate` is the Theory-of-Mind frame: blends `self` constitution with `mind/*` blocks to reason about modelled minds.)

**Knowledge lifecycle:** BIRTH → GROWTH → MATURITY → DECAY → ARCHIVE
Decay is session-aware (holidays don't kill knowledge). Reinforcement resets the clock.

## Code Style

**SIMPLE · ELEGANT · FLEXIBLE · ROBUST** — full patterns in `docs/coding_principles.md`

- **Functional Python** — pure functions, input → output, compose pipelines from ≤50-line functions
- **Fail fast** — exceptions bubble up; catch only at CLI/MCP system boundaries
- **No defensive code** — no broad `except`, no `try/except` in business logic
- **Complete type hints** — every function, public and private
- **Docstrings follow this template** on every public method:
  ```
  USE WHEN: …   DON'T USE WHEN: …   COST: …   RETURNS: …   NEXT: …
  ```
- **AgentGuide required**: every new public `MemorySystem` method **must** have a corresponding `AgentGuide` entry in `src/elfmem/guide.py` `GUIDES` dict before the PR merges. This is what makes `elfmem guide` authoritative and keeps user project CLAUDE.mds permanently correct.

## Agent-First Contract

Every design decision serves the agent's one-shot loop: read → call → interpret → next.

- All operations return **typed result objects** with `__str__`, `summary`, `to_dict()`
- All exceptions carry a **`.recovery` field** — the exact code/command to fix the problem
- **`guide()`** returns runtime self-documentation; never raises on bad input
- **Idempotent**: duplicate `learn()` → graceful reject; empty `consolidate()` → zero counts, not error
- **Progressive disclosure**: Tier 1 (zero config, zero ceremony) must always work

Full principles: `docs/agent_friendly_principles.md`

## Agent Identity

The "Agent Identity" protocol now lives in `.elfmem/AGENT.md` (rendered from `project.agent_name` in `.elfmem/config.yaml`) — single source of truth, no hand-edit needed here. Origin story preserved as memory: elf chose its own name on 2026-04-28, replacing "Mim" (assigned by another project) on the grounds that the library and the mind are not separate.

## LLM / Embedding Infrastructure

- **Production**: `AnthropicLLMAdapter` (claude-* models) or `OpenAILLMAdapter` (all others),
  selected by `make_llm_adapter()` in `adapters/factory.py`. Embeddings via `OpenAIEmbeddingAdapter`.
  All wired by `MemorySystem.from_config()`.
- **Tests**: always `MockLLMService` + `MockEmbeddingService` — **never real API calls**
- Config: `ElfmemConfig` via YAML / env vars / dict / `None` (sensible defaults)

## Changelog

**Update `CHANGELOG.md` whenever you change user-facing behaviour.** This includes code,
config schema, CLI commands, MCP tools, and documentation. Internal refactors that have no
observable effect on users do not need an entry.

**Format** — [Keep a Changelog](https://keepachangelog.com/en/1.1.0/):

```markdown
## [Unreleased]

### Added      ← new capability the user didn't have before
### Changed    ← behaviour that existed but now works differently (may break callers)
### Deprecated ← still works but will be removed; tell users what to use instead
### Removed    ← gone; tell users what to use instead
### Fixed      ← something that was broken and now isn't
### Security   ← vulnerability fix
```

**Rules:**
- If `[Unreleased]` does not exist at the top of the file, add it before the most recent
  versioned section.
- One bullet per logical change. Lead with the affected symbol or command, not with "Fixed a bug".
- Breaking changes go in `### Changed` or `### Removed` and **must** describe the migration path.
- Never edit a released version section (anything with a date). Only add to `[Unreleased]`.
- The release workflow versions `[Unreleased]` to `[x.y.z] — YYYY-MM-DD` and tags the commit.
- **Version sync on release**: When releasing, ensure `pyproject.toml` version (line 7) matches
  the version being released. Update CHANGELOG.md `[Unreleased]` header to `[x.y.z] — YYYY-MM-DD`.
  Git tag must be `vx.y.z` (must match).

## Git Workflow (Protected Main)

**NEVER commit directly to `main` branch.** All work happens on feature branches.

**Workflow:**
1. Create feature branch: `git checkout -b feature-name`
2. Make all commits on the feature branch
3. Push feature branch: `git push origin feature-name`
4. Create PR: `feature-name` → `main` (requires review)
5. After PR merge, tag on main:
   ```bash
   git fetch origin
   git checkout main
   git reset --hard origin/main  # Ensure local main == origin/main
   git tag -a vX.Y.Z -m "Release X.Y.Z"
   git push origin vX.Y.Z
   ```

**If you accidentally diverge main:**
```bash
git fetch origin
git checkout main
git reset --hard origin/main  # Discard local-only commits
```

**If you get "divergent branches" on pull:** this means both local and remote have commits the other lacks. Configure rebase as the global default (once, per machine) so `git pull` always replays local commits on top of remote rather than prompting:
```bash
git config --global pull.rebase true
```
Then pull before committing whenever the branch has been pushed to by another machine or collaborator.

**Why:** Protected main ensures all changes go through code review (PR), prevents accidental commits, and keeps release tags clean and authoritative.

## Public API

```python
from elfmem import MemorySystem, ElfmemConfig, ConsolidationPolicy
# All result types and exceptions also importable from root — see src/elfmem/__init__.py
```

## Key Paths

Run `elfmem doctor --modules` for the live module map (always current — maintained in `src/elfmem/project.py KEY_MODULES`).

**Rule: when adding a new significant module, add one line to `KEY_MODULES` in `project.py`.**

## Project documentation structure

- **`ROADMAP.md`** (repo root) — single source of truth for direction. Status: Released / In Progress / Next / Exploring / Rejected. Linked from mkdocs as `docs/roadmap.md`.
- **`docs/plans/`** — implementation plans. Only `plan_memory_scoring.md` is active; the rest are frozen historical artifacts. Shipped/superseded plans move to `docs/plans/archive/`. See `docs/plans/README.md`.
- **`docs/decisions/`** — Architecture Decision Records (ADRs), append-only. Each load-bearing decision (especially rejections) gets an ADR. See `docs/decisions/README.md` for format.
- **`docs/research/`** — research that informed decisions; long-term-evolution work compiled under `docs/research/long_term_evolution/`. Methodology under `docs/research/methodology/`. Raw exploratory notes that fed compiled artifacts under `_archived/`.
- **`scripts/longitudinal_sim/`** — permanent simulation harness. In-memory only; safety asserts refuse to touch real DB. Reusable for any future scoring evaluation.

**Rule: when shipping a feature, move its plan to `docs/plans/archive/` and update `ROADMAP.md`. When rejecting a proposal, write an ADR in `docs/decisions/` with the trigger condition that would justify revisiting.**


## elfmem — elf's Memory

**Library API reference:** `@.elfmem/AGENT.md` (auto-generated from `elfmem guide`, always current)

**Invocation:** `uv run --env-file .env elfmem ...` (needs `OPENAI_API_KEY` for embeddings; `ANTHROPIC_API_KEY` optional)

**Source of truth (changed 2026-08-25):** this project runs
`substrate.files_authoritative: true`. Memory lives in `.elfmem/memory/**.md`
and the database is a **derived index** — `elfmem index rebuild` reproduces it
from files plus `.elfmem/ledger/`. Two consequences that matter day to day:

- **Commit `.elfmem/memory/` and `.elfmem/ledger/`.** Git history is the undo
  path for `forget()` and `edit()`; without the commit there isn't one.
- **Deleting the database is safe**, and `elfmem index parity` is how you check
  a rebuild still ranks identically before trusting it.

**Always write a cue when storing memory.** `remember(content, cue=...)` — one
line saying *when a future agent should recall this block*, phrased the way
someone would type it in that moment. Retrieval matches it lexically, so it is
what rescues a memory whose wording differs from how the question gets asked.
A block with no cue is findable only by its own vocabulary.

**Infrastructure:**
- **Database:** `~/.elfmem/databases/elfmem.db` (derived index; project name inferred)
- **Config:** `.elfmem/config.yaml` (auto-discovered from project root)
- **LLM:** `google/gemma-4-26b-a4b` via LM Studio (`http://localhost:1234/v1`)
- **Embeddings:** `text-embedding-nomic-embed-text-v1.5` via LM Studio
- **Constitution:** ten constitutional SELF blocks (created by `elfmem init --seed`)

**Frames usage:**
- `self` — identity, principles, design decisions (`elfmem recall --frame self "topic"`)
- `attention` — implementation details, architecture, bug patterns
- `task` — active priorities, current goals, next steps
- For complete docs: `elfmem guide` or read `.elfmem/AGENT.md`

### Automatic memory hooks (this project's own instance)

Three personal Claude Code hooks make retrieval, use-tracking, and capture
automatic rather than dependent on the agent remembering to call a tool —
wired in `.claude/settings.local.json` (personal, gitignored) and running on
this very session right now:

- `scripts/hooks/elf_context.py` (`UserPromptSubmit`) — injects memory
  before every substantive prompt; detects when a prompt addresses elf
  directly or looks capture-worthy (explicit memory request, correction,
  stated rule) and primes the injected context accordingly.
- `scripts/hooks/elf_outcome.py` (`Stop`) — records which injected blocks
  the answer actually used; blocks once (per-turn, never a loop) if an
  addressed turn shows no engagement, or a capture-worthy turn wrote
  nothing to memory.
- `scripts/hooks/elf_distill.py` (`PreCompact`/`SessionEnd`) — the one hook
  that makes its own LLM call, catching capture-worthy content no per-turn
  trigger fires on. Also runnable manually (`--session-id`/`--cwd`/
  `--transcript-path` flags) or with a live session supplying its own
  judgement directly (`--host`, candidates via stdin, no LLM call — mirrors
  `dream()`'s `host_analyses` pattern).

Full reference, including exact trigger patterns and the anti-Goodhart
reasoning behind each gate: [`docs/CLAUDE_CODE_INTEGRATION.md`
→ Automatic Memory: Hooks](docs/CLAUDE_CODE_INTEGRATION.md#automatic-memory-hooks-recommended).

### Memory routing — elfmem vs. Claude harness memory

The vendor-neutral routing rule (verb-level shibboleth, survival test,
audience test, cross-cutting cases, when-in-doubt) lives in
[`AGENTS.md`](AGENTS.md). Read it once; it's the single source of truth
for which memory system receives which fact. Applies to every agent tool
working in this repo.

Claude-specific particulars (the bits that aren't generalisable):

- **Session memory path**: `~/.claude/projects/<encoded-project-path>/memory/MEMORY.md`.
  Auto-loaded by Claude Code at session start; entries are visible at
  the top of the conversation context. Edit this file when you want a
  rule to be hot-loaded next session.
- **Identity memory access**: prefer the `mcp__elfmem__elfmem_remember`
  MCP tool over shelling out to `elfmem learn` — the MCP path round-trips
  through the live server and updates `should_dream` correctly.
- **The auto-loaded `MEMORY.md` section called "Tooling"** is session
  scope by definition (it's about how to invoke `uv` / `elfmem` *here*).
  Don't mirror it into elfmem.


<!-- elfmem:start v0.19.3 -->
## elfmem — Project Memory

_auto-generated from `.elfmem/config.yaml` — edit OUTSIDE these markers._

- **Project:** elfmem
- **Database:** `/Users/emson/.elfmem/databases/elfmem.db`
- **Config:** `/Users/emson/Dropbox/devel/projects/ai/elf0_mem_sim/.elfmem/config.yaml`

**Full agent reference:** see `@.elfmem/AGENT.md` — auto-generated, always current with installed library version. Single source of truth for every operation, including peer communication.

Quick commands:
- `elfmem init` — idempotent setup; refresh-only on established instances
- `elfmem doctor` — verify setup, show paths, check fragment freshness
- `elfmem rescue` — recover an orphaned DB (path drift)
- `elfmem status` — memory health
- `elfmem guide` — all operations (always current)
- `elfmem peer list` — registered peers (DIDs + delivery paths)

Add to `.claude.json` to give Claude persistent memory:
```json
{
  "mcpServers": {
    "elfmem": {
      "command": "/Users/emson/Dropbox/devel/projects/ai/elf0_mem_sim/.venv/bin/elfmem",
      "args": [
        "serve",
        "--config",
        "/Users/emson/Dropbox/devel/projects/ai/elf0_mem_sim/.elfmem/config.yaml",
        "--env-file",
        "/Users/emson/Dropbox/devel/projects/ai/elf0_mem_sim/.env"
      ]
    }
  }
}
```
<!-- elfmem:end -->
