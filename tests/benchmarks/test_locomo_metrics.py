"""Tests for LoCoMo benchmark scoring functions."""

import pytest

nltk = pytest.importorskip("nltk", reason="nltk not installed")

from benchmarks.locomo.metrics import (
    adversarial_score,
    f1_score,
    multihop_f1,
    normalize_answer,
    openended_f1,
    retrieval_recall,
    score_qa,
)


# --- normalize_answer ---


class TestNormalizeAnswer:
    def test_lowercases(self) -> None:
        assert normalize_answer("Hello World") == "hello world"

    def test_strips_articles(self) -> None:
        assert normalize_answer("the cat and a dog") == "cat dog"

    def test_strips_punctuation(self) -> None:
        assert normalize_answer("hello, world!") == "hello world"

    def test_collapses_whitespace(self) -> None:
        assert normalize_answer("  too   many   spaces  ") == "too many spaces"

    def test_combined(self) -> None:
        assert normalize_answer("The Quick, Brown Fox!") == "quick brown fox"


# --- f1_score ---


class TestF1Score:
    def test_exact_match(self) -> None:
        assert f1_score("the answer", "the answer") == 1.0

    def test_no_overlap(self) -> None:
        assert f1_score("cat", "dog") == 0.0

    def test_partial_overlap(self) -> None:
        score = f1_score("big red car", "small red car")
        # "red" and "car" overlap (2 of 3 pred, 2 of 3 gt)
        # precision = 2/3, recall = 2/3, f1 = 2/3
        assert abs(score - 2 / 3) < 0.001

    def test_empty_both(self) -> None:
        assert f1_score("", "") == 1.0

    def test_empty_prediction(self) -> None:
        assert f1_score("", "some answer") == 0.0

    def test_empty_ground_truth(self) -> None:
        assert f1_score("some answer", "") == 0.0

    def test_porter_stemming(self) -> None:
        # "running" stems to "run", so should match
        score = f1_score("running", "run")
        assert score == 1.0

    def test_stemming_partial(self) -> None:
        # "dogs running quickly" vs "dog runs slow"
        # stems: [dog, run, quickli] vs [dog, run, slow]
        # common: dog, run (2)
        # precision = 2/3, recall = 2/3, f1 = 2/3
        score = f1_score("dogs running quickly", "dog runs slow")
        assert abs(score - 2 / 3) < 0.001


# --- multihop_f1 ---


class TestMultihopF1:
    def test_single_part(self) -> None:
        score = multihop_f1("paris", "paris")
        assert score == 1.0

    def test_comma_separated(self) -> None:
        # Prediction matches first part exactly but not second
        score = multihop_f1("paris", "paris, london")
        # F1("paris", "paris") = 1.0, F1("paris", "london") = 0.0
        assert abs(score - 0.5) < 0.001

    def test_all_parts_match(self) -> None:
        # F1("paris london", "paris") = 2*(0.5*1.0)/(0.5+1.0) = 2/3
        # F1("paris london", "london") = 2/3
        # Average = 2/3
        score = multihop_f1("paris london", "paris, london")
        assert abs(score - 2 / 3) < 0.001

    def test_exact_both_parts(self) -> None:
        # Prediction that exactly matches both parts individually
        score_a = multihop_f1("paris", "paris, paris")
        assert score_a == 1.0


# --- openended_f1 ---


class TestOpenendedF1:
    def test_uses_first_segment(self) -> None:
        score = openended_f1("blue", "blue; the sky is blue")
        assert score == 1.0

    def test_ignores_after_semicolon(self) -> None:
        score = openended_f1("red", "blue; red is wrong")
        assert score == 0.0


# --- adversarial_score ---


class TestAdversarialScore:
    def test_abstains_not_mentioned(self) -> None:
        assert adversarial_score("This is not mentioned in the text") == 1.0

    def test_abstains_no_information(self) -> None:
        assert adversarial_score("There is no information available") == 1.0

    def test_case_insensitive(self) -> None:
        assert adversarial_score("NOT MENTIONED anywhere") == 1.0

    def test_provides_answer(self) -> None:
        assert adversarial_score("The answer is 42") == 0.0

    def test_empty_prediction(self) -> None:
        assert adversarial_score("") == 0.0


# --- retrieval_recall ---


class TestRetrievalRecall:
    def test_full_hit(self) -> None:
        assert retrieval_recall(["D1:3", "D2:1"], ["D1:3", "D2:1"]) == 1.0

    def test_partial_hit(self) -> None:
        assert retrieval_recall(["D1:3"], ["D1:3", "D2:1"]) == 0.5

    def test_no_hit(self) -> None:
        assert retrieval_recall(["D3:1"], ["D1:3", "D2:1"]) == 0.0

    def test_empty_evidence(self) -> None:
        assert retrieval_recall(["D1:3"], []) == 1.0

    def test_empty_retrieved(self) -> None:
        assert retrieval_recall([], ["D1:3"]) == 0.0

    def test_superset_retrieved(self) -> None:
        assert retrieval_recall(["D1:1", "D1:3", "D2:1", "D3:5"], ["D1:3", "D2:1"]) == 1.0


# --- score_qa (integration) ---


class TestScoreQA:
    def test_category_2_standard(self) -> None:
        result = score_qa("7 May 2023", category=2, ground_truth="7 May 2023")
        assert result["f1"] == 1.0

    def test_category_5_adversarial(self) -> None:
        result = score_qa("not mentioned", category=5)
        assert result["adversarial"] == 1.0
        assert "f1" not in result

    def test_category_1_multihop(self) -> None:
        result = score_qa("paris", category=1, ground_truth="paris, london")
        assert abs(result["f1"] - 0.5) < 0.001

    def test_includes_retrieval_recall(self) -> None:
        result = score_qa(
            "answer",
            category=2,
            ground_truth="answer",
            retrieved_ids=["D1:3"],
            evidence_ids=["D1:3", "D2:1"],
        )
        assert result["retrieval_recall"] == 0.5
