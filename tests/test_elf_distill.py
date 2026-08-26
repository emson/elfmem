"""Tests for scripts/hooks/elf_distill.py -- the PreCompact/SessionEnd hook.

Same discipline as test_hook_scripts.py: unit-test everything pure (marker
offset tracking, transcript slicing, prompt building, response parsing), and
leave the network call (LM Studio) and the DB write untested here -- those
are verified live against the sandbox, the same way every other hook in this
project has been.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[1] / "scripts" / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import elf_distill  # noqa: E402

# --- marker offset tracking ---------------------------------------------------


def test_read_processed_defaults_to_zero_when_missing(tmp_path: Path) -> None:
    assert elf_distill.read_processed(tmp_path / "nope.distilled") == 0


def test_write_then_read_processed_roundtrips(tmp_path: Path) -> None:
    marker = tmp_path / "sess.distilled"
    elf_distill.write_processed(marker, 7)
    assert elf_distill.read_processed(marker) == 7


def test_read_processed_tolerates_garbage(tmp_path: Path) -> None:
    marker = tmp_path / "sess.distilled"
    marker.write_text("not a number")
    assert elf_distill.read_processed(marker) == 0


# --- transcript slicing --------------------------------------------------------


def _write_transcript(tmp_path: Path, rows: list[dict]) -> Path:
    path = tmp_path / "transcript.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows))
    return path


def test_all_rows_reads_every_line(tmp_path: Path) -> None:
    rows = [{"type": "user", "message": {"content": "a"}}, {"type": "assistant", "message": {"content": [{"type": "text", "text": "b"}]}}]
    path = _write_transcript(tmp_path, rows)
    assert len(elf_distill._all_rows(path)) == 2


def test_new_rows_skips_already_processed(tmp_path: Path) -> None:
    rows = [{"type": "user", "message": {"content": str(i)}} for i in range(5)]
    path = _write_transcript(tmp_path, rows)
    fresh = elf_distill.new_rows(path, processed=3)
    assert len(fresh) == 2
    assert fresh[0]["message"]["content"] == "3"


def test_new_rows_empty_when_nothing_new(tmp_path: Path) -> None:
    rows = [{"type": "user", "message": {"content": "a"}}]
    path = _write_transcript(tmp_path, rows)
    assert elf_distill.new_rows(path, processed=1) == []


def test_new_rows_missing_transcript_returns_empty(tmp_path: Path) -> None:
    assert elf_distill.new_rows(tmp_path / "gone.jsonl", processed=0) == []


# --- conversation text extraction ---------------------------------------------


def test_extract_conversation_text_includes_both_speakers() -> None:
    rows = [
        {"type": "user", "message": {"content": "we dropped blue-green last year"}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Got it, noted."}]}},
    ]
    text = elf_distill.extract_conversation_text(rows)
    assert "we dropped blue-green last year" in text
    assert "Got it, noted." in text


def test_extract_conversation_text_excludes_tool_calls_and_results() -> None:
    rows = [
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": "irrelevant"}]}},
        {
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}]},
        },
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Files listed."}]}},
    ]
    text = elf_distill.extract_conversation_text(rows)
    assert "irrelevant" not in text
    assert "Bash" not in text
    assert "Files listed." in text


# --- prompt construction -------------------------------------------------------


def test_build_prompt_includes_self_text_and_conversation() -> None:
    prompt = elf_distill.build_prompt(
        self_text="I am elf, curious and transparent.",
        conversation_text="User: remember the new rule.",
    )
    assert "I am elf, curious and transparent." in prompt
    assert "User: remember the new rule." in prompt
    assert "JSON" in prompt


# --- response parsing -----------------------------------------------------------


def test_parse_candidates_valid_json_array() -> None:
    raw = json.dumps([
        {"content": "Fact one.", "cue": "when asked about fact one", "tags": ["a"]},
        {"content": "Fact two.", "cue": "when asked about fact two"},
    ])
    candidates = elf_distill.parse_candidates(raw)
    assert len(candidates) == 2
    assert candidates[0]["tags"] == ["a"]
    assert candidates[1]["tags"] == []  # missing tags normalised to empty list


def test_parse_candidates_strips_markdown_fences() -> None:
    raw = '```json\n[{"content": "Fact.", "cue": "when it matters"}]\n```'
    candidates = elf_distill.parse_candidates(raw)
    assert len(candidates) == 1
    assert candidates[0]["content"] == "Fact."


def test_parse_candidates_empty_array_is_valid() -> None:
    assert elf_distill.parse_candidates("[]") == []


def test_parse_candidates_skips_malformed_items_keeps_good_ones() -> None:
    raw = json.dumps([
        {"content": "Good fact.", "cue": "when needed"},
        {"content": ""},  # missing cue, empty content -- invalid
        {"cue": "no content field"},  # missing content -- invalid
        "just a string",  # not even a dict -- invalid
        {"content": "Another good one.", "cue": "also when needed"},
    ])
    candidates = elf_distill.parse_candidates(raw)
    assert [c["content"] for c in candidates] == ["Good fact.", "Another good one."]


def test_parse_candidates_caps_at_max() -> None:
    raw = json.dumps([
        {"content": f"Fact {i}.", "cue": f"cue {i}"} for i in range(elf_distill.MAX_CANDIDATES + 3)
    ])
    candidates = elf_distill.parse_candidates(raw)
    assert len(candidates) == elf_distill.MAX_CANDIDATES


def test_parse_candidates_garbage_input_returns_empty() -> None:
    assert elf_distill.parse_candidates("not json at all") == []
    assert elf_distill.parse_candidates('{"not": "a list"}') == []


# --- main()'s pre-network control flow: no-op and skip paths ------------------


def test_main_noop_when_nothing_new(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    import io

    rows = [{"type": "user", "message": {"content": "hello there, this is plenty of characters"}}]
    transcript = _write_transcript(tmp_path, rows)
    marker = elf_distill.marker_file(tmp_path, "sess-1")
    elf_distill.write_processed(marker, len(rows))  # already fully processed

    payload = {"session_id": "sess-1", "cwd": str(tmp_path), "transcript_path": str(transcript)}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert elf_distill.main() == 0
    assert elf_distill.read_processed(marker) == len(rows)  # untouched


def test_main_skips_and_does_not_advance_marker_when_too_little_new_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io

    rows = [{"type": "user", "message": {"content": "hi"}}]  # trivially short
    transcript = _write_transcript(tmp_path, rows)
    marker = elf_distill.marker_file(tmp_path, "sess-1")

    payload = {"session_id": "sess-1", "cwd": str(tmp_path), "transcript_path": str(transcript)}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert elf_distill.main() == 0
    # Marker must NOT advance -- this thin slice should accumulate with
    # whatever comes next, not be silently dropped.
    assert elf_distill.read_processed(marker) == 0


# --- CLI arg parsing: pytest's own argv must never leak into these flags -----


def test_parse_args_defaults_to_none_ignoring_unrelated_argv() -> None:
    # parse_known_args, not parse_args: a hook invocation's real argv is
    # whatever Claude Code passes (nothing relevant), and in tests it's
    # pytest's own flags -- unrecognized args must never crash this.
    args = elf_distill._parse_args(["--some-pytest-flag", "value"])
    assert args.session_id is None
    assert args.cwd is None
    assert args.transcript_path is None
    assert args.host is False


def test_parse_args_reads_explicit_flags() -> None:
    args = elf_distill._parse_args([
        "--session-id", "abc", "--cwd", "/tmp/x", "--transcript-path", "/tmp/x/t.jsonl", "--host",
    ])
    assert args.session_id == "abc"
    assert args.cwd == "/tmp/x"
    assert args.transcript_path == "/tmp/x/t.jsonl"
    assert args.host is True


# --- manual-CLI mode: explicit flags instead of a hook-JSON stdin payload ----


def test_main_manual_cli_mode_uses_flags_not_stdin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io

    rows = [{"type": "user", "message": {"content": "x" * 300}}]
    transcript = _write_transcript(tmp_path, rows)
    marker = elf_distill.marker_file(tmp_path, "sess-cli")

    called: dict = {}

    async def fake_distill(project_root, conversation_text):
        called["project_root"] = project_root
        called["conversation_text"] = conversation_text
        return {"candidates": 0, "stored": []}

    monkeypatch.setattr(elf_distill, "_distill", fake_distill)
    # stdin deliberately left as something that would fail json.loads if the
    # manual-CLI branch ever mistakenly tried to read it as a hook payload.
    monkeypatch.setattr("sys.stdin", io.StringIO("not valid json"))
    monkeypatch.setattr("sys.argv", [
        "elf_distill.py", "--session-id", "sess-cli",
        "--cwd", str(tmp_path), "--transcript-path", str(transcript),
    ])
    assert elf_distill.main() == 0
    assert called["project_root"] == tmp_path
    assert "x" * 300 in called["conversation_text"]
    assert elf_distill.read_processed(marker) == 1


# --- host mode: pre-reasoned candidates supplied directly, no LLM call ------


def test_main_host_mode_writes_supplied_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io

    written: list = []

    async def fake_write(project_root, candidates):
        written.extend(candidates)
        return [f"id-{i}" for i in range(len(candidates))]

    monkeypatch.setattr(elf_distill, "_write_candidates", fake_write)
    candidates_json = json.dumps([
        {"content": "Host-supplied fact.", "cue": "when it matters", "tags": ["x"]},
    ])
    monkeypatch.setattr("sys.stdin", io.StringIO(candidates_json))
    monkeypatch.setattr("sys.argv", ["elf_distill.py", "--host", "--cwd", str(tmp_path)])
    assert elf_distill.main() == 0
    assert len(written) == 1
    assert written[0]["content"] == "Host-supplied fact."


def test_main_host_mode_advances_marker_when_transcript_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io

    rows = [{"type": "user", "message": {"content": "a"}}, {"type": "user", "message": {"content": "b"}}]
    transcript = _write_transcript(tmp_path, rows)
    marker = elf_distill.marker_file(tmp_path, "sess-host")

    async def fake_write(project_root, candidates):
        return ["id-0"]

    monkeypatch.setattr(elf_distill, "_write_candidates", fake_write)
    candidates_json = json.dumps([{"content": "Fact.", "cue": "cue"}])
    monkeypatch.setattr("sys.stdin", io.StringIO(candidates_json))
    monkeypatch.setattr("sys.argv", [
        "elf_distill.py", "--host", "--cwd", str(tmp_path),
        "--session-id", "sess-host", "--transcript-path", str(transcript),
    ])
    assert elf_distill.main() == 0
    assert elf_distill.read_processed(marker) == 2


def test_main_host_mode_skips_write_when_no_valid_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io

    called = {"write": False}

    async def fake_write(project_root, candidates):
        called["write"] = True
        return []

    monkeypatch.setattr(elf_distill, "_write_candidates", fake_write)
    monkeypatch.setattr("sys.stdin", io.StringIO("[]"))
    monkeypatch.setattr("sys.argv", ["elf_distill.py", "--host", "--cwd", str(tmp_path)])
    assert elf_distill.main() == 0
    assert called["write"] is False
