# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.1.x   | Yes       |

## Scope

elfmem is a local-first library. It stores agent memory in a SQLite file on disk. There is no network-facing server component except when running `elfmem serve` as an MCP server, which binds to stdio (not a network socket) by default.

Key areas relevant to security:

- **API keys** — elfmem reads keys from environment variables via LiteLLM. Keys never appear in config files or the database.
- **Database file** — the SQLite file contains all agent memory. Protect it with standard file system permissions. Do not store it in a world-readable location.
- **Prompt content** — memory blocks are stored as plain text. Do not store secrets, credentials, or PII in elfmem memory blocks.
- **MCP tool input** — when running as an MCP server, tool arguments come from the connected LLM agent. Treat them with the same trust level as LLM output (i.e., do not execute them as shell commands).

## Reporting a Vulnerability

If you find a security vulnerability, please do **not** open a public GitHub issue.

Email the maintainer directly at the address in `pyproject.toml`, or open a [GitHub Security Advisory](https://github.com/emson/elfmem/security/advisories/new) (private disclosure).

Include:
- A description of the vulnerability
- Steps to reproduce
- Potential impact
- Your suggested fix, if any

You can expect an acknowledgement within 48 hours and a resolution or workaround within 14 days for confirmed vulnerabilities.

## Out of Scope

The following are not considered security vulnerabilities for this project:

- LLM prompt injection via memory content (the agent controls what it stores)
- Performance issues
- Missing features
