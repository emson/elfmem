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


# --- capture-trigger detection ------------------------------------------------


@pytest.mark.parametrize(
    "prompt",
    [
        "Remember that we always deploy through canary.",
        "Worth remembering: the staging key rotates weekly.",
        "Make a note: billing needs two approvals.",
        "Note that the old process no longer applies.",
        "Don't forget we switched providers last month.",
        "Keep in mind the release window closes Friday.",
        "From now on, always squash commits before merging.",
        "Going forward we use trunk-based development.",
        "That's outdated, we don't do that anymore.",
        "That info is no longer accurate.",
    ],
)
def test_capture_trigger_positives(prompt: str) -> None:
    assert elf_context._is_capture_worthy(prompt)


@pytest.mark.parametrize(
    "prompt",
    [
        "What time does the deploy run?",
        "Can you fix this bug in the parser?",
        "Actually, let's use a different approach here.",
        "The root cause was a race condition.",
        "Remembering the API is tricky sometimes.",
    ],
)
def test_capture_trigger_negatives(prompt: str) -> None:
    # "Actually, ..." and prose-conclusion phrasing ("the root cause was")
    # are deliberately excluded -- both fire on ordinary technical
    # back-and-forth far too often to be a useful signal at this precision.
    assert not elf_context._is_capture_worthy(prompt)


# A novelty filter reusing ATTENTION's own top score (skip the capture nudge
# when the top match already looks like a near-duplicate) was built and live
# -tested against the sandbox here, then reverted: the retrieval `score` is a
# blended ranking value (reinforcement, recency, alignment, exploration bonus
# all factor in), not the raw cosine similarity `near_dup_near_threshold` is
# calibrated against. A control probe with zero relation to any stored fact
# still scored 0.93-0.94 against every block in a 4-block sandbox corpus --
# the two quantities share a [0, 1] range and a name, nothing else. A correct
# version needs a real embedding call against the active corpus (dedup.py's
# actual near-dup path), which is real cost this session chose not to add
# speculatively. See the capture-design memory block for the finding.


# --- pending-file contract between the two hooks -----------------------------


def test_pending_roundtrip(tmp_path: Path) -> None:
    injected = {"abc123": "some block content", "def456": "another block"}
    elf_context.write_pending(
        tmp_path, "sess-1", injected, addressed=True, capture_worthy=True
    )
    blocks, addressed, capture_worthy = elf_outcome.read_pending(
        elf_context.pending_file(tmp_path, "sess-1")
    )
    assert blocks == injected
    assert addressed is True
    assert capture_worthy is True


def test_read_pending_tolerates_legacy_flat_layout(tmp_path: Path) -> None:
    path = tmp_path / "old.pending.json"
    path.write_text(json.dumps({"abc123": "content"}))
    blocks, addressed, capture_worthy = elf_outcome.read_pending(path)
    assert blocks == {"abc123": "content"}
    assert addressed is False
    assert capture_worthy is False


def test_read_pending_tolerates_pre_capture_layout(tmp_path: Path) -> None:
    # A pending file from the engagement-gate redesign, before capture_worthy
    # existed -- {"addressed": ..., "blocks": ...} with no capture_worthy key.
    path = tmp_path / "mid.pending.json"
    path.write_text(json.dumps({"addressed": True, "blocks": {"a": "x"}}))
    blocks, addressed, capture_worthy = elf_outcome.read_pending(path)
    assert blocks == {"a": "x"}
    assert addressed is True
    assert capture_worthy is False


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


# --- active-verb detection: MCP call and CLI-via-Bash, both count ------------


def test_turn_active_verbs_sees_mcp_call(tmp_path: Path) -> None:
    rows = [
        {"type": "user", "message": {"content": "prompt"}},
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "name": "mcp__elfmem__elfmem_remember", "input": {}}
                ]
            },
        },
    ]
    turn = elf_outcome._turn_rows(_transcript(tmp_path, rows))
    assert elf_outcome.turn_active_verbs(turn) == {"remember"}


