---
type: inbox
created: 2026-04-27
status: growing
---

# Proposal: Theory of Mind blocks for elfmem (week-1 build)

## The Problem: Why elf Needs Explicit Mind Models

elf operates under the entrepreneurial mandate: help Ben identify and act on wealth-building opportunities. This requires making predictions about what others will do and want.

**The current gap:** elf's understanding of other minds is implicit. It lives scattered across:
- Ben's feedback blocks ("Ben values shipping over synthesising")
- Vault content about customers (guesses from research sources)
- Embedding similarity (if customer archetype X is similar to Y, they probably want Z)

This approach has three weaknesses:

1. **Predictions are not falsifiable.** When elf says "this customer will want a hosted model over files," that claim lives as semantic similarity, not as a checkable hypothesis. No `verify_at` date. No way to know if the prediction was right. No learning signal.

2. **Other minds are modelled implicitly through Ben's narrative.** If Ben hasn't mentioned what customers fear, elf has no model of customer fear. elf cannot ask "what would change their mind?" because elf has no mind to reason about. 

3. **Calibration is impossible.** Multi-scale learning loops (just designed) can close on outcomes and reinforce/decay constitutional SELF blocks. But they cannot calibrate a model of Ben's mind or customer minds because those models have no explicit representation. The loops can only update Ben's and elf's own behaviour, not the model of others.

**The consequence:** Yesterday, elf tried to judge whether the sveltetemplates "moat" was real. elf called it "a hope, not a moat" but had nothing concrete to put in its place — no model of why customers buy templates, what they fear, what they would pay. Just absence.

With explicit ToM blocks, that gaps gets filled.

## The Solution: Theory of Mind Blocks

**One-sentence summary.** Add a `mind` block category and two edge relation types (`predicts`, `validates`) so elf can hold falsifiable models of other minds, calibrated by outcome closure.

**How it works in one scenario:**

elf is in simulate mode, thinking about sveltetemplates strategy (the AI tool builders vertical). elf recalls the "customer" ToM block. It contains:

```markdown
## Goals
- Ship fast without learning deployment details
- Keep token spend predictable and low
- Own their differentiation layer (the agent-ready layer)

## Fears
- Complex setup (will cause them to abandon)
- Surprise costs (API bill doubles)
- Template becomes a commodity (vendor lock-in for safety)

## Beliefs
- Agent-ready layers matter (they use Claude Code daily)
- Files are getting commoditised (cheap copies on Gumroad)
- Customisation is a tax (they want it, but hate paying for it)

## Predictions
- Will pay £49-99/mo for a hosted version with auto-updates. verify_at: 2026-06-30
- Will abandon if setup takes >30min. verify_at: 2026-05-15
- Will ask for agent-customisation layer within first month. verify_at: 2026-05-20
```

elf inhabits this mind. It asks: "if you are this customer, what do I say to you about sveltetemplates?" The simulate frame retrieves this ToM block. elf generates: "You want to ship fast with an agent layer you own. This template does that without making you deploy infrastructure. No API surprises because hosting is baked in."

That's a different pitch than without the ToM. With ToM, it's specific to what the customer wants and fears. Without it, it's generic.

Now imagine the outcome: the customer **does** pay £49/mo by June 30. elf calls `elfmem outcome --against-mind <customer-id> --hit --reason "signed up week 1 at tier price"`. The ToM block gets reinforced. So do the SELF blocks in elf that drove the prediction (entrepreneurial focus, domain-first thinking, ability to model customer minds). Next time elf thinks about pricing strategy, those blocks are slightly stronger.

