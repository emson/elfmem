"""Tests for the personal Claude Code hooks in scripts/hooks/.

These are scripts, not library modules, so they are imported by path. The
tests stay at the layer the bugs can actually live in: the address regex,
the transcript-turn parsing, the pending-file contract between the two
hooks, and the gate decision in elf_outcome.main() -- exercised end-to-end
through crafted stdin/transcript because every gate outcome asserted here
returns before any database write.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[1] / "scripts" / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import elf_context  # noqa: E402
import elf_outcome  # noqa: E402

# --- address detection -------------------------------------------------------


@pytest.mark.parametrize(
    "prompt",
    [
        "As Elf, tell me about this project.",
        "Tell me what you think about this, as elf.",
        "hey elf, run the tests",
        "elf: add tests for the gate",
        "Elf, what do you think?",
    ],
)
def test_vocative_prompts_are_addressed(prompt: str) -> None:
    assert elf_context._addresses_elf(prompt)


@pytest.mark.parametrize(
    "prompt",
    [
        "Is the best way to ensure elf is loaded to run a load command?",
        "As elf's architecture shows, retrieval is frame-driven.",
        "What does elf stand for?",
        "The elf package uses SQLite.",
        "OK elf status looks good to me.",
    ],
)
def test_topic_mentions_are_not_addressed(prompt: str) -> None:
    assert not elf_context._addresses_elf(prompt)


# --- pending-file contract between the two hooks -----------------------------


def test_pending_roundtrip(tmp_path: Path) -> None:
    injected = {"abc123": "some block content", "def456": "another block"}
    elf_context.write_pending(tmp_path, "sess-1", injected, addressed=True)
    blocks, addressed = elf_outcome.read_pending(
        elf_context.pending_file(tmp_path, "sess-1")
    )
    assert blocks == injected
    assert addressed is True


def test_read_pending_tolerates_legacy_flat_layout(tmp_path: Path) -> None:
    path = tmp_path / "old.pending.json"
    path.write_text(json.dumps({"abc123": "content"}))
    blocks, addressed = elf_outcome.read_pending(path)
    assert blocks == {"abc123": "content"}
    assert addressed is False


# --- transcript-turn parsing -------------------------------------------------


def _transcript(tmp_path: Path, rows: list[dict]) -> Path:
    path = tmp_path / "transcript.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows))
    return path


def test_turn_rows_start_at_last_string_user_entry(tmp_path: Path) -> None:
    rows = [
        {"type": "user", "message": {"content": "first prompt"}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "old"}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result"}]}},
        {"type": "user", "message": {"content": "second prompt"}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "new"}]}},
    ]
    turn = elf_outcome._turn_rows(_transcript(tmp_path, rows))
    assert elf_outcome.turn_response(turn) == "new"


def test_turn_tool_names_sees_calls_prose_cannot(tmp_path: Path) -> None:
    rows = [
        {"type": "user", "message": {"content": "prompt"}},
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "tool_use", "name": "mcp__elfmem__elfmem_status", "input": {}}]
            },
        },
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "done"}]}},
    ]
    turn = elf_outcome._turn_rows(_transcript(tmp_path, rows))
    assert elf_outcome.turn_tool_names(turn) == ["mcp__elfmem__elfmem_status"]
    assert elf_outcome.turn_response(turn) == "done"


# --- the gate, end-to-end through main() -------------------------------------

# Injected content deliberately shares no vocabulary with the responses below,
# so lexical attribution scores zero and the gate decision alone varies.
_INJECTED = {"b1": "quixotic zymurgy blatherskite phlogiston"}


def _run_outcome(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
    *,
    addressed: bool,
    rows: list[dict],
) -> str:
    elf_context.write_pending(tmp_path, "sess-1", _INJECTED, addressed=addressed)
    payload = {
        "session_id": "sess-1",
        "cwd": str(tmp_path),
        "transcript_path": str(_transcript(tmp_path, rows)),
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert elf_outcome.main() == 0
    return capsys.readouterr().out


_PROSE_ONLY = [
    {"type": "user", "message": {"content": "As elf, what colour is the sky?"}},
    {"type": "assistant", "message": {"content": [{"type": "text", "text": "It is blue today."}]}},
]

_WITH_ELFMEM_CALL = [
    {"type": "user", "message": {"content": "As elf, what colour is the sky?"}},
    {
        "type": "assistant",
        "message": {
            "content": [{"type": "tool_use", "name": "mcp__elfmem__elfmem_recall", "input": {}}]
        },
    },
    {"type": "assistant", "message": {"content": [{"type": "text", "text": "It is blue today."}]}},
]


def test_gate_blocks_addressed_turn_with_no_engagement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    out = _run_outcome(tmp_path, monkeypatch, capsys, addressed=True, rows=_PROSE_ONLY)
    decision = json.loads(out)
    assert decision["decision"] == "block"
    assert "elfmem" in decision["reason"]


def test_gate_passes_unaddressed_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    out = _run_outcome(tmp_path, monkeypatch, capsys, addressed=False, rows=_PROSE_ONLY)
    assert out == ""


def test_gate_passes_addressed_turn_with_active_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    out = _run_outcome(
        tmp_path, monkeypatch, capsys, addressed=True, rows=_WITH_ELFMEM_CALL
    )
    assert out == ""


def test_gate_fires_once_per_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    out = _run_outcome(tmp_path, monkeypatch, capsys, addressed=True, rows=_PROSE_ONLY)
    assert json.loads(out)["decision"] == "block"
    # The retry's Stop event finds no pending file and must let the turn end.
    payload = {
        "session_id": "sess-1",
        "cwd": str(tmp_path),
        "transcript_path": str(_transcript(tmp_path, _PROSE_ONLY)),
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert elf_outcome.main() == 0
    assert capsys.readouterr().out == ""
