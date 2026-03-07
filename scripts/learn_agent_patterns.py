#!/usr/bin/env python
"""Extract and learn agent usage patterns for elfmem into memory."""
import asyncio
from elfmem.smart import SmartMemory

AGENT_PATTERNS = [
    # Remember (Learn) Patterns
    {
        "content": (
            "Remember after surprise: When observation differs from expectation, it signals "
            "a knowledge gap worth learning. Surprise magnitude indicates learning value. "
            "Formula: if |observation - expectation| > threshold, remember(content, confidence=0.5 + surprise_magnitude). "
            "Why: surprises reveal model gaps; high surprise = high learning value."
        ),
        "tags": ["agent-pattern/remember", "learning-mechanism", "core-heuristic"],
    },
    {
        "content": (
            "Remember patterns, not events: Store generalizable rules and behaviors, not "
            "specific occurrences. 'When concurrent writes exceed 100/sec, implement connection pooling' "
            "(pattern) beats 'API failed at 14:32 on 2026-03-07' (event). "
            "Why: Patterns transfer to new situations; events are non-transferable. "
            "Natural decay will remove non-reinforced noise anyway."
        ),
        "tags": ["agent-pattern/remember", "knowledge-structure", "core-heuristic"],
    },
    {
        "content": (
            "Remember connections: When learning something new, search existing memory for "
            "related blocks and explicitly note relationships. Store connections as edges in the graph. "
            "Why: Isolated knowledge decays without reinforcement pathways. Connected knowledge compounds "
            "when retrieved—reinforcing one pattern can reinforce related patterns through edges."
        ),
        "tags": ["agent-pattern/remember", "graph-structure", "learning-mechanism"],
    },
    {
        "content": (
            "Tag hierarchically for retrieval: Use semantic hierarchies (domain/category/subcategory) "
            "instead of flat tags like 'learned' or 'important'. Example: 'programming/python/concurrency' "
            "enables multi-grain queries and precise filtering. "
            "Why: Flat tags force broad queries (low precision). Hierarchies enable you to tune specificity."
        ),
        "tags": ["agent-pattern/remember", "metadata-strategy", "retrieval-optimization"],
    },
    {
        "content": (
            "Confidence reflects actual certainty, not recency: Confidence should represent "
            "reliability: 0.3 = seen once, contradictions exist; 0.7 = multiple confirmations, "
            "tested in varied conditions; 0.95 = deeply validated, used successfully 10+ times. "
            "Don't confuse with recency (decay handles freshness) or importance (reinforcement handles that). "
            "Why: Retrieved high-confidence blocks are more likely useful; agents weight low-confidence knowledge appropriately."
        ),
        "tags": ["agent-pattern/remember", "confidence-calibration", "core-heuristic"],
    },
    # Recall (Retrieve) Patterns
    {
        "content": (
            "Frame selection is task-dependent: Choose retrieval frame by task type. "
            "ATTENTION → novel problems (broad scope). TASK → goal-focused execution. SELF → values/identity conflicts. "
            "WORLD → understanding context. SHORT_TERM → quick lookup. "
            "Why: Different tasks need different knowledge. Frame optimization dramatically improves retrieval quality."
        ),
        "tags": ["agent-pattern/recall", "frame-selection", "core-heuristic"],
    },
    {
        "content": (
            "Query semantically for maximum relevance: Formulate queries by intent, not keywords. "
            "'concurrent programming patterns' beats 'python'. 'API error handling best practices' is better "
            "than 'TypeError in line 42'. Goldilocks zone: specific enough to be useful, general enough to transfer. "
            "Why: Semantic queries capture intent; too broad = noise; too specific = no transfer."
        ),
        "tags": ["agent-pattern/recall", "query-strategy", "retrieval-optimization"],
    },
    {
        "content": (
            "Handle contradictions via recursive recall: When blocks contradict, don't pick one arbitrarily. "
            "Query SELF frame (do my values guide me?) and WORLD frame (what's broader context?). "
            "Design resolution experiment to understand conflict. "
            "Why: Contradictions signal incomplete model or domain instability. Ignoring them compounds confusion. "
            "Recursive frames provide context for genuine resolution."
        ),
        "tags": ["agent-pattern/recall", "conflict-resolution", "learning-mechanism"],
    },
    {
        "content": (
            "Silence is signal—empty recall is a knowledge gap: When recall(query) returns nothing, "
            "that's important information. Action depends on phase: exploration phase → explore and remember. "
            "Execution phase → be very careful (untested). High-stakes → STOP and design experiment first. "
            "Why: Gaps are highest-learning opportunities. Treating gaps seriously leads to robust knowledge."
        ),
        "tags": ["agent-pattern/recall", "gap-detection", "learning-mechanism"],
    },
    {
        "content": (
            "Expand graph when top-K feels insufficient: If top-5 by similarity don't satisfy, "
            "use graph expansion to recover related-but-not-similar knowledge. "
            "Useful when: task is novel, domain is interconnected, need broad context. "
            "Why: Similarity may miss connected context. Graph expansion retrieves knowledge you didn't know was relevant."
        ),
        "tags": ["agent-pattern/recall", "graph-expansion", "retrieval-optimization"],
    },
    # Outcome (Reinforce) Patterns
    {
        "content": (
            "Signal as expectation - observation: Close the feedback loop by storing expectations before acting, "
            "then comparing observation after. signal = |observation - expectation| / max_range. "
            "Expected 'success', got 'timeout' → signal = 0.1 (bad prediction). "
            "Expected 'timeout', got 'timeout' → signal = 0.5 (model correct, no learning). "
            "Why: Surprise captures learning value. Mispredictions drive learning."
        ),
        "tags": ["agent-pattern/outcome", "feedback-loop", "core-heuristic"],
    },
    {
        "content": (
            "Weight reflects confidence in signal, not importance of outcome: "
            "Tight feedback loops (action → outcome in seconds) weight = 1.0. "
            "Loose loops (action → outcome in days) weight = 0.5. Noisy environment weight = 0.3. "
            "Why: Tight feedback = reliable learning. Loose feedback = learn slower to avoid false confidence. "
            "Weight calibration prevents over-learning from uncertain signals."
        ),
        "tags": ["agent-pattern/outcome", "signal-calibration", "core-heuristic"],
    },
    {
        "content": (
            "Batch outcomes to reduce noise: Don't signal on every action. Collect related outcomes, "
            "compute average signal and confidence = 1.0 - std(outcomes). Batch reveals true signal; "
            "single outcomes are influenced by randomness. "
            "Why: Averaging reveals underlying pattern. Consistency (low variance) indicates reliable learning."
        ),
        "tags": ["agent-pattern/outcome", "signal-filtering", "learning-mechanism"],
    },
    {
        "content": (
            "Reinforce patterns, not events: After successful action, identify which retrieved blocks enabled success. "
            "Reinforce the reasoning (pattern) that transferred, not the specific event. "
            "Tag source: 'successful_execution'. This creates audit trail. "
            "Why: Reinforcing patterns scales to new situations. Reinforcing events doesn't transfer."
        ),
        "tags": ["agent-pattern/outcome", "reinforcement-strategy", "learning-mechanism"],
    },
    {
        "content": (
            "Penalize confidently-wrong more than weakly-wrong: High-confidence errors are more damaging. "
            "Block with confidence=0.9 that was wrong gets harsher penalty than confidence=0.3 that was wrong. "
            "Formula: signal_adjustment = 1.0 - block.confidence. "
            "Why: Prevents over-confidence accumulation. Low-confidence knowledge is expected to be unreliable."
        ),
        "tags": ["agent-pattern/outcome", "confidence-dynamics", "learning-mechanism"],
    },
    # Curate (Maintain) Patterns
    {
        "content": (
            "Trigger curation on accumulation OR stability: Curate when blocks_in_inbox > 50 (too many uncommitted). "
            "Or when days_since_last_curate > 7 (time-based). Or when new_blocks_in_session < 2 AND time_in_session > 2h "
            "(stability signal—learning has slowed). "
            "Why: Accumulation hurts retrieval. Stability is right time to consolidate what you learned."
        ),
        "tags": ["agent-pattern/curate", "maintenance-trigger", "core-heuristic"],
    },
    {
        "content": (
            "Preserve constitutional blocks at all costs: Constitutional blocks (tagged 'self/constitutional') "
            "have PERMANENT decay (λ = 0.00001). They are identity. Never archive them. Keep confidence = 1.0. "
            "Auto-guaranteed in SELF frame retrieval. Auto-reinforced during curate. "
            "Why: Constitutional blocks are bedrock. If identity decays, agent becomes directionless."
        ),
        "tags": ["agent-pattern/curate", "identity-preservation", "core-heuristic"],
    },
    {
        "content": (
            "Reinforce top-K by recent usage: After curation, boost confidence of top 10 blocks "
            "used in last 7 days: confidence = min(0.99, confidence + 0.05). Reset decay timer. "
            "Why: Knowledge you use often should survive. Reinforcement combats natural decay. "
            "Creates virtuous cycle: use → curate → reinforced → more useful."
        ),
        "tags": ["agent-pattern/curate", "knowledge-maintenance", "learning-mechanism"],
    },
    {
        "content": (
            "Archive weak edges aggressively: Prune edges with confidence < 0.3 that haven't been "
            "traversed in 30+ days. Don't delete (reversible needed), archive them. "
            "Why: Weak edges create false paths and retrieval noise. If edge hasn't been useful in 30 days, "
            "probably not valuable. Cleaner graph = more reliable retrieval."
        ),
        "tags": ["agent-pattern/curate", "graph-maintenance", "retrieval-optimization"],
    },
    # High-Level Patterns
    {
        "content": (
            "Knowledge lifecycle: BIRTH (remember with low confidence after surprise) → GROWTH (multiple "
            "uses increase confidence, edges strengthen) → MATURITY (confidence 0.7-0.95, well-connected, "
            "regularly retrieved) → DECAY (not used 12+ days, confidence drifts) → ARCHIVE (explicit curation). "
            "Why: Understanding lifecycle prevents premature death of good knowledge and persistent storage of noise."
        ),
        "tags": ["agent-pattern/lifecycle", "knowledge-dynamics", "core-heuristic"],
    },
    {
        "content": (
            "Session management cycle: START → recall recent context (last 24h). DURING → normal operation, "
            "batch outcomes. END → curate blocks, reinforce top patterns, reflect on surprises. "
            "Reflection questions: learning rate healthy? unresolved contradictions? which patterns did I rely on? "
            "what surprised me? what should I curate/archive? "
            "Why: Sessions are natural consolidation points. Reflection converts experience to learning."
        ),
        "tags": ["agent-pattern/session", "workflow-pattern", "learning-mechanism"],
    },
    {
        "content": (
            "Multi-domain agents: Early (domains separate) → use domain-specific frames, keep SELF unified. "
            "Growth (domains interleave) → create cross-domain connectors, tag transfers. Mature (stable patterns) → "
            "extract meta-knowledge that applies everywhere. Selective integration → merge frames only when domains interleave. "
            "Why: Prevents false transfer early; enables genuine synthesis when appropriate."
        ),
        "tags": ["agent-pattern/multidomain", "workflow-pattern", "learning-mechanism"],
    },
    # Anti-Patterns
    {
        "content": (
            "Anti-pattern: Remember everything. Symptom: hundreds of low-confidence blocks, retrieval returns 50+ "
            "irrelevant results. Why it fails: Events don't transfer; noise accumulates faster than useful knowledge. "
            "Fix: Remember only patterns (generalizable rules), surprising outcomes, or transferable lessons. "
            "Trust decay to remove non-reinforced noise."
        ),
        "tags": ["agent-pattern/anti-pattern", "learning-mistake", "debugging"],
    },
    {
        "content": (
            "Anti-pattern: No reinforcement/feedback loop. Symptom: Knowledge gradually becomes stale; agent forgets "
            "what worked; useful knowledge dies. Why it fails: Without signals, all knowledge decays equally; useful "
            "patterns can't compound. Fix: After significant actions, collect outcome signal. Batch for reliability. "
            "Reinforce blocks that guided success. Curate periodically."
        ),
        "tags": ["agent-pattern/anti-pattern", "learning-mistake", "debugging"],
    },
    {
        "content": (
            "Anti-pattern: Ignore contradictions. Symptom: Retrieve Block A and Block B that contradict; pick one "
            "arbitrarily; contradiction festers. Why it fails: Contradictions indicate incomplete model; ignoring them "
            "compounds confusion. Fix: Flag contradictions immediately. Query SELF/WORLD for context. Design resolution "
            "experiment. Resolve explicitly, then re-learn."
        ),
        "tags": ["agent-pattern/anti-pattern", "learning-mistake", "debugging"],
    },
    {
        "content": (
            "Anti-pattern: Generic tags. Symptom: tags=['learned', 'important', 'fact']. Why it fails: Tags become "
            "noise (every block identical); can't filter. Fix: Use semantic hierarchy (domain/category/subcategory). "
            "Examples: 'programming/python/concurrency', 'pattern/optimization', 'self/value'. "
            "Specific enough to filter; general enough to transfer."
        ),
        "tags": ["agent-pattern/anti-pattern", "metadata-mistake", "debugging"],
    },
]


async def learn_patterns(db_path: str, config_path: str | None = None) -> None:
    """Learn agent usage patterns into elfmem."""
    async with SmartMemory.managed(db_path, config=config_path) as mem:
        print(f"Learning {len(AGENT_PATTERNS)} agent usage patterns...\n")

        for i, pattern in enumerate(AGENT_PATTERNS, 1):
            result = await mem.remember(
                pattern["content"],
                tags=pattern["tags"],
            )
            status = result.status
            block_id = result.block_id[:8]
            content_preview = pattern["content"][:65]
            print(f"  [{i:2d}] {status:25s} {block_id}  {content_preview}...")

        print(f"\n✓ Agent usage patterns learned!")


if __name__ == "__main__":
    import sys

    db = sys.argv[1] if len(sys.argv) > 1 else "~/.elfmem/agent.db"
    config = sys.argv[2] if len(sys.argv) > 2 else None
    asyncio.run(learn_patterns(db, config))