Now imagine a miss: the customer asks for full customisation on day 1 (the opposite of the prediction that they'd ask by month end). elf calls `elfmem outcome --against-mind <customer-id> --miss --reason "requested full bespoke integration, rejected template layer"`. The ToM block decays. The SELF block "customers will adopt templates over custom-build" decays. elf learns: at this price point and for this audience, the theory was wrong.

That's calibration across minds, not just elf's own constitution.

## How It Plugs Into Existing Systems

### With the Simulate Frame

The simulate frame (designed yesterday) biases retrieval toward SELF blocks so elf can inhabit a perspective. ToM blocks sit at medium-high weight in simulate retrieval:

```
SELF blocks:      weight 10.0  (the perspective being inhabited)
ToM blocks:       weight 6.0   (minds being reasoned about)
Decision blocks:  weight 5.0   (unresolved hypotheses)
Recent tasks:     weight 3.0   (what's alive)
Knowledge:        weight 1.0   (grounding only)
```

When elf runs simulate with a ToM retrieved, the stances it produces are *conditioned on an explicit model of the other mind*. The stances become falsifiable: "given what I think you want and fear, you will do X."

### With Multi-Scale Learning Loops

Multi-scale loops (designed yesterday) already exist in elfmem:

- `outcome()` updates block confidence with Bayesian delta
- Edges get reinforced on hit, decayed on miss
- The loop closes: predict → act → observe → calibrate → predict better

ToM blocks plug in as the blocks being calibrated. Predictions live as `predicts` edges between a ToM block and a decision block. When the decision closes, the edge gets reinforced or decayed, and confidence on the ToM block moves with it.

Example edge lifecycle:
```
Day 1:  elf creates edge: mind(customer) --predicts--> decision(will-pay-49/mo)
        weight=0.7, confidence=0.5

Day 15: Customer signs up. elf calls: outcome(decision-id, hit, "signed week 1")
        Edge weight reinforced to 0.8
        ToM block confidence: 0.5 → 0.55

Day 45: Second customer asks for full bespoke. elf calls: outcome(decision-id, miss, "rejected template")
        Edge weight decayed to 0.65
        ToM block confidence: 0.55 → 0.48
```

Over time, the ToM block's confidence reflects whether elf's model of that mind is accurate or drifting.

## What We Actually Build

| Change | Where | Scope |
|--------|-------|-------|
| New block category `mind` | Convention + docs | 0 LOC (metadata string) |
| Two edge relation types: `predicts`, `validates` | `Edge.relation_type` already accepts strings | 0 LOC (config) |
| `simulate` frame retrieval policy | `.elfmem/config.yaml` + `scoring.py` | ~50 LOC |
| CLI: `elfmem mind <subject>` | New command module | ~200 LOC |
| CLI: `elfmem outcome --against-mind` | Wrapper around existing `outcome()` | ~150 LOC |

**Total scope: ~400 LOC for a feature that unlocks explicit world-modelling.** No schema migrations. No DB changes. The entire feature is a convention layer on top of existing primitives (blocks, edges, outcome closure, decay tiers).

## ToM Block Content Schema

### Frontmatter (structured)

```yaml
subject: "[[agentmkts-customer-archetype]]"  # wiki entity ref
category: mind
last_calibrated: 2026-04-27
decay_tier: durable                          # λ=0.001; slow decay
prediction_count: 3
hit_count: 1
miss_count: 0
mean_confidence: 0.52
```

### Body (markdown, human-readable)

```markdown
## Goals
What this mind is trying to achieve. One per bullet.

- Ship fast without learning infra details
- Keep API costs predictable
- Own the agent differentiation layer

## Beliefs
What this mind holds to be true. Reference sources where possible.

- Agent-ready code is a moat (they use Claude Code daily; see [[agentmkts-transcript]])
- Files are commoditising (competing SvelteKit templates on Gumroad; see [[claude-skills-monetisation-landscape]])
- Customisation has high discovery cost (they'll ask, but hate paying by the hour)

## Fears
What this mind wants to avoid. Be specific.

- Complex setup (abandonment risk if >30min to first deploy)
- Surprise costs (API bills scare them more than upfront price)
- Template becomes a commodity they don't own (vendor lock-in)

## Motivations
What actually drives decisions. Often hidden behind goals/fears. Optional but powerful.

- Ego/status: want to say "I built this with agents"
- Autonomy: want to own their differentiation, not rent it
- Speed: want to ship month 1, not month 3

## Predictions
Falsifiable claims about what this mind will do. Require verify_at date.

- Will pay £49-99/mo for hosted auto-updates (not files). verify_at: 2026-06-30
  - Reasoning: owns differentiation + predictable cost + no setup friction
  
- Will request agent-customisation layer within 30 days. verify_at: 2026-05-20
  - Reasoning: they'll want to tweak prompts for their domain
  
- Will abandon if setup takes >30min. verify_at: 2026-05-15
  - Reasoning: deployment anxiety + competing alternatives (hire devs, build custom)
```

**Why markdown + headings, not structured JSON?**
- Keeps block content in text (existing LLM consolidation works)
- Readable by humans (Ben can edit a ToM block as a note)
- Survives Hebbian learning (similarity scoring operates on text)
- Matches vault conventions (everything is markdown)

## Real Example: elf's Customer ToM Block

elf would create this when modelling a specific customer archetype engaged with elfmem:

```yaml
subject: "[[elfmem-early-adopter]]"
category: mind
last_calibrated: 2026-04-28
decay_tier: durable
prediction_count: 0
hit_count: 0
miss_count: 0
mean_confidence: 0.50
```

```markdown
## Goals
- Build agents that learn and improve over time (not static systems)
- Integrate memory into existing agent frameworks with minimal friction
- Maintain observability of what agents know and why they decide

## Beliefs
- Knowledge decay matters (forgetting bad data is as important as learning good data)
- Predictability of LLM behavior improves with good context
- Graph-based memory integrates better with agentic workflows than linear RAG

## Fears
- Memory system becomes a black box (can't audit what agent knows)
- Integration overhead outweighs the memory benefit
- Stale or contradictory knowledge accumulates and breaks agent decisions

## Predictions
- Will integrate elfmem into their agent framework within 2 weeks of launch. verify_at: 2026-05-12
  - Reasoning: minimal Python dependency, SQLite backend (no ops), API is straightforward

- Will use the simulate frame to test agent decisions before deployment. verify_at: 2026-06-01
  - Reasoning: observability of "what would this agent think in context X?" is valuable for debugging

- Will request per-domain decay tuning (some knowledge lasts months, some lasts days). verify_at: 2026-06-15
  - Reasoning: their domain mixes evergreen patterns with rapidly-changing facts
```

As predictions close (adoption confirmed, feature adoption measured, requests arrived), elf calibrates this model: hits reinforce it, misses decay it. Over time, the model becomes more accurate.

## Integration with Simulate Frame + Multi-Scale Loops

**The full cycle:**

1. **elf inhabits** → calls `elfmem frame simulate --mind customer`
2. **ToM blocks retrieved** → customer goals, fears, predictions available
3. **elf generates stance** → "Here's what I'd say to this customer given what I know about them"
4. **Stance becomes prediction** → `elfmem connect <mind-block> <decision-block> predicts weight=0.7`
5. **Outcome arrives** → Customer does X (or doesn't)
6. **elf closes loop** → `elfmem outcome --against-mind <id> --hit/miss --reason "..."`
7. **Calibration happens** → ToM block confidence updated; SELF blocks reinforced/decayed
8. **Next time** → `elf inhabits` with a slightly better model of the customer mind

This is Active Inference applied to world-modelling: the agent's generative model of other minds improves with each prediction cycle.

## Edge Cases and Mitigations (with examples)

| Edge case | Mitigation | Example |
|-----------|-----------|---------|
| **Hallucinated minds** — elf invents customer preferences to seem capable | ToM confidence capped at 0.6 until N≥3 predictions close. Can't reinforce fiction. | If elf says "customers fear complexity" but all three predictions miss, confidence drops to 0.45. |
| **Subject conflation** — Two Bens with same name create one ToM | Subject field MUST be `[[wiki-slug]]` pointing to actual entity. CLI enforces. | `elfmem mind create` fails if subject is not a valid wiki link. |
| **Stale ToM** — Mind modelled once in month 1, never updated | DURABLE decay tier (λ=0.001); weekly `dream` flags ToM blocks with `last_calibrated > 30 days`. | If a ToM hasn't had a prediction close in 30 days, `dream` output: "Ben's model is stale — consider a check-in prediction." |
| **Prediction inflation** — Every casual statement becomes a prediction | `predicts` edge only created if elf's simulate output explicitly contains a `verify_at` date | Without `verify_at`, the statement is just narrative in the ToM block, not a falsifiable edge. |
| **Privacy** — ToM of non-consenting third parties | Third-party ToM blocks default to `private: true`; never indexed by MCP | A customer's ToM is not shared externally. Ben approves before elf shares. |
| **Confirmation bias** — elf says "hit" for marginal outcomes to inflate hit rate | `outcome --against-mind` requires both hit/miss flag AND a reason; reason is embedded in edge metadata for later audit | Years later, elf can review: "Did I really think that was a hit?" See the reason I wrote. |
| **Cross-mind contamination** — Customer model bleeds into Ben model | Each ToM is a separate block. Similarity edges are fine; content stays isolated. | Ben's ToM and customer's ToM can have a co_retrieval edge (they co-appear in retrieval), but they don't merge. |
| **Goodhart on hit rate** — elf optimises for prediction accuracy instead of calibration | Track `mean_confidence_delta` per ToM, not hit count. Favour moving towards truth over being right. | A ToM that decayed from 0.7 to 0.6 because elf was wrong is *good* — it's calibrating. |

## Three Alternatives Considered

1. **Structured JSON ToM** (rejected)
   - Pros: queryable, type-safe
   - Cons: breaks text-content assumption, requires schema migration, won't survive similarity scoring
   - Verdict: Over-engineered for a convention layer

2. **One ToM per prediction** (rejected)
   - Pros: fine-grained, one update per closure
   - Cons: explodes block count (10 predictions = 10 blocks), loses subject coherence, makes "model a mind" hard
   - Verdict: Fragment the identity we're trying to model

3. **One ToM per subject, append-only with consolidation via dream** (chosen)
   - Pros: coherent unit, compounds, leverages existing consolidation, markdown-native
   - Cons: body can get long; mitigated by dream consolidation
   - Verdict: Matches elf's architecture and the vault's ethos

## Falsifiable Success and Kill Criteria (Phase 0 discipline)

**Success at 4 weeks (2026-05-25):**
- ≥3 ToM blocks live (Ben + 2 customer archetypes)
- ≥10 closed predictions across them
- Mean confidence delta is non-zero (calibration is moving the needle)
- At least one prediction-miss visibly changed a subsequent simulate output (we learn from being wrong)
- `elfmem outcome` calls are happening as part of weekly cadence, not ad-hoc

**Kill at 4 weeks if:**
- Zero closed predictions (feature is capture-only, no closure loop working)
- All predictions hit trivially (e.g., "will engage with the product" — unfalsifiable)
- Simulate frame outputs are identical with vs without ToM retrieval (no signal, feature adds noise not intelligence)
- ToM blocks are too expensive to maintain (updates take longer than value delivered)

**Named user:** Ben. First ToM is Ben's. First closed predictions are about Ben (will he publish the blog? Will he ship Phase 0 by deadline?). Self-bootstrapping because Ben's behaviour is observable and immediate.

**Success metric we'll compute at week 4:** (closed predictions with non-zero delta) / (total predictions) >= 0.6. Not perfect predictions, but *moving* predictions.

## Implementation Sequence (week 1, ~5 days)

| Day | Output | Spike risk |
|-----|--------|-----------|
| 1 | `simulate` frame entry in `.elfmem/config.yaml`; retrieval scoring policy (SELF 10.0, mind/decision 6.0/5.0, task 3.0, knowledge 1.0). Test that `frame simulate` retrieves mind blocks higher than knowledge blocks. | Is the scoring hook flexible enough? (Answered by open questions.) |
| 2 | `elfmem mind <subject>` CLI: create / append-prediction / list / show. Template a Ben ToM block. | Do categories accept "mind" string? (Answered by open questions.) |
| 3 | `elfmem outcome --against-mind <id>` wrapper; wire `predicts` / `validates` edges through `connects()`. Smoke test: predict → outcome → edge reinforced. | Do `connects()` accept arbitrary relation strings? (Answered by open questions.) |
| 4 | Migrate three predictions about Ben from feedback memory into his ToM block. Run bootstrap: `elfmem mind append Ben --prediction "Will publish blog by..." --verify-at 2026-05-04`. | Does the CLI make migration frictionless enough? |
| 5 | Smoke test: run simulate frame with Ben's ToM retrieved; compare outputs with/without ToM. Confirm visible signal. | Does the simulate frame actually change output quality? (Kill criterion.) |

## What We Explicitly Do NOT Build in Week 1

- Episode blocks (distinct from ToM; comes week 2)
- Mode-monitoring metadata (week 3)
- Typed causal edges beyond predicts/validates (week 4-5)
- Goal hierarchy (after N=10 ToM closures)
- ToM versioning / time-travel queries (nice-to-have)
- Multi-agent ToM (ToM of agents modelling other agents; future)
- LLM-driven ToM auto-population from sources (future; risky)

## Open Questions for the Elfmem Maintainer

1. **Categories:** Is there a registered category list, or are categories truly free-form strings? Does "mind" need a code change or just convention?
2. **Relations:** Does `connects()` accept arbitrary `relation` strings, or is there a guard list? Do "predicts" and "validates" need code changes?
3. **Scoring policies:** Where do frame-specific retrieval policies live? Is `scoring.py` the only hook, or is there a frame-specific registry?
4. **Decay tiers:** Best practice — set decay tier per-block at `remember()` time, or as a category-level default in config?

## Alignment with Project Axioms

- **Capture is frictionless** — `elfmem mind` CLI with minimal syntax; auto-linking to wiki entities
- **Agent maintains** — elf owns ToM blocks; Ben approves on outcome closure
- **Knowledge compounds** — Each closed prediction reinforces/decays blocks and edges; minds get more accurate over time
- **Elfmem is intelligence layer** — ToM is core to intelligence: the agent's generative model of others
- **Everything is markdown** — ToM blocks are vault-native notes, human-readable

## Reference

- Originating designs: [[alv-simulated-self-cognitive-mode.md]], [[alv-multi-scale-learning-loops.md]], [[alv-cognitive-abstractions-roadmap.md]]
- Calibration mechanism: elfmem `outcome()` with confidence delta + edge reinforcement
- Playbook: [[project-build-playbook.md]] Phase 0 gates applied to infrastructure
- Theory: Active Inference (Friston) applied to explicit world-modelling
