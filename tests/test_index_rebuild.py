"""Tests for elfmem.memory.index_rebuild — U-002 (`elfmem index` rebuild, L1 -> L2).

Two of the four contracted test names (test_self_md_appears_in_self_frame,
test_self_md_absent_from_ls_listing) are implemented against RebuildResult
and the blocks table directly rather than through frame()/ls() — neither
exists yet (frame() integration and ls() belong to units not yet built; see
results/U-002.md "Missing context"). What each name promises — self.md is
available for the self frame, and self.md never enters the block table — is
still exactly what's asserted.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from elfmem.db.queries import get_active_blocks, get_tags
from elfmem.memory.index_rebuild import MemoryDirNotFoundError, rebuild_index


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def memory_dir(tmp_path: Path) -> Path:
    root = tmp_path / ".elfmem" / "memory"
    _write(
        root / "self.md",
        "# Identity\n\nMinimum force. Apply the smallest change that solves it.\n",
    )
    _write(
        root / "notes" / "principles.md",
        "## Minimum force on commands\n"
        "<!-- id: 8f3a2b1c  tags: [self/value, cli]  pinned: true  created: 2026-05-08 -->\n"
        "\n"
        "Before adding a new top-level command, apply the test: does this "
        "extend an existing verb?\n",
    )
    _write(
        root / "log" / "2026-08.md",
        "## Fresh observation\n"
        "<!-- id: 1a2b3c4d  tags: [attention] -->\n"
        "\n"
        "Something learned this session, not yet reviewed.\n",
    )
    return root


class TestRebuildMatchesReferenceFixture:
    async def test_rebuild_matches_reference_fixture(
        self, db_conn, memory_dir, mock_embedding, mock_llm
    ):
        result = await rebuild_index(
            db_conn, memory_dir, mock_embedding, mock_embedding.model_name
        )

        assert result.blocks_written == 2
        assert result.parse_errors == []

        active = await get_active_blocks(db_conn)
        by_id = {b["id"]: b for b in active}
        assert "8f3a2b1c" in by_id
        assert by_id["8f3a2b1c"]["status"] == "active"
        assert by_id["8f3a2b1c"]["embedding"] is not None
        assert await get_tags(db_conn, "8f3a2b1c") == ["cli", "self/value"]

        # log/ lands as inbox, not active -- get_active_blocks won't see it.
        assert "1a2b3c4d" not in by_id

        # Zero LLM calls -- embeddings are a distinct, expected cost.
        assert mock_llm.process_block_calls == 0
        assert mock_llm.propose_amendment_calls == 0
        assert mock_embedding.embed_calls > 0


class TestMissingMemoryDir:
    async def test_missing_memory_dir_fails_loudly(
        self, db_conn, tmp_path, mock_embedding
    ):
        missing = tmp_path / "does-not-exist"
        with pytest.raises(MemoryDirNotFoundError) as exc_info:
            await rebuild_index(
                db_conn, missing, mock_embedding, mock_embedding.model_name
            )
        assert exc_info.value.recovery


class TestSelfMdNeverEntersBlockTable:
    async def test_self_md_appears_in_self_frame(
        self, db_conn, memory_dir, mock_embedding
    ):
        # "Appears in the self frame" -- frame() wiring doesn't exist yet
        # (belongs to a later unit), but the content this unit is
        # responsible for making available is present and correct.
        result = await rebuild_index(
            db_conn, memory_dir, mock_embedding, mock_embedding.model_name
        )
        assert result.self_content is not None
        assert "Minimum force" in result.self_content

    async def test_self_md_absent_from_ls_listing(
        self, db_conn, memory_dir, mock_embedding
    ):
        # "Absent from ls()" -- ls() doesn't exist yet, so asserted directly
        # against the table it would list from: self.md's content must
        # never appear as a block row (Invariant 2).
        await rebuild_index(
            db_conn, memory_dir, mock_embedding, mock_embedding.model_name
        )
        active = await get_active_blocks(db_conn)
        contents = [b["content"] for b in active]
        assert not any("Minimum force" in c for c in contents)


class TestNoSelfMd:
    async def test_missing_self_md_is_not_an_error(
        self, db_conn, tmp_path, mock_embedding
    ):
        root = tmp_path / ".elfmem" / "memory"
        _write(
            root / "notes" / "x.md",
            "## Only block\n<!-- id: onlyone -->\n\nJust this.\n",
        )
        result = await rebuild_index(
            db_conn, root, mock_embedding, mock_embedding.model_name
        )
        assert result.self_content is None
        assert result.blocks_written == 1


class TestMalformedFrontmatterSurfaced:
    async def test_malformed_frontmatter_collected_not_silent(
        self, db_conn, tmp_path, mock_embedding
    ):
        root = tmp_path / ".elfmem" / "memory"
        _write(
            root / "notes" / "broken.md",
            "## Broken\n<!-- id: bad  tags: [unterminated -->\n\nStill has content.\n",
        )
        result = await rebuild_index(
            db_conn, root, mock_embedding, mock_embedding.model_name
        )
        assert result.blocks_written == 1  # still written, just flagged
        assert len(result.parse_errors) == 1
        path, err = result.parse_errors[0]
        assert path.name == "broken.md"
        assert err.title == "Broken"


class TestCategoryRoundTrips:
    async def test_category_derived_from_source_filename(
        self, db_conn, tmp_path, mock_embedding
    ):
        root = tmp_path / ".elfmem" / "memory"
        _write(
            root / "notes" / "mind.md",
            "## Alice model\n<!-- id: mindblock1 -->\n\nAlice goals: ship on time.\n",
        )
        _write(
            root / "notes" / "message.md",
            "## A message\n<!-- id: msgblock1 -->\n\nHello there.\n",
        )
        await rebuild_index(db_conn, root, mock_embedding, mock_embedding.model_name)

        active = await get_active_blocks(db_conn)
        by_id = {b["id"]: b for b in active}
        assert by_id["mindblock1"]["category"] == "mind"
        assert by_id["msgblock1"]["category"] == "message"

    async def test_mind_blocks_survive_rebuild_findable_by_category(
        self, db_conn, tmp_path, mock_embedding
    ):
        # The concrete failure mode this guards against: mind_list() /
        # get_active_blocks_by_category(conn, "mind") returning nothing
        # after a rebuild because every block landed under "knowledge".
        root = tmp_path / ".elfmem" / "memory"
        _write(
            root / "notes" / "mind.md",
            "## Alice model\n<!-- id: mindblock1 -->\n\nAlice goals: ship on time.\n",
        )
        await rebuild_index(db_conn, root, mock_embedding, mock_embedding.model_name)

        from elfmem.db.queries import get_active_blocks_by_category

        mind_blocks = await get_active_blocks_by_category(db_conn, "mind")
        assert [b["id"] for b in mind_blocks] == ["mindblock1"]


class TestConfidenceAlphaBetaRoundTrip:
    async def test_evidence_fields_read_back_from_frontmatter_extra(
        self, db_conn, tmp_path, mock_embedding
    ):
        root = tmp_path / ".elfmem" / "memory"
        _write(
            root / "notes" / "knowledge.md",
            "## Reinforced fact\n"
            "<!-- id: evidenced1  confidence: 0.8200  alpha: 4.1000  beta: 0.9000 -->\n"
            "\n"
            "Redis pool size 20 works well in production.\n",
        )
        await rebuild_index(db_conn, root, mock_embedding, mock_embedding.model_name)

        active = await get_active_blocks(db_conn)
        row = next(b for b in active if b["id"] == "evidenced1")
        assert abs(row["confidence"] - 0.82) < 0.001
        assert abs(row["success_count"] - 4.10) < 0.001
        assert abs(row["failure_count"] - 0.90) < 0.001

    async def test_missing_evidence_fields_fall_back_to_neutral_default(
        self, db_conn, tmp_path, mock_embedding
    ):
        root = tmp_path / ".elfmem" / "memory"
        _write(
            root / "notes" / "knowledge.md",
            "## Plain fact\n<!-- id: plain1 -->\n\nNo evidence fields at all.\n",
        )
        await rebuild_index(db_conn, root, mock_embedding, mock_embedding.model_name)

        active = await get_active_blocks(db_conn)
        row = next(b for b in active if b["id"] == "plain1")
        assert abs(row["confidence"] - 0.50) < 0.001
        assert abs(row["success_count"] - 0.50) < 0.001
        assert abs(row["failure_count"] - 0.50) < 0.001

    async def test_malformed_evidence_field_falls_back_rather_than_raising(
        self, db_conn, tmp_path, mock_embedding
    ):
        root = tmp_path / ".elfmem" / "memory"
        _write(
            root / "notes" / "knowledge.md",
            "## Odd fact\n<!-- id: odd1  confidence: not-a-number -->\n\nSome content.\n",
        )
        result = await rebuild_index(db_conn, root, mock_embedding, mock_embedding.model_name)
        assert result.blocks_written == 1

        active = await get_active_blocks(db_conn)
        row = next(b for b in active if b["id"] == "odd1")
        assert abs(row["confidence"] - 0.50) < 0.001


class TestExtensionPoint:
    async def test_additional_fold_steps_contribute_to_count(
        self, db_conn, memory_dir, mock_embedding
    ):
        async def fake_peer_fold(conn, embedding_service, embedding_model):
            del conn, embedding_service, embedding_model
            return 3

        result = await rebuild_index(
            db_conn,
            memory_dir,
            mock_embedding,
            mock_embedding.model_name,
            additional_fold_steps=[fake_peer_fold],
        )
        assert result.blocks_written == 2 + 3


class TestPhase0RoundTrip:
    """`created`, `pinned`, and the decay tier used to be silently lost on
    every rebuild: `created_at` was written to the file and then overwritten
    with the rebuild time, `pinned` had no DB column at all, and
    `decay_lambda` fell through to the STANDARD default regardless of tier.
    All three feed retrieval ranking or removal safety."""

    async def test_created_at_survives_rebuild(
        self, db_conn, memory_dir, mock_embedding
    ):
        await rebuild_index(
            db_conn, memory_dir, mock_embedding, mock_embedding.model_name
        )
        active = await get_active_blocks(db_conn)
        row = next(b for b in active if b["id"] == "8f3a2b1c")
        assert row["created_at"] == "2026-05-08"

    async def test_pinned_survives_rebuild(
        self, db_conn, memory_dir, mock_embedding
    ):
        await rebuild_index(
            db_conn, memory_dir, mock_embedding, mock_embedding.model_name
        )
        active = await get_active_blocks(db_conn)
        row = next(b for b in active if b["id"] == "8f3a2b1c")
        assert bool(row["pinned"]) is True

    async def test_decay_lambda_is_derived_from_tier_not_defaulted(
        self, db_conn, tmp_path, mock_embedding
    ):
        """A constitutional block is PERMANENT (λ=1e-05). Before Phase 0 it
        came back STANDARD (λ=0.01) — a 1000x faster decay clock on a block
        that is supposed to be effectively immortal."""
        root = tmp_path / ".elfmem" / "memory"
        _write(
            root / "notes" / "knowledge.md",
            "## A principle\n"
            "<!-- id: perm0001  tags: [self/constitutional]  pinned: true -->\n"
            "\n"
            "Complexity is debt.\n",
        )
        await rebuild_index(db_conn, root, mock_embedding, mock_embedding.model_name)
        active = await get_active_blocks(db_conn)
        row = next(b for b in active if b["id"] == "perm0001")
        assert row["decay_lambda"] == pytest.approx(0.00001)

    async def test_block_without_created_frontmatter_still_gets_a_timestamp(
        self, db_conn, memory_dir, mock_embedding
    ):
        """The log block carries no `created:` field. It must fall back to
        now, not to NULL — created_at is NOT NULL in the schema."""
        await rebuild_index(
            db_conn, memory_dir, mock_embedding, mock_embedding.model_name
        )
        from elfmem.db.queries import get_inbox_blocks

        inbox = await get_inbox_blocks(db_conn)
        row = next(b for b in inbox if b["id"] == "1a2b3c4d")
        assert row["created_at"]


class TestFormatV2Rebuild:
    """Block format v2: cue and volatility class are declared fields, and
    typed links are the frontmatter encoding for a graph edge that the build
    plan recorded as a genuine unassigned gap."""

    async def test_cue_and_class_survive_rebuild(
        self, db_conn, tmp_path, mock_embedding
    ):
        root = tmp_path / ".elfmem" / "memory"
        _write(
            root / "notes" / "knowledge.md",
            "## A principle\n"
            "<!-- id: cue00001  cls: identity  tags: [self/constitutional] -->\n"
            "cue:: deciding whether to add a command or extend one\n"
            "\n"
            "Complexity is debt.\n",
        )
        await rebuild_index(db_conn, root, mock_embedding, mock_embedding.model_name)
        active = await get_active_blocks(db_conn)
        row = next(b for b in active if b["id"] == "cue00001")
        assert row["cue"] == "deciding whether to add a command or extend one"
        assert row["volatility_class"] == "identity"
        # The inline fields are metadata, not body text.
        assert "cue::" not in row["content"]

    async def test_typed_links_become_graph_edges(
        self, db_conn, tmp_path, mock_embedding
    ):
        from sqlalchemy import text

        root = tmp_path / ".elfmem" / "memory"
        _write(
            root / "notes" / "knowledge.md",
            "## Source\n<!-- id: srcaaaa1 -->\n"
            "refines:: [[tgtbbbb2]]\n\nSource body.\n"
            "\n## Target\n<!-- id: tgtbbbb2 -->\n\nTarget body.\n",
        )
        result = await rebuild_index(
            db_conn, root, mock_embedding, mock_embedding.model_name
        )
        assert result.edges_written == 1
        assert result.dangling_links == []
        row = (await db_conn.execute(text(
            "SELECT from_id, to_id, relation_type, origin, declared_by FROM edges"
        ))).mappings().one()
        # Endpoints canonicalised; direction preserved only by declared_by.
        assert (row["from_id"], row["to_id"]) == ("srcaaaa1", "tgtbbbb2")
        assert row["relation_type"] == "refines"
        assert row["origin"] == "agent"
        assert row["declared_by"] == "srcaaaa1"

    async def test_link_to_unknown_block_is_reported_not_raised(
        self, db_conn, tmp_path, mock_embedding
    ):
        """Pointing at a block you have not written yet is ordinary vault
        practice, and it is what a hand-deleted block leaves behind."""
        root = tmp_path / ".elfmem" / "memory"
        _write(
            root / "notes" / "knowledge.md",
            "## Source\n<!-- id: srcaaaa1 -->\nsupports:: [[nosuchblock]]\n\nBody.\n",
        )
        result = await rebuild_index(
            db_conn, root, mock_embedding, mock_embedding.model_name
        )
        assert result.blocks_written == 1
        assert result.edges_written == 0
        assert result.dangling_links == [("srcaaaa1", "supports", "nosuchblock")]


class TestLedgerRestoresHistory:
    """The three retrieval-composite terms a rebuild used to zero."""

    async def test_reinforcement_and_recency_replay_from_the_ledger(
        self, db_conn, tmp_path, mock_embedding
    ):
        from elfmem.memory.ledger import record_assembly

        root = tmp_path / ".elfmem" / "memory"
        _write(
            root / "notes" / "knowledge.md",
            "## A block\n<!-- id: hist0001 -->\n\nSome content.\n",
        )
        ledger = tmp_path / ".elfmem" / "ledger"
        record_assembly(ledger, ["hist0001"], active_hours=1.0)
        record_assembly(ledger, ["hist0001"], active_hours=6.25)

        await rebuild_index(
            db_conn, root, mock_embedding, mock_embedding.model_name,
            ledger_dir=ledger,
        )
        row = next(b for b in await get_active_blocks(db_conn) if b["id"] == "hist0001")
        assert row["reinforcement_count"] == 2
        assert row["last_reinforced_at"] == pytest.approx(6.25)

    async def test_without_a_ledger_rebuild_degrades_to_previous_behaviour(
        self, db_conn, memory_dir, mock_embedding
    ):
        """Omitting ledger_dir must not become an error: a global instance
        with no project root has nowhere to put one."""
        await rebuild_index(
            db_conn, memory_dir, mock_embedding, mock_embedding.model_name
        )
        row = next(b for b in await get_active_blocks(db_conn) if b["id"] == "8f3a2b1c")
        assert row["reinforcement_count"] == 0


class TestNestedCategoryRoundTrip:
    """A category containing a slash exports to a nested file. Taking only the
    filename stem drops the prefix, which merges unrelated categories."""

    async def test_slashed_category_survives_rebuild(
        self, db_conn, tmp_path, mock_embedding
    ):
        root = tmp_path / ".elfmem" / "memory"
        _write(
            root / "notes" / "pattern" / "strategy.md",
            "## Nested\n<!-- id: nest0001 -->\n\nA pattern/strategy block.\n",
        )
        _write(
            root / "notes" / "strategy.md",
            "## Flat\n<!-- id: flat0001 -->\n\nAn ordinary strategy block.\n",
        )
        await rebuild_index(db_conn, root, mock_embedding, mock_embedding.model_name)

        active = {b["id"]: b["category"] for b in await get_active_blocks(db_conn)}
        assert active["nest0001"] == "pattern/strategy"
        assert active["flat0001"] == "strategy"


class TestLedgerLambdaBeatsTierDerivation:
    async def test_replayed_lambda_wins_over_the_tag_derived_tier(
        self, db_conn, tmp_path, mock_embedding
    ):
        """A constitutional block derives to PERMANENT (1e-05). If the ledger
        says otherwise, the ledger wins -- it carries what actually happened."""
        from elfmem.memory.ledger import KIND_SEED, append

        root = tmp_path / ".elfmem" / "memory"
        _write(
            root / "notes" / "knowledge.md",
            "## Penalised\n<!-- id: pen00001  tags: [self/constitutional] -->\n"
            "\nContent.\n",
        )
        ledger = tmp_path / ".elfmem" / "ledger"
        append(ledger, KIND_SEED, active_hours=0.0, id="pen00001",
               created="2026-01-01", n=0, lah=0.0, a=0.5, b=0.5, lam=0.02)

        await rebuild_index(
            db_conn, root, mock_embedding, mock_embedding.model_name,
            ledger_dir=ledger,
        )
        row = next(b for b in await get_active_blocks(db_conn) if b["id"] == "pen00001")
        assert row["decay_lambda"] == pytest.approx(0.02)

    async def test_without_a_ledger_the_tier_derivation_still_applies(
        self, db_conn, tmp_path, mock_embedding
    ):
        root = tmp_path / ".elfmem" / "memory"
        _write(
            root / "notes" / "knowledge.md",
            "## Fresh\n<!-- id: fresh001  tags: [self/constitutional] -->\n"
            "\nContent.\n",
        )
        await rebuild_index(db_conn, root, mock_embedding, mock_embedding.model_name)
        row = next(b for b in await get_active_blocks(db_conn) if b["id"] == "fresh001")
        assert row["decay_lambda"] == pytest.approx(0.00001)


class TestLearnedEdgesRestore:
    """The graph is carried by the ledger, not recomputed.

    Similarity edges look derivable and are not: consolidation scores them
    against *summary* embeddings and temporal proximity at promotion time, so
    recomputing from content builds a different graph. Co-retrieval edges are
    pure history. Losing them zeroed `centrality`, one of the five terms in
    the retrieval composite, on every rebuild.
    """

    async def _two_blocks(self, tmp_path):
        root = tmp_path / ".elfmem" / "memory"
        _write(
            root / "notes" / "knowledge.md",
            "## A\n<!-- id: aaa11111 -->\n\nFirst block.\n"
            "\n## B\n<!-- id: bbb22222 -->\n\nSecond block.\n",
        )
        return root

    async def test_edge_state_survives_in_full(
        self, db_conn, tmp_path, mock_embedding
    ):
        from sqlalchemy import text

        from elfmem.memory.ledger import KIND_LINK, append

        root = await self._two_blocks(tmp_path)
        ledger = tmp_path / ".elfmem" / "ledger"
        append(ledger, KIND_LINK, active_hours=12.5, **{"from": "aaa11111", "to": "bbb22222"},
               rel="co_occurs", o="co_retrieval", w=0.55, rc=7, lah=12.5)

        result = await rebuild_index(
            db_conn, root, mock_embedding, mock_embedding.model_name,
            ledger_dir=ledger,
        )
        assert result.edges_written == 1
        row = (await db_conn.execute(text("SELECT * FROM edges"))).mappings().one()
        assert row["relation_type"] == "co_occurs"
        assert row["origin"] == "co_retrieval"
        assert row["weight"] == pytest.approx(0.55)
        assert row["reinforcement_count"] == 7
        assert row["last_active_hours"] == pytest.approx(12.5)

    async def test_edge_to_a_block_that_did_not_survive_is_dropped_and_counted(
        self, db_conn, tmp_path, mock_embedding
    ):
        """`archive/` is deliberately never re-read, so every edge touching an
        archived block falls away here. Reported, never raised."""
        from elfmem.memory.ledger import KIND_LINK, append

        root = await self._two_blocks(tmp_path)
        ledger = tmp_path / ".elfmem" / "ledger"
        append(ledger, KIND_LINK, active_hours=1.0, **{"from": "aaa11111", "to": "gone9999"},
               rel="similar", o="similarity", w=0.7, rc=0)

        result = await rebuild_index(
            db_conn, root, mock_embedding, mock_embedding.model_name,
            ledger_dir=ledger,
        )
        assert result.edges_written == 0
        assert result.edges_dropped == 1

    async def test_declared_link_wins_the_relation_but_keeps_earned_counts(
        self, db_conn, tmp_path, mock_embedding
    ):
        """A human or agent asserting 'refines' outranks whatever similarity
        called the pair. The accumulated weight and reinforcement are earned
        history and are not part of that assertion, so they survive."""
        from sqlalchemy import text

        from elfmem.memory.ledger import KIND_LINK, append

        root = tmp_path / ".elfmem" / "memory"
        _write(
            root / "notes" / "knowledge.md",
            "## A\n<!-- id: aaa11111 -->\nrefines:: [[bbb22222]]\n\nFirst block.\n"
            "\n## B\n<!-- id: bbb22222 -->\n\nSecond block.\n",
        )
        ledger = tmp_path / ".elfmem" / "ledger"
        append(ledger, KIND_LINK, active_hours=9.0, **{"from": "aaa11111", "to": "bbb22222"},
               rel="similar", o="similarity", w=0.81, rc=5, lah=9.0)

        await rebuild_index(
            db_conn, root, mock_embedding, mock_embedding.model_name,
            ledger_dir=ledger,
        )
        row = (await db_conn.execute(text("SELECT * FROM edges"))).mappings().one()
        assert row["relation_type"] == "refines"
        assert row["origin"] == "agent"
        assert row["declared_by"] == "aaa11111"
        assert row["weight"] == pytest.approx(0.81)
        assert row["reinforcement_count"] == 5

    async def test_non_canonical_edge_is_repaired_not_duplicated(
        self, db_conn, tmp_path, mock_embedding
    ):
        """Real data carries edges stored with from_id > to_id, violating the
        canonical-ordering invariant. Replay keys on the canonical pair, so
        the edge is restored once, in the right order."""
        from sqlalchemy import text

        from elfmem.memory.ledger import KIND_LINK, append

        root = await self._two_blocks(tmp_path)
        ledger = tmp_path / ".elfmem" / "ledger"
        append(ledger, KIND_LINK, active_hours=1.0, **{"from": "bbb22222", "to": "aaa11111"},
               rel="replies_to", o="agent", w=0.8, rc=0)

        await rebuild_index(
            db_conn, root, mock_embedding, mock_embedding.model_name,
            ledger_dir=ledger,
        )
        rows = (await db_conn.execute(text("SELECT from_id, to_id FROM edges"))).all()
        assert rows == [("aaa11111", "bbb22222")]
