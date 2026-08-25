"""Which retrieved blocks actually made it into the answer.

The ledger's `asm` event records what was *assembled* into a frame, and costs
nothing. What it cannot say is whether any of it was used. That gap is why
`record_assembly` carries the note that the voluntary feedback verb has been
called nine times across three real instances: reinforcement currently counts
retrievals, so a block that is retrieved constantly and drawn on never rises
and falls exactly like one that does the work.

This module closes the gap with the cheapest evidence that exists: the
response is right there, and a block that genuinely informed it usually
restates several of the block's distinctive words.

Deliberately lexical, not semantic. An embedding comparison would score a
response that merely shares a *topic* with a block as highly as one that used
it -- and topical overlap is precisely what retrieval already selected for, so
the measure would mostly re-measure its own input.

The error is deliberately one-sided. A paraphrase that reuses none of the
block's vocabulary scores zero and the use goes unrecorded; nothing is
penalised for it. The opposite mistake -- crediting a block that contributed
nothing -- would feed the ranking a signal indistinguishable from real use,
so the scoring is tuned to miss rather than to over-claim.
"""

from __future__ import annotations

import re

# Terms shorter than this are dropped wholesale: they are dominated by
# function words and by fragments too common to distinguish one block.
_MIN_TERM_CHARS = 4

# Common English function words that survive the length filter. Everything
# shorter ("the", "a", "of", "is") is already excluded by _MIN_TERM_CHARS,
# which is why this list is short enough to read.
_STOPWORDS = frozenset({
    "about", "after", "again", "against", "already", "also", "although",
    "always", "another", "because", "been", "before", "being", "below",
    "between", "both", "cannot", "come", "could", "does", "doing", "done",
    "down", "during", "each", "either", "else", "even", "ever", "every",
    "from", "further", "give", "goes", "going", "have", "having", "here",
    "however", "into", "itself", "just", "keep", "like", "made", "make",
    "many", "may", "might", "more", "most", "much", "must", "need", "never",
    "next", "none", "only", "other", "over", "own", "part", "perhaps",
    "rather", "really", "same", "seem", "several", "shall", "should", "since",
    "some", "still", "such", "take", "than", "that", "their", "them", "then",
    "there", "these", "they", "thing", "this", "those", "though", "through",
    "thus", "time", "under", "until", "upon", "used", "using", "very", "want",
    "well", "were", "what", "when", "where", "whether", "which", "while",
    "will", "with", "within", "without", "would", "your",
})

_TOKEN_RE = re.compile(r"[a-z0-9_]+")

# A block counts as used when this fraction of its distinctive terms appear in
# the response.
#
# Measured, not chosen. Scoring all 148 active blocks of a real corpus against
# a real 9,746-character answer -- the worst case, since a long response
# covers more vocabulary by accident -- gives the fraction of the *whole
# corpus* that would be credited:
#
#     >=0.30   33%      >=0.45   4.1%
#     >=0.35   18%      >=0.55   1.4%
#
# The curve has a knee between 0.35 and 0.45 and this sits just past it.
# Below the knee the measure degrades fast: at 0.30 a third of everything in
# memory reads as "used", which is not a signal.
#
# Known limitation: the score is sensitive to response length, because a
# longer answer contains more terms to match by chance. The bias therefore
# credits more blocks on long turns than short ones. Left uncorrected --
# normalising needs corpus IDF, and the untreated direction is defensible
# (a long substantive answer probably did draw on more of its context).
# tests/test_attribution.py pins the corpus-wide rate so a regression here
# is visible rather than silent.
USE_THRESHOLD = 0.45


def distinctive_terms(text: str) -> frozenset[str]:
    """The terms of *text* that could identify it inside a response.

    USE WHEN: Scoring attribution, or explaining a score to a human.
    DON'T USE WHEN: You want the full token stream -- this drops most of it
        by design.
    COST: Instant. One regex pass, no I/O.
    RETURNS: frozenset of lowercased terms, function words and short tokens
        removed. Empty for text that is all stopwords, which callers must
        treat as "unscoreable", not as "unused".
    NEXT: Pass to attribution_score, or show the intersection to a human
        asking why a block was credited.
    """
    return frozenset(
        token for token in _TOKEN_RE.findall(text.lower())
        if len(token) >= _MIN_TERM_CHARS and token not in _STOPWORDS
    )


def attribution_score(block_content: str, response: str) -> float:
    """How much of a block's distinctive vocabulary reappears in a response.

    USE WHEN: Deciding whether a retrieved block informed an answer.
    DON'T USE WHEN: You need to know whether the block was *correct* --
        this measures relevance, never truth. Feeding it into the Beta
        posterior would redefine confidence from "has proven right" to
        "gets talked about".
    COST: Instant. Pure set arithmetic, no I/O and no LLM.
    RETURNS: float in [0.0, 1.0] -- the fraction of the block's distinctive
        terms present in the response. 0.0 when the block has no distinctive
        terms at all, so an unscoreable block is never credited.
    NEXT: Compare against USE_THRESHOLD, then pass the surviving ids to
        MemorySystem.record_use().
    """
    terms = distinctive_terms(block_content)
    if not terms:
        return 0.0
    present = terms & distinctive_terms(response)
    return len(present) / len(terms)


def attributed_ids(
    blocks: dict[str, str],
    response: str,
    *,
    threshold: float = USE_THRESHOLD,
) -> list[str]:
    """Ids of the blocks whose content shows through in *response*.

    USE WHEN: A turn has finished and you know which blocks were assembled
        into it. This is the whole read path for a usage hook.
    DON'T USE WHEN: The response is not yet complete -- a partial answer
        under-reports, and the error is silent.
    COST: Instant. Linear in the number of blocks.
    RETURNS: list[str] of block ids scoring at or above *threshold*, ordered
        by score descending so a caller logging a subset keeps the clearest
        evidence.
    NEXT: MemorySystem.record_use(ids).

    Args:
        blocks: block id -> the content that was rendered into the frame.
        response: the answer that was produced with those blocks in context.
        threshold: minimum attribution score. Defaults to USE_THRESHOLD.
    """
    scored = [
        (block_id, attribution_score(content, response))
        for block_id, content in blocks.items()
    ]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return [block_id for block_id, score in scored if score >= threshold]
