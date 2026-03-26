# Local Development Setup

This guide is for contributors working directly on the elfmem source. If you installed elfmem
from PyPI (`pip install elfmem`), none of this applies to you — just set your API keys and go.

## Clone and install

```bash
git clone https://github.com/emson/elfmem.git
cd elfmem
uv sync --extra dev
```

This installs all dependencies including the CLI, MCP server, and test tools.

## Run the checks

All three must pass clean before opening a PR:

```bash
uv run pytest -q                              # 400+ tests, ~10 seconds, no API keys
uv run ruff check src/ tests/               # lint
uv run mypy --ignore-missing-imports src/elfmem/  # type check
```

## Install specific extras

When working on the source, always use `uv sync --extra <name>` rather than
`uv add elfmem[<name>]`. The project is named `elfmem`, so `uv add elfmem[mcp]`
tries to add itself as a dependency — uv correctly rejects this as a self-reference.

```bash
# All development dependencies (recommended)
uv sync --extra dev

# Specific extras
uv sync --extra mcp --extra cli
uv sync --all-extras

# Verify an extra installed correctly
uv run python -c "import fastmcp; print('fastmcp OK')"
uv run python -c "import typer; print('typer OK')"
```

## Run the MCP server locally

```bash
uv run elfmem serve --db ~/.elfmem/agent.db
```

## Run the CLI

```bash
uv run elfmem --help
uv run elfmem status
```

## Build the package

```bash
uv build
# Inspect the wheel
unzip -l dist/*.whl | head -30
```

## Build the documentation site

```bash
uv run mkdocs serve    # live-reload preview at http://127.0.0.1:8000
uv run mkdocs build    # static output in site/
```
