# Contributing to elfmem

Thank you for your interest in contributing. elfmem is designed around a small set of strong principles — contributions that stay within those principles are very welcome.

## Design Principles

Before writing code, read `CLAUDE.md` (codebase principles) and `docs/amgs_architecture.md` (full technical spec). The short version:

- **Simple, elegant, flexible, robust** — in that order.
- **Functional Python** — pure functions, compose pipelines from ≤50-line functions.
- **Fail fast** — exceptions bubble up; catch only at CLI/MCP boundaries.
- **No defensive code** — no broad `except`, no `try/except` in business logic.
- **Complete type hints** — every function, public and private.
- **Mock-first testing** — all logic verified without API keys.

## Getting Started

```bash
git clone https://github.com/emson/elfmem.git
cd elfmem
uv sync --extra dev
uv run pytest          # all tests must pass
uv run ruff check src/ tests/
uv run mypy --ignore-missing-imports src/elfmem/
```

All three commands must pass clean before opening a PR.

## What We Welcome

- **Bug fixes** — with a regression test that fails before the fix and passes after.
- **New frames** — follow the pattern in `src/elfmem/context/frames.py`.
- **New decay tiers** — follow the constants in `src/elfmem/memory/blocks.py`.
- **Adapter improvements** — LiteLLM / embedding changes that don't break the port protocol.
- **Documentation improvements** — typos, clarity, better examples.
- **Example agents** — new files in `examples/` showing real usage patterns.

## What to Discuss First

Open an issue before starting work on:

- New public API methods on `MemorySystem`
- Changes to the scoring formula (`src/elfmem/scoring.py`)
- Changes to the database schema (`src/elfmem/db/models.py`)
- New dependencies

These touch the frozen core and need alignment before implementation.

## Pull Request Process

1. Fork the repo, create a branch from `main`.
2. Write tests first. Every bug fix needs a regression test. Every new feature needs coverage.
3. Keep changes focused. One concern per PR.
4. Update docstrings if you change public API behaviour. Follow the template in `CLAUDE.md`.
5. Run `ruff`, `mypy`, and `pytest` locally — the CI will run them too.
6. Open the PR with a clear description of what changed and why.

## Testing

All tests use deterministic mock services — no API key required:

```python
from elfmem.adapters.mock import make_mock_llm, make_mock_embedding

llm = make_mock_llm(alignment_overrides={"identity": 0.95})
embedding = make_mock_embedding()
```

Never add tests that make real API calls. Never add `time.sleep()` in tests.

## Commit Style

Plain English, imperative mood. Examples:

```
Add temporal decay to co-retrieval edges
Fix empty query crash in frame() when SELF frame is active
Update LiteLLMAdapter to use create_with_completion for token tracking
```

## Code of Conduct

Be direct, be kind, stay on topic. Disagreements about design are normal and healthy — work through them with evidence and examples, not authority.

## Questions

Open a [GitHub Discussion](https://github.com/emson/elfmem/discussions) for design questions, or a [GitHub Issue](https://github.com/emson/elfmem/issues) for bugs.
