"""elfmem — Self-aware adaptive memory for LLM agents.

Quick start::

    from elfmem import MemorySystem

    system = await MemorySystem.from_config("agent.db")
    async with system.session():
        await system.learn("User prefers dark mode")
        ctx = await system.frame("attention", query="preferences")
        print(ctx.text)  # inject into your LLM prompt

Agent self-documentation::

    print(system.guide())           # overview of all operations
    print(system.guide("learn"))    # detailed guide for learn()
    s = await system.status()       # health snapshot + suggested action
"""

from importlib.metadata import version as _version

from elfmem.api import MemorySystem
from elfmem.config import ElfmemConfig, LoggingConfig
from elfmem.exceptions import (
    BlockNotActiveError,
    ConfigError,
    ConnectError,
    DegreeLimitError,
    ElfmemError,
    FrameError,
    PeerError,
    SelfLoopError,
    SessionError,
    StorageError,
)
from elfmem.policy import ConsolidationPolicy
from elfmem.types import (
    ConnectByQueryResult,
    ConnectResult,
    ConnectSpec,
    ConnectsResult,
    ConsolidateResult,
    CurateResult,
    DisconnectResult,
    DisplacedEdge,
    ExportResult,
    FrameResult,
    ImportResult,
    LearnDocumentResult,
    LearnResult,
    MindOutcomeResult,
    MindPredictResult,
    MindShowResult,
    MindSummary,
    OperationRecord,
    OutcomeResult,
    PeerInboxResult,
    PeerInboxStatus,
    PeerInfo,
    PeerSendResult,
    PredictionDetail,
    ScoredBlock,
    SetupResult,
    SystemStatus,
    TokenUsage,
)

__version__ = _version("elfmem")

__all__ = [
    "__version__",
    # Core
    "MemorySystem",
    "ElfmemConfig",
    "LoggingConfig",
    "ConsolidationPolicy",
    # Result types
    "LearnResult",
    "LearnDocumentResult",
    "ConsolidateResult",
    "FrameResult",
    "CurateResult",
    "OutcomeResult",
    "SetupResult",
    "ScoredBlock",
    "SystemStatus",
    "OperationRecord",
    "TokenUsage",
    "ConnectResult",
    "ConnectByQueryResult",
    "ConnectsResult",
    "ConnectSpec",
    "DisconnectResult",
    "DisplacedEdge",
    # Mind (Theory of Mind) types
    "MindSummary",
    "MindPredictResult",
    "MindShowResult",
    "MindOutcomeResult",
    "PredictionDetail",
    # Peer communication types
    "PeerError",
    "PeerInfo",
    "PeerSendResult",
    "PeerInboxResult",
    "PeerInboxStatus",
    "ExportResult",
    "ImportResult",
    # Exceptions
    "ElfmemError",
    "SessionError",
    "ConfigError",
    "StorageError",
    "FrameError",
    "ConnectError",
    "SelfLoopError",
    "BlockNotActiveError",
    "DegreeLimitError",
]
