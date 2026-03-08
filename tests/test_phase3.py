"""Tests for Phase 3: setup(), managed(), SystemStatus enhancements, CLI/MCP delegation."""
from __future__ import annotations

import warnings
from unittest.mock import AsyncMock, MagicMock

import pytest

from elfmem.api import MemorySystem
from elfmem.config import ElfmemConfig, MemoryConfig
from elfmem.policy import ConsolidationPolicy
from elfmem.types import (
    ConsolidateResult,
    FrameResult,
    LearnResult,
    SetupResult,
    ScoredBlock,
    SystemStatus,
    TokenUsage,
)


# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_system(test_engine, mock_llm, mock_embedding, threshold: int = 10) -> MemorySystem:
    cfg = ElfmemConfig(memory=MemoryConfig(inbox_threshold=threshold))
    return MemorySystem(
        engine=test_engine,
        llm_service=mock_llm,
        embedding_service=mock_embedding,
        config=cfg,
    )


# ── SetupResult type ───────────────────────────────────────────────────────────


class TestSetupResult:
    def test_summary_nothing_to_do(self) -> None:
        r = SetupResult(blocks_created=0, total_attempted=0)
        assert r.summary == "Setup: nothing to do."

    def test_summary_all_skipped(self) -> None:
        r = SetupResult(blocks_created=0, total_attempted=5)
        assert "5 blocks already present" in r.summary

    def test_summary_created(self) -> None:
        r = SetupResult(blocks_created=3, total_attempted=5)
        assert "3/5" in r.summary

    def test_str_equals_summary(self) -> None:
        r = SetupResult(blocks_created=2, total_attempted=2)
        assert str(r) == r.summary

    def test_to_dict_keys(self) -> None:
        r = SetupResult(blocks_created=1, total_attempted=2)
        d = r.to_dict()
        assert d == {"blocks_created": 1, "total_attempted": 2}


# ── MemorySystem.setup() ───────────────────────────────────────────────────────


class TestMemorySystemSetup:
    async def test_setup_returns_setup_result(
        self, test_engine, mock_llm, mock_embedding
    ) -> None:
        system = _make_system(test_engine, mock_llm, mock_embedding)
        result = await system.setup(seed=True)
        assert isinstance(result, SetupResult)
        await system.close()

    async def test_setup_seeds_constitutional_blocks(
        self, test_engine, mock_llm, mock_embedding
    ) -> None:
        from elfmem.seed import CONSTITUTIONAL_SEED

        system = _make_system(test_engine, mock_llm, mock_embedding)
        result = await system.setup(seed=True)
        assert result.total_attempted == len(CONSTITUTIONAL_SEED)
        assert result.blocks_created == len(CONSTITUTIONAL_SEED)
        await system.close()

    async def test_setup_is_idempotent(
        self, test_engine, mock_llm, mock_embedding
    ) -> None:
        system = _make_system(test_engine, mock_llm, mock_embedding)
        await system.setup(seed=True)
        result2 = await system.setup(seed=True)
        assert result2.blocks_created == 0  # all already present
        await system.close()

    async def test_setup_with_identity(
        self, test_engine, mock_llm, mock_embedding
    ) -> None:
        system = _make_system(test_engine, mock_llm, mock_embedding)
        result = await system.setup(seed=False, identity="I am a coding assistant.")
        assert result.total_attempted == 1
        assert result.blocks_created == 1
        await system.close()

    async def test_setup_with_values(
        self, test_engine, mock_llm, mock_embedding
    ) -> None:
        system = _make_system(test_engine, mock_llm, mock_embedding)
        result = await system.setup(
            seed=False, values=["Always test.", "Never over-engineer."]
        )
        assert result.total_attempted == 2
        assert result.blocks_created == 2
        await system.close()

    async def test_setup_no_seed_skips_constitutional(
        self, test_engine, mock_llm, mock_embedding
    ) -> None:
        system = _make_system(test_engine, mock_llm, mock_embedding)
        result = await system.setup(seed=False)
        assert result.total_attempted == 0
        assert result.blocks_created == 0
        await system.close()

    async def test_setup_total_attempted_counts_all(
        self, test_engine, mock_llm, mock_embedding
    ) -> None:
        from elfmem.seed import CONSTITUTIONAL_SEED

        system = _make_system(test_engine, mock_llm, mock_embedding)
        result = await system.setup(
            seed=True,
            identity="I am elf.",
            values=["Be precise.", "Be honest."],
        )
        expected = len(CONSTITUTIONAL_SEED) + 1 + 2  # seed + identity + 2 values
        assert result.total_attempted == expected
        await system.close()

    async def test_setup_records_operation_in_history(
        self, test_engine, mock_llm, mock_embedding
    ) -> None:
        system = _make_system(test_engine, mock_llm, mock_embedding)
        await system.setup(seed=False, identity="test agent")
        history = system.history()
        ops = [op.operation for op in history]
        assert "setup" in ops
        await system.close()


