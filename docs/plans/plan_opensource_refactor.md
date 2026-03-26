# elfmem: Open-Source Launch Refactor Plan

**Date:** 2026-03-26
**Scope:** Full assessment and action plan for publishing elfmem to PyPI as a professional,
well-respected open-source project.

---

## 1. Executive Summary

elfmem is a technically excellent, well-engineered memory system for LLM agents. The core
architecture is sound, the code quality is high (strict mypy, ruff, 400+ tests, complete type
hints), and the documentation is unusually comprehensive. The CI/CD pipeline, CONTRIBUTING.md,
SECURITY.md, CHANGELOG.md, and PyPI publish workflow are already in place.

Two substantive code changes are required before any PyPI publish:

1. **Remove LiteLLM and instructor entirely.** The LiteLLM project has documented security
   vulnerabilities. More importantly, elfmem makes exactly four method calls into the LLM
   layer — replacing LiteLLM and instructor with two thin official-SDK adapters is simpler,
   more secure, and eliminates a large and fast-moving transitive dependency tree.

2. **Remove SmartMemory entirely.** There are no external users. Shipping a deprecated shim
   in the first public release creates confusion and maintenance debt for no benefit.

Beyond these two code changes, the remaining gaps are surface area: a cluttered root
directory, no hosted documentation site, no issue/PR templates, no coverage reporting, and an
[Unreleased] CHANGELOG section that needs versioning before publish.

This plan is prioritized into three tiers:

- **P0:** Must fix before any PyPI publish
- **P1:** Fix before or immediately after first publish (within days)
- **P2:** Improve over the following weeks

Estimated effort to reach P0: 3-4 days. P1: 3-5 days additional. P2: ongoing.

---

## 2. Problem Statement Assessment

### 2.1 Does elfmem solve a real, well-defined problem?

**Yes.** The problem is clearly stated and genuinely underserved:

> LLM agents are stateless by default. Every conversation starts from zero. Context windows
> are finite. RAG retrieves documents but does not learn. Existing memory libraries either
> require external infrastructure (vector databases, Redis, Postgres) or provide only simple
> key-value stores with no lifecycle management.

elfmem solves this with a single-file, zero-infrastructure system that implements:
- Adaptive decay (knowledge fades when unused; survives weekends via session-aware clock)
- Hebbian reinforcement (knowledge that gets used survives longer)
- A knowledge graph (semantic connections, not just similarity search)
- Identity persistence (the SELF frame)
- Domain-agnostic Bayesian confidence updates (outcome feedback)
- Contradiction detection (LLM-powered; newer knowledge wins)

### 2.2 Is the problem clearly communicated?

**Partially.** The README is very comprehensive but buries the core problem statement. A
first-time visitor arrives at a code example and feature list before understanding *why* this
exists and *how* it differs from alternatives like mem0, LangChain Memory, or Zep.

The "three rhythms" mental model is memorable and elegant. It should be introduced *earlier*,
with a clear "Without elfmem, X. With elfmem, Y." statement at the top.

### 2.3 Is the documentation consistent with the implementation?

**Mostly yes**, with two gaps:
- `CHANGELOG.md` has a long `[Unreleased]` section that includes major features not in the
  0.1.0 release entry (visualization, Hebbian learning, ConsolidationPolicy, MCP tools). This
  mismatch confuses users about what version they are running.
- `SETUP_QUICK_FIX.md` at the repo root exposes an internal development friction (uv
  self-dependency) that is irrelevant to PyPI users and signals instability to contributors.

---

## 3. Current State Assessment

### 3.1 What is already in excellent shape

| Area | Status | Notes |
|------|--------|-------|
| Source code architecture | Excellent | 4-layer model, clean boundaries, pure functions |
| Protocol boundaries | Excellent | `LLMService` / `EmbeddingService` ports enable adapter swap |
| Type safety | Excellent | mypy strict mode throughout |
| Test coverage | Excellent | 400+ tests, mock-first, deterministic |
| Agent-friendly API | Excellent | Result types with `__str__`/`.summary`/`.to_dict()` |
| Exception design | Excellent | `.recovery` field on every exception |
| Scoring system | Excellent | Frozen, pure, mathematically sound |
| Configuration | Excellent | Pydantic models, YAML/env/dict support |
| Session-aware decay | Excellent | Monotonic clock, survives downtime |
| CI/CD pipeline | Good | GitHub Actions, both ci.yml and publish.yml present |
| PyPI packaging | Good | hatchling, OIDC trusted publishing configured |
| CHANGELOG | Present | Keep a Changelog format; needs versioning work |
| CONTRIBUTING.md | Good | Clear, concise, sets expectations |
| SECURITY.md | Present | Needs content review |
| LICENSE | Present | MIT |
| Examples | Good | calibrating_agent.py, decision_maker.py |

### 3.2 Gaps identified