def test_turn_active_verbs_sees_bash_cli_call(tmp_path: Path) -> None:
    rows = [
        {"type": "user", "message": {"content": "prompt"}},
        {
            "type": "assistant",
            "message": {
                "content": [{
                    "type": "tool_use", "name": "Bash",
                    "input": {"command": "cd /repo && .venv/bin/elfmem remember 'fact' --cue 'when asked'"},
                }]
            },
        },
    ]
    turn = elf_outcome._turn_rows(_transcript(tmp_path, rows))
    assert elf_outcome.turn_active_verbs(turn) == {"remember"}


def test_turn_active_verbs_ignores_unrelated_bash(tmp_path: Path) -> None:
    rows = [
        {"type": "user", "message": {"content": "prompt"}},
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "tool_use", "name": "Bash", "input": {"command": "git status"}}]
            },
        },
    ]
    turn = elf_outcome._turn_rows(_transcript(tmp_path, rows))
    assert elf_outcome.turn_active_verbs(turn) == set()


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
    capture_worthy: bool = False,
    injected: dict[str, str] | None = None,
) -> str:
    elf_context.write_pending(
        tmp_path, "sess-1", _INJECTED if injected is None else injected,
        addressed=addressed, capture_worthy=capture_worthy,
    )
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


_CAPTURE_PROSE_ONLY = [
    {"type": "user", "message": {"content": "Remember that we always deploy through canary."}},
    {"type": "assistant", "message": {"content": [{"type": "text", "text": "Got it, will keep that in mind."}]}},
]


def test_capture_gate_blocks_when_no_write_verb(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    out = _run_outcome(
        tmp_path, monkeypatch, capsys,
        addressed=False, capture_worthy=True, rows=_CAPTURE_PROSE_ONLY,
    )
    decision = json.loads(out)
    assert decision["decision"] == "block"
    assert "remember" in decision["reason"].lower()


def test_capture_gate_passes_with_mcp_remember_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    rows = [
        _CAPTURE_PROSE_ONLY[0],
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "name": "mcp__elfmem__elfmem_remember", "input": {}}
                ]
            },
        },
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Stored."}]}},
    ]
    out = _run_outcome(
        tmp_path, monkeypatch, capsys, addressed=False, capture_worthy=True, rows=rows,
    )
    assert out == ""


def test_capture_gate_passes_with_bash_remember_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    rows = [
        _CAPTURE_PROSE_ONLY[0],
        {
            "type": "assistant",
            "message": {
                "content": [{
                    "type": "tool_use", "name": "Bash",
                    "input": {"command": "elfmem remember 'x' --cue 'y'"},
                }]
            },
        },
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Stored."}]}},
    ]
    out = _run_outcome(
        tmp_path, monkeypatch, capsys, addressed=False, capture_worthy=True, rows=rows,
    )
    assert out == ""


def test_capture_gate_fires_even_with_nothing_retrieved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    # A genuinely novel fact may match nothing in ATTENTION recall -- the
    # capture check must not be gated behind injected being non-empty, or the
    # exact case it exists for (new knowledge with no prior neighbour) is the
    # one case it silently skips.
    out = _run_outcome(
        tmp_path, monkeypatch, capsys,
        addressed=False, capture_worthy=True, rows=_CAPTURE_PROSE_ONLY, injected={},
    )
    decision = json.loads(out)
    assert decision["decision"] == "block"


def test_capture_gate_silent_when_not_capture_worthy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    out = _run_outcome(
        tmp_path, monkeypatch, capsys, addressed=False, capture_worthy=False, rows=_PROSE_ONLY,
    )
    assert out == ""


def test_both_gates_combine_into_one_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    rows = [
        {"type": "user", "message": {"content": "As elf, remember that we always deploy through canary."}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "It is blue today."}]}},
    ]
    out = _run_outcome(
        tmp_path, monkeypatch, capsys, addressed=True, capture_worthy=True, rows=rows,
    )
    decision = json.loads(out)
    assert decision["decision"] == "block"
    assert "engag" in decision["reason"].lower()
    assert "remember" in decision["reason"].lower()


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
