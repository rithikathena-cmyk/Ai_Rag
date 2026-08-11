import json

from app.gateway.claude_gateway import GenerationError, claude_gateway
from app.gateway.prompt_manager import load_prompt
from app.gateway.schemas import GenerateRequest, ModelTier

JUDGE_SYSTEM_PROMPT = load_prompt("judge_agent", "v2").text


class JudgeError(Exception):
    pass


def judge_answer(question: str, answer: str, sources: list[str], *, request_id: str | None = None) -> dict:
    """`request_id`, when supplied, is threaded into the underlying gateway
    call so this judge call's token usage lands in gateway_usage_logs under
    the same request_id as the rest of an evaluation run (see
    services/evaluation/runner.py) — reusing the existing gateway audit
    trail as the source of truth for tokens/cost/model, rather than a second
    tracking mechanism."""
    sources_block = "\n\n".join(f"[{i + 1}] {s}" for i, s in enumerate(sources)) or "(no sources were retrieved)"
    user_message = f"Question: {question}\n\nRetrieved sources:\n{sources_block}\n\nGenerated answer:\n{answer}"

    try:
        result = claude_gateway.generate(
            GenerateRequest(
                agent_name="eval_judge",
                system=JUDGE_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}],
                tier=ModelTier.FAST,
                max_tokens=500,
                request_id=request_id,
                cache_system=True,
            )
        )
    except GenerationError as exc:
        raise JudgeError(str(exc)) from exc

    if result.stop_reason == "refusal":
        raise JudgeError("Judge model refused to score this answer")

    text = result.text.strip()
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
        # New in v2 — see judge_agent_v2.yaml. Absent/malformed on an older
        # cached result or a judge response that skips the field defaults to
        # 0.0 rather than raising, matching every other score's handling above.
        "citation_accuracy": float(data.get("citation_accuracy", 0.0)),
        "answer_relevance": float(data.get("answer_relevance", 0.0)),
        "notes": str(data.get("notes", "")),
    }
