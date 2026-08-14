"""Groundedness (hallucination) check on the model's reply — a local NLI
(natural-language-inference) cross-encoder (cross-encoder/nli-deberta-v3-base
by default) scoring whether the reply is actually supported by the sources
it was generated from, not just whether it *cites* them.
services/guardrails/citation_rail.py's check_citations() only checks for the
PRESENCE of a `[n]`-style marker; it has no way to distinguish a
well-formatted but unsupported or contradicted claim from a genuinely
grounded one. This check is the accuracy signal check_citations() doesn't
provide.

Reuses this codebase's existing reranking infrastructure's loading pattern
(services/reranking/model_loader.py: sentence-transformers' CrossEncoder, on
CPU, a lazy singleton) rather than inventing a new model-loading convention —
sentence-transformers is already a hard dependency (requirements.txt), just
a different pretrained checkpoint than the reranker's.

Deliberately never blocks — same policy as check_citations() and for the
same reason (see that function's docstring): an NLI classifier scoring a
long, multi-source premise against a full reply is a noisier signal than a
literal citation-marker check, and a false positive here would refuse an
otherwise-correct answer. This is a visible trace signal, not a gate.
"""

import threading

from app.core.yaml_config import load_yaml_config
from app.services.guardrails.types import GuardrailStep

NAME = "groundedness_check"

_model_lock = threading.Lock()
_model = None


def _get_model(model_name: str):
    """Built once, lazily, on first real use — same convention as every
    other model-backed check in this package (presidio_check.py's
    _get_analyzer(), gliner_check.py's _get_model(), etc). Double-checked
    locking so concurrent first requests can't race to load two separate
    model instances."""
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is None:
            from sentence_transformers import CrossEncoder

            _model = CrossEncoder(model_name, device="cpu")
    return _model


def _config() -> dict:
    return load_yaml_config("guardrails.yaml").get("groundedness_check", {})


def check_groundedness(reply: str, sources: list[dict]) -> GuardrailStep:
    """Reached only when output guardrails didn't already block the reply
    (routers/chat.py skips this call entirely on a blocked reply — there's
    nothing meaningful to ground-check a refusal against). Concatenates all
    retrieved sources' text as the NLI premise and the reply as the
    hypothesis — one classification call per turn, not one per sentence, to
    keep this check's cost bounded and predictable."""
    if not sources:
        return GuardrailStep(NAME, "pass", "No sources were used, nothing to ground")

    cfg = _config()
    if not cfg.get("enabled", True):
        return GuardrailStep(NAME, "pass", "Check disabled")

    premise = " ".join(s.get("text", "") for s in sources)[: cfg.get("max_premise_chars", 4000)]
    hypothesis = reply[: cfg.get("max_reply_chars", 2000)]
    if not premise.strip() or not hypothesis.strip():
        return GuardrailStep(NAME, "pass", "Nothing to score")

    model_name = cfg.get("model_name", "cross-encoder/nli-deberta-v3-base")

    try:
        model = _get_model(model_name)
        scores = model.predict([(premise, hypothesis)], apply_softmax=True)[0]
        id2label = {int(k): str(v).lower() for k, v in model.model.config.id2label.items()}
    except Exception as exc:
        fail_closed = cfg.get("fail_closed", False)
        action = "block" if fail_closed else "pass"
        policy = "failed closed (blocking)" if fail_closed else "failed open"
        return GuardrailStep(NAME, action, f"check unavailable, {policy}: {type(exc).__name__}")

    label_scores = {id2label.get(i, str(i)): float(s) for i, s in enumerate(scores)}
    contradiction_score = label_scores.get("contradiction", 0.0)
    threshold = float(cfg.get("contradiction_threshold", 0.5))

    if contradiction_score >= threshold:
        return GuardrailStep(
            NAME, "pass",
            f"Reply may contradict its retrieved sources (contradiction score={contradiction_score:.2f}) "
            "— flagged, not blocked",
        )
    return GuardrailStep(
        NAME, "pass", f"Reply appears consistent with its sources (contradiction score={contradiction_score:.2f})"
    )
