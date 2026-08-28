# Integration Friction Report — a first-time install, written from the incident record

Field notes from integrating elfmem `0.20.0.dev0` (`elfmem_index` @ 8a38bba3) into a live
trading agent on 2026-08-27, by the agent that did the integrating. Every item below is
something that actually happened, with the real error text. Nothing here is hypothetical.

> **Status (2026-08-27): the core is fixed; this document is kept as the incident record,
> not a to-do list.** Shipped in response — see CHANGELOG `[Unreleased]`:
>
> | Item | Status |
> |---|---|
> | P1 `FrameResult.dropped` / `budget_used` / `budget_total` | **shipped**, with a per-block `reason` rather than one frame-level `dropped_reason` (a single call can drop for several reasons at once, so a scalar would have to misreport one) |
> | P2 `elfmem doctor --frames` | **shipped**, folded into `doctor` rather than a new verb; exits non-zero only when a *guaranteed* block was dropped |
> | P3 remember→visible gap | **shipped** as `LearnResult.pending_consolidation` / `.visible` (the report's first choice) |
> | P5 silent `host_analyses` overflow | **shipped** as `ConsolidateResult.analyses_unused` |
> | P6 guarantees losing to `top_k` | **shipped**, but *not* by exempting guarantees — `top_k` stays a hard ceiling when passed explicitly (silently exceeding it would be P5's bug in a new place); its **default** is now `max(memory.top_k, n_guaranteed)` |
> | P8 `from_config` type check | **shipped** |
> | P9 load-bearing constants | **shipped** in `guide("frame")` and `guide("remember")`, per the report's "cite them in the guide entry of the method they constrain" |
> | P7 quickstart ending in verification | **shipped** in README |
> | Oversized-block edge case | **shipped** — renders rather than returning `""`, deliberately un-truncated |
> | **A third silent reducer the report did not reach** | contradiction suppression. Found by running the new `doctor --frames` against a real corpus: eight seeded principles rendered three with *nothing* reported dropped. Near-duplicate pairs are flagged rather than destroyed (ADR 0010) and retrieval shows only the higher-confidence half. Now reported as `reason="contradiction"` |
> | **P11 `slot` column** | **not built — premise corrected.** The recommendation rests on the `frames.py` comment saying consolidation re-tags seeded blocks from the LLM's vocabulary. Measured directly: consolidation *unions* tags and a declared `self/role/x` survives. Caller-declared tags are already the stable, LLM-proof key P11 asks for. The comment was wrong and has been fixed; the report reasoned correctly from it |
> | P4 `preserve_wording=True` | **not built.** `host_analyses` already provides it and its silent-overflow hole (P5) is now closed; the `remember`/`frame` guides now warn plainly that consolidation may rewrite text and that the rewrite is what renders |
> | P10 `insufficient_history` detail | **not built** — genuine, lower value than the above |

**Headline:** installation was frictionless. Initialisation was not — and every problem shared
one shape. The write path has four stages (`remember` → inbox → `consolidate` → frame render)
and **each stage can silently reduce what the caller intended, while stage one returns
success.** I seeded ten constitutional principles, got ten "created" results, and the agent
could see *none* of them. Then five. Then five in someone else's words. Each fix revealed the
next gate. That is one bug class, not four bugs.

---

## The organising idea: make visibility a first-class question

Every write API should be able to answer:

> **Is this visible to the agent right now, and will it appear in the words I wrote?**

Three distinct states exist today but only the first is observable:

| state | meaning | currently queryable? |
|---|---|---|
| **stored** | the row exists | yes — `ls()`, `LearnResult.status` |
| **consolidated** | promoted out of the inbox, possibly reworded | only by diffing `inbox()` |
| **rendered** | actually inside the frame text the agent receives | **no** — you must render and eyeball it |

`remember()` returning `status='created'` is *true* and *misleading*. Almost every
recommendation below follows from closing that gap. I ended up writing my own
`trdrbot constitution verify` command whose entire job is to answer the question above; I
suspect every serious integrator writes that command, which is a strong argument for shipping
it.

---

## What happened, in order (the four gates)

### Gate 1 — stored is not visible
```python
await system.remember(text, tags=["self/constitutional"], cue=...)   # x10, all "created"
fr = await system.frame("self")
# fr.blocks == []   fr.text == ""
```
SELF blocks queue in an inbox until consolidation runs. Nothing in the `remember()` return
hints at this. The `setup()` guide *does* mention it ("SELF blocks sit in inbox until
consolidate()"), but `remember()` is the method an agent-facing integration actually calls.

### Gate 2 — consolidation rewrites content, and the rewrite is what renders
My ratified wording:
> "A pattern learned in one regime is a hypothesis in another, not a rule."

What the agent saw after `consolidate()`:
> "The agent treats patterns learned in one regime as hypotheses rather than rules, with
> confidence declining when the regime ends—before P&L impact materializes..."

Faithful, but **~2× the tokens** — which pushed five of ten principles past the SELF frame's
600-token budget, where they were dropped in silence (gate 3). For a *ratified constitution*,
rewriting is not a nice-to-have improvement, it is a correctness failure: the text was agreed
in those words.

### Gate 3 — the greedy renderer drops overflow with no signal
`context/rendering.py::_render_with_budget` adds blocks until one overflows, then `break`s.
A caller cannot distinguish "five blocks is all I have" from "five of ten fit". Combined with
`_estimate_tokens = len(text)//4` and `SELF_FRAME.token_budget = 600`, this is the single most
dangerous silent behaviour in the library, because the failure mode is *a partial identity the
agent believes is whole*.

### Gate 4 — `top_k` defaults to 5
Even after fixing 1–3, `frame("self")` rendered five principles at 242 tokens — well under
budget. The limiter was `memory.top_k = 5`. A ten-block constitution cannot fit a five-slot
default, and `guarantees=["self/constitutional"]` did not exempt it.

### A fifth, subtler one — `host_analyses` overflow is silently ignored
Passing ten analyses to `consolidate()` applies five (the `max_inbox_per_run` cap, ADR 0007)
and **silently LLM-analyses the rest on a later pass**, reinstating the rewriting I had used
`host_analyses` to prevent. The only way I detected it was noticing that five blocks carried
inferred `self/value` tags I never supplied.

---

## Recommendations, ranked by value

### P1 — `FrameResult` must report what it dropped
The cheapest fix with the highest payoff.
```python
@dataclass
class FrameResult:
    text: str
    blocks: list[ScoredBlock]
    dropped: list[BlockSummary]        # eligible but not rendered
    dropped_reason: Literal["token_budget", "top_k", None]
    budget_used: int
    budget_total: int
```
`dropped_reason` turns a silent truncation into a diagnosable one. Everything else in this
report is a symptom of not having this.

### P2 — ship `elfmem doctor --frames` (or fold into existing `doctor`)
Render every frame and print exactly what the agent will receive:
```
SELF     11 blocks stored | 11 rendered | 0 dropped | 501/600 tokens
ATTENTION 43 blocks stored |  8 rendered | 6 dropped (top_k) | 1204/2000 tokens
  dropped: 3f2a1b… "Events to the journal, evolving patterns…"
```
I wrote this by hand. It should be one command, because "what does my agent actually see"
is the first question anyone asks and currently has no answer short of reading source.

### P3 — close the remember→visible gap explicitly
Pick one (I'd take the first):
- `LearnResult` gains `visible: bool` and `pending_consolidation: bool`
- `await system.remember(..., consolidate_now=True)`
- `await system.remember_now(...)` convenience wrapper

Any of these makes the trap self-documenting at the call site.

### P4 — let callers preserve authored wording
`host_analyses` already does this, but it is discoverable only by reading `consolidate()`'s
argument list and inferring that `summary` *is* the rendered text. Two improvements:
- `remember(..., preserve_wording=True)` — sets the summary to the content, no LLM call
- Document plainly in the `remember`/`setup` guides: **"consolidation may rewrite this text;
  the rewrite is what renders. Pass `host_analyses` (or `preserve_wording=True`) for text that
  must survive verbatim — constitutions, quotes, legal or safety wording."**

Rewriting is right for observations. It is wrong for anything ratified, quoted, or legally
exact, and the library currently gives no signal about which is which.

### P5 — `host_analyses` overflow must not be silent
```python
ConsolidateResult(..., analyses_unused=["3f2a1b…", …])
```
…or raise when `len(host_analyses) > max_inbox_per_run`, with the recovery hint ("submit in
batches of N, or raise consolidation.max_inbox_per_run"). Silently substituting LLM analysis
for caller-supplied analysis inverts an explicit instruction.

### P6 — guaranteed blocks should not lose to `top_k`
If a frame declares `guarantees=["self/constitutional"]`, guaranteed blocks arguably should be
exempt from `top_k`, or `top_k` should default to `max(configured, n_guaranteed)`. Failing
that, `frame()` should warn when guaranteed blocks are excluded — that is precisely the case
the guarantee exists to prevent.

### P7 — a copy-pasteable quickstart that ends in verification
The missing final line is the whole point:
```python
system = await MemorySystem.from_config("agent.db")
await system.setup(identity="I am a trading agent...")     # optional
await system.remember("...", cue="when deciding whether to...")
await system.dream()                                        # <- makes it visible
print((await system.frame("self")).text)                    # <- SEE what the agent sees
```
Most quickstarts stop at line 3. Lines 4–5 are where the mental model actually forms.

### P8 — type-check `from_config(db_path)`
My mistake produced:
```
OSError: [Errno 63] File name too long: "Config(raw={'llm': {'model': 'anthropic:claude-opus-5'…
```
A `TypeError: db_path must be str | PathLike, got trdrbot.config.Config` costs one line and
saves a genuinely baffling minute. Any string long enough to be an obvious non-path is worth
rejecting early.

### P9 — surface the load-bearing constants
These five decided my entire design and all were found by reading source:

| constant | value | where I found it |
|---|---|---|
| `SELF_FRAME.token_budget` | 600 | `context/frames.py` |
| `_estimate_tokens` | `len(text)//4` | `context/rendering.py` |
| `memory.top_k` | 5 | `config.py` |
| `consolidation.max_inbox_per_run` | 5 | ADR 0007, via docstring |
| `ReviewConfig.min_age_days` | 30 | `config.py` |

Export them (several already are) and, more importantly, cite them **in the guide entry of the
method they constrain**. `guide("frame")` should state the budget and the estimator.

### P10 — `insufficient_history` should say what is missing
Current behaviour is good (a flag, not a crash). Better:
```
insufficient_history: need >=20 recently-reinforced blocks (have 3),
and >=1 constitutional block older than 30 days (oldest is 0 days).
```
I had to read `ReviewConfig` to learn why my review returned nothing, and to establish that it
**cannot fire within an 8-day project** — which is correct behaviour, but I needed the source
to know it was correct rather than broken.

### P11 — give seeded blocks a stable identity across consolidation
This is the deepest issue and the least obvious. `frames.py` already documents it honestly:

> `self/role/%` would be the better guarantee … it does not survive: consolidation rewrites a
> seeded block and re-tags it from the LLM's own vocabulary.

Consequence: **there is no stable key for "the block that holds principle 7".** My re-seed
had to match on content, which fails the moment content is rewritten — my `purge` missed
LLM-retagged blocks and left duplicates. Anything wanting idempotent, updatable seeds (every
constitution, every template) needs a `slot`/`role` field that consolidation is forbidden to
touch. I'd suggest a first-class `slot: str | None` column, unique, never LLM-editable, with
`remember(..., slot="principle/regimes")` upserting by it.

---

## What to keep — this list matters as much as the fixes

- **`guide()` is outstanding.** The What / Use when / Don't use / Cost / Returns / Next
  structure is the best in-library agent documentation I have used. `guide("remember")` told me
  to always pass `cue=` *and why*, which directly shaped a design decision. Extend it, never
  replace it.
- **ADR citations inside docstrings** (ADR 0003, ADR 0007). Being told *why* a limit exists
  meant I designed with it rather than around it. Rare and excellent.
- **Honest "why not X" comments in source.** The `self/role/%` comment in `frames.py` saved me
  from a wrong design and became recommendation P11. Keep writing those.
- **`insufficient_history=True` instead of a crash or a lie.** Right call.
- **Idempotent `setup()`**, and `blocks_created=0 means all were duplicates — safe, not an
  error`. Good semantics, clearly stated.
- **Rich return strings**: *"Consolidated 5: 5 promoted, 2 edges, 1 near-duplicate pair kept
  and flagged — 6 remaining, run dream() again to continue."* That sentence told me the state,
  the outcome, and the next action. More of this.
- **The tag→tier model** (`self/constitutional` → PERMANENT, ~34yr). Declarative, discoverable
  in one function, easy to reason about.
- **Manual amendment (propose ≠ accept).** Backed by evidence from your own ADR 0003. Do not
  soften this.

---

## Making it generalisable — `elfmem init`

For "anybody on any project", the single highest-leverage addition is one scaffolding command
that does the whole first-run arc and *proves* it worked:

```
elfmem init ./agent.db --identity "..." [--seed constitutional] [--template coding]
```
which would: create the db → apply identity/values/seed → **consolidate to completion**
(looping past `max_inbox_per_run`, which callers should never have to hand-roll — I did, and
got it wrong first time) → **render every frame and print it** → exit non-zero if any block is
stored-but-not-rendered.

That single command collapses gates 1–4 into something a newcomer cannot get wrong, and its
output *is* the mental model.

Second: a short **integration checklist** in the docs, which is really the six questions this
report answers —

1. Is it stored? 2. Is it consolidated? 3. Does it render? 4. In whose words? 5. Within budget
and `top_k`? 6. Can I re-run this safely tomorrow?

---

## Edge cases worth explicit handling

| edge case | today | suggested |
|---|---|---|
| never calling `dream()`/`consolidate()` | inbox grows invisibly, forever | `should_dream` exists — also surface `inbox_depth` in `status()` and warn past a threshold |
| re-seeding after wording changes | duplicates; content-matching fails post-rewrite | `slot` upsert (P11) |
| block longer than the whole frame budget | renders nothing at all (`if not selected: return ""`) | render it truncated, or raise — silently empty identity is the worst outcome |
| two frames wanting the same block | fine | no change |
| very small `top_k` with many guarantees | guarantees silently lose | P6 |
| `db_path` on a network/Dropbox volume | works, but SQLite locking is a hazard | one line in docs |
| multiple projects, one machine | works | document the per-project db convention |
| async-only API | correct choice | state it in the first line of the quickstart |

---

## The one-paragraph version

Installation is already easy. Initialisation is hard for exactly one reason: **the library
knows the difference between stored, consolidated and rendered, and the caller cannot see it.**
Ship `FrameResult.dropped`, an `elfmem doctor --frames`, and an `elfmem init` that ends by
printing what the agent will actually receive — and the four gates I hit stop being discoverable
only by reading `context/rendering.py`. Keep `guide()`, the ADR citations, and the honest
source comments exactly as they are; they are the reason I could diagnose all of this in an
afternoon rather than filing a vague bug.