The gaps below are organized by category and priority.

---

## 4. Gap Analysis and Recommendations

### 4.1 LiteLLM and instructor: Remove and Replace with Native Adapters (P0)

#### 4.1.1 Why remove

The LiteLLM project has documented security vulnerabilities. The `litellm` PyPI package
includes gateway proxy code, admin API routes, and a large transitive dependency tree even
when used purely as a library. This makes each `litellm` release a significant supply-chain
surface. Additionally, LiteLLM breaks its own API between minor versions — a real risk for a
library that needs stable dependencies.

`instructor` is a thin convenience layer over LiteLLM for structured outputs. Removing LiteLLM
removes the reason to have instructor.

Both dependencies should be cut entirely before the first PyPI publish.

#### 4.1.2 What elfmem actually needs from the LLM layer

The LLM layer serves exactly four method signatures defined in `ports/services.py`:

```
LLMService.process_block(block, self_context) → BlockAnalysis
LLMService.detect_contradiction(block_a, block_b) → float

EmbeddingService.embed(text) → np.ndarray
EmbeddingService.embed_batch(texts) → list[np.ndarray]
```

These four methods are the entire interface. The existing Protocol boundary is already correct.
Only the concrete adapter (`adapters/litellm.py`) needs to change.

#### 4.1.3 Provider landscape and the two-adapter model

Most LLM providers in 2026 follow one of two API shapes:

- **OpenAI-compatible:** `POST /v1/chat/completions` and `POST /v1/embeddings`. Covers
  OpenAI, Ollama, Together AI, Groq, Mistral, and most new entrants. The official `openai`
  Python SDK supports all of these via the `base_url` parameter.

- **Anthropic:** `POST /v1/messages`. The official `anthropic` Python SDK.

Both official SDKs are security-maintained by the provider, have stable APIs, have lean
transitive dependency trees, and return token usage natively. Neither requires a third-party
aggregator.

This maps directly to two concrete adapters:

| Adapter | SDK | Covers |
|---------|-----|--------|
| `AnthropicLLMAdapter` | `anthropic.AsyncAnthropic` | All Claude models |
| `OpenAILLMAdapter` | `openai.AsyncOpenAI` | OpenAI, Ollama, Together, Groq, Mistral, any OpenAI-compatible API |
| `OpenAIEmbeddingAdapter` | `openai.AsyncOpenAI` | OpenAI embeddings, Ollama embeddings, any OpenAI-compatible embedding API |

Anthropic does not offer an embedding API; `OpenAIEmbeddingAdapter` is the only embedding
adapter needed.

#### 4.1.4 Structured outputs without instructor

elfmem uses instructor for two structured outputs: `BlockAnalysisModel` (alignment score,
tags, summary) and `ContradictionScore` (float). Both are simple. Both official SDKs support
native structured outputs without a wrapper library:

**Anthropic** uses tool use with forced tool choice. The Pydantic model schema is converted
to a tool input schema. The SDK guarantees a valid JSON response matching the schema:
```python
response = await client.messages.create(
    model=model,
    tools=[{"name": "analyze", "input_schema": BlockAnalysisModel.model_json_schema()}],
    tool_choice={"type": "tool", "name": "analyze"},
    messages=[{"role": "user", "content": prompt}],
)
result = BlockAnalysisModel.model_validate(response.content[0].input)
```

**OpenAI** (GPT-4o and newer) uses the structured outputs beta which guarantees schema
compliance. Older models use JSON mode with Pydantic parsing:
```python
# Structured outputs (GPT-4o, GPT-4o-mini, newer):
response = await client.beta.chat.completions.parse(
    model=model,
    response_format=BlockAnalysisModel,
    messages=[{"role": "user", "content": prompt}],
)
result = response.choices[0].message.parsed

# JSON mode fallback (older models, Ollama):
response = await client.chat.completions.create(
    model=model,
    response_format={"type": "json_object"},
    messages=[{"role": "user", "content": prompt}],
)
result = BlockAnalysisModel.model_validate_json(response.choices[0].message.content)
```

Both approaches eliminate instructor. Retry logic is handled by the SDK's built-in
`max_retries` parameter, not by instructor's retry mechanism.

#### 4.1.5 Token counting

Both SDKs return token usage on every response:
- OpenAI: `response.usage.prompt_tokens`, `response.usage.completion_tokens`
- Anthropic: `response.usage.input_tokens`, `response.usage.output_tokens`

This maps directly to the existing `TokenCounter.record_llm()` and `record_embedding()`
calls, with minor field name adjustments per provider.

#### 4.1.6 Auto-detection factory

`MemorySystem.from_config()` currently constructs `LiteLLMAdapter` directly. Replace this
with a factory function in `adapters/factory.py`:

