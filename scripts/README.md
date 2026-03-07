# Utility Scripts

One-time learning scripts for seeding elfmem with domain knowledge. These are **not** part of the main package — they're examples and utilities for knowledge initialization.

## Scripts

### `learn_sim_concepts.py`
Extract reusable design simulation concepts from the project design methodology.
- **12 concepts** tagged with `methodology/` and `design-pattern`
- Useful for any project that needs system design before implementation
- Usage: `python scripts/learn_sim_concepts.py ~/.elfmem/agent.db`

### `learn_agent_patterns.py`
Extract agent usage patterns for elfmem (remember, recall, outcome, curate operations).
- **26 patterns** covering:
  - 5 Remember patterns
  - 5 Recall patterns
  - 5 Outcome patterns
  - 5 Curate patterns
  - 4 High-level patterns
  - 2 Anti-patterns
- Tagged with `agent-pattern/`, `learning-mechanism`, `core-heuristic`, `debugging`
- Usage: `python scripts/learn_agent_patterns.py ~/.elfmem/agent.db`

### `learn_cognitive_loop_operations.py`
Learn operationalized cognitive loop concepts (decision trees, reflection protocols).
- **22 concepts** covering:
  - Core feedback loop
  - 10 Constitutional block operationalizations
  - 3 Reflection protocols (daily, weekly, monthly)
  - 5 Decision trees
  - Loop closure conditions
- Tagged with `cognitive-loop/`, `decision-framework`, `operational-rule`
- Sequential execution; slow due to LLM API calls
- Usage: `python scripts/learn_cognitive_loop_operations.py ~/.elfmem/agent.db`

### `learn_cognitive_loop_operations_fast.py`
**Optimized version** of above with `asyncio.gather()` batching for 4-5x speedup.
- Same 22 concepts, processed in batches of 5
- Usage: `python scripts/learn_cognitive_loop_operations_fast.py ~/.elfmem/agent.db`

### `seed_self.py`
**DEPRECATED** — This functionality is now integrated into the CLI.
- Use instead: `elfmem init --seed` (seeded by default)
- Or: `elfmem init --no-seed` to skip seeding

## Running Utilities

**Set up environment:**
```bash
export ELFMEM_DB=~/.elfmem/agent.db
export ELFMEM_CONFIG=~/.elfmem/config.yaml
```

**Initialize elfmem with constitutional blocks:**
```bash
elfmem init --seed --self "Your agent identity"
```

**Learn design concepts (optional, for documentation purposes):**
```bash
python scripts/learn_sim_concepts.py
python scripts/learn_agent_patterns.py
python scripts/learn_cognitive_loop_operations_fast.py
```

## Notes

- All scripts are **idempotent** — safe to re-run; duplicates are silently skipped
- Scripts use the same `SmartMemory` API as the main package
- Learning takes time due to LLM embedding calls (2-3s per concept)
- For production use, call these at system initialization or via CLI commands
- These scripts are **examples** — adapt them for your own knowledge domains

See `docs/` for detailed guides on:
- Agent usage patterns (`docs/agent_usage_patterns_guide.md`)
- Cognitive loop operations (`docs/cognitive_loop_operations_guide.md`)
- Design simulation methodology (`docs/design_simulation_guide.md`)
