#!/usr/bin/env python
"""Extract and learn core concepts from the elfmem Design Simulation system."""
import asyncio
from elfmem.smart import SmartMemory

CONCEPTS = [
    {
        "content": (
            "Document-driven specification: Use structured markdown files with explicit "
            "mathematical computation as the 'whiteboard' for system design, making every "
            "computation auditable and editable. The execution engine is human reasoning over "
            "documents, not code."
        ),
        "tags": ["methodology/specification", "design-pattern"],
    },
    {
        "content": (
            "Micro-scenario pattern: Test individual concepts through self-contained "
            "thought experiments with minimal setup (3-5 blocks), one clear question, worked "
            "computation, result, and insight. Rapidly builds intuition and finds design gaps."
        ),
        "tags": ["methodology/testing", "design-pattern"],
    },
    {
        "content": (
            "Symbolic approximation: When exact computation is impossible (e.g., semantic similarity, "
            "real embeddings), use explicit symbolic values (0-1 range) with stated assumptions. "
            "This unblocks design without requiring full implementation."
        ),
        "tags": ["methodology/approximation", "design-pattern"],
    },
    {
        "content": (
            "Phase-based development: Progression from Explorations (micro-scenarios, rapid testing) "
            "to Playgrounds (subsystem specs, formalized patterns) to Executable Specs (code-generation source). "
            "Each phase builds on the previous with increasing formality."
        ),
        "tags": ["methodology/development", "design-pattern"],
    },
    {
        "content": (
            "Complete score breakdown pattern: When reporting composite scores, always show "
            "the full breakdown with each component: (component_value × weight = contribution), "
            "then sum. Makes scoring transparent and debuggable."
        ),
        "tags": ["methodology/reporting", "design-pattern"],
    },
    {
        "content": (
            "Inline YAML for state representation: Define system state compactly in exploration "
            "setup using YAML with semantic paths (blocks.A.confidence, edges.A→B.weight). "
            "Makes state explicit and reproducible."
        ),
        "tags": ["methodology/notation", "design-pattern"],
    },
    {
        "content": (
            "Exact math formulas as design currency: Encode domain logic in precise mathematical "
            "formulas (decay_weight = e^(-λ×t), Score = Σ(w_i × component_i)) that become the "
            "spec. Makes reasoning reproducible and implementations verifiable."
        ),
        "tags": ["methodology/specification", "design-pattern"],
    },
    {
        "content": (
            "Workable approximations table: When designing, distinguish what works well in documents "
            "(exact math, state transitions, frame logic) vs what needs approximation (semantic similarity, "
            "graph centrality) vs what needs code (real embeddings, performance profiling). Prevents false "
            "confidence in document simulations."
        ),
        "tags": ["methodology/planning", "design-pattern"],
    },
    {
        "content": (
            "Variation-seeded exploration: After computing a micro-scenario result, ask 'what if we changed X?' "
            "to seed the next exploration. This creates a connected sequence of experiments that build intuition "
            "about parameter sensitivity and design trade-offs."
        ),
        "tags": ["methodology/learning", "design-pattern"],
    },
    {
        "content": (
            "Convention-first design: Establish consistent naming, notation, and reporting conventions early "
            "(block shorthand, edge notation, time units, score format). Consistency reduces cognitive load and "
            "makes explorations reviewable and shareable."
        ),
        "tags": ["methodology/structure", "design-pattern"],
    },
    {
        "content": (
            "Edge case reasoning in documents: Exploit the slow thinking advantage of documents to reason through "
            "edge cases (boundary conditions, empty states, cycles in graphs) before implementation. Document these "
            "as explicit test cases that implementation must satisfy."
        ),
        "tags": ["methodology/testing", "design-pattern"],
    },
    {
        "content": (
            "File-driven exploration progression: Organize explorations by sequential numbering (NNN_short_name.md) "
            "and explicit Status markers (draft|running|complete|superseded). This creates an auditable, referable "
            "sequence that becomes the spec."
        ),
        "tags": ["methodology/organization", "design-pattern"],
    },
]


async def learn_concepts(db_path: str, config_path: str | None = None) -> None:
    """Learn design simulation concepts into elfmem."""
    async with SmartMemory.managed(db_path, config=config_path) as mem:
        print(f"Learning {len(CONCEPTS)} design simulation concepts...\n")

        for i, concept in enumerate(CONCEPTS, 1):
            result = await mem.remember(
                concept["content"],
                tags=concept["tags"],
            )
            status = result.status
            block_id = result.block_id[:8]
            print(f"  [{i:2d}] {status:25s} {block_id}  {concept['content'][:65]}...")

        print("\n✓ Design simulation concepts learned!")


if __name__ == "__main__":
    import sys
    db = sys.argv[1] if len(sys.argv) > 1 else "~/.elfmem/agent.db"
    config = sys.argv[2] if len(sys.argv) > 2 else None
    asyncio.run(learn_concepts(db, config))