```python
def make_llm_adapter(config: LLMConfig, token_counter: TokenCounter | None) -> LLMService:
    """Select the correct LLM adapter based on the model name prefix."""
    if config.model.startswith("claude"):
        return AnthropicLLMAdapter(config, token_counter=token_counter)
    return OpenAILLMAdapter(config, token_counter=token_counter)

def make_embedding_adapter(
    config: EmbeddingConfig, token_counter: TokenCounter | None
) -> EmbeddingService:
    """All embedding providers use the OpenAI-compatible adapter."""
    return OpenAIEmbeddingAdapter(config, token_counter=token_counter)
```

Detection logic: `"claude-*"` model names go to `AnthropicLLMAdapter`; everything else goes
to `OpenAILLMAdapter`. This covers all current documented use cases. If a future provider
needs a separate adapter, extend the factory.

#### 4.1.7 Config changes

The `LLMConfig` and `EmbeddingConfig` Pydantic models in `config.py` need no structural
changes. All existing fields (`model`, `temperature`, `max_tokens`, `timeout`, `base_url`,
`process_block_model`, `contradiction_model`) are preserved. The default model
`"claude-haiku-4-5-20251001"` maps to `AnthropicLLMAdapter` via the factory. OpenAI models
(`"gpt-4o-mini"`, `"gpt-4o"`) and Ollama models (`"llama3.2"` with `base_url`) map to
`OpenAILLMAdapter`.

One minor config clarification: rename `api_base` → `base_url` at the adapter level for
consistency (already named `base_url` in `LLMConfig`; the LiteLLM adapter internally mapped
this to `api_base` in its kwargs — that internal rename disappears).

#### 4.1.8 File changes in src/elfmem/adapters/

```
Before:
  adapters/
    litellm.py          ← DELETE (LiteLLMAdapter + LiteLLMEmbeddingAdapter)
    mock.py             ← UNCHANGED
    models.py           ← UNCHANGED (BlockAnalysisModel, ContradictionScore)

After:
  adapters/
    anthropic.py        ← NEW (AnthropicLLMAdapter)
    openai.py           ← NEW (OpenAILLMAdapter + OpenAIEmbeddingAdapter)
    factory.py          ← NEW (make_llm_adapter, make_embedding_adapter)
    mock.py             ← UNCHANGED
    models.py           ← UNCHANGED
```

Update `src/elfmem/api.py`: replace `LiteLLMAdapter`/`LiteLLMEmbeddingAdapter` construction
in `from_config()` with calls to `make_llm_adapter()` and `make_embedding_adapter()`.

#### 4.1.9 Dependency changes

| Package | Before | After | Reason |
|---------|--------|-------|--------|
| `litellm` | `>=1.30` (required) | **REMOVED** | Security; replaced by official SDKs |
| `instructor` | `>=1.2` (required) | **REMOVED** | Only needed for litellm integration |
| `openai` | transitive | `>=1.50` (required) | Official SDK for OpenAI + compatible APIs |
| `anthropic` | transitive | `>=0.40` (required) | Official SDK for Claude models |

Net effect: the direct dependency count drops by two. The transitive dependency tree shrinks
significantly — litellm pulls in ~20 transitive packages, many security-relevant (httpx
plugins, proxy server code, tiktoken, etc.).

#### 4.1.10 Tests

The mock adapters (`MockLLMService`, `MockEmbeddingService`) and all test fixtures in
`conftest.py` are untouched. Every test that currently passes continues to pass. The
adapters tested by `tests/test_mock_adapters.py` are unchanged.

No new tests for the concrete adapters are needed at this stage — the Protocol boundary
is the contract; the adapters are thin wrappers. If integration tests are added later,
they belong in a separate `tests/integration/` directory marked to skip without API keys.

---

### 4.2 SmartMemory: Remove Entirely (P0)

`src/elfmem/smart.py` is a deprecated compatibility shim over `MemorySystem`. There are no
external users. Shipping a deprecated class in the first public release creates confusion
("which should I use?") and maintenance surface for no benefit.

**Action:** Remove `smart.py` without a deprecation path.

- Delete `src/elfmem/smart.py`
- Remove `SmartMemory` export from `src/elfmem/__init__.py`
- Update `tests/conftest.py`: replace any `SmartMemory` fixtures with `MemorySystem` directly
- Update any remaining test files that use `SmartMemory`
- Add a single CHANGELOG entry: "Removed SmartMemory (deprecated shim; use MemorySystem)"

This is a clean cut. There are no users to break.

---

### 4.3 CHANGELOG and Version Management (P0)

**Problem:** The `[Unreleased]` section in `CHANGELOG.md` contains a large set of features
that are clearly implemented and merged (visualization, Hebbian learning, ConsolidationPolicy,
10 MCP tools, batch embedding, token tracking, etc.). The pyproject.toml still says
`version = "0.1.0"`.

