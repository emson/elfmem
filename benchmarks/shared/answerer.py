"""OpenAI-compatible LLM client for generating benchmark answers via LM Studio."""

import random

from openai import AsyncOpenAI

SYSTEM_PROMPT = (
    "You are an answer extraction system. Extract the answer directly from "
    "the conversation. Rules:\n"
    "- Give a short, direct answer (1-10 words)\n"
    "- Use the exact words and names from the conversation\n"
    "- For dates: give the date exactly as stated\n"
    "- For people: include their name\n"
    "- For topics: state the specific topic mentioned\n"
    "- Do NOT explain or qualify your answer\n"
    "- If the information is in the conversation, state it\n"
    "- If truly not in the conversation, say 'not mentioned'"
)

ADVERSARIAL_SYSTEM_PROMPT = (
    "You are a careful reader. Based ONLY on the conversation provided, "
    "determine if the question can be answered. "
    "If the information IS discussed, give the answer. "
    "If the information is NOT mentioned in the conversation, you MUST respond "
    "with exactly: 'not mentioned'"
)


def _build_qa_prompt(context: str, question: str) -> str:
    """Build a standard QA user prompt."""
    return (
        f"Conversation:\n{context}\n\n"
        f"Question: {question}\n\n"
        f"Extract the answer from the conversation. Answer in as few words as possible:"
    )


def _build_adversarial_prompt(
    context: str, question: str, adversarial_answer: str
) -> str:
    """Build a prompt for adversarial (category 5) questions.

    These questions ask about things NOT in the conversation. The model should
    respond with 'not mentioned' rather than hallucinating an answer.
    """
    return (
        f"Conversation:\n{context}\n\n"
        f"Question: {question}\n\n"
        f"If this information is discussed in the conversation, give the answer. "
        f"If it is NOT mentioned in the conversation, respond with 'not mentioned'.\n\n"
        f"Answer:"
    )


async def generate_answer(
    context: str,
    question: str,
    category: int,
    adversarial_answer: str = "",
    model: str = "google/gemma-4-26b-a4b",
    max_tokens: int = 100,
    base_url: str = "http://localhost:1234/v1",
) -> str:
    """Generate an answer to a benchmark question using an OpenAI-compatible API.

    Args:
        context: The conversation context to answer from.
        question: The question to answer.
        category: Question category (1-5). Category 5 uses adversarial prompt.
        adversarial_answer: The misleading answer option for category 5.
        model: Model identifier for LM Studio.
        max_tokens: Maximum tokens in the response.
        base_url: LM Studio API base URL.

    Returns:
        The model's response text.
    """
    client = AsyncOpenAI(base_url=base_url, api_key="lm-studio")

    if category == 5:
        system_prompt = ADVERSARIAL_SYSTEM_PROMPT
        user_prompt = _build_adversarial_prompt(context, question, adversarial_answer)
    else:
        system_prompt = SYSTEM_PROMPT
        user_prompt = _build_qa_prompt(context, question)

    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=max_tokens,
        temperature=0.0,
    )
    return response.choices[0].message.content or ""
