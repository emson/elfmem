"""Every parameter a registered MCP tool declares must reach its delegate.

`elfmem_remember` once accepted `cue`, advertised it in the tool schema,
documented it as mandatory — and then called `_tool_remember(content,
tags=tags)`, dropping it. It returned success, so nothing looked wrong; the
cue simply vanished. The unit test that should have caught it exercised
`_tool_remember`, the layer *below* the bug.

This is a structural check rather than one more example: it fails for any
tool that grows a parameter and forgets to forward it.
"""

from __future__ import annotations

import pathlib
import re

MCP_SOURCE = pathlib.Path("src/elfmem/mcp.py")

_TOOL_RE = re.compile(
    r"@mcp\.tool\(\)\nasync def (?P<name>\w+)\((?P<params>.*?)\) -> [^:]+:"
    r"(?P<body>.*?)(?=\n@mcp\.tool\(\)|\nasync def |\Z)",
    re.S,
)
_DELEGATE_RE = re.compile(r"return await (?P<delegate>_tool_\w+)\((?P<args>.*?)\)\n", re.S)


def _declared_params(params: str) -> set[str]:
    return {
        part.split(":")[0].strip()
        for part in params.split(",")
        if ":" in part and part.split(":")[0].strip()
    }


def test_every_declared_parameter_is_forwarded_to_its_delegate() -> None:
    source = MCP_SOURCE.read_text(encoding="utf-8")
    tools = list(_TOOL_RE.finditer(source))
    assert tools, "no @mcp.tool() functions found — has the file moved?"

    dropped: list[str] = []
    checked = 0
    for tool in tools:
        call = _DELEGATE_RE.search(tool.group("body"))
        if call is None:
            continue  # tool does its own work rather than delegating
        checked += 1
        forwarded = call.group("args")
        for param in sorted(_declared_params(tool.group("params"))):
            if param not in forwarded:
                dropped.append(
                    f"{tool.group('name')} declares {param!r} but never passes "
                    f"it to {call.group('delegate')}"
                )

    assert checked, "no delegating tools matched — the regex needs updating"
    assert not dropped, "MCP tools silently discard parameters:\n  " + "\n  ".join(dropped)


def test_remember_forwards_the_cue_specifically() -> None:
    """The regression that motivated the structural check above."""
    source = MCP_SOURCE.read_text(encoding="utf-8")
    tool = next(
        t for t in _TOOL_RE.finditer(source) if t.group("name") == "elfmem_remember"
    )
    call = _DELEGATE_RE.search(tool.group("body"))
    assert call is not None
    assert "cue" in call.group("args")