**Recommendation:**
1. Rename `[Unreleased]` to `[0.2.0] - 2026-03-26` in CHANGELOG.md
2. Add the SmartMemory removal and the LiteLLM → native adapters change to this entry
3. Bump `version = "0.2.0"` in pyproject.toml
4. Tag the release `v0.2.0` in git immediately before PyPI publish
5. Add a brief note explaining that 0.1.0 was pre-publication and 0.2.0 is the first
   public release

The Alpha classifier is fine to keep (Development Status :: 3 - Alpha) until the API
stabilizes across real-world usage.

---

### 4.4 Root Directory Cleanup (P0)

**Problem:** The repo root is cluttered with files that confuse a first-time visitor on GitHub.
A professional open-source project should have a clean, predictable root.

**Current root contains files that should move:**
- `SETUP_QUICK_FIX.md` — internal dev friction (uv self-dependency); irrelevant to PyPI
  users; signals instability to new contributors
- `SIMULATION_OVERVIEW.md` — 15KB philosophy document; valuable but misplaced at root
- `START_HERE.md` — navigation guide whose role is filled by the README on GitHub/PyPI
- `QUICKSTART.md` — should live in docs/ and be linked from README

**Recommendation:**

Move to `docs/`:
- `SIMULATION_OVERVIEW.md` → `docs/design/simulation_overview.md`
- `START_HERE.md` → remove (README is the entry point for external visitors)
- `QUICKSTART.md` → `docs/quickstart.md` (update links in README)
- `SETUP_QUICK_FIX.md` → `docs/dev/setup_local.md` (reframe as local dev notes)

Keep at root (standard open-source layout):
```
README.md  CHANGELOG.md  CONTRIBUTING.md  SECURITY.md  CODE_OF_CONDUCT.md  LICENSE
pyproject.toml  uv.lock  .python-version  mkdocs.yml
alembic/  alembic.ini  src/  tests/  docs/  examples/  scripts/  sim/
```

---

### 4.5 docs/ Directory Cleanup (P0)

**Problem:** The `docs/` directory mixes user-facing documentation with internal research
notes and brainstorming files. Documentation tools and first-time contributors expect a
clean hierarchy.

**Files to remove or archive (internal notes with no user value):**
- `brainstorm_adaptive_intelligence.md`
- `brainstorm_outcome_feedback.md`
- `note_elfmem_trading_example.md`
- `note_team_steps.md`
- `notes.md`
- `amgs_instructions.md`
- `DREAMING_IMPLEMENTATION_SUMMARY.md` (content is in CHANGELOG)
- `SETUP_COMPLETE.md` (setup checkpoint)
- `prompt_ab_testing.md`, `prompt_team_01.md` (internal prompt experiments)

**Proposed clean docs/ structure:**
```
docs/
  index.md                       # Entry point
  quickstart.md                  # Moved from root
  how_it_works.md                # Three rhythms, frames, lifecycle
  configuration.md               # Config reference (providers, YAML format, env vars)
  api_reference.md               # Links to auto-generated reference
  agents.md                      # Building agents guide
  mcp.md                         # MCP server guide
  cli.md                         # CLI reference
  visualization.md               # Visualization guide
  architecture/
    amgs_architecture.md
    agent_friendly_principles.md
    coding_principles.md
    simulation_overview.md       # Moved from root
  design/
    README.md                    # What these plans are
    (all plans/ content)
  dev/
    setup_local.md               # Developer setup (replaces SETUP_QUICK_FIX.md)
    testing_principles.md
```

The `docs/plans/` directory is valuable for contributors — it shows design thinking. Keep it,
but add a `README.md` explaining that plans are frozen design-time records.

---

### 4.6 README.md: Problem Statement and Positioning (P0)

**Problem:** The README is comprehensive but starts with a code example before explaining the
problem being solved. A first-time visitor does not immediately understand why elfmem exists
or how it differs from alternatives.

**Missing from README:**
1. A crisp problem statement before any code
2. A "why elfmem" section framing the three rhythms as the solution
3. Comparison to alternatives (mem0, LangChain Memory, Zep, custom RAG)
4. Prominent badges

**Recommendation:** Restructure the first ~60 lines:

```markdown
# elfmem

[![CI](badge)] [![PyPI](badge)] [![Python 3.11+](badge)] [![MIT](badge)]

**Adaptive memory for LLM agents. Knowledge that gets used survives. Knowledge
that doesn't fades away. One file, zero infrastructure.**

## The problem

LLM agents are stateless. Every session starts from zero. Context windows fill up.
RAG retrieves documents but does not learn from them. Most memory libraries require
infrastructure (vector databases, Redis, Postgres) or offer only simple key-value
stores with no concept of relevance decay.

## The solution: three rhythms

[code example] → [three rhythms diagram] → [why elfmem table] → [features] → ...
```

Add a brief comparison table:

