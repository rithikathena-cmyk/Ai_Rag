import json
from typing import Generator, Iterable

from app.gateway.schemas import StreamChunk, TokenUsage


def stream_anthropic_response(
    client, *, model: str, max_tokens: int, system, messages: list[dict], effort: str | None = None,
    supports_extended_reasoning: bool = True,
) -> Generator[StreamChunk, None, None]:
    """Wraps the Anthropic streaming context manager as a StreamChunk
    generator. The final chunk carries done=True and the completed
    TokenUsage; every chunk before that is a text delta."""
    kwargs: dict = dict(model=model, max_tokens=max_tokens, system=system, messages=messages)
    # thinking/output_config are bundled extended-reasoning controls some
    # models (e.g. claude-haiku-4-5) reject outright — see
    # gateway/schemas.py::ModelTierConfig.supports_extended_reasoning.
    if supports_extended_reasoning:
        kwargs["thinking"] = {"type": "adaptive"}
        if effort:
            kwargs["output_config"] = {"effort": effort}

    with client.messages.stream(**kwargs) as stream:
        for text in stream.text_stream:
            yield StreamChunk(text=text)
        final = stream.get_final_message()
        usage = TokenUsage(
            input_tokens=getattr(final.usage, "input_tokens", 0),
            output_tokens=getattr(final.usage, "output_tokens", 0),
        )
        yield StreamChunk(text="", done=True, usage=usage)


def to_sse(chunks: Iterable[StreamChunk]) -> Generator[str, None, None]:
    """Adapts a StreamChunk stream into Server-Sent-Events lines, ready for
    `fastapi.responses.StreamingResponse(gen, media_type="text/event-stream")`."""
    for chunk in chunks:
        payload: dict = {"text": chunk.text, "done": chunk.done}
        if chunk.usage:
            payload["usage"] = {"input_tokens": chunk.usage.input_tokens, "output_tokens": chunk.usage.output_tokens}
        yield f"data: {json.dumps(payload)}\n\n"
