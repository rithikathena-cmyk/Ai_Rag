import json

import anthropic

from app.core.config import settings
from app.services.generation.client import get_client
from app.services.monitoring.metrics import record_token_usage

JUDGE_SYSTEM_PROMPT = """You are a strict evaluator of RAG (retrieval-augmented generation) answers.
Given a question, the source passages that were retrieved for it, and a generated answer, score the answer.

Respond with ONLY a JSON object, no other text, in exactly this shape:
{
  "groundedness": <float 0-1, how well the answer's claims are supported by the sources>,
  "faithfulness": <float 0-1, how free the answer is from contradicting the sources>,
  "total_claims": <int, number of distinct factual claims made in the answer>,
  "hallucinated_claims": <int, number of those claims NOT supported by any source>,
  "notes": <one or two sentence explanation of the scores>
}

If the answer declines to answer or states it lacks enough information, set total_claims and
hallucinated_claims to 0, and score groundedness/faithfulness based on whether that refusal was
actually warranted given the sources."""


class JudgeError(Exception):
    pass


def judge_answer(question: str, answer: str, sources: list[str]) -> dict:
    sources_block = "\n\n".join(f"[{i + 1}] {s}" for i, s in enumerate(sources)) or "(no sources were retrieved)"
    user_message = f"Question: {question}\n\nRetrieved sources:\n{sources_block}\n\nGenerated answer:\n{answer}"

    try:
        response = get_client().messages.create(
            model=settings.claude_model_name,
            max_tokens=500,
            thinking={"type": "adaptive"},
            output_config={"effort": settings.claude_effort},
            system=JUDGE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
    except anthropic.APIError as exc:
        raise JudgeError(str(exc)) from exc

    if response.usage:
        record_token_usage(
            "eval_judge", settings.claude_model_name, response.usage.input_tokens, response.usage.output_tokens
        )

    if response.stop_reason == "refusal":
        raise JudgeError("Judge model refused to score this answer")

    text = "".join(block.text for block in response.content if block.type == "text").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise JudgeError(f"Judge returned non-JSON output: {text[:200]!r}") from exc

    total_claims = max(0, int(data.get("total_claims", 0)))
    hallucinated_claims = max(0, min(total_claims, int(data.get("hallucinated_claims", 0))))
    hallucination_rate = (hallucinated_claims / total_claims) if total_claims > 0 else 0.0

    return {
        "groundedness": float(data.get("groundedness", 0.0)),
        "faithfulness": float(data.get("faithfulness", 0.0)),
        "hallucination_rate": hallucination_rate,
        "total_claims": total_claims,
        "hallucinated_claims": hallucinated_claims,
        "notes": str(data.get("notes", "")),
    }