| Feature | elfmem | mem0 | LangChain Memory | Chroma/Weaviate |
|---------|--------|------|-----------------|-----------------|
| Infrastructure required | None (SQLite) | Postgres/Redis | In-memory | Vector DB server |
| Adaptive decay | Yes | No | No | No |
| Knowledge graph | Yes | No | No | No |
| Contradiction detection | Yes | No | No | No |
| Session-aware clock | Yes | No | No | No |
| MCP native | Yes | No | No | No |
| Official SDK only | Yes | No | Varies | No |

Note the "Official SDK only" row — this directly communicates the LiteLLM removal as a
feature, not just an absence.

---

### 4.7 README.md: Badges (P1)

**Missing:** No badges. Badges communicate project health at a glance for new visitors.

Add to README.md header:
```markdown
[![CI](https://github.com/emson/elfmem/actions/workflows/ci.yml/badge.svg)](...)
[![PyPI version](https://badge.fury.io/py/elfmem.svg)](...)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](...)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](...)
[![Coverage](https://codecov.io/gh/emson/elfmem/badge.svg)](...)
```

---

### 4.8 CI/CD Improvements (P1)

**Problems:**

1. No coverage reporting. Without a coverage badge, users cannot assess test quality.
2. `publish.yml` only runs tests on Python 3.11, not both 3.11 and 3.12 (unlike ci.yml).
3. No scheduled CI run to catch breakage from dependency updates.
4. mypy uses `--ignore-missing-imports` which suppresses real type errors.
5. `ci.yml` does not verify the package builds (`uv build`).

**Recommendations:**

Add to `ci.yml`:
```yaml
- name: Coverage (pytest-cov)
  run: uv run pytest --cov=elfmem --cov-report=xml -q

- name: Upload coverage
  uses: codecov/codecov-action@v4
  with:
    file: coverage.xml

- name: Build check
  run: uv build
```

Fix `publish.yml` to use the same Python version matrix as `ci.yml`.

Add `pytest-cov` to dev dependencies in pyproject.toml.

Add a weekly scheduled run to catch dep drift:
```yaml
on:
  schedule:
    - cron: '0 8 * * 1'  # every Monday at 08:00 UTC
```

---

### 4.9 pyproject.toml Improvements (P1)

**Problems and recommendations:**

1. **Dependencies:** After the LiteLLM removal, the core dependency list becomes:
   ```toml
   dependencies = [
       "sqlalchemy>=2.0",
       "aiosqlite>=0.19",
       "alembic>=1.13",
       "pydantic>=2.0",
       "numpy>=1.26",
       "openai>=1.50,<2.0",     # replaces litellm (OpenAI + compatible APIs)
       "anthropic>=0.40,<1.0",  # replaces litellm (Claude models)
       "pyyaml>=6.0",
       # greenlet removed — transitive dep of SQLAlchemy, not a direct dependency
   ]
   ```
   `litellm`, `instructor`, and `greenlet` are removed.
   `openai` and `anthropic` are added with upper bounds.
   `fastmcp` should also get an upper bound: `fastmcp>=2.0,<3.0`.

2. **No `Documentation` URL:** Add `Documentation = "https://emson.github.io/elfmem"` to
   `[project.urls]`.

3. **Missing `Changelog` URL:** Add
   `Changelog = "https://github.com/emson/elfmem/blob/main/CHANGELOG.md"`.

4. **Keywords too narrow:** Expand for discoverability:
   ```toml
   keywords = [
       "memory", "llm", "agents", "rag", "sqlite",
       "adaptive", "cognitive", "mcp", "knowledge-graph",
       "autonomous-agents", "anthropic", "openai", "embeddings",
   ]
   ```

5. **`py.typed` marker:** Create `src/elfmem/py.typed` (empty file). Without it, downstream
   type checkers ignore elfmem's inline types (PEP 561).

6. **Python 3.13:** Add to classifiers and CI matrix after verifying
   `openai` and `anthropic` SDKs pass on 3.13 (they do as of March 2026).

7. **Additional classifiers:**
   ```toml
   "Topic :: Software Development :: Libraries :: Python Modules",
   "Programming Language :: Python :: 3.13",
   ```

---

### 4.10 GitHub Repository Setup (P1)

**Missing:**

1. **Issue templates** (`.github/ISSUE_TEMPLATE/`): No templates. Low-quality issues with
   missing context are harder to triage.

2. **PR template** (`.github/pull_request_template.md`): No checklist for contributors.

3. **CODE_OF_CONDUCT.md**: Expected by the open-source community. Contributor Covenant 2.1
   is the standard boilerplate.

4. **Dependabot** (`.github/dependabot.yml`): Auto-update PRs for `openai` and `anthropic`
   which both release regularly.

