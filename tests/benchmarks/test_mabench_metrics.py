"""Tests for MemoryAgentBench metrics."""

import pytest

pytest.importorskip("rouge_score", reason="rouge-score not installed")

from benchmarks.memoryagentbench.metrics import (
    exact_match,
    f1_score,
    normalize_answer,
    score_question,
    substring_match,
)


class TestNormalizeAnswer:
    def test_lowercases(self) -> None:
        assert normalize_answer("Hello World") == "hello world"

    def test_strips_articles(self) -> None:
        assert normalize_answer("the quick brown fox") == "quick brown fox"

    def test_strips_punctuation(self) -> None:
        assert normalize_answer("hello, world!") == "hello world"

    def test_handles_int(self) -> None:
        assert normalize_answer(42) == "42"


class TestF1Score:
    def test_exact_match(self) -> None:
        assert f1_score("hello world", "hello world") == 1.0

    def test_no_overlap(self) -> None:
        assert f1_score("foo bar", "baz qux") == 0.0

    def test_partial_overlap(self) -> None:
        score = f1_score("the cat sat", "cat sat on mat")
        assert 0.3 < score < 0.9

    def test_empty_both(self) -> None:
        assert f1_score("", "") == 1.0

    def test_empty_prediction(self) -> None:
        assert f1_score("", "hello") == 0.0

    def test_no_stemming(self) -> None:
        # Unlike LoCoMo, MABench uses no stemming — "running" != "run"
        score = f1_score("running", "run")
        assert score == 0.0


class TestExactMatch:
    def test_identical(self) -> None:
        assert exact_match("hello world", "hello world") == 1.0

    def test_different(self) -> None:
        assert exact_match("hello", "world") == 0.0

    def test_case_insensitive(self) -> None:
        assert exact_match("Hello", "hello") == 1.0


class TestSubstringMatch:
    def test_substring_present(self) -> None:
        assert substring_match("the answer is Paris", "paris") == 1.0

    def test_substring_absent(self) -> None:
        assert substring_match("the answer is Lyon", "paris") == 0.0


class TestScoreQuestion:
    def test_single_ground_truth(self) -> None:
        scores = score_question("Paris", ["Paris"])
        assert scores["f1"] == 1.0
        assert scores["exact_match"] == 1.0

    def test_multiple_ground_truths_takes_best(self) -> None:
        scores = score_question("Lyon", ["Paris", "Lyon"])
        assert scores["f1"] == 1.0

    def test_no_match(self) -> None:
        scores = score_question("Berlin", ["Paris", "Lyon"])
        assert scores["f1"] == 0.0
        assert scores["exact_match"] == 0.0
