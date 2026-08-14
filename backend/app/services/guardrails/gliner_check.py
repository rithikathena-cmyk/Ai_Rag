"""GLiNER-based semantic PII check — a zero-shot NER model
(github.com/urchade/GLiNER), run entirely in-process (no separate server;
NeMo Guardrails ships its own GLiNER integration but that one talks to an
external "GLiNER server" over HTTP — this module uses the `gliner` pip
package directly instead, matching how every other model this app already
loads works: presidio_check.py's spaCy model, the BGE-M3 embedding model,
RapidOCR — all in-process lazy singletons, not a second server process to
deploy and keep alive).

Positioned alongside presidio_check.py as a second, complementary semantic
PII layer, not a replacement: Presidio's allowlist here is deliberately
narrow (structurally-precise identifier TYPES only — passport/IBAN/bank
account/driver's license/medical license/crypto; see that module's
docstring) specifically because Presidio's own general-purpose recognizers
false-positive heavily on this app's business vocabulary. GLiNER is a
zero-shot model — you supply the label set at call time — so this module
takes the same lesson further: the default LABELS below are natural-language
descriptions of the identifier SHAPES pii.py's regex and presidio_check.py's
allowlist don't already cover (an address phrased in prose, a government ID
number described rather than pattern-matched, a financial account number
spelled out narratively), not a broad "any personal information" sweep.

Deliberately EXCLUDES person-name and organization-style labels: this app is
an HR/manufacturing-incident-heavy internal assistant where legitimately
discussing a named employee (an incident reporter, a shift lead, a
candidate) is routine, expected content — not a leak. A GLiNER call
configured to catch "person name" would block or redact nearly every
ordinary HR/incident query, the exact false-positive trap presidio_check.py
already documented for Presidio's own default PERSON recognizer. If a future
need arises to catch names specifically, that requires the same kind of
empirical calibration against real traffic presidio_check.py's allowlist
went through — not a default flipped on here without that evidence.
"""

import threading

from app.core.yaml_config import load_yaml_config
from app.services.guardrails.types import GuardrailStep

NAME = "gliner_check"

# Natural-language label set — GLiNER matches free-text descriptions, not
# fixed enum values, so these are written the way GLiNER's own examples
# phrase labels (lowercase, descriptive noun phrases), not the SCREAMING_CASE
# Presidio/pii.py use for their own detectors.
# "physical address" and "government identification number" (the original
# wording) were measurably too broad: GLiNER scored a plain phone number
# ("312-555-0173") at 0.634 against "physical address" and 0.709-0.82
# against "government identification number" — both above the default 0.6
# threshold, both false positives that would fully BLOCK an otherwise-
# correctly-redactable reply (pii.py's own phone recognizer already handles
# phone numbers correctly; this label was mis-attributing them to the wrong
# category, not adding real coverage). Found via
# tests/test_chat_authorized_pii_grounding.py failing against real content.
# Narrowed wording measurably fixes both while preserving genuine detection:
# "home address or mailing address" scores 0.000 on that same phone number
# but still 0.76-0.92 on real addresses ("42 Oakwood Lane, Springfield",
# "123 Main Street, Suite 400"); "government-issued identification number
# such as a social security number or passport number" scores 0.000 on the
# phone number but still 0.68 on a real SSN-shaped string ("123-45-6789").
# Re-measure with a script like this before further wording changes —
# see semantic_check.py's _UNSAFE_EXAMPLES comment for why intuition about
# which wording helps isn't reliable for embedding/NER models generally.
_DEFAULT_LABELS = (
    "home address or mailing address",
    "government-issued identification number such as a social security number or passport number",
    "financial account number",
    "medical condition or health information",
)

_model_lock = threading.Lock()
_model = None


def _get_model(model_name: str):
    """Built once, lazily, on first real use — loading a GLiNER checkpoint
    has real cost (a model download on first run, then real memory/CPU to
    keep loaded), and a deployment that disables this check should never pay
    it, same convention as presidio_check.py's _get_analyzer(). Double-
    checked locking so concurrent first requests can't race to load two
    separate model instances. Not rebuilt if model_name changes at runtime
    (a config change requires a process restart to take effect) — matches
    how presidio_check.py's spaCy engine is also a fixed-at-first-use
    singleton, not re-evaluated per call."""
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is None:
            from gliner import GLiNER

            _model = GLiNER.from_pretrained(model_name)
    return _model


def _config() -> dict:
    return load_yaml_config("guardrails.yaml").get("gliner_check", {})


def check_with_gliner(text: str) -> GuardrailStep:
    """Called from both run_input_guardrails() and run_output_guardrails()
    — same function, same config (guardrails.yaml's gliner_check:), same
    label set, mirroring presidio_check.py's dual-sided wiring.

    Fails CLOSED by default on any model error (not loaded, unexpected
    exception) — same reasoning as presidio_check.py's fail_closed default:
    a classifier failure means "unknown whether this text is safe," not
    "safe." Set fail_closed: false in config to restore fail-open."""
    cfg = _config()
    if not cfg.get("enabled", True):
        return GuardrailStep(NAME, "pass", "Check disabled")

    truncated = text[: cfg.get("max_input_chars", 2000)]
    if not truncated.strip():
        return GuardrailStep(NAME, "pass", "Empty input")

    model_name = cfg.get("model_name", "urchade/gliner_small-v2.1")
    labels = cfg.get("labels") or list(_DEFAULT_LABELS)
    threshold = float(cfg.get("score_threshold", 0.6))

    try:
        entities = _get_model(model_name).predict_entities(truncated, labels, threshold=threshold)
    except Exception as exc:
        fail_closed = cfg.get("fail_closed", True)
        action = "block" if fail_closed else "pass"
        policy = "failed closed (blocking)" if fail_closed else "failed open"
        return GuardrailStep(NAME, action, f"check unavailable, {policy}: {type(exc).__name__}")

    if not entities:
        return GuardrailStep(NAME, "pass", "No high-confidence entities detected")

    # Label + count only, e.g. "physical address×2" — the matched span text
    # never appears in this detail string, same "labels only, never values"
    # rule pii.py/presidio_check.py follow for their own audit-log-reachable
    # detail strings (this reaches record_guardrail_event() ->
    # GET /admin/guardrail-analytics).
    found_labels = sorted({e["label"] for e in entities})
    return GuardrailStep(NAME, "block", f"Detected: {', '.join(found_labels)}")
