# Scoring Memory for Evolving Agents — A Research Survey and Synthesis

**Status**: research document — under review, not yet a plan
**Author**: elf (Ben Emson, with Claude Sonnet 4.6 collaboration)
**Date**: 2026-05-17
**Driver**: [elfmem issue #50](https://github.com/emson/elfmem/issues/50) — Dmitry's "I just told it something important and it ignored that next turn" report

---

## Abstract

How should an LLM agent's persistent memory rank its own contents for retrieval, promote items that prove useful, and let unused items fade — over weeks, months, and many thousands of items?

This survey reviews five decades of relevant research across cognitive science (ACT-R, Ebbinghaus), educational data mining (Bayesian Knowledge Tracing, FSRS-5, Half-Life Regression), generative-agent memory (Park et al.'s memory stream), production LLM agent memory systems (MemGPT, A-MEM, Mem0, MemMachine, SimpleMem), continual learning for neural networks (EWC, FOREVER, SuRe), and Bayesian online inference. We find broad convergence on six principles: multi-signal scoring, sufficient-statistics state, per-block adaptive forgetting, power-law retrievability, topological structure, and event-log audit trails. We also find sharp divergence on three open questions: how to weight competing signals, how to detect "useful retrieval" without explicit feedback, and how to maintain calibration as system state evolves.

We synthesise these findings into a candidate architecture for the elfmem library, identifying four high-leverage upgrades to its current confidence-as-scalar model: (1) decompose confidence into three orthogonal channels (importance, utility, retrievability) following Park 2023, (2) adopt FSRS-5's power-law forgetting curve with per-block stability, (3) store Bayesian sufficient statistics rather than point estimates, and (4) make retrieval and rescoring additive over an event log rather than destructive overwrites. We close with a discussion of scale, security, and migration challenges, and identify open problems that warrant further investigation before architectural commitment.

---

## 1. Introduction

### 1.1 The problem

A persistent agent memory must answer three questions on every retrieval:

1. **What is in this memory** — semantic match between query and stored content.
2. **How much should we trust it** — has past use validated this memory's correctness?
3. **How available should it be right now** — is it fresh, fading, or forgotten?

Most contemporary systems collapse questions (2) and (3) into a single scalar — commonly called *confidence*, *importance*, or *score* — which is updated on every relevant event. This collapse is the source of many observed defects:

- Cliffs and discontinuities at threshold boundaries (elfmem #50)
- Destructive rescore that erases earned utility (elfmem rescore.py:245)
- Peer transfer cannot epistemically merge two opinions
- Fresh items fail to surface despite high relevance
- Bedrock items dominate forever, freezing the system against new knowledge
- Performance degrades 15–43 percentage points over quarterly versus weekly evaluation windows in 2026 LLM-agent memory benchmarks ([State of AI Agent Memory 2026](https://mem0.ai/blog/state-of-ai-agent-memory-2026))

These are not implementation accidents — they are predictable consequences of single-scalar conflation. The same defects appear in domain after domain whenever a scoring system tries to compress multiple semantically distinct quantities into one persistent number that is overwritten by every operation.

### 1.2 Why this question now

Three converging pressures make this an open question worth investing in:

**Persistence is becoming the default.** Until 2024, most production LLM agents were session-scoped — memory complications could be deferred. In 2025–2026, agents are increasingly persistent over months ([Continual Learning: The Missing Capability for Reliable AI at Scale](https://medium.com/@adnanmasood/continual-learning-the-missing-capability-for-reliable-ai-at-scale-c225e1a523de)), and the durability problem moves from research curiosity to engineering reality.

**Scoring is now empirically benchmarked.** LoCoMo, AMA-Bench, and similar benchmarks ([AMA-Bench](https://arxiv.org/html/2602.22769v1)) allow head-to-head comparison of memory systems. The 2026 leaderboard is genuinely competitive: MemMachine (0.9169), A-MEM, Mem0, and others within points of each other, with architecture choices showing measurable impact.

**Cognitive science and ML are finally converging.** FSRS-5 (deployed at Anki's billion-review scale) confirms that power-law forgetting is empirically superior to exponential decay for human memory. Park et al. 2023's "memory stream" approach validates tripartite scoring (importance + recency + relevance) for agents. Continual-learning research on catastrophic forgetting in neural networks has independently arrived at replay-based and regularisation-based methods that mirror the spaced-repetition findings.

The question is no longer "what might work" but "given five decades of converging evidence across disciplines, what should the architecture actually be."

### 1.3 Scope and method

This survey covers approaches relevant to **scoring memory items in persistent agent systems where**:

- Items have heterogeneous semantic content (not numeric features)
- The system has no oracle for ground truth — feedback is partial and noisy
- Items must be promoted, degraded, and possibly forgotten over time
- Multi-agent and peer-sharing scenarios may be supported
- The system must scale from 100 to 100,000+ items
- The system runs against persistent SQL-style storage, not in-memory only

We deliberately exclude: pure information-retrieval ranking (TF-IDF, BM25, learning-to-rank for static corpora), recommendation systems for collaborative filtering, and graph-database trust propagation algorithms — except where they cross-pollinate with the memory question above.

Empirical evidence comes from: (a) published benchmarks (LoCoMo, AMA-Bench), (b) a custom simulation of five candidate scoring approaches against synthetic agent workloads (`/tmp/confidence_sim.py` in the elfmem repository), and (c) the elfmem codebase as a concrete case study.

---

## 2. Background and related work

We organise the prior art into six streams, ordered roughly by recency of contribution.

### 2.1 Classical foundations: forgetting curves and activation theory

The empirical study of memory decay begins with Hermann Ebbinghaus's 1885 self-experiments, which established that recall probability decays approximately exponentially with time since encoding. The functional form `R(t) = exp(-t/S)` — where `R` is retrievability and `S` is memory strength — remained dominant for over a century.

Anderson's **ACT-R** (Adaptive Control of Thought — Rational), developed from 1976 and refined through the 1990s, formalised memory as **activation**: the log-sum of past-use events, each decayed by a power-law of elapsed time:

```
A_i = ln(Σ_k t_k^{-d})  +  contextual_terms  +  noise
```

Where `t_k` is time since the k-th past retrieval of item `i`, `d ≈ 0.5` is the decay parameter. Crucially, activation is **computed at query time from an event log**, not stored as state. Successful retrievals leave new events that lift future activation.

ACT-R has been the gold-standard cognitive model for retrieval for 30 years. It has been validated against thousands of human-memory experiments and inspires modern memory architectures including FSRS, A-MEM, and Generative Agents.

### 2.2 Spaced repetition: SM-2 → FSRS-5 → HLR

The applied side of memory science is spaced repetition — the practical art of scheduling reviews to maximise long-term retention.

**SM-2** (Wozniak, 1985) introduced the now-universal "answer easy/good/hard/again" rating loop. Each item carries an interval; correct answers multiply the interval (ease factor ≈ 2.5), wrong answers reset it. SM-2 powered SuperMemo through the 1990s and remains Anki's legacy default.

**Half-Life Regression** (Settles & Meeder at Duolingo, 2016) reframed the problem as supervised learning: predict the *half-life* `h` of a word in a student's memory given features. The model `p(recall) = 2^{-Δ/h}` is functionally equivalent to exponential decay with `h = S·ln(2)`. Duolingo reported a 45% reduction in recall-prediction error versus baselines and a 12% increase in user engagement after deployment ([Settles & Meeder 2016](https://research.duolingo.com/papers/settles.acl16.pdf)).

**FSRS-5** (Open Spaced Repetition project, 2024) is the current state-of-the-art, deployed at Anki scale (billions of reviews). It models three components:

- **Difficulty** `D` ∈ [1, 10] — how inherently hard the item is
- **Stability** `S` — current memory strength; hours until retrievability falls to 0.9
- **Retrievability** `R(t) = (1 + F·t/S)^C` — **power-law**, with FSRS-5 fitting `F ≈ 0.5, C ≈ -0.5`

Critically, FSRS-5 rejects exponential decay in favour of power-law on empirical grounds: across billions of reviews, the power-law form better predicts long-term recall.

On each review, FSRS-5 updates `S` non-linearly:

```
S_new = S · (1 + e^a · (11 - D) · S^b · (e^{c(1-R)} - 1))   on success
S_new = D^c · S^{-d} · (1 - R)^e · (e^{f(1 - signal)})        on failure
```

Two key behavioural properties fall out:

- **Desirable difficulty** — successful recalls at low retrievability give larger stability gains than at high retrievability. The system rewards intervals where the answer was at the edge of forgetting.
- **Per-item adaptation** — each item has its own `(D, S)` evolving from history. Easy items grow stable quickly; hard items remain fragile.

FSRS-5 fits 19 floating-point parameters from user data. For agents, we either fit these from synthetic traces or ship defaults derived from FSRS's published values.

### 2.3 Generative agents: tripartite scoring

Park et al.'s 2023 "Generative Agents: Interactive Simulacra of Human Behavior" ([arxiv:2304.03442](https://ar5iv.labs.arxiv.org/html/2304.03442)) introduced the **memory stream** architecture that has since become the dominant pattern for LLM agent memory.

Each memory item carries a natural-language description, a creation timestamp, and a most-recent-access timestamp. Retrieval combines three signals normalised to [0, 1]:

- **Recency** — exponential decay since last access
- **Importance** — an LLM-rated integer (1–10) assigned at write time, "is this mundane or core?"
- **Relevance** — cosine similarity between query embedding and item embedding

Min-max normalised and equal-weighted:

```
score = α·recency + β·importance + γ·relevance
```

(Park et al. use `α = β = γ = 1`.)

The architectural commitments here are subtle but consequential:

- **Importance is a separate axis**, not derived from utility or recency. It is set by the LLM at write-time and refreshed only on re-encoding events.
- **No single confidence value** — three signals composed at retrieval, none of them updated by every operation.
- **Recency uses exponential decay** (Park et al. predate FSRS-5's power-law evidence).
- **Items are not actively forgotten** — they fade in ranking but remain in storage.

The Generative Agents paper has inspired the majority of subsequent LLM agent memory work. Its three-axis decomposition is the most widely adopted pattern in 2024–2026 systems.

### 2.4 Production agent memory systems (2024–2026)

A wave of practical agent-memory systems has emerged since 2024, benchmarked against each other on LoCoMo, AMA-Bench, and similar evaluations.

**MemGPT** (Packer et al., Berkeley 2024) treats LLM context as RAM and external memory as disk, with paging operations the LLM can invoke. Scoring is implicit — items are paged based on LLM-issued instructions rather than a continuous score. MemGPT pioneered the OS-inspired memory hierarchy but is token-heavy at scale.

**A-MEM** (2025, [arxiv:2502.12110](https://arxiv.org/pdf/2502.12110)) takes a Zettelkasten approach: each new memory is written as a "note" that automatically links to top-K related existing notes via embedding similarity. The link graph is the central data structure. A-MEM reports 85–93% token reduction versus MemGPT and 2× better multi-hop reasoning. The Zettelkasten linking pattern — where new notes find their place by attaching to existing relevant ones — converges with elfmem's `connect()` operation.

**Mem0** ([arxiv:2504.19413](https://arxiv.org/pdf/2504.19413)) emphasises **memory update operations** (insert / modify / delete) rather than append-only memory. An LLM judges whether a new observation should create a new memory, modify an existing one, or be discarded. Mem0 reports 26% accuracy gains over OpenAI's reference memory implementation at 91% lower latency. The "memory is not append-only" principle is the key architectural commitment.

**MemMachine** ([arxiv:2604.04853](https://arxiv.org/html/2604.04853v1), 2026) leads the LoCoMo benchmark at 0.9169. Its core innovation is **ground-truth preservation**: every original observation is stored verbatim alongside its summaries, so corrupted summaries can be regenerated. The system reaches deeper benchmark scores than Mem0, Zep, Memobase, LangMem, and OpenAI's baseline.

**Memory OS of AI Agent** ([ACL 2025](https://aclanthology.org/2025.emnlp-main.1318.pdf)) formalises an OS-inspired three-tier hierarchy: short-term (in-context), mid-term (recent active), long-term (archived but recoverable). Items migrate between tiers based on access patterns and aging — the explicit borrowing of cache eviction policies (LRU/LFU/ARC) into memory management.

**SimpleMem** (2026) achieved +29.6 points on temporal queries and +23.1 points on multi-hop reasoning by adopting **single-pass hierarchical extraction + multi-signal retrieval**, evidence that the multi-signal pattern continues to dominate as benchmarks grow more demanding.

The cross-cutting pattern: every leading 2024–2026 system uses ≥3 retrieval signals, separates importance/utility from recency, and treats memory as updateable rather than append-only.

### 2.5 Bayesian online inference and knowledge tracing

The educational-AI community has worked on a closely related problem for two decades: **knowledge tracing** — given a student's history of correct/incorrect answers, estimate their current mastery probability for each skill.

**Bayesian Knowledge Tracing** (Corbett & Anderson 1995) models each skill as a Hidden Markov Model with four parameters: `p(learn)`, `p(slip)`, `p(guess)`, `p(forget)`. The posterior `p(knows skill | observations)` is updated by every observed answer. BKT remained the gold standard until ~2015 and is still widely deployed.

**Deep Knowledge Tracing** (Piech et al. 2015) replaced the per-skill HMM with an LSTM over the student's full answer history. Reported large gains but lost the interpretability of BKT's `p(knows)`.

**Bayesian Cognitive-aware Key-Value Memory Networks** (BCKVMN, 2024) is a hybrid that combines DKVMN's memory-network substrate with Bayesian update layers, restoring interpretability while keeping deep-learning expressiveness ([Cognitive-aware Key–Value Memory Networks for Knowledge Tracing](https://www.sciencedirect.com/science/article/abs/pii/S0957417424018001)).

The relevant takeaway for agent memory: **Bayesian sufficient statistics (α, β counts under a Beta-Binomial model) are the canonical way to track "how much do we know about this item" under partial feedback.** This is what knowledge tracing has converged on after 30 years.

A Beta(α, β) posterior has three properties no point-estimate has:

- **Additive evidence** — new observations compose by addition: `(α + s, β + (1-s))`
- **Native uncertainty** — variance `αβ / ((α+β)²(α+β+1))` quantifies how much we know
- **Decay-compatible** — old evidence can be down-weighted via `α' = αλ + s`, `β' = βλ + (1-s)`

These properties dissolve the "rescore clobbers outcome history" defect at architectural level: rescore is simply another evidence event added to the count, not an overwrite.

### 2.6 Continual learning and catastrophic forgetting

Neural-network research has its own version of the problem: how do you train on stream B without destroying what was learned on stream A? This is "catastrophic forgetting" and has been heavily studied since the 1990s.

The dominant strategies divide into four families:

- **Regularisation** — penalise updates that move away from previously-important parameters. **Elastic Weight Consolidation** (Kirkpatrick et al. 2017) uses the Fisher Information Matrix; reduced forgetting on knowledge-graph tasks from 12.62% to 6.85% in 2025 evaluations.
- **Replay** — periodically interleave examples from old streams during new training. **FOREVER** (2026) explicitly adopts the Ebbinghaus forgetting curve to schedule replays.
- **Architecture-based** — assign different parameters to different tasks (PNNs, MoE).
- **Surprise-driven** — **SuRe** (2025, [arxiv:2511.22367](https://arxiv.org/abs/2511.22367)) prioritises replay of high-NLL (most surprising) sequences. Self-Distillation Fine-Tuning (SDFT, MIT/ETH 2025) distills the model's existing knowledge into the new training signal.

The most important conceptual import to agent memory: **forgetting is structural, not accidental**. Without explicit replay/regularisation, learned content actively interferes with new content. The Ebbinghaus curve is empirically observed in LLMs themselves, not just human subjects. Recent results show forgetting in LLMs "exhibits structured temporal patterns resembling human memory decay, with rapid early performance drops followed by slower degradation" ([FOREVER 2026](https://arxiv.org/html/2601.03938v1)).

The corollary for memory systems: **spaced rehearsal of old items is not a nice-to-have. It is a prerequisite for long-term durability.**

---

## 3. Problem formulation

Let `M = {m_1, ..., m_N}` be the memory store. Each item `m_i` has:

- Content `c_i` (text)
- Embedding `e_i ∈ ℝ^D`
- Event log `E_i = [(type, signal, weight, time), ...]`
- Possibly-derived state `θ_i` (sufficient statistics, stability, etc.)

We must define a retrieval function `score: (m_i, query, frame, time) → ℝ` that, given:

- The query embedding `q`
- The retrieval frame (SELF, ATTENTION, TASK, SIMULATE) which determines weighting
- The current time `t`

Returns a real-valued score whose top-K under that score is the "best" memory selection.

We require the system to support four operations beyond retrieval:

- **Promote** — observed positive evidence raises an item's future-retrieval probability
- **Degrade** — observed negative evidence (or disuse) lowers it
- **Re-evaluate** — periodically reassess against drifting SELF
- **Merge** — combine state from a peer agent's view of the same item

The conflation problem we identified empirically is precisely this: most current systems use a single scalar `c_i` updated by every operation, which prevents principled implementations of merge (no arithmetic combines two scalars), re-evaluate (must overwrite to update), and the promote/degrade pair (must use a single update rule for two semantically different events).

The decomposition we'll propose is straightforward: replace `c_i` with three semantically distinct quantities — `importance_i`, `utility_i`, `retrievability_i` — each with its own update rules, composed at retrieval time.

---

## 4. Cross-system synthesis — recurring principles and divergences

### 4.1 Convergent principles

Across the systems surveyed, six principles recur with notable consistency:

#### P1 — Multi-signal scoring

Every leading 2024–2026 system uses ≥3 retrieval signals: Park (recency + importance + relevance), A-MEM (link-graph + relevance), SimpleMem (multi-signal retrieval explicitly named in title). The single-confidence scalar appears nowhere in the SOTA.

The mathematical reason: a single scalar can only represent one one-dimensional ordering. The four properties retrieval must reflect (semantic match, learned utility, freshness, identity-alignment) are not co-monotonic and cannot be projected onto one axis without information loss.

#### P2 — Sufficient-statistics state, not point estimates

BKT stores `(p_learned, observations)`. FSRS-5 stores `(D, S, last_review)`. Mem0 maintains a structured store updateable by LLM operations. None of the leading systems use a single confidence number per item as their state.

The recurring substrate is the **Beta-Binomial sufficient statistics** `(α, β)`: success and failure counts. From these, the posterior mean `α/(α+β)` recovers a "confidence-like" number; the variance `αβ/((α+β)²(α+β+1))` gives uncertainty; new evidence composes by addition.

#### P3 — Per-item adaptive forgetting rate

FSRS's per-card stability is the most heavily validated example. The principle generalises: forgetting rate should be a function of item history, not a global constant or a tier-based one.

Items the system has validated through many positive outcomes should fade slowly. Items repeatedly contradicted by outcomes should fade quickly. Items never tested should fade at a default rate. The forgetting rate is itself a learned quantity.

#### P4 — Power-law retrievability, not exponential

This is the most recent empirical update. Ebbinghaus's exponential form `R(t) = exp(-t/S)` was the default for a century. FSRS-5's power-law form `R(t) = (1 + F·t/S)^C` better fits Anki's billion-review dataset and is now the empirical default for human-memory modeling.

For agent memory, the evidence base is thinner — but the FSRS finding aligns with general scaling-law observations in machine learning and human cognition. Power-law decay produces fatter tails (more long-lived items) which is desirable for systems that should preserve rare-but-valuable knowledge.

#### P5 — Topological structure over flat retrieval

Pure vector-search retrieval (top-K by cosine) is dominated in benchmarks by systems that exploit graph structure: A-MEM Zettelkasten, Zep's temporal knowledge graph, RAPTOR's recursive trees, MemMachine's hierarchical organisation. The improvement is consistent (often 10–20+ points on multi-hop reasoning benchmarks).

The mechanism: spreading activation across graph edges lets memory recall related-but-not-similar items that pure vector search misses. elfmem's existing knowledge-graph substrate already enables this; the question is how aggressively to use it in retrieval.

#### P6 — Event log as audit trail

A-MEM, MemMachine, and FOREVER all store the originating events rather than (or alongside) derived state. The pattern: state is a view computed from events; events are the source of truth.

The architectural payoff is that operations like rescore become **additive** (append a new event with new evidence) rather than **destructive** (overwrite the current state with the new estimate). The rescore-clobbering-outcome bug in elfmem v0.13.3 is exactly the bug this pattern dissolves.

### 4.2 Where SOTA diverges

Three questions remain genuinely open in 2026:

#### D1 — How to weight competing signals

Park 2023 uses equal weights with min-max normalisation. Modern systems sometimes learn weights from data via reinforcement learning or hindsight optimisation. For an open-source library shipped to many users, none of those is appropriate.

The practical answer that appears across systems is **per-frame configurable weights**: SELF frame weights identity-alignment heavily; ATTENTION frame weights freshness heavily; TASK frame weights validated utility heavily. elfmem already implements per-frame weights — the question is which signals each frame should consume.

#### D2 — How to detect "useful retrieval" without explicit feedback

For the system to learn, it needs to know which retrievals were useful and which weren't. Park 2023 relies on LLM-generated reflection. A-MEM relies on explicit agent confirmation. Mem0 uses LLM judgment in the update phase. FSRS-5 relies on explicit student ratings.

None of these is automatic. An open question is whether an agent can self-report (via a `confirm()` API) or whether useful-retrieval must be inferred (the retrieved item's content appears in the agent's next operation, or the operation succeeds with high confidence).

elfmem's `outcome()` mechanism is one specific answer — explicit agent-issued positive/negative feedback after the fact. The question is whether implicit signals (next-step usage patterns) should additionally feed the system.

#### D3 — How to maintain calibration as state evolves

The LLM rating used for importance is itself a noisy measurement. If the LLM systematically over-rates blocks about topic X, blocks about X will dominate retrieval forever. Multi-day calibration drift is observed in production systems ([State of AI Agent Memory 2026](https://mem0.ai/blog/state-of-ai-agent-memory-2026)).

Mitigations proposed in the literature: (a) periodic rescoring against current state (elfmem's deep-sleep rescore), (b) explicit calibration runs against held-out test sets, (c) Bayesian variance penalising over-confident estimates. None is universally adopted.

### 4.3 The convergence model

Synthesising the convergent principles produces a candidate model whose shape recurs in nearly every successful system:

```
state per item:
    embedding e
    event log E = [...]
    derived: (α, β) — Bayesian sufficient statistics for utility
    derived: stability S, difficulty D — FSRS-style forgetting parameters
    semi-derived: importance i — LLM-rated, refreshed periodically

score at query time = weighted sum of (
    semantic similarity to query,
    utility posterior mean,
    retrievability R(t),
    importance i,
    exploration bonus proportional to uncertainty
)

every operation appends to event log; state is updated incrementally;
nothing is overwritten.
```

This is the shape we'll formalise in §5.

---

## 5. Proposed framework

### 5.1 Architectural decomposition

We propose decomposing memory state into three orthogonal channels:

| Channel | Stored as | Updated by | Interpretation |
|---|---|---|---|
| **Importance** | scalar `i ∈ [0,1]` | LLM rating at write; refreshed by rescore | "How core or mundane is this content with respect to current SELF?" |
| **Utility** | sufficient stats `(α, β)` | every outcome() event; consolidate folds in LLM rating as evidence | "How often has this proved correct/useful when applied?" |
| **Retrievability** | stability `S`, difficulty `D`, last-event time | every relevant event; FSRS-style updates | "How available is this item right now under power-law forgetting?" |

The three channels capture semantically distinct dimensions. None is derivable from the others; each updates by different events; each contributes to retrieval scoring differently across frames.

### 5.2 Power-law retrievability with adaptive stability

Following FSRS-5, retrievability for item `i` at time `t` is:

```
R_i(t) = (1 + F · (t - t_last_event_i) / S_i)^C
```

With FSRS-5 defaults `F = 0.5, C = -0.5`. At `t = t_last_event`, `R = 1.0`. As `t - t_last` grows, `R` falls following a power-law (slower than exponential).

Stability `S_i` updates on every retrievability-relevant event. On positive outcome:

```
S_new = S · (1 + e^a · (11 - D) · S^b · (e^{c(1-R)} - 1))
```

On negative outcome or contradicting evidence:

```
S_new = D^c · S^{-d} · (1 - R)^e · e^{f·(1 - signal)}
```

Coefficients `(a, b, c, d, e, f)` are FSRS-5 defaults, made configurable for elfmem-specific tuning over time.

Difficulty `D_i` updates on outcomes as well, sliding upward on failures and downward on successes:

```
delta_D = -k · (signal - 0.5) · 2.0
D_new = clamp(D + (10 - D) · delta_D / 9.0, 1.0, 10.0)
```

Where `k` is a learning-rate coefficient.

### 5.3 Bayesian utility with sufficient statistics

Following 30 years of knowledge tracing precedent, utility is modeled as a Beta-Binomial posterior over a hidden "is-this-useful" parameter `θ_i ∈ [0,1]`. We store two counters per block:

```
α_i — accumulated success evidence (positive observations)
β_i — accumulated failure evidence (negative observations)
```

The mean utility is `α/(α+β)`; the variance is `αβ/((α+β)²(α+β+1))`. New evidence updates additively:

```
on outcome(signal, weight):
    α += signal * weight
    β += (1 - signal) * weight
```

On consolidate (LLM rating folded as evidence, weighted):

```
α += LLM_alignment * w_llm
β += (1 - LLM_alignment) * w_llm
```

On peer import (other agent's view of the same content, trust-weighted):

```
α += α_other * trust
β += β_other * trust
```

The architectural payoff: **rescore is no longer destructive**. Periodic re-evaluation against drifting SELF folds in a new evidence event with weight `w_rescore`, adding to the counts. Past outcomes are never overwritten.

Optional: time-weight decay of old evidence via `α' = α·λ + s`, `β' = β·λ + (1-s)` with decay coefficient `λ ∈ (0, 1]`. With `λ = 1` evidence is permanent; with `λ < 1` old evidence is gradually downweighted. This is the standard online-learning extension.

### 5.4 Importance as a separate axis

Following Park 2023, **importance is rated by the LLM at write time and refreshed by deep-sleep rescore**. It is *not* updated by retrieval-side events. The semantic distinction:

- **Importance** answers "is this content fundamentally core to the agent's identity?"
- **Utility** answers "has this content proven useful when applied?"

A block can be high-importance but never used (a deeply-held principle not yet tested). A block can be high-utility but low-importance (an effective heuristic for a narrow domain). These are uncorrelated quantities.

elfmem's existing `analysis.alignment_score` already plays this role — we relabel it `importance` to match Park's terminology and clarify the semantics.

### 5.5 Event log as source of truth

A new `block_events` table stores every retrieval-relevant operation:

```
block_events:
    block_id        TEXT
    event_type      TEXT   -- learn, consolidate, outcome, recall_hit,
                            -- rescore, peer_import, connect
    signal          REAL   -- [0,1] for outcome-type events
    weight          REAL   -- evidence strength
    t               REAL   -- event time in active hours
```

Every memory-touching operation appends a row. The derived state on `blocks` is updated incrementally — when an outcome event is logged, `α`, `β`, `S`, and `D` are updated atomically. The event log itself is never modified.

This pattern decouples derivation from storage: at any point we can recompute derived state from the event log, allowing us to change update formulas in future versions without data migration. It also enables audit, replay, and what-if analysis ("what if we had used different update rules?").

### 5.6 Composite retrieval scoring

The retrieval score combines five terms:

```
score(m_i, query, frame, t) =
      w_sim         · cosine(query, m_i.embedding)
    + w_utility     · α_i / (α_i + β_i)
    + w_retrieve    · R_i(t)
    + w_importance  · importance_i
    + κ             · sqrt(utility_variance_i)
```

The exploration term `κ · sqrt(variance)` is Thompson-sampling-inspired: items with high utility uncertainty get a small bonus, lifting them into retrieval for exploration. This is the mathematical answer to "fresh blocks fail top-K" — uncertainty is high for new items, so they get a discoverable exploration lift.

Frame-specific weight choices (initial proposed defaults, to be tuned):

| Frame | w_sim | w_util | w_retrieve | w_importance | κ |
|---|---|---|---|---|---|
| SELF | 0.30 | 0.10 | 0.15 | 0.40 | 0.05 |
| ATTENTION | 0.55 | 0.15 | 0.20 | 0.05 | 0.05 |
| TASK | 0.40 | 0.30 | 0.15 | 0.10 | 0.05 |
| SIMULATE | 0.45 | 0.15 | 0.20 | 0.15 | 0.05 |

SELF frame retrieves identity-defining content — weighted heavily on importance. ATTENTION frame retrieves recently-relevant context — weighted on freshness and similarity. TASK frame retrieves proven approaches — weighted on utility. SIMULATE frame blends SELF and ATTENTION for theory-of-mind reasoning. These should be empirically tuned.

### 5.7 Graph integration: Zettelkasten linking

elfmem already implements a knowledge graph via `connect()`. We propose to extend it with **automatic Zettelkasten-style linking** during dream consolidation:

- On consolidate, compute top-K most-similar existing active blocks for each new block.
- For each candidate `(new, existing)` pair above a similarity threshold, propose an edge.
- Edges are proposed at low initial weight; outcomes-on-either-side reinforce them.

The retrieval score gains an optional graph-spreading term (used in SIMULATE frame primarily):

```
score' = score + w_graph · max_neighbor_score(item, query)
```

This spreads activation through the graph — neighbors of high-scoring items get a lift. The mechanism is faithful to ACT-R's contextual term and to A-MEM's Zettelkasten architecture.

### 5.8 Hierarchical compression

Following MemoryOS and SimpleMem, we propose three representation tiers per cluster of related blocks:

| Tier | Representation | Cardinality |
|---|---|---|
| Raw | original block content | always 1:1 |
| Summary | LLM-condensed version | 1 per dream cycle that touched the block |
| Abstract | high-level "what is this cluster about" | created when cluster reaches N members |

Retrieval can begin at the abstract tier for breadth, descend to summaries for context, drop to raws for verbatim citation. This addresses the 15–43-point quarterly degradation observed in 2026 benchmarks (largely a context-bloat problem) by making the most-recently-relevant content available without serializing the full memory.

elfmem already has summary blocks; the abstract tier is the new addition.

---

## 6. Mathematical formalism

### 6.1 Notation

| Symbol | Definition |
|---|---|
| `m_i` | i-th memory item |
| `t` | current time (active hours) |
| `t^{last}_i` | time of last event on item i |
| `E_i` | event log for item i |
| `α_i, β_i` | Beta-Binomial success/failure counts |
| `S_i, D_i` | FSRS stability, difficulty |
| `importance_i` | LLM-rated [0,1] |
| `R_i(t)` | retrievability at time t |
| `u_i` | utility posterior mean |
| `v_i` | utility posterior variance |

### 6.2 Derived quantities

```
u_i        = α_i / (α_i + β_i)                                — posterior mean
v_i        = α_i β_i / ((α_i + β_i)² (α_i + β_i + 1))         — posterior variance
R_i(t)     = (1 + 0.5 · (t - t^{last}_i) / S_i)^{-0.5}        — FSRS-5 retrievability
```

### 6.3 Properties

| Property | Holds? | Notes |
|---|---|---|
| Monotonicity in α | Yes (∂u/∂α > 0) | More positive evidence → higher utility |
| Monotonicity in t | R decreasing in t | Older items have lower retrievability |
| Continuity | Yes for all signals | No cliffs, smooth differentiable |
| Idempotence of no-op | Yes | Adding `(0, 0)` evidence leaves `(α, β)` unchanged |
| Composability over peers | Yes for utility | `(α_1 + α_2, β_1 + β_2)` is a valid posterior |
| Composability over time | Yes via event log | Replay possible |
| Calibration to LLM signal | Honest under `w_llm = 1.0` | `(0.5·w_llm + α, 0.5·w_llm + β)` treats LLM as one observation |
| Native uncertainty | Yes | Variance gives exploration bonus |
| Range coverage | Full [0,1] for u; [0,1] for R | Both signals can reach any value in [0,1] |

### 6.4 Score function specification

```
score(m_i, query, frame, t):
    sim         = cosine(query_embedding, m_i.embedding)
    utility     = u_i
    retrieve    = R_i(t)
    importance  = m_i.importance
    explore     = κ · sqrt(v_i)
    
    return  frame.w_sim       · sim
          + frame.w_utility   · utility
          + frame.w_retrieve  · retrieve
          + frame.w_importance · importance
          + frame.kappa        · explore
```

All five terms are in [0, 1]; the score is in [0, sum_of_weights]. With weights summing to ≤ 1, scores are in [0, 1]. With κ in [0, 0.1], the exploration term is bounded.

### 6.5 Update rules

**On `learn` (new block written to inbox):**

```
α_i = w_init · 0.5
β_i = w_init · 0.5
S_i = S_init               (default 24 hours)
D_i = D_init               (default 5.0)
importance_i = 0.5         (refined at consolidate)
t^{last}_i = t
append event(type=learn, weight=w_init)
```

**On `consolidate` (LLM-rated, promoted to active):**

```
importance_i = LLM_alignment_score
α_i += LLM_alignment_score · w_llm
β_i += (1 - LLM_alignment_score) · w_llm
t^{last}_i = t
append event(type=consolidate, signal=LLM_alignment_score, weight=w_llm)
```

**On `outcome(signal, weight)`:**

```
α_i += signal · weight
β_i += (1 - signal) · weight
S_i = update_stability(S_i, D_i, R_i(t), signal)
D_i = update_difficulty(D_i, signal)
t^{last}_i = t
append event(type=outcome, signal=signal, weight=weight)
```

**On `rescore` (deep-sleep, refresh against current SELF):**

```
importance_i = new_LLM_alignment_score
α_i += new_LLM_alignment_score · w_rescore
β_i += (1 - new_LLM_alignment_score) · w_rescore
t^{last}_i = t
append event(type=rescore, signal=new_LLM_alignment_score, weight=w_rescore)
```

Crucially, rescore is **additive** — it does not overwrite prior outcome evidence.

**On `peer_import` (other agent's view of the same content):**

```
α_i += α_other · trust_factor
β_i += β_other · trust_factor
S_i = max(S_i, S_other · trust_factor)
t^{last}_i = t
append event(type=peer_import, signal=α_other/(α_other+β_other), weight=trust_factor)
```

This makes peer merging arithmetic — the only sufficient-statistics-based merge that exists.

---

## 7. Implications and challenges

### 7.1 Scale: event log growth

The event log grows monotonically with system age. At 100,000 blocks × N events per block × M bytes per event, storage costs are real but bounded. FSRS-5 ships with billions of reviews in production via simple SQL backends; the scaling problem is solvable but needs attention.

Mitigations:
- **Tiered retention**: keep last 90 days verbatim, summary statistics for older.
- **Pruning archival**: when a block is archived, prune its event log to summary statistics; preserve `α`, `β`, `S`, `D` only.
- **Event compaction**: dream cycles can compact runs of similar events into aggregate events (e.g., 5 positive outcomes within 24 hours → one aggregate event with weight=5).

### 7.2 Calibration

The proposal trusts the LLM's `alignment_score` as evidence. If the LLM is miscalibrated (overconfident on some topics, underconfident on others), the system inherits this. The variance term `v_i` partially compensates — items the LLM is uncertain about get exploration bonuses — but no mechanism fully eliminates LLM calibration drift.

The recommended mitigation: **explicit recalibration runs**. Periodically (perhaps monthly), the system runs a held-out test set against current LLM behavior and updates the `w_llm` weight if systematic miscalibration is detected. This is implementable as an `elfmem doctor --recalibrate` command.

### 7.3 Migration from existing systems

elfmem currently stores a single `confidence` scalar updated by consolidate, outcome, and rescore. Migration to the proposed framework requires:

1. **Schema migration**: add `success_count`, `failure_count`, `stability`, `difficulty`, `last_event_t` columns. Bootstrap from current values: `α = c · κ`, `β = (1-c) · κ` with `κ = prior_strength`. Stability bootstrapped from `decay_lambda` (S = 1/λ).
2. **Event log bootstrap**: synthesise events from existing `block_outcomes` table (one event per past outcome) and from current block state (one "consolidate" event from existing confidence).
3. **Backwards compatibility**: keep `confidence` column as a denormalised view (`confidence = α/(α+β)`); existing readers see equivalent values.

Migration is one-time per database, additive (no data loss), and reversible (we keep `confidence` until v0.18.0 at least).

### 7.4 Privacy and security

The event log is sensitive — it captures detailed usage patterns. Two concerns:

- **Peer merging**: a malicious peer could flood low-quality blocks to corrupt utility estimates. Mitigation: trust-weighted merge with per-source rate limits.
- **Audit replay**: the event log enables exact reconstruction of agent behavior. Mitigation: encrypt at rest if user opt-in; provide `elfmem privacy --forget block_id` that purges events for a specific item.

### 7.5 Implementation complexity

The framework adds ~2 schema columns, one new table, and ~500 LOC across consolidate, outcome, rescore, and scoring. The complexity is real but bounded; FSRS-5 has been implemented in 100 lines of Python ([Borretti's implementation](https://borretti.me/article/implementing-fsrs-in-100-lines)).

Per-frame weight tuning is the largest hidden cost — empirical evaluation against test workloads is required to set sensible defaults.

---

## 8. Open problems and future work

### 8.1 Calibration of LLM-as-evidence

The framework treats LLM alignment_score as one observation worth `w_llm` evidence. Is `w_llm = 1.0` (one outcome event) the right calibration? If the LLM is more reliable than a single outcome, `w_llm` should be higher; if less, lower. Empirical work on LLM-as-judge calibration is ongoing in the broader ML community.

### 8.2 Detecting useful retrieval

Without explicit feedback, the system cannot reinforce items that were retrieved and proved useful. Park 2023 uses reflection; A-MEM uses confirmation; FSRS-5 uses explicit ratings. For elfmem, an explicit `confirm()` API or implicit "next-operation-references-block" heuristic both have merit. This deserves its own design study.

### 8.3 Optimal κ for exploration bonus

Thompson sampling theory suggests κ ≈ 1.0 in normalised form; for our composite score the right value depends on the variance distribution. Empirical tuning required.

### 8.4 FSRS-5 parameters for non-flashcard domains

FSRS-5 is fit to flashcard-review data. Whether the same parameters generalise to agent-memory data (where "reviews" are heterogeneous events) is an open question. A user-fit version of FSRS for elfmem deployment data is worth exploring.

### 8.5 Multi-frame composition

The framework specifies per-frame weights but does not address what happens when an agent's needs span frames (e.g., "what do I know about X and how is it relevant to my current task?"). Composing frames principled — perhaps as a mixture distribution — is open.

### 8.6 Event log compaction without loss

The pruning strategies in §7.1 are lossy in principle (aggregate events lose timing information). Designing compaction that preserves what matters for scoring (recency-weighted aggregate counts) while discarding bulk is an applied research question.

### 8.7 Hierarchical retrieval algorithm

The three-tier representation (raw/summary/abstract) needs a concrete retrieval algorithm: when do we ascend the hierarchy versus stay at raw? MemoryOS uses an OS-style page-fault analogy; whether that's the best approach for our use case is open.

### 8.8 Empirical benchmark for elfmem

elfmem currently lacks a head-to-head benchmark against MemMachine, A-MEM, Mem0 on LoCoMo or AMA-Bench. Building this benchmark — or contributing elfmem as an entrant to existing benchmarks — would let us empirically compare design choices.

---

## 9. Conclusion

The question "how should an agent's memory rank itself?" has been studied for five decades across cognitive science, education, and machine learning. The convergent answer across these traditions:

1. **Decompose** the question into orthogonal axes — importance, utility, retrievability, semantic match — rather than collapsing them into a single confidence scalar.
2. **Store sufficient statistics**, not point estimates — `(α, β)` Beta-Binomial counts compose additively under new evidence.
3. **Adapt forgetting per item**, not by global rule — FSRS-5's per-item stability is the most empirically validated example.
4. **Use power-law retrievability**, not exponential — the empirical evidence from billions of reviews favours `(1 + F·t/S)^C` over `exp(-t/S)`.
5. **Build retrieval over a graph**, not a flat list — topological structure consistently outperforms vector search.
6. **Treat events as the source of truth**, with derived state as a view — this makes update operations additive, rescore non-destructive, and peer merging arithmetic.

These six principles are not novel — each has been articulated in multiple traditions for years. What is novel in 2026 is their convergence into a single architecture that production agent-memory systems are independently arriving at. elfmem can adopt this convergence directly, building on its existing strong substrate (knowledge graph, outcome-driven evidence, four-rhythm consolidation) rather than re-inventing.

The proposed framework — three orthogonal channels, sufficient-statistics state, FSRS-5 retrievability, event-log audit trail, frame-weighted composite scoring with exploration bonus — synthesises five decades of work into a concrete architecture for elfmem v0.16–v0.19. The architecture is principled, scalable, and addresses the specific defects observed in the current system (cliff, rescore clobber, cold-start gap, peer-merge incoherence) at the structural level rather than through symptomatic patches.

What remains is empirical validation: per-frame weight tuning, FSRS-5 parameter fitting for our domain, head-to-head benchmarking against the SOTA. These are bounded engineering problems, not open architectural ones. The framework is ready for implementation planning.

---

## References

### Cognitive science foundations
- Anderson, J.R. (1996). "ACT: A simple theory of complex cognition." *American Psychologist*.
- Ebbinghaus, H. (1885). *Über das Gedächtnis*. (English translation: *Memory: A Contribution to Experimental Psychology*, 1913.)

### Spaced repetition
- [The FSRS Algorithm — open-spaced-repetition Wiki](https://github.com/open-spaced-repetition/free-spaced-repetition-scheduler)
- [Expertium: A Technical Explanation of FSRS](https://expertium.github.io/Algorithm.html)
- [Borretti: Implementing FSRS in 100 Lines](https://borretti.me/article/implementing-fsrs-in-100-lines)
- [Denicola: Spaced Repetition Systems Have Gotten Way Better](https://domenic.me/fsrs/)
- [Settles & Meeder (2016) — A Trainable Spaced Repetition Model for Language Learning (Duolingo HLR)](https://research.duolingo.com/papers/settles.acl16.pdf)
- [PNAS: Enhancing human learning via spaced repetition optimization](https://www.pnas.org/doi/pdf/10.1073/pnas.1815156116)

### Generative agents and LLM agent memory
- [Park et al. (2023) — Generative Agents: Interactive Simulacra of Human Behavior](https://ar5iv.labs.arxiv.org/html/2304.03442)
- [A-Mem: Agentic Memory for LLM Agents (2025)](https://arxiv.org/pdf/2502.12110)
- [Mem0: Building Production-Ready AI Agents with Scalable Long-Term Memory (2025)](https://arxiv.org/pdf/2504.19413)
- [MemMachine: Ground-Truth-Preserving Memory System (2026)](https://arxiv.org/html/2604.04853v1)
- [Memory OS of AI Agent (EMNLP 2025)](https://aclanthology.org/2025.emnlp-main.1318.pdf)
- [State of AI Agent Memory 2026 — mem0.ai](https://mem0.ai/blog/state-of-ai-agent-memory-2026)
- [Memory for Autonomous LLM Agents: Mechanisms, Evaluation, Frontiers (survey, 2026)](https://arxiv.org/html/2603.07670v1)
- [LLM Agent Memory: A Survey from a Unified Representation–Management Perspective](https://www.preprints.org/manuscript/202603.0359)
- [AMA-Bench: Evaluating Long-Horizon Memory for Agentic Applications](https://arxiv.org/html/2602.22769v1)
- [Dynamic Human-like Memory Recall and Consolidation in LLM-Based Agents (2026)](https://arxiv.org/html/2404.00573v1)
- [Enhancing Memory Retrieval in Generative Agents — Frontiers (2025)](https://www.frontiersin.org/journals/psychology/articles/10.3389/fpsyg.2025.1591618/full)
- [Memory Systems for AI Agents — Steve Kinney](https://stevekinney.com/writing/agent-memory-systems)

### Knowledge tracing
- Corbett, A.T. & Anderson, J.R. (1995). "Knowledge Tracing: Modeling the Acquisition of Procedural Knowledge."
- Piech, C. et al. (2015). "Deep Knowledge Tracing."
- [Bayesian Cognitive-aware Key-Value Memory Networks for Knowledge Tracing (2024)](https://www.sciencedirect.com/science/article/abs/pii/S0957417424018001)
- [A Survey of Knowledge Tracing: Models, Variants, and Applications](https://arxiv.org/html/2105.15106v4)
- [Deep Learning Based Knowledge Tracing: A Review (2025)](https://dl.acm.org/doi/10.1145/3729605.3729620)

### Continual learning and catastrophic forgetting
- Kirkpatrick, J. et al. (2017). "Overcoming catastrophic forgetting in neural networks." (EWC) *PNAS*.
- [FOREVER: Forgetting Curve-Inspired Memory Replay for Language Model Continual Learning (2026)](https://arxiv.org/html/2601.03938v1)
- [SuRe: Surprise-Driven Prioritised Replay for Continual LLM Learning (2025)](https://arxiv.org/abs/2511.22367)
- [Continual Learning of Large Language Models: A Comprehensive Survey (CSUR 2025)](https://dl.acm.org/doi/10.1145/3735633)

### Internal artefacts
- elfmem repository, `/tmp/confidence_sim.py` — 5-mapping × 5-scenario simulation
- elfmem `docs/plans/plan_confidence_architecture.md` — preliminary architectural plan (untracked)
- elfmem `docs/coding_principles.md` — implementation constraints (SIMPLE, ELEGANT, FLEXIBLE, ROBUST)

---

*This document is research, not yet design. The framework proposed here is a synthesis of prior work; the specific choices for elfmem (parameter values, weight defaults, migration sequencing) are open for review and refinement. A planning document will follow.*
