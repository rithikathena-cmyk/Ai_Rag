import re

from app.core.yaml_config import load_yaml_config
from app.services.guardrails.types import GuardrailStep

NAME = "output_citation_check"

_CITATION_PATTERN = re.compile(r"\[(\d+)\]")


def _enabled() -> bool:
    return bool(load_yaml_config("guardrails.yaml").get("output_citation_rail", {}).get("citation_check_enabled", True))


def check_citations(reply: str, sources: list[dict]) -> GuardrailStep:
    """Flags (never blocks — see backend/config/guardrails.yaml) a reply that
    used retrieved sources but contains no bracketed citation marker, an
    early signal of an ungrounded claim slipping past the model's own
    citation habit (the planner's system prompt already instructs it to cite
    every claim; this is the check that catches when it doesn't). Also flags
    a citation number that doesn't correspond to any retrieved source (e.g.
    `[7]` when only 3 sources were retrieved) — a fabricated-looking
    citation, since a model-generated citation is never trusted just because
    it's present and well-formed; see this function's own "never blocks"
    behavior above for why this stays a flag, not a hard rejection, same as
    the missing-citation case."""
    if not sources:
        return GuardrailStep(NAME, "pass", "No sources were used, nothing to cite")
    if not _enabled():
        return GuardrailStep(NAME, "pass", "Check disabled")
    matches = _CITATION_PATTERN.findall(reply)
    if not matches:
        return GuardrailStep(NAME, "pass", "Reply used retrieved sources but contains no citation marker")
    fabricated = sorted({n for n in matches if not (1 <= int(n) <= len(sources))}, key=int)
    if fabricated:
        return GuardrailStep(
            NAME, "pass",
            f"Reply cites source(s) {', '.join(fabricated)}, which don't exist among the "
            f"{len(sources)} retrieved source(s) — possible fabricated citation",
        )
    return GuardrailStep(NAME, "pass", "Reply cites its sources")


def confidence_score(sources: list[dict]) -> str:
    """high/medium/low, derived from the reranked sources' relevance scores
    — a measure of how well-matched the retrieved context was, not a claim
    about the reply's factual correctness. 'n/a' when no sources were used
    (nothing to score confidence against)."""
    if not sources:
        return "n/a"
    scores = [s.get("score", 0.0) for s in sources]
    avg = sum(scores) / len(scores)
    if avg >= 0.7:
        return "high"
    if avg >= 0.4:
        return "medium"
    return "low"
