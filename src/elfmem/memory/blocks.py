"""Block-level helpers: content hashing, decay tier assignment, lambda lookup."""

from __future__ import annotations

import hashlib

from elfmem.scoring import LAMBDA
from elfmem.types import DecayTier


def compute_content_hash(content: str) -> str:
    """Compute content-addressable block ID: sha256(normalised)[:16].

    Normalisation: strip + lowercase (matches queries.content_hash).
    """
    normalised = content.strip().lower()
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()[:16]


def determine_decay_tier(tags: list[str], category: str) -> DecayTier:
    """Assign a DecayTier from tags and category.

    Priority (first match wins):
    1. any tag == "self/constitutional"  → PERMANENT
    2. any tag in {self/value, self/constraint, self/goal} → DURABLE
    3. category == "observation"         → EPHEMERAL
    4. everything else                   → STANDARD
    """
    tag_set = set(tags)
    if "self/constitutional" in tag_set:
        return DecayTier.PERMANENT
    durable_tags = {"self/value", "self/constraint", "self/goal"}
    if tag_set & durable_tags:
        return DecayTier.DURABLE
    if category == "observation":
        return DecayTier.EPHEMERAL
    return DecayTier.STANDARD


def decay_lambda_for_tier(tier: DecayTier) -> float:
    """Return the λ constant for a given DecayTier."""
    return LAMBDA[tier]
