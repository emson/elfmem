"""Tests for elfmem.memory.ledger — the append-only history the markdown
substrate structurally cannot hold.

The property under test throughout is that a rebuild can reconstruct exactly
what the index knew, because three of the five retrieval-composite terms
(recency, reinforcement, centrality) live nowhere in the files.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from elfmem.memory.ledger import (
    KIND_BIRTH,
    KIND_OUTCOME,
    KIND_REMOVE,
    KIND_SEED,
    append,
    ledger_dir_for,
    record_assembly,
    replay,
)


class TestAppendAndReplay:
    def test_birth_sets_creation_date(self, tmp_path: Path):
        d = tmp_path / "ledger"
        append(d, KIND_BIRTH, active_hours=0.0, id="aaa")
        state = replay(d).blocks["aaa"]
        assert state.created_at is not None
        assert state.removed is False

    def test_assembly_drives_reinforcement_and_recency(self, tmp_path: Path):
        d = tmp_path / "ledger"
        record_assembly(d, ["aaa"], active_hours=1.0, frame="attention")
        record_assembly(d, ["aaa"], active_hours=4.5, frame="attention")
        state = replay(d).blocks["aaa"]
        assert state.reinforcement_count == 2
        assert state.last_reinforced_at == pytest.approx(4.5)

    def test_outcomes_accumulate_into_the_beta_posterior(self, tmp_path: Path):
        d = tmp_path / "ledger"
        append(d, KIND_OUTCOME, active_hours=1.0, id="aaa", sig=1.0, w=1.0)
        append(d, KIND_OUTCOME, active_hours=2.0, id="aaa", sig=0.0, w=0.5)
        state = replay(d).blocks["aaa"]
        assert state.alpha == pytest.approx(1.5)
        assert state.beta == pytest.approx(1.0)

    def test_removal_is_recorded_not_erased(self, tmp_path: Path):
        """The whole point of an append-only log: a forgotten block still has
        a history, which is what makes the removal auditable."""
        d = tmp_path / "ledger"
        append(d, KIND_BIRTH, active_hours=0.0, id="aaa")
        append(d, KIND_REMOVE, active_hours=1.0, id="aaa", why="forgotten")
        state = replay(d).blocks["aaa"]
        assert state.removed is True
        assert state.created_at is not None

    def test_co_retrieval_pairs_are_canonical(self, tmp_path: Path):
        d = tmp_path / "ledger"
        record_assembly(d, ["zzz", "aaa"], active_hours=1.0)
        record_assembly(d, ["aaa", "zzz"], active_hours=2.0)
        assert replay(d).co_retrieval == {("aaa", "zzz"): 2}

    def test_seed_carries_pre_ledger_state(self, tmp_path: Path):
        d = tmp_path / "ledger"
        append(
            d, KIND_SEED, active_hours=3.25, id="aaa",
            created="2026-05-08T09:14:22+00:00", n=7, lah=3.25, a=4.1, b=0.9,
        )
        state = replay(d).blocks["aaa"]
        assert state.created_at == "2026-05-08T09:14:22+00:00"
        assert state.reinforcement_count == 7
        assert state.last_reinforced_at == pytest.approx(3.25)
        assert state.alpha == pytest.approx(4.1)
        assert state.beta == pytest.approx(0.9)

    def test_zero_valued_seed_fields_are_not_replaced_by_defaults(
        self, tmp_path: Path
    ):
        """beta is legitimately 0.0 for a block promoted at confidence 1.0.
        `value or default` silently invented evidence for 27 of 145 real
        blocks before this was caught."""
        d = tmp_path / "ledger"
        append(d, KIND_SEED, active_hours=0.0, id="aaa", created="x",
               n=0, lah=0.0, a=1.0, b=0.0)
        state = replay(d).blocks["aaa"]
        assert state.beta == pytest.approx(0.0)
        assert state.reinforcement_count == 0


class TestRobustness:
    def test_corrupt_line_is_skipped_and_counted_never_raised(
        self, tmp_path: Path
    ):
        """A damaged ledger must degrade the history it can reconstruct, not
        block the rebuild. Fail-soft here is deliberate — the ledger feeds
        derived state, unlike block files, which assert truth."""
        d = tmp_path / "ledger"
        append(d, KIND_BIRTH, active_hours=0.0, id="aaa")
        ledger_file = next(d.glob("*.jsonl"))
        with ledger_file.open("a", encoding="utf-8") as fh:
            fh.write('{"t":"2026-08-01","k":"birth","id":"trunc\n')
            fh.write("not json at all\n")
        append(d, KIND_BIRTH, active_hours=1.0, id="bbb")

        result = replay(d)
        assert result.skipped_lines == 2
        assert set(result.blocks) == {"aaa", "bbb"}

    def test_missing_ledger_directory_replays_empty(self, tmp_path: Path):
        result = replay(tmp_path / "nope")
        assert result.blocks == {}
        assert result.events_read == 0

    def test_every_line_stays_atomically_appendable(self, tmp_path: Path):
        """Lines must stay under PIPE_BUF so concurrent O_APPEND writes from
        two processes cannot interleave. A wide frame is chunked, not
        truncated, so no reinforcement is lost."""
        d = tmp_path / "ledger"
        record_assembly(d, [f"block{i:04d}" for i in range(500)], active_hours=1.0)
        lines = next(d.glob("*.jsonl")).read_text(encoding="utf-8").splitlines()
        assert len(lines) > 1
        assert all(len(line.encode("utf-8")) < 4096 for line in lines)
        assert len(replay(d).blocks) == 500

    def test_events_replay_in_deterministic_order(self, tmp_path: Path):
        """The parity gate compares rankings that depend on replayed state,
        so replay order cannot vary between runs."""
        d = tmp_path / "ledger"
        for i in range(20):
            append(d, KIND_OUTCOME, active_hours=float(i), id="aaa",
                   sig=1.0, w=0.1)
        first = replay(d).blocks["aaa"]
        second = replay(d).blocks["aaa"]
        assert first.alpha == second.alpha

    def test_lines_are_valid_json_objects(self, tmp_path: Path):
        d = tmp_path / "ledger"
        append(d, KIND_BIRTH, active_hours=1.5, id="aaa")
        line = next(d.glob("*.jsonl")).read_text(encoding="utf-8").strip()
        event = json.loads(line)
        assert event["k"] == KIND_BIRTH
        assert event["id"] == "aaa"
        # Active hours on every event: last_reinforced_at is measured on the
        # session-aware clock, and wall-clock stamps cannot reconstruct it.
        assert event["ah"] == pytest.approx(1.5)


class TestLedgerLocation:
    def test_ledger_sits_beside_memory_not_inside_it(self, tmp_path: Path):
        """`index check`/`rebuild` glob memory/**/*.md; a ledger inside that
        tree would need them to learn to skip it."""
        memory = tmp_path / ".elfmem" / "memory"
        assert ledger_dir_for(memory) == tmp_path / ".elfmem" / "ledger"


class TestLedgerScoping:
    """The ledger records the history of the project's own memory. Events
    from some other database must not land in it."""

    def test_db_override_disables_the_ledger(self, tmp_path: Path):
        from elfmem.api import _db_matches_project
        from elfmem.config import ElfmemConfig, ProjectConfig

        cfg = ElfmemConfig(
            project=ProjectConfig(name="proj", db=str(tmp_path / "real.db"))
        )
        assert _db_matches_project(cfg, str(tmp_path / "real.db")) is True
        assert _db_matches_project(cfg, str(tmp_path / "scratch-copy.db")) is False

    def test_config_without_a_project_section_never_conflicts(self):
        from elfmem.api import _db_matches_project
        from elfmem.config import ElfmemConfig

        assert _db_matches_project(ElfmemConfig(), "/anywhere/at/all.db") is True


class TestDecayLambdaIsHistoryNotDerivation:
    """`decay_lambda` starts as a pure function of tags, but `outcome()` with
    a negative signal multiplies it (accelerate_block_decay) and tags change
    after promotion. Re-deriving it from current tags therefore discards
    accumulated history: on a peer instance that demoted 20 of 235 blocks,
    19 from PERMANENT to DURABLE -- a 100x faster decay clock."""

    def test_seeded_lambda_replays_exactly(self, tmp_path: Path):
        d = tmp_path / "ledger"
        # 0.02 corresponds to no tier at all: it is a penalised STANDARD block.
        append(d, KIND_SEED, active_hours=0.0, id="pen00001", created="x",
               n=0, lah=0.0, a=0.5, b=0.5, lam=0.02)
        assert replay(d).blocks["pen00001"].decay_lambda == pytest.approx(0.02)

    def test_absent_lambda_replays_as_none_so_the_tier_fallback_applies(
        self, tmp_path: Path
    ):
        d = tmp_path / "ledger"
        append(d, KIND_SEED, active_hours=0.0, id="old00001", created="x",
               n=0, lah=0.0, a=0.5, b=0.5)
        assert replay(d).blocks["old00001"].decay_lambda is None


class TestInstanceClockAndSummary:
    def test_activity_clock_replays(self, tmp_path: Path):
        """Every block's recency is measured against this clock. An index
        rebuilt without it computes recency from zero, which makes
        `hours_since` negative and inverts the scale."""
        from elfmem.memory.ledger import KIND_INSTANCE

        d = tmp_path / "ledger"
        append(d, KIND_INSTANCE, active_hours=33.96, total_ah=33.96)
        assert replay(d).total_active_hours == pytest.approx(33.96)

    def test_absent_clock_replays_as_none(self, tmp_path: Path):
        d = tmp_path / "ledger"
        append(d, KIND_BIRTH, active_hours=0.0, id="aaa")
        assert replay(d).total_active_hours is None

    def test_summary_replays(self, tmp_path: Path):
        d = tmp_path / "ledger"
        append(d, KIND_SEED, active_hours=0.0, id="aaa", created="x",
               n=0, lah=0.0, a=0.5, b=0.5, sum="A distilled sentence.")
        assert replay(d).blocks["aaa"].summary == "A distilled sentence."


class TestOversizedEventHandling:
    def test_oversized_text_field_is_dropped_by_name_not_by_whitelist(
        self, tmp_path: Path, caplog
    ):
        """An earlier version kept a fixed whitelist, which discarded seeded
        evidence (alpha/beta/counts) rather than the oversized prose that
        actually caused the overflow."""
        d = tmp_path / "ledger"
        append(d, KIND_SEED, active_hours=0.0, id="big00001", created="x",
               n=9, lah=1.5, a=4.1, b=0.9, sum="x" * 6000)
        state = replay(d).blocks["big00001"]
        assert state.summary is None          # the oversized field went
        assert state.alpha == pytest.approx(4.1)   # the evidence stayed
        assert state.beta == pytest.approx(0.9)
        assert state.reinforcement_count == 9

    def test_every_written_line_stays_under_the_atomic_write_limit(
        self, tmp_path: Path
    ):
        d = tmp_path / "ledger"
        append(d, KIND_SEED, active_hours=0.0, id="big00001", created="x",
               n=0, lah=0.0, a=0.5, b=0.5, sum="y" * 9000)
        for line in next(d.glob("*.jsonl")).read_text(encoding="utf-8").splitlines():
            assert len(line.encode("utf-8")) < 4096
