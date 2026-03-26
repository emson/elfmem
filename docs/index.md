# elfmem

**Adaptive memory for LLM agents. Knowledge that gets used survives. Knowledge that doesn't fades away. One file, zero infrastructure.**

---

## The problem

LLM agents are stateless by default. Every session starts from zero. Context windows fill up and reset. RAG retrieves documents but never learns from them. Most memory libraries either demand external infrastructure — vector databases, Redis, Postgres — or offer only a key-value store with no concept of decay, identity, or relevance.

## The solution: three rhythms

```python
from elfmem import MemorySystem

system = await MemorySystem.from_config("agent.db")

async with system.session():
    await system.learn("User always prefers explicit error messages over generic ones.")

    context = await system.frame("attention", query="error handling style")
    print(context.text)   # Inject into your LLM prompt
```

| Rhythm | Operation | Cost | Trigger |
|--------|-----------|------|---------|
| **Heartbeat** | `learn()` | Milliseconds — no API | After every discovery |
| **Breathing** | `dream()` | Seconds — one LLM call | At natural pause points |
| **Sleep** | `curate()` | Minutes — periodic | Automatically on schedule |

---

## Get started

<div class="grid cards" markdown>

-   **Quick Start**

    Running in five minutes — MCP, CLI, and Python.

    [Quick Start &rarr;](quickstart.md)

-   **Configuration**

    Providers, models, YAML config, Ollama local setup.

    [Configuration &rarr;](SETUP_AND_CONFIG.md)

-   **MCP Server**

    Give Claude persistent memory across sessions.

    [MCP Server &rarr;](MCP_SERVER_SETUP.md)

-   **Building Agents**

    Discipline loops, calibration, outcome signals.

    [Agent Patterns &rarr;](agent_usage_patterns_guide.md)

</div>

---

## Why elfmem

| Feature | elfmem | mem0 | LangChain Memory | Chroma/Weaviate |
|---------|--------|------|-----------------|-----------------|
| Infrastructure required | None (SQLite) | Postgres/Redis | In-memory | Vector DB server |
| Adaptive decay | Yes | No | No | No |
| Knowledge graph | Yes | No | No | No |
| Contradiction detection | Yes | No | No | No |
| Session-aware clock | Yes | No | No | No |
| MCP native | Yes | No | No | No |
| Official SDKs only | Yes | No | Varies | No |

---

## Install

```bash
pip install 'elfmem[tools]'          # CLI + MCP server + Python library

export ANTHROPIC_API_KEY=sk-ant-...  # Claude (LLM)
export OPENAI_API_KEY=sk-...         # OpenAI embeddings (text-embedding-3-small)
```

See [Configuration](SETUP_AND_CONFIG.md) for a fully local Ollama setup.

---

## Links

[GitHub](https://github.com/emson/elfmem){ .md-button }
[PyPI](https://pypi.org/project/elfmem){ .md-button }
[Changelog](https://github.com/emson/elfmem/blob/main/CHANGELOG.md){ .md-button }
