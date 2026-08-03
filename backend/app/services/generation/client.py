import anthropic

from app.core.config import settings


class GenerationError(Exception):
    pass


_client: anthropic.Anthropic | None = None


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        if not settings.anthropic_api_key:
            raise GenerationError("ANTHROPIC_API_KEY is not configured")
        _client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return _client


def generate_answer(system: str, user_message: str) -> tuple[str, str]:
    try:
        response = get_client().messages.create(
            model=settings.claude_model_name,
            max_tokens=settings.claude_max_tokens,
            thinking={"type": "adaptive"},
            output_config={"effort": settings.claude_effort},
            system=system,
            messages=[{"role": "user", "content": user_message}],
        )
    except anthropic.APIError as exc:
        raise GenerationError(str(exc)) from exc

    if response.stop_reason == "refusal":
        return "", "refusal"

    text = "".join(block.text for block in response.content if block.type == "text")
    return text, response.stop_reason