5. **Repository topics on GitHub:** Add via GitHub UI: `memory`, `llm`, `agents`, `sqlite`,
   `mcp`, `adaptive-memory`, `knowledge-graph`, `anthropic`, `openai`.

**Recommended files:**

`.github/ISSUE_TEMPLATE/bug_report.md`:
```markdown
---
name: Bug report
about: Something isn't working
labels: bug
---

**elfmem version:**
**Python version:**
**LLM provider and model:**
**How to reproduce:**
**Expected behaviour:**
**Actual behaviour:**
**Relevant config (sanitize API keys):**
```

`.github/pull_request_template.md`:
```markdown
## Summary

## Type of change
- [ ] Bug fix
- [ ] New feature
- [ ] Documentation

## Checklist
- [ ] `uv run pytest -q` passes
- [ ] `uv run ruff check src/ tests/` passes
- [ ] `uv run mypy --ignore-missing-imports src/elfmem/` passes
- [ ] CHANGELOG.md updated (under [Unreleased])
```

`.github/dependabot.yml`:
```yaml
version: 2
updates:
  - package-ecosystem: "pip"
    directory: "/"
    schedule:
      interval: "weekly"
    open-pull-requests-limit: 5
  - package-ecosystem: "github-actions"
    directory: "/"
    schedule:
      interval: "weekly"
```

---

### 4.11 Documentation Site (P1)

**Problem:** The project has extensive documentation in `docs/` but no hosted documentation
site. A project of this quality without a documentation site signals it is not yet
production-ready.

**Recommendation:** MkDocs with the Material theme (de-facto standard for Python projects).

Add to `pyproject.toml` dev dependencies:
```toml
"mkdocs-material>=9.0",
"mkdocs-autorefs>=0.5",
"mkdocstrings[python]>=0.24",
```

Add `mkdocs.yml` to repo root:
```yaml
site_name: elfmem
site_description: Adaptive memory for LLM agents
site_url: https://emson.github.io/elfmem
repo_url: https://github.com/emson/elfmem
repo_name: emson/elfmem

theme:
  name: material
  palette:
    primary: indigo

nav:
  - Home: index.md
  - Quick Start: quickstart.md
  - How It Works: how_it_works.md
  - Configuration: configuration.md
  - Interfaces:
    - Python API: api.md
    - MCP Server: mcp.md
    - CLI: cli.md
  - Building Agents: agents.md
  - API Reference: reference/
  - Architecture: architecture/amgs_architecture.md
  - Contributing: contributing.md
  - Changelog: https://github.com/emson/elfmem/blob/main/CHANGELOG.md
```

Add `.github/workflows/docs.yml`:
```yaml
name: Deploy Docs
on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync --extra dev
      - run: uv run mkdocs gh-deploy --force
```

---

### 4.12 API Stability Declaration (P1)

**Recommendation:** Add an "API Stability" section to README.md:

```markdown
## API stability

**Stable (no breaking changes within 0.x):**
- `MemorySystem` public methods
- All result types in `elfmem.types`
- All exception types in `elfmem.exceptions`
- `ElfmemConfig`, `ConsolidationPolicy`

**Internal (may change without notice):**
- `elfmem.operations.*`, `elfmem.memory.*`, `elfmem.db.*`, `elfmem.context.*`
- `elfmem.adapters.*` (adapter implementations; the Protocol in `elfmem.ports` is stable)
- All private methods (`_*`)
- Database schema (migrate with `alembic upgrade head`)
```

---

### 4.13 Embedding Model Migration Warning (P1)

**Problem:** Once the embedding model is set, it cannot be changed without re-embedding all
blocks. A user who changes `embeddings.model` on an existing database gets silent errors.

**Recommendation:**

1. Store the embedding model name in `system_config` on first use (currently stored
   per-block in `blocks.embedding_model` but not at the system level).
2. In `MemorySystem.from_config()`, check if `system_config.embedding_model` is set and
   differs from the configured model — raise `ConfigError` with a `.recovery` hint.
3. Add a prominent warning in the README Configuration section.
4. Document a manual re-embedding procedure (or add an `elfmem migrate-embeddings` CLI
   command in a later release).

---

### 4.14 sim/ Directory in Public Repo (P2)

The `sim/` directory contains 26 design explorations with mathematical proofs and decision
rationale. These are unusual for an open-source project but add real credibility — they show
that the system is principled, not assembled from intuition.

**Recommendation:** Keep `sim/` but add `sim/README.md` explaining:
- What these explorations are (design decision records with mathematical proofs)
- That they are frozen design-time artifacts, not active code
- That the scoring formula, decay model, and edge weights derive from specific explorations

This transparency is a genuine differentiator. Very few projects publish their design math.

---

### 4.15 SECURITY.md Content (P2)

