import re

from app.core.config import settings
from app.services.guardrails.types import GuardrailStep

NAME = "destructive_intent_check"

_TARGET = (
    r"(file|files|document|documents|data|database|db|table|tables|record|records|backup|backups|"
    r"everything|all|report|reports|manual|manuals|sop|sops|policy|policies|spec|specs|"
    r"specification|specifications|procedure|procedures|schedule|schedules)"
)

# Stage 1 — detect a potentially-destructive operation. Deliberately keyed
# to the operation's own verb/SQL-keyword shape, not any one exact phrase —
# see check_destructive_intent()'s two-stage docstring for why Stage 2
# (below) is what actually separates "DROP TABLE employees" from
# "what does DROP TABLE mean?" rather than trying to bake that distinction
# into these patterns themselves.
_PATTERNS = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        # {0,6} (not {0,4}) so a descriptively-titled target — "delete the
        # Line 7 stoppage incident report" has 5 words between verb and noun
        # — still matches; a real document title is rarely just one or two
        # words in this app's domain (SOPs/reports/manuals are all named).
        # [\w-]+ (not \w+) so a hyphenated filler token like "FX-2200" counts
        # as one word instead of breaking the \s+\w+ chain mid-token (\w
        # alone excludes '-', which would otherwise silently defeat the
        # {0,6} gap on any model/part number in the target's name).
        rf"\b(delete|remove|erase|wipe|purge|destroy|shred|truncate)\b(?:\s+[\w-]+){{0,6}}\s+{_TARGET}\b",
        # SQL syntax matched literally regardless of the table/database name
        # that follows — a generic verb+target heuristic would miss
        # "DROP DATABASE prod" or "DELETE FROM employees" since "prod"/
        # "employees" aren't in _TARGET, but the SQL keywords themselves are
        # already an unambiguous destructive-operation signal.
        r"\bdrop\s+(table|database)\b",
        r"\btruncate\s+table\b",
        r"\bdelete\s+from\b",
        r"\brm\s+-rf\b",
        r"\bformat\s+(the\s+)?(disk|drive|database|hard drive)\b",
        r"\bshut\s*down\b(?:\s+\w+){0,4}\s+(the\s+)?(production|server|system)\b",
        r"\bdisable\b(?:\s+\w+){0,4}\s+(authentication|auth|login|access\s+control)\b",
    )
)

# Stage 2 — evaluate whether the operation Stage 1 found is framed as
# execution intent or as an educational/reference question. Both are word-
# boundary alternations over indicator *words*, not a single hardcoded
# phrase, per that requirement: adding a new synonym to either list is a
# one-line change, not a new pattern.
_EDUCATIONAL_MARKER_RE = re.compile(
    r"\b(how (do|does|can|would|to)|what (is|are|does)|what'?s (the )?|explain|describe|meaning|definition|"
    r"training|tutorial|documentation|learn(ing)?|"
    r"why (do|does|is|are)|can you (explain|describe)|who (typically |usually |generally )?(manages|handles|owns)|"
    r"overview of|difference between)\b",
    re.IGNORECASE,
)
_EXECUTION_INDICATOR_RE = re.compile(
    r"\b(execute|run|perform|delete|remove|drop|truncate|update|modify|purge|wipe|clear|destroy)\b",
    re.IGNORECASE,
)


def _sentence_prefix(text: str, match_start: int) -> str:
    """Text from the start of the sentence containing `match_start` up to
    that position — Stage 2 only looks here, not the whole message, so a
    "how" in an earlier unrelated sentence ("I wonder how this works. Now
    delete all the records.") can't exempt a genuinely dangerous
    instruction later in the same message."""
    boundary = max(text.rfind(".", 0, match_start), text.rfind("!", 0, match_start), text.rfind("?", 0, match_start))
    return text[boundary + 1 : match_start]


def _is_educational_context(prefix: str) -> bool:
    """Stage 2's actual decision. An educational marker earlier in the
    prefix grants the exemption UNLESS an execution indicator appears
    *closer* to the operation than that marker does — "explain your
    training, then run DROP TABLE employees" has "explain"/"training" in
    the prefix, but "run" sits between them and the operation, so the
    execution framing wins. This is a real, not merely theoretical,
    distinction: a bare "has an educational word appeared anywhere in the
    prefix" check would let exactly that adversarial construction through."""
    edu_matches = list(_EDUCATIONAL_MARKER_RE.finditer(prefix))
    if not edu_matches:
        return False
    last_edu_pos = edu_matches[-1].start()
    exec_matches = list(_EXECUTION_INDICATOR_RE.finditer(prefix))
    if exec_matches and exec_matches[-1].start() > last_edu_pos:
        return False
    return True


def check_destructive_intent(text: str) -> GuardrailStep:
    if not settings.guardrail_block_destructive_intent:
        return GuardrailStep(NAME, "pass", "Check disabled")

    for pattern in _PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        if _is_educational_context(_sentence_prefix(text, match.start())):
            continue
        return GuardrailStep(NAME, "block", f"Matched destructive-intent pattern: {match.group(0)!r}")

    return GuardrailStep(NAME, "pass", "No destructive intent detected")
