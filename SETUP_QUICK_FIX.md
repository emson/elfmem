# Quick Fix: "MCP server requires the 'mcp' extra"

You're here because you got this error:
```
uv run elfmem serve
MCP server requires the 'mcp' extra:
  pip install 'elfmem[mcp]'  or  uv add 'elfmem[mcp]'
```

## The One-Line Fix

```bash
uv sync --extra mcp --extra cli
```

That's it. Then run:
```bash
uv run elfmem serve --db ~/.elfmem/agent.db --config ~/.elfmem/config.yaml
```

## Why This Happened

- You ran `uv sync` without the `--extra` flags
- This installed only base dependencies (no `fastmcp`, no `typer`)
- When the CLI tried to import `fastmcp`, it failed
- You tried `uv add elfmem[mcp]` to fix it, but got a self-dependency error

## Why `uv add elfmem[mcp]` Doesn't Work

- The project itself is named `elfmem` (in `pyproject.toml`)
- `uv add elfmem[mcp]` tries to add a **third-party package** named `elfmem` to the **local package** named `elfmem`
- This is a self-dependency, which uv forbids
- **Always use `uv sync --extra mcp --extra cli`** instead (note: `--extra` is singular, repeated for each extra)

## Verify It's Fixed

```bash
# Check fastmcp is installed
uv run python -c "import fastmcp; print('✓ fastmcp OK')"

# Check typer is installed
uv run python -c "import typer; print('✓ typer OK')"

# Run the server
uv run elfmem serve --help
```

## All Commands (Reference)

```bash
# Install/update with all extras (DO THIS)
uv sync --extra mcp --extra cli

# Alternative: install all optional extras
uv sync --all-extras

# What NOT to do (will fail)
uv add elfmem[mcp]        # ✗ Self-dependency error
uv add elfmem[cli]        # ✗ Self-dependency error
pip install elfmem[mcp]   # ✗ Wrong tool (use uv)

# Test the setup
uv run elfmem --help
uv run elfmem serve --db ~/.elfmem/agent.db
```

---

**Need more details?** See `docs/MCP_SERVER_SETUP.md` for the full guide.
