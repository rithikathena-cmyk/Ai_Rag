"""Claude Gateway — the single path every agent uses to talk to Anthropic.

No module outside this package should import `anthropic` or
`langchain_anthropic` directly. Use `claude_gateway.generate()`,
`claude_gateway.stream()`, or `claude_gateway.get_langchain_model()`.
"""

from app.gateway.claude_gateway import GenerationError, claude_gateway

__all__ = ["claude_gateway", "GenerationError"]
