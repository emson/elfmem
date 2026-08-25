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
    if category == "mind":
        return DecayTier.DURABLE
    if category == "observation":
        return DecayTier.EPHEMERAL
    return DecayTier.STANDARD


def decay_lambda_for_tier(tier: DecayTier) -> float:
    """Return the λ constant for a given DecayTier."""
    return LAMBDA[tier]


# The volatility vocabulary. Deliberately three words: this classifies how
# fast a claim stops being *true*, which is a different question from how fast
# it stops being *used* (that is DecayTier, above). A block can be highly
# salient and badly stale at once — the "important, verify first" case that a
# single fused score cannot express.
VOLATILITY_CLASSES: tuple[str, ...] = ("identity", "project", "status")


def determine_volatility_class(tags: list[str], category: str) -> str:
    """Classify how fast this block's *truth* decays.

    Priority (first match wins):
    1. self/constitutional                       -> identity (years)
    2. other self/* tags, or a decision/mind block -> project (months)
    3. observation / message / task / attention  -> status   (days)
    4. everything else                           -> project

    `identity` is deliberately narrow. An earlier draft also admitted
    `self/value` and `self/constraint`, which the LLM tagger applies liberally:
    on the real corpus that classified 105 of 145 blocks as identity, and a
    class that covers two thirds of memory discriminates nothing.

    Nothing consumes this yet. It is declared metadata, written into the block
    format now because format changes are free before the substrate becomes
    authoritative and expensive afterwards. Whether a class-based confidence
    half-life actually earns its place is an open experiment (E5), and this
    mapping is a first pass to be revisited when that experiment runs.
    """
    tag_set = set(tags)
    if "self/constitutional" in tag_set:
        return "identity"
    if any(t.startswith("self/") for t in tag_set) or category in {"decision", "mind"}:
        return "project"
    if category in {"observation", "message", "task", "attention"}:
        return "status"
    return "project"
