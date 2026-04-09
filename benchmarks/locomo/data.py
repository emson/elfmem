"""Typed dataclasses and loader for LoCoMo benchmark data."""

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Turn:
    """A single dialogue turn in a conversation session."""

    speaker: str
    dia_id: str
    text: str


@dataclass
class Session:
    """A conversation session with a timestamp and sequence of turns."""

    session_num: int
    date_time: str
    turns: list[Turn]


@dataclass
class QAPair:
    """A question-answer pair with category and evidence references."""

    question: str
    category: int
    evidence: list[str]
    answer: str | None = None  # None for category 5
    adversarial_answer: str | None = None  # only for category 5


@dataclass
class Conversation:
    """A complete LoCoMo conversation with sessions and QA pairs."""

    sample_id: str
    speaker_a: str
    speaker_b: str
    sessions: list[Session]
    qa_pairs: list[QAPair]


def _parse_sessions(conv_data: dict) -> list[Session]:
    """Extract all sessions from a conversation dict by scanning dynamic keys."""
    sessions: list[Session] = []
    for i in range(1, 50):
        key = f"session_{i}"
        if key not in conv_data:
            continue
        date_time = conv_data.get(f"{key}_date_time", "")
        turns = [
            Turn(speaker=t["speaker"], dia_id=t["dia_id"], text=t["text"])
            for t in conv_data[key]
        ]
        sessions.append(Session(session_num=i, date_time=date_time, turns=turns))
    return sessions


def _parse_qa_pairs(qa_list: list[dict]) -> list[QAPair]:
    """Parse raw QA dicts into typed QAPair objects."""
    return [
        QAPair(
            question=qa["question"],
            category=qa["category"],
            evidence=qa.get("evidence", []),
            answer=qa.get("answer"),
            adversarial_answer=qa.get("adversarial_answer"),
        )
        for qa in qa_list
    ]


def load_locomo(path: Path) -> list[Conversation]:
    """Load locomo10.json into typed Conversation objects.

    Args:
        path: Path to the locomo JSON file.

    Returns:
        List of Conversation objects with sessions and QA pairs.

    Raises:
        FileNotFoundError: If the path does not exist.
        json.JSONDecodeError: If the file is not valid JSON.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    conversations: list[Conversation] = []
    for entry in raw:
        conv_data = entry["conversation"]
        sessions = _parse_sessions(conv_data)
        qa_pairs = _parse_qa_pairs(entry.get("qa", []))
        conversations.append(
            Conversation(
                sample_id=entry["sample_id"],
                speaker_a=conv_data["speaker_a"],
                speaker_b=conv_data["speaker_b"],
                sessions=sessions,
                qa_pairs=qa_pairs,
            )
        )
    return conversations