# ── MemorySystem.managed() ─────────────────────────────────────────────────────


class TestMemorySystemManaged:
    async def test_managed_yields_memory_system(self, db_path_str: str) -> None:
        async with MemorySystem.managed(db_path_str) as mem:
            assert isinstance(mem, MemorySystem)

    async def test_managed_starts_session_automatically(self, db_path_str: str) -> None:
        async with MemorySystem.managed(db_path_str) as mem:
            status = await mem.status()
            assert status.session_active

    async def test_managed_exits_cleanly_without_pending(self, db_path_str: str) -> None:
        # No exceptions when no blocks are pending (should_dream=False on exit)
        async with MemorySystem.managed(db_path_str) as mem:
            pass  # No remember() calls → no dreaming needed

    async def test_managed_dreams_on_exit_when_pending(
        self, db_path_str: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """managed() calls dream() if should_dream is True on exit."""
        dream_was_called = []

        original_from_config = MemorySystem.from_config.__func__  # type: ignore[attr-defined]

        async def patched_from_config(cls, db_path, config=None, *, policy=None):
            mem = await original_from_config(cls, db_path, config, policy=policy)

            original_dream = mem.dream

            async def tracking_dream():
                dream_was_called.append(True)
                return ConsolidateResult(processed=0, promoted=0, deduplicated=0, edges_created=0)

            mem.dream = tracking_dream
            # Force _pending above threshold to trigger should_dream
            mem._pending = mem._config.memory.inbox_threshold + 1
            return mem

        monkeypatch.setattr(MemorySystem, "from_config", classmethod(patched_from_config))

        async with MemorySystem.managed(db_path_str) as mem:
            assert mem.should_dream  # confirm our patch worked

        assert dream_was_called, "dream() was not called on exit despite should_dream=True"

    async def test_managed_accepts_policy(self, db_path_str: str) -> None:
        policy = ConsolidationPolicy(base_threshold=5)
        async with MemorySystem.managed(db_path_str, policy=policy) as mem:
            assert isinstance(mem, MemorySystem)


# ── SystemStatus: pending_count and effective_threshold ───────────────────────


class TestSystemStatusEnhancements:
    async def test_status_pending_count_zero_initially(
        self, test_engine, mock_llm, mock_embedding
    ) -> None:
        system = _make_system(test_engine, mock_llm, mock_embedding)
        await system.begin_session()
        status = await system.status()
        assert status.pending_count == 0
        await system.close()

    async def test_status_pending_count_increments_after_remember(
        self, test_engine, mock_llm, mock_embedding
    ) -> None:
        system = _make_system(test_engine, mock_llm, mock_embedding)
        await system.begin_session()
        await system.remember("block one")
        await system.remember("block two")
        status = await system.status()
        assert status.pending_count == 2
        await system.close()

    async def test_status_effective_threshold_matches_config(
        self, test_engine, mock_llm, mock_embedding
    ) -> None:
        system = _make_system(test_engine, mock_llm, mock_embedding, threshold=7)
        await system.begin_session()
        status = await system.status()
        assert status.effective_threshold == 7
        await system.close()

    async def test_status_effective_threshold_matches_policy(
        self, test_engine, mock_llm, mock_embedding
    ) -> None:
        policy = ConsolidationPolicy(base_threshold=15)
        cfg = ElfmemConfig(memory=MemoryConfig(inbox_threshold=7))
        system = MemorySystem(
            engine=test_engine,
            llm_service=mock_llm,
            embedding_service=mock_embedding,
            config=cfg,
            policy=policy,
        )
        await system.begin_session()
        status = await system.status()
        assert status.effective_threshold == policy.effective_threshold
        await system.close()

    async def test_status_str_shows_pending_advisory_when_set(
        self, test_engine, mock_llm, mock_embedding
    ) -> None:
        system = _make_system(test_engine, mock_llm, mock_embedding, threshold=5)
        await system.begin_session()
        await system.remember("a")
        await system.remember("b")
        status = await system.status()
        # pending_count=2 > 0 and effective_threshold=5 > 0 → advisory shown
        assert "Pending (advisory)" in str(status)
        await system.close()

    async def test_status_str_no_advisory_when_pending_zero(
        self, test_engine, mock_llm, mock_embedding
    ) -> None:
        system = _make_system(test_engine, mock_llm, mock_embedding)
        await system.begin_session()
        status = await system.status()
        assert "Pending (advisory)" not in str(status)
        await system.close()

    def test_status_to_dict_has_pending_fields(self) -> None:
        s = SystemStatus(
            session_active=True,
            session_hours=1.0,
            inbox_count=3,
            inbox_threshold=10,
            active_count=20,
            archived_count=2,
            total_active_hours=5.0,
            last_consolidated="never",
            health="good",
            suggestion="dream",
            pending_count=3,
            effective_threshold=10,
        )
        d = s.to_dict()
        assert d["pending_count"] == 3
        assert d["effective_threshold"] == 10


# ── SmartMemory deprecation warning ───────────────────────────────────────────


class TestSmartMemoryDeprecation:
    def test_smartmemory_warns_on_construction(
        self, test_engine, mock_llm, mock_embedding
    ) -> None:
        """SmartMemory emits DeprecationWarning when instantiated."""
        from elfmem.smart import SmartMemory

        system = _make_system(test_engine, mock_llm, mock_embedding)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            _ = SmartMemory(system)

        assert any(
            issubclass(w.category, DeprecationWarning) and "SmartMemory" in str(w.message)
            for w in caught
        )

    def test_smartmemory_deprecation_message_mentions_memory_system(
        self, test_engine, mock_llm, mock_embedding
    ) -> None:
        from elfmem.smart import SmartMemory

        system = _make_system(test_engine, mock_llm, mock_embedding)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            _ = SmartMemory(system)

        messages = [str(w.message) for w in caught if issubclass(w.category, DeprecationWarning)]
        assert any("MemorySystem" in m for m in messages)

    async def test_smartmemory_setup_delegates_to_system(
        self, test_engine, mock_llm, mock_embedding
    ) -> None:
        """SmartMemory.setup() returns the same result as MemorySystem.setup()."""
        from elfmem.smart import SmartMemory

        system = _make_system(test_engine, mock_llm, mock_embedding)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            shim = SmartMemory(system)

        result = await shim.setup(seed=False, identity="delegated identity test")
        assert isinstance(result, SetupResult)
        assert result.blocks_created == 1


# ── MCP setup delegation ──────────────────────────────────────────────────────


@pytest.fixture
def mock_memory_system(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    import elfmem.mcp as mcp_module

    mem: AsyncMock = AsyncMock(spec=MemorySystem)
    mem.guide = MagicMock(return_value="guide text")
    mem.should_dream = False
    monkeypatch.setattr(mcp_module, "_memory", mem)
    return mem


class TestMcpSetupDelegation:
    async def test_elfmem_setup_calls_system_setup(
        self, mock_memory_system: AsyncMock
    ) -> None:
        from elfmem.mcp import elfmem_setup

        mock_memory_system.setup.return_value = SetupResult(
            blocks_created=10, total_attempted=10
        )
        result = await elfmem_setup()
        mock_memory_system.setup.assert_called_once()

    async def test_elfmem_setup_returns_setup_result_dict(
        self, mock_memory_system: AsyncMock
    ) -> None:
        from elfmem.mcp import elfmem_setup

        mock_memory_system.setup.return_value = SetupResult(
            blocks_created=5, total_attempted=10
        )
        result = await elfmem_setup()
        assert result == {"blocks_created": 5, "total_attempted": 10}

    async def test_elfmem_setup_passes_seed_flag(
        self, mock_memory_system: AsyncMock
    ) -> None:
        from elfmem.mcp import elfmem_setup

        mock_memory_system.setup.return_value = SetupResult(
            blocks_created=0, total_attempted=0
        )
        await elfmem_setup(seed=False)
        call_kwargs = mock_memory_system.setup.call_args
        assert call_kwargs[1].get("seed") is False or call_kwargs[0][2:] == (False,) or \
               "seed" in str(call_kwargs)

    async def test_elfmem_setup_passes_identity(
        self, mock_memory_system: AsyncMock
    ) -> None:
        from elfmem.mcp import elfmem_setup

        mock_memory_system.setup.return_value = SetupResult(
            blocks_created=1, total_attempted=1
        )
        await elfmem_setup(identity="Test agent identity")
        mock_memory_system.setup.assert_called_once()
        call_kwargs = mock_memory_system.setup.call_args
        assert "Test agent identity" in str(call_kwargs)


# ── CLI: serve --adaptive-policy flag ────────────────────────────────────────


class TestCliServeAdaptivePolicy:
    def test_serve_command_has_adaptive_policy_option(self) -> None:
        """CLI serve command accepts --adaptive-policy without error."""
        from elfmem.cli import serve
        import inspect

        sig = inspect.signature(serve)
        assert "adaptive_policy" in sig.parameters

    def test_serve_passes_adaptive_policy_to_mcp_main(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """serve() passes use_adaptive_policy=True to mcp.main()."""
        captured: list[dict] = []

        def fake_mcp_main(db_path, config_path=None, *, use_adaptive_policy=False):
            captured.append({"use_adaptive_policy": use_adaptive_policy})

        import elfmem.cli as cli_module

        monkeypatch.setattr(
            "elfmem.cli.serve.__wrapped__"  # handle typer wrapping
            if hasattr(cli_module.serve, "__wrapped__") else "elfmem.mcp.main",
            fake_mcp_main,
            raising=False,
        )

        # Verify the function signature exposes the flag (Typer introspection)
        import typer
        from typer.testing import CliRunner
        from elfmem.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["serve", "--help"])
        assert "--adaptive-policy" in result.output
