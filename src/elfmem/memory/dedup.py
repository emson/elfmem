"""Cosine similarity — the shared primitive for near-duplicate and contradiction detection.

The actual near-duplicate resolution and supersession logic lives in
``operations/consolidate.py`` (``_collect_decisions`` / ``_apply_decisions``),
not here. This module previously also held ``find_near_duplicate`` and
``resolve_near_duplicate`` — dead code with zero callers in ``src/`` (an
earlier evolution of this pipeline moved that logic into consolidate.py's
decision/apply split without deleting the superseded originals). Removed in
v2 step 1 alongside the pin guard, since the stale docstring's "the new block
inherits nothing" was mistaken for live behaviour once already — see
docs/plans/plan_v2_substrate_reevaluation.md §2.3.
"""

from __future__ import annotations

import numpy as np


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Compute cosine similarity between two normalised vectors."""
    dot = float(np.dot(a, b))
    # Clamp to [-1, 1] to guard against floating-point drift
    return max(-1.0, min(1.0, dot))
