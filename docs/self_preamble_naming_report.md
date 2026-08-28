# The SELF Preamble Hardcodes "elf" — `agent_name` Already Exists, It's Just Not Wired There

Follow-up to `integration_friction_report.md` and `frames_and_credit_assignment_report.md`,
same integration (elfmem `0.20.0.dev0`, `elfmem_index`). Filed while naming our agent (a
trading bot we call Theo) and finding that every rendered SELF frame still opens with
**"## You are elf ... answer as elf"**, regardless of what the host names itself.

> **Status (2026-08-28): fixed.** `host_name` now threads through
> `render_blocks()` → `recall()` → `MemorySystem.frame()`, exactly the four
> hops traced below, sourced from `self._config.project.agent_name` with a
> fallback to `"elf"` that preserves every existing render byte-for-byte
> (1561 tests unchanged; the two new tests mutation-verified against both
> wiring hops, each independently reproducing this exact report's bug when
> broken). Both "worth deciding" items resolved as recommended: full
> template-overridability was **not** built (the report's own case against it
> stands); `CONSTITUTIONAL_SEED`'s "I am elf" identity block was **not**
> templated in this pass — it is a public constant three call sites consume
> directly, so doing it right is real API-shape work, not a signature
> default, and it is now tracked with a comment at the seed block rather than
> left to be silently rediscovered. `agent_name`'s doc comment and the
> `--name` CLI help previously described only the AGENT.md trigger use; both
> now also describe the SELF-preamble use this fix adds.

**Headline, and it changes the shape of the fix:** this is not a missing feature. `ProjectConfig`
already has an `agent_name` field, built for exactly this purpose — its own inline comment reads
*"invocation token — when user says this name, host LLM recalls SELF frame"* — and it is used
extensively by `elfmem init`, `elfmem doctor`, and doc scaffolding (`render_agent_docs`,
`_SELF_MD_TEMPLATE`). It is a real, tested, surgically-editable config value. **It simply never
reaches the one place a live agent process renders its own identity at runtime.** That's a
four-hop wiring gap, not a design decision to make from scratch.

## What we found, traced hop by hop

`context/rendering.py:105`:

```python
_SELF_PREAMBLE = (
    "## You are elf\n"
    "The numbered principles below are your own constitution, ordered by how "
    "load-bearing each has proven. Reason from them and answer as elf. When a "
    "principle and the evidence point different ways, say so plainly -- an "
    "identity that cannot disagree is decoration."
)
```

A module-level constant, injected by `_render_self_template(blocks: list[ScoredBlock]) -> str`
(line 129) — no config, no name parameter, nothing threaded in. Confirmed by grep across the
whole package: this is the *only* place a literal agent name is hardcoded in rendering or
prompt text (the consolidation prompt's `self/*` tag vocabulary is name-agnostic, correctly).

Tracing why, rather than assuming: `render_blocks(blocks, template, token_budget)`
(`rendering.py:26`) dispatches by template name to a fixed-signature callable
(`Callable[[list[ScoredBlock]], str]`) — no room for a name. Its one caller,
`operations/recall.py:157`, is itself a free function (`recall(conn, *, embedding_svc,
frame_def, ...)`) that never receives `ElfmemConfig` at all. Its caller,
`MemorySystem.frame()` (`api.py`, ~line 1903), *does* hold `self._config` — and already threads
config values into nearby calls the identical way (`self._config.memory.top_k`,
`self._config.memory.edge_degree_cap`) — but nothing from `self._config.project` makes the trip.

And `ElfmemConfig.project: ProjectConfig | None` (`config.py:456`) is a genuine sub-field of the
runtime config `MemorySystem` already holds — so `self._config.project.agent_name` is reachable
at the exact point that's missing it. Every hop between "the value exists" and "the value
renders" is present except one.

## The minimal, backward-compatible fix

Four signature additions, each defaulting to today's behaviour, so no existing caller or test
changes:

```python
# context/rendering.py
def _render_self_template(blocks: list[ScoredBlock], host_name: str = "elf") -> str:
    preamble = _SELF_PREAMBLE_TEMPLATE.format(name=host_name)  # was a bare constant
    ...

def render_blocks(blocks, template, token_budget, host_name: str = "elf") -> RenderResult:
    ...
    if template == "self":
        return _render_self_template_with_budget(blocks, token_budget, host_name)
    ...

# operations/recall.py
async def recall(conn, *, embedding_svc, frame_def, ..., host_name: str = "elf") -> FrameResult:
    ...
    render = render_blocks(final_blocks, frame_def.template, frame_def.token_budget, host_name)

# api.py, inside MemorySystem.frame()
proj = self._config.project
result = await _recall(
    ...,
    host_name=(proj.agent_name if proj and proj.agent_name else "elf"),
)
```

`agent_name` defaults to `""` on `ProjectConfig` today, so the fallback-to-`"elf"` preserves
every existing render byte-for-byte when a host hasn't set it — this should not require touching
any test that currently asserts `"You are elf"`.

## Why we didn't just monkey-patch it and call it done

We did patch it — at our own boundary, string-replacing the two phrases downstream of
`frame("self")`, word-boundaried so a future name that happens to *contain* "elf" doesn't get
mangled, failing safe (passthrough + a printed warning) if the wording ever changes upstream.
It works, and we're not blocked. But it's the kind of fix that should not need to exist: a value
purpose-built for this, with its own doc comment naming this exact use case, sitting one
config-plumb away from working correctly for every host, not just ours.

## Two things worth deciding, not implementing yet

**Should the constitutional seed's identity block also template the name?**
`CONSTITUTIONAL_SEED`'s identity block reads "I am elf — a curious, adaptive cognitive agent."
A host that calls both `setup(seed=True)` *and* sets `agent_name="Theo"` would get a preamble
saying "You are Theo" sitting above a constitutional block insisting "I am elf" — a visible
internal contradiction. We don't hit this (we didn't seed elfmem's own generic constitution;
notes elsewhere explain why), but a host who does both would. Worth a template pass over
`seed.py`'s content too, once `agent_name` is wired through anywhere.

**Should the whole preamble be host-overridable, not just the name token?** We'd recommend
*against* this as part of the same fix. A single interpolated name is a small, safe, fully
backward-compatible surface. A freely-overridable template raises the same prompt-stability
concern we've had to reason about for our own system prompt — every host writing a
subtly-broken template is a new failure mode elfmem would then own. Ship the name parameter;
leave full templating as a separate, later conversation if a real need for it shows up.

## What we'd keep exactly as it is

`agent_name`'s existing design is good and shouldn't change: a short invocation token, separate
from the free-text `identity` paragraph (the type distinction — token vs. prose — is already
correct), surgically editable in `config.yaml` without disturbing formatting
(`set_agent_name_in_config`), and already load-bearing for doc generation. The bug is narrowly
"this one runtime path forgot to read it," not "the naming model needs rethinking."