Review the existing `SECURITY.md` to ensure it covers:
- How to report a vulnerability (private disclosure mechanism, e.g. GitHub private advisory)
- What constitutes a security issue in elfmem's context
- Response time commitments
- The primary security concern specific to elfmem: **prompt injection via learned knowledge
  blocks**. A malicious actor who can inject knowledge into the memory store could influence
  an agent's future behaviour. This threat model should be acknowledged explicitly.

---

### 4.16 Python 3.13 Support (P2)

Add Python 3.13 to `ci.yml` matrix and `pyproject.toml` classifiers. The `openai` and
`anthropic` SDKs both support 3.13. Verify with:

```yaml
matrix:
  python-version: ["3.11", "3.12", "3.13"]
```

---

## 5. Prioritized Action Plan

### P0: Must complete before any PyPI publish

| # | Action | File(s) | Effort |
|---|--------|---------|--------|
| 1 | Write `AnthropicLLMAdapter` using `anthropic` SDK | `adapters/anthropic.py` | 3 hrs |
| 2 | Write `OpenAILLMAdapter` + `OpenAIEmbeddingAdapter` using `openai` SDK | `adapters/openai.py` | 3 hrs |
| 3 | Write adapter factory | `adapters/factory.py` | 30 min |
| 4 | Update `api.py` to use the factory instead of `LiteLLMAdapter` | `api.py` | 30 min |
| 5 | Delete `adapters/litellm.py` | `adapters/litellm.py` | 5 min |
| 6 | Remove `litellm` and `instructor` from dependencies | `pyproject.toml` | 10 min |
| 7 | Add `openai>=1.50,<2.0` and `anthropic>=0.40,<1.0` to dependencies | `pyproject.toml` | 10 min |
| 8 | Verify full test suite passes (all tests use mocks; should be green) | `tests/` | 30 min |
| 9 | Delete `src/elfmem/smart.py` | `smart.py` | 5 min |
| 10 | Remove `SmartMemory` from `__init__.py` | `__init__.py` | 10 min |
| 11 | Update `conftest.py` and any tests that reference `SmartMemory` | `tests/` | 1 hr |
| 12 | Version the `[Unreleased]` CHANGELOG section as 0.2.0 | `CHANGELOG.md` | 30 min |
| 13 | Bump `version = "0.2.0"` in pyproject.toml | `pyproject.toml` | 5 min |
| 14 | Write README "Problem Statement / Why elfmem" section with comparison table | `README.md` | 2 hrs |
| 15 | Move `SETUP_QUICK_FIX.md` → `docs/dev/setup_local.md` | root | 10 min |
| 16 | Move `SIMULATION_OVERVIEW.md` → `docs/design/` | root | 10 min |
| 17 | Move `QUICKSTART.md` → `docs/`, update README links | root | 20 min |
| 18 | Remove `START_HERE.md` from root (README serves this role) | root | 5 min |
| 19 | Remove or archive internal research files from `docs/` | `docs/` | 1 hr |
| 20 | Remove `greenlet` from direct dependencies | `pyproject.toml` | 5 min |

### P1: Complete within days of first publish

| # | Action | File(s) | Effort |
|---|--------|---------|--------|
| 21 | Add README badges (CI, PyPI, Python, License, Coverage) | `README.md` | 30 min |
| 22 | Add Documentation and Changelog URLs to pyproject.toml | `pyproject.toml` | 15 min |
| 23 | Expand keywords in pyproject.toml | `pyproject.toml` | 15 min |
| 24 | Add `py.typed` marker file | `src/elfmem/py.typed` | 5 min |
| 25 | Add upper bound `fastmcp>=2.0,<3.0` | `pyproject.toml` | 5 min |
| 26 | Create `.github/ISSUE_TEMPLATE/bug_report.md` | `.github/` | 20 min |
| 27 | Create `.github/ISSUE_TEMPLATE/feature_request.md` | `.github/` | 15 min |
| 28 | Create `.github/pull_request_template.md` | `.github/` | 15 min |
| 29 | Create `CODE_OF_CONDUCT.md` | root | 10 min |
| 30 | Create `.github/dependabot.yml` | `.github/` | 15 min |
| 31 | Add `pytest-cov` to dev deps and configure coverage in ci.yml | `pyproject.toml`, `ci.yml` | 30 min |
| 32 | Add codecov upload step to CI | `ci.yml` | 20 min |
| 33 | Add build check step to `ci.yml` (`uv build`) | `ci.yml` | 10 min |
| 34 | Fix publish.yml to use Python version matrix | `publish.yml` | 10 min |
| 35 | Add API stability declaration to README | `README.md` | 30 min |
| 36 | Add embedding model change warning to README and `from_config()` | `README.md`, `api.py` | 1 hr |
| 37 | Configure MkDocs Material and set up GitHub Pages workflow | `mkdocs.yml`, `docs.yml` | 3 hrs |
| 38 | Write `docs/index.md` as the MkDocs entry point | `docs/index.md` | 1 hr |

