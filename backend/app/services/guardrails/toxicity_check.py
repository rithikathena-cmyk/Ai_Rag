"""Local toxicity/harassment classifier — a HuggingFace text-classification
model (unitary/toxic-bert by default: BERT fine-tuned on the Jigsaw Toxic
Comment dataset, multi-label — toxic/severe_toxic/obscene/threat/insult/
identity_hate, each an independent sigmoid score, not a softmax over
mutually exclusive classes), run in-process via transformers.pipeline() with
top_k=None so every label's score comes back in one call. Same "local model,
no API key, no network call at inference time beyond a one-time download"
shape as deberta_injection_check.py/gliner_check.py/presidio_check.py.

Fills a real gap: nothing else in this pipeline looks at abusive/harassing
language specifically — injection.py/destructive.py/semantic_check.py/
deberta_injection_check.py are all about instruction manipulation, and
presidio_check.py/gliner_check.py/pii.py are all about personal-information
exposure. A message that's simply hostile or hateful, with no injection
attempt and no PII, passes every existing check untouched.

Runs on BOTH input and output — same function, same config
(guardrails.yaml's toxicity_check:) — mirroring presidio_check.py/
gliner_check.py's dual-sided wiring: a user's message can be abusive, and a
generated reply could (much more rarely, but worth the same floor) echo
hostile language quoted from a retrieved document without the right framing.
"""

import threading

from app.core.yaml_config import load_yaml_config
from app.services.guardrails.types import GuardrailStep

NAME = "toxicity_check"

_pipeline_lock = threading.Lock()
_pipeline = None


def _get_pipeline(model_name: str):
    """Built once, lazily, on first real use — same convention as
    deberta_injection_check.py's _get_pipeline(): a deployment that disables
    this check never pays the model-load cost. Double-checked locking so
    concurrent first requests can't race to load two separate pipelines."""
    global _pipeline
    if _pipeline is not None:
        return _pipeline
    with _pipeline_lock:
        if _pipeline is None:
            from transformers import pipeline as hf_pipeline

            _pipeline = hf_pipeline("text-classification", model=model_name, top_k=None)
    return _pipeline


def _config() -> dict:
    return load_yaml_config("guardrails.yaml").get("toxicity_check", {})


def check_toxicity(text: str) -> GuardrailStep:
    """Fails OPEN by default on any model error — this is an additive layer
    with no existing deterministic check backing up this specific category,
    but an infra problem here still must not block an ordinary message. Set
    fail_closed: true in config to reverse this."""
    cfg = _config()
    if not cfg.get("enabled", True):
        return GuardrailStep(NAME, "pass", "Check disabled")

    truncated = text[: cfg.get("max_input_chars", 2000)]
    if not truncated.strip():
        return GuardrailStep(NAME, "pass", "Empty input")

    model_name = cfg.get("model_name", "unitary/toxic-bert")
    threshold = float(cfg.get("score_threshold", 0.7))

    try:
        scores = _get_pipeline(model_name)(truncated)[0]
    except Exception as exc:
        fail_closed = cfg.get("fail_closed", False)
        action = "block" if fail_closed else "pass"
        policy = "failed closed (blocking)" if fail_closed else "failed open"
        return GuardrailStep(NAME, action, f"check unavailable, {policy}: {type(exc).__name__}")

    triggered = sorted((s["label"], float(s["score"])) for s in scores if float(s["score"]) >= threshold)
    if triggered:
        labels = ", ".join(f"{label} ({score:.2f})" for label, score in triggered)
        return GuardrailStep(NAME, "block", f"Classified as toxic: {labels}")

    return GuardrailStep(NAME, "pass", "No toxic content detected")
