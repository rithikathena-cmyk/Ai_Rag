"""Credential/secret-shaped value detection — runs on BOTH sides of a turn.

The pattern list itself used to live only in output.py (system-prompt/secret
LEAKAGE detection on the model's reply). Nothing scanned the user's own
INPUT message for the same shapes, so a message containing a real AWS key,
GitHub token, or private-key block passed every existing input check
untouched and reached the LLM, the messages table, and this pipeline's own
audit trace verbatim — the same class of exposure output.py already guards
against on the way out, just missing on the way in. Moved here so both
directions share one definition instead of drifting apart.

Deliberately shape-based (looks like an actual populated secret), not
keyword-based ("api key" as a bare phrase) — see each pattern's own
reasoning where non-obvious. A message that says "where do I find my API
key?" must not block; a message containing what looks like a real key value
must.
"""

import re

from app.core.yaml_config import load_yaml_config
from app.services.guardrails.types import GuardrailStep

NAME = "secret_detected_check"

#: (label, pattern) pairs — the label is what check_secrets()/redact_secrets()
#: below report; a category name only, never the matched value, same "labels
#: only" discipline pii.py's _summarize() and gliner_check.py's canonical
#: labels already apply to their own detail strings.
CREDENTIAL_PATTERNS: tuple[tuple[str, re.Pattern], ...] = tuple(
    (label, re.compile(p))
    for label, p in (
        ("API_KEY", r"\bsk-[A-Za-z0-9_-]{20,}\b"),  # Anthropic/OpenAI-style secret key
        ("AWS_ACCESS_KEY", r"\bAKIA[A-Z0-9]{16}\b"),
        ("JWT", r"\b[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),  # header.payload.signature
        ("GITHUB_TOKEN", r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),  # personal access / OAuth / app token
        ("SLACK_TOKEN", r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),  # bot/user/app token
        ("GOOGLE_API_KEY", r"\bAIza[0-9A-Za-z_-]{35}\b"),
        ("STRIPE_KEY", r"\bsk_live_[0-9a-zA-Z]{24,}\b"),
        ("PRIVATE_KEY", r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |)PRIVATE KEY-----"),  # PEM block
        # A stated plaintext password — unlike every pattern above, a
        # password has no fixed SHAPE (it can be any string), so this is
        # deliberately anchored on DISCLOSURE PHRASING instead:
        # "password"/"passwd"/"pwd" directly followed by "is"/":"/"=" and a
        # value — the same "keyword + adjacency, not keyword alone" pattern
        # pii.py's own context-gated recognizers (PHONE, DATE_OF_BIRTH)
        # already use elsewhere in this codebase. A question with no value
        # following the keyword ("where do I find my password?", "is my
        # password secure?") does not match, mirroring this module's own
        # "a message that says 'where do I find my API key?' must not
        # block" rule above. Known, accepted trade-off: this cannot
        # distinguish a genuine disclosure ("my password is hunter2...")
        # from an informational statement sharing the same shape ("the
        # password requirement is 8 characters") — the same kind of
        # imperfect-but-net-positive coverage CONNECTION_STRING_CREDENTIAL
        # below already accepts (it can't tell a live credential from one
        # pasted as a documentation example either). No existing test
        # exercises that ambiguous case; narrow this with measured evidence
        # if one surfaces, rather than guessing further.
        ("PASSWORD", r"(?i)\b(?:password|passwd|pwd)\s*(?:is|[:=])\s*\S+"),
        # Connection-string credentials — postgres://user:pass@host, mysql://,
        # mongodb://, redis:// — the password segment specifically, not just
        # any URL (a doc/URL mentioning "postgres://host/db" with no
        # credentials embedded is not a leak).
        ("CONNECTION_STRING_CREDENTIAL", r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://[^\s:/@]+:[^\s:/@]+@[^\s/]+"),
    )
)


def _enabled() -> bool:
    return bool(load_yaml_config("guardrails.yaml").get("secret_detection", {}).get("enabled", True))


def check_secrets(text: str) -> GuardrailStep:
    """Input-side use of CREDENTIAL_PATTERNS above. Never echoes the matched
    value in `detail` — only which pattern/category matched — same
    discipline pii.py's redact_pii() applies to its own GuardrailStep.detail
    (see that module's comment on why a raw-value-bearing detail string is
    itself a leak once it flows into the audit trace/analytics endpoint)."""
    if not _enabled():
        return GuardrailStep(NAME, "pass", "Check disabled")

    for label, pattern in CREDENTIAL_PATTERNS:
        if pattern.search(text):
            return GuardrailStep(NAME, "block", f"Detected: {label}")

    return GuardrailStep(NAME, "pass", "No credential-shaped value detected")


def redact_secrets(text: str) -> tuple[str, bool]:
    """Replaces every credential-shaped match with a fixed placeholder,
    returning (redacted_text, anything_redacted). Unlike check_secrets()
    above (input-side, block on match), this is the retrieval-side use: a
    document a user is genuinely authorized to retrieve can still happen to
    contain a real embedded credential (a config file, README, or support
    ticket that got ingested) — blocking the whole retrieval over that would
    make an otherwise-legitimate document permanently unusable, but there is
    also no legitimate reason for a live credential value to reach the LLM's
    context or a user-facing citation verbatim. Applied to BOTH the
    LLM-visible and display views of retrieved text (services/reranking/
    pipeline.py) — stricter than PII's raw/display split (see pii.py's
    DualText), which does let the LLM see the raw, unredacted value for
    legitimate authorized-lookup cases pii.py's module docstring documents;
    there's no equivalent legitimate case for the model needing a real
    secret value to answer a question."""
    redacted = text
    found = False
    for _label, pattern in CREDENTIAL_PATTERNS:
        if pattern.search(redacted):
            found = True
            redacted = pattern.sub("[REDACTED_SECRET]", redacted)
    return redacted, found