### P2: Ongoing improvement

| # | Action | File(s) | Effort |
|---|--------|---------|--------|
| 39 | Add Python 3.13 to CI matrix and classifiers | `ci.yml`, `pyproject.toml` | 30 min |
| 40 | Add `sim/README.md` explaining design explorations | `sim/README.md` | 30 min |
| 41 | Add `docs/design/README.md` explaining plans | `docs/design/README.md` | 20 min |
| 42 | Add `elfmem migrate-embeddings` CLI command | `src/elfmem/cli.py` | 4 hrs |
| 43 | Review and update SECURITY.md (add prompt injection threat model) | `SECURITY.md` | 1 hr |
| 44 | Add Ollama quickstart to docs (local model, no API key) | `docs/configuration.md` | 30 min |
| 45 | Add GitHub repository topics via GitHub UI | GitHub | 10 min |
| 46 | Set up TestPyPI publish step for staging verification | `publish.yml` | 30 min |
| 47 | Weekly scheduled CI run | `ci.yml` | 10 min |

---

## 6. Success Criteria

A high-quality PyPI launch means:

**Technical:**
- [ ] `pip install elfmem` works on Python 3.11 and 3.12
- [ ] `pip install 'elfmem[tools]'` installs CLI and MCP server cleanly
- [ ] `elfmem init && elfmem remember "test" && elfmem recall "test"` works end-to-end
  with both `ANTHROPIC_API_KEY` and `OPENAI_API_KEY`
- [ ] Full test suite passes in CI on both Python versions
- [ ] mypy strict passes
- [ ] ruff passes
- [ ] No `litellm` or `instructor` in the dependency tree
- [ ] `SmartMemory` is not importable from `elfmem`
- [ ] No open P0 or P1 issues

**Discoverability:**
- [ ] PyPI listing shows: description, keywords, Documentation URL, Changelog URL, Python versions
- [ ] GitHub repository has CI badge, PyPI badge, coverage badge in README
- [ ] Documentation site live and linked from PyPI

**Community:**
- [ ] `CODE_OF_CONDUCT.md` present
- [ ] Issue templates for bug reports and feature requests
- [ ] PR template with checklist
- [ ] Dependabot enabled

**Documentation:**
- [ ] README opens with a problem statement before showing code
- [ ] Three rhythms mental model explained within first screen
- [ ] Comparison to alternatives present (including "official SDK only" row)
- [ ] Embedding model warning is prominent
- [ ] API stability declaration present
- [ ] Documentation site renders all key sections

---

## 7. What NOT to Change

1. **The three rhythms model** is elfmem's core identity. Do not dilute it.
2. **The Protocol boundary** (`LLMService`, `EmbeddingService`) is the right abstraction
   and stays unchanged. Only the concrete adapters change.
3. **The mock adapters** (`MockLLMService`, `MockEmbeddingService`) and all 400+ tests are
   untouched. The adapter swap is invisible to the test suite.
4. **The `sim/` directory** with mathematical explorations adds credibility. Keep it.
5. **The agent-friendly principles** (result types with `__str__`, `.recovery` on exceptions)
   are the key differentiator vs generic memory libraries. Never regress.
6. **The scoring system** is frozen by design. Do not change scoring weights or decay
   constants without a new exploration in `sim/`.
7. **SQLite-only in Phase 1** — resist adding Postgres/Redis support until the SQLite path
   is polished and the user base validates demand.
8. **The `guide()` method** — runtime self-documentation is a genuinely novel feature for
   agent integrations. Keep it current as new methods are added.

---

## 8. Recommended Launch Sequence

1. **Branch:** Create `release/0.2.0` from main
2. **Adapter swap:** Complete items 1-8 (write new adapters, delete litellm.py)
3. **SmartMemory removal:** Complete items 9-11
4. **Cleanup:** Complete items 12-20 (CHANGELOG, README, docs reorganization)
5. **Verify:** `uv build && pip install dist/*.whl` in a clean venv; run through the
   quickstart manually with both Anthropic and OpenAI keys
6. **Read as a stranger:** Open the GitHub repo as if arriving for the first time.
   Does the README compel? Is the purpose clear within 30 seconds?
7. **Tag:** `git tag v0.2.0 && git push origin v0.2.0`
8. **Publish:** GitHub Actions publish.yml triggers automatically on the tag
9. **Verify:** Check the PyPI listing immediately; install from PyPI into a fresh venv
10. **P1 sprint:** Badges, issue templates, MkDocs site — within the same week
11. **Announce:** GitHub release with curated CHANGELOG summary

---

*Generated by deep codebase analysis on 2026-03-26.*
*Revised: LiteLLM removed in favour of native Anthropic + OpenAI adapters; SmartMemory removed.*
*Next review: after 0.2.0 PyPI publish.*
