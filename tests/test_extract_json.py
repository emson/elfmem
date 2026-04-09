"""Tests for _extract_json — markdown fence stripping from LLM responses."""

from elfmem.adapters.openai import _extract_json


class TestExtractJson:
    def test_clean_json_unchanged(self) -> None:
        text = '{"score": 0.5}'
        assert _extract_json(text) == '{"score": 0.5}'

    def test_strips_json_fence(self) -> None:
        text = '```json\n{"score": 0.5}\n```'
        assert _extract_json(text) == '{"score": 0.5}'

    def test_strips_bare_fence(self) -> None:
        text = '```\n{"score": 0.5}\n```'
        assert _extract_json(text) == '{"score": 0.5}'

    def test_strips_multiline_json(self) -> None:
        text = '```json\n{\n  "alignment_score": 0.8,\n  "tags": [],\n  "summary": "test"\n}\n```'
        result = _extract_json(text)
        assert '"alignment_score": 0.8' in result
        assert '"summary": "test"' in result

    def test_strips_surrounding_whitespace(self) -> None:
        text = '  \n```json\n{"score": 0.0}\n```\n  '
        assert _extract_json(text) == '{"score": 0.0}'

    def test_empty_string(self) -> None:
        assert _extract_json("") == ""

    def test_no_fences_with_whitespace(self) -> None:
        text = '  {"score": 1.0}  '
        assert _extract_json(text) == '{"score": 1.0}'

    def test_fence_without_newline(self) -> None:
        text = '```json{"score": 0.5}```'
        assert _extract_json(text) == '{"score": 0.5}'
