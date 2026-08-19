"""Normalization + structural/checksum validation for PII candidates found
by pii_patterns.py. Kept separate from the regexes themselves so each type's
"is this actually valid, not just shaped-like" logic is unit-testable in
isolation, and so pii.py's redaction loop stays a plain
match-then-normalize-then-validate pipeline for every type.
"""

import re

_INDIAN_MOBILE_PREFIXES = "6789"

_DIGIT_WORD_MAP = {
    "zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
}


def digit_words_to_digits(value: str) -> str:
    """Converts a pii_patterns.PHONE_SPELLED_OUT_RE match ("two zero six ...")
    into its digit-character equivalent ("206...") so the ordinary digit-
    based canonicalize_phone/is_valid_phone pipeline — built for digit
    characters, not English words — can validate it unchanged. Case-
    insensitive on the word itself; unrecognized words (shouldn't occur,
    since the regex only matches the words in _DIGIT_WORD_MAP to begin
    with) contribute nothing rather than raising."""
    words = re.findall(r"[A-Za-z]+", value)
    return "".join(_DIGIT_WORD_MAP.get(w.lower(), "") for w in words)

# Verhoeff checksum tables — the algorithm UIDAI uses for Aadhaar's 12th
# (check) digit. Unlike a simple mod-10/Luhn check, Verhoeff catches all
# single-digit errors and all adjacent-transposition errors, which is why
# it's the standard choice for a 12-digit identifier that's frequently
# hand-typed. `_D` is the multiplication table, `_P` is the permutation
# applied per digit position (cycling through 8 permutations), `_INV` is the
# inverse table (unused here — only needed for *generating* a check digit,
# not validating one).
_VERHOEFF_D = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 2, 3, 4, 0, 6, 7, 8, 9, 5),
    (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
    (3, 4, 0, 1, 2, 8, 9, 5, 6, 7),
    (4, 0, 1, 2, 3, 9, 5, 6, 7, 8),
    (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2),
    (7, 6, 5, 9, 8, 2, 1, 0, 4, 3),
    (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
    (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)
_VERHOEFF_P = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
    (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
    (8, 9, 1, 6, 0, 4, 3, 5, 2, 7),
    (9, 4, 5, 3, 1, 2, 6, 8, 7, 0),
    (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5),
    (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)


def normalize_email(value: str) -> str:
    """Lowercases the whole address — the same address must always produce
    the same hash regardless of how it was cased when typed (e.g.
    John.Doe@MAIL.COM and john.doe@mail.com are the same mailbox on every
    real mail system; only the local part is technically case-sensitive per
    RFC 5321, but no real provider enforces that, so treating the whole
    thing as case-insensitive matches actual behavior)."""
    return value.strip().lower()


def is_valid_spelled_out_email(value: str) -> bool:
    """The real structural gate for pii_patterns.EMAIL_SPELLED_OUT_RE, which
    on its own only requires a 4+-word chain joined by dot/at/hyphen/dash/
    underscore — loose enough to tolerate a spelled-out hyphen inside a
    domain label ("harborline hyphen test"), but too loose on its own to
    rule out an unrelated run-on sentence. Requiring >=1 "at" AND >=2 "dot"
    as whole words is the same signal the original rigid pattern encoded
    structurally (one dot-segment on each side of "at"); checking it here
    instead lets the regex's word/connector shape stay flexible."""
    return bool(re.search(r"\bat\b", value, re.IGNORECASE)) and len(re.findall(r"\bdot\b", value, re.IGNORECASE)) >= 2


def normalize_phone(value: str) -> str:
    """Strips everything but digits. Deliberately does NOT strip a leading
    '+' before this point — normalize_phone is used both for validation
    (needs the raw digit count including any country code) and as the
    hashing key (needs to be stable regardless of formatting), so a caller
    that wants a specific country's local-number form should strip the
    country/trunk prefix itself (see is_valid_phone for that logic) rather
    than this function silently guessing."""
    return re.sub(r"\D", "", value)


def _strip_indian_prefixes(digits: str) -> str:
    """Peels off a leading '0' trunk code and/or '91' country code, in
    whichever combination is present, down to the bare local number. Some
    sources write both at once ("091-9876543210" = trunk '0' + country '91'
    + 10-digit number, 13 digits total) — a single if/elif that only ever
    strips one or the other misses that combined form, so this loops rather
    than picking exactly one branch."""
    core = digits
    if core.startswith("0") and len(core) > 10:
        core = core[1:]
    if core.startswith("91") and len(core) == 12:
        core = core[2:]
    return core


def canonicalize_phone(value: str) -> str:
    """The hashing key for PHONE — distinct from normalize_phone() (which
    keeps the full digit string, country code included, for length-based
    validation) because the spec this was built against requires
    '+91 9876543210', '+919876543210', and '09876543210' to all hash
    identically: that only holds if the Indian trunk '0' / country code '91'
    is stripped down to the bare 10-digit core first. Non-Indian numbers
    (+1, +44, ...) fall back to the full normalized digit string — this
    codebase has no general per-country trunk-prefix table, so those are
    guaranteed self-consistent (same input format hashes the same) but not
    cross-format-equivalent (e.g. a UK number with vs. without '+44' isn't
    unified) the way the Indian case explicitly is."""
    digits = normalize_phone(value)
    core = _strip_indian_prefixes(digits)
    if len(core) == 10 and core[0] in _INDIAN_MOBILE_PREFIXES:
        return core
    return digits


_NANP_LOCAL_LENGTH = 7  # NANP local number: 3-digit exchange + 4-digit subscriber, no area code (e.g. "555-0199")


def is_valid_phone(value: str) -> bool:
    """True if `value` normalizes to a plausible phone number. Three
    acceptance paths: (1) a 10-digit Indian mobile number, after stripping
    any '0' trunk / '91' country prefix, starting with a valid mobile
    prefix (6-9) — checked first since it's the most specific/confident
    signal available; (2) a generic international number for every other
    supported format (+1 US, +44 UK, ...), where no prefix-table validation
    is practical without a full numbering-plan database; (3) a bare 7-digit
    NANP local number (no area code, e.g. "555-0199") — the shortest
    accepted length, and NOT on its own sufficient to redact: this function
    only decides "shaped like a phone number," and pii.py's PHONE recognizer
    additionally requires phone_confidence() != "low" before actually
    redacting (context word or internal formatting), same as every other
    length here — a bare, unformatted 7-digit run with nothing else
    (an employee ID, ticket number, or quantity that happens to be 7 digits)
    stays LOW and is left untouched. See pii_patterns.py's test suite for
    the calibration this mirrors at 10/12 digits.

    The generic (non-NANP-local) path caps at 12 bare digits *without* an
    explicit '+', not E.164's full 15 — a bare 13-16 digit run with no
    country-code marker is at least as likely to be a credit-card number
    (CREDIT_CARD_RE's own range), and PHONE is checked before CREDIT_CARD in
    pii.py's recognizer order specifically so a real phone number wins that
    ambiguity; without this cap PHONE would instead swallow genuine card
    numbers first. An explicit '+' is treated as an unambiguous "this is a
    country-coded phone number" marker, so it's allowed the full E.164
    range."""
    digits = normalize_phone(value)
    core = _strip_indian_prefixes(digits)
    if len(core) == 10 and core[0] in _INDIAN_MOBILE_PREFIXES:
        return True
    if value.strip().startswith("+"):
        return 10 <= len(digits) <= 15
    if len(digits) == _NANP_LOCAL_LENGTH:
        return True
    return 10 <= len(digits) <= 12


_PHONE_CONTEXT_RE = re.compile(r"\b(phone|mobile|tele\s*phone|contact(\s+number)?|call(\s+me)?|dial|cell)\b", re.IGNORECASE)
_PHONE_CONTEXT_WINDOW = 30  # chars scanned on each side of the match for context words


def phone_confidence(full_text: str, match_start: int, match_end: int, raw_value: str) -> str:
    """HIGH/MEDIUM/LOW confidence that a PHONE-shaped regex candidate is
    actually a phone number, not some other 10-ish-digit identifier (an
    order/ticket/employee id, a quantity, a timestamp, ...) — pii.py's
    PHONE recognizer only redacts on HIGH/MEDIUM, never LOW. Three signals,
    checked in order of how unambiguous they are:

    - HIGH: an explicit phone-context word (phone/mobile/contact/call/...)
      within a short window before or after the match, OR the value itself
      carries an international '+' country-code prefix — either one is
      close to unambiguous on its own.
    - MEDIUM: no context word and no country code, but the matched value
      has internal formatting (spaces/hyphens/parens) — "98765-43210" reads
      as a phone number by its shape alone, even with zero surrounding
      context, whereas a truly bare digit run doesn't.
    - LOW: a bare, unformatted digit run with neither context nor
      formatting nor a country code — structurally indistinguishable from
      an order number, employee id, or any other 10-digit identifier, so
      redacting it unconditionally is exactly the false-positive pattern
      this function exists to avoid ("1234567890" must not auto-redact)."""
    if raw_value.strip().startswith("+"):
        return "high"
    window_before = full_text[max(0, match_start - _PHONE_CONTEXT_WINDOW) : match_start]
    window_after = full_text[match_end : match_end + _PHONE_CONTEXT_WINDOW]
    if _PHONE_CONTEXT_RE.search(window_before) or _PHONE_CONTEXT_RE.search(window_after):
        return "high"
    # A digit run glued directly onto a preceding '-' with no space (e.g.
    # "GEN-INC-ENG-2026-009") is almost always the numeric tail of a larger
    # hyphen-joined reference/document/case ID, not a standalone phone
    # number — a real phone number is never written hyphen-glued onto a
    # letter prefix like that. Live-verified false positive: exactly this
    # shape (a year-then-sequence document ID suffix, e.g. "2026-009") was
    # redacted as PHONE with zero phone context nearby, purely because its
    # own internal hyphen satisfied the formatting check below. Internal
    # hyphen/space formatting alone isn't a reliable medium-confidence
    # signal in that position, so it's excluded here; a genuine nearby
    # context word can still promote it to HIGH via the check above.
    glued_onto_hyphenated_prefix = match_start > 0 and full_text[match_start - 1] == "-"
    if not glued_onto_hyphenated_prefix and re.search(r"[\s\-()]", raw_value):
        return "medium"
    return "low"


def normalize_pan(value: str) -> str:
    return value.strip().upper()


def is_valid_pan(value: str) -> bool:
    """Structural validation only (5 letters + 4 digits + 1 letter) — PAN
    has no public checksum digit the way Aadhaar does, so "valid" here means
    "matches the issued structure", which is what actually distinguishes a
    PAN from an arbitrary 10-character alphanumeric string (the false
    positive the spec calls out)."""
    normalized = normalize_pan(value)
    return bool(re.fullmatch(r"[A-Z]{5}[0-9]{4}[A-Z]", normalized))


def normalize_aadhaar(value: str) -> str:
    return re.sub(r"\D", "", value)


def is_aadhaar_shaped(value: str) -> bool:
    """Structural gate only: exactly 12 digits. This — not
    is_valid_aadhaar() below — is what pii.py uses to decide whether to
    redact. Deliberately does NOT also require "doesn't start with 0/1"
    (the real UIDAI issuance rule, checked in is_valid_aadhaar() instead):
    redaction is a privacy control, where a false negative (a real Aadhaar
    number leaks because it happened to fail a stricter check — plausible
    from a single typo in the source text, or because it's a documentation/
    placeholder example rather than a real issued number) is a materially
    worse outcome than a false positive (an innocuous 12-digit number gets
    redacted). Full validation is exposed separately for callers that need
    actual validity, not just redaction."""
    digits = normalize_aadhaar(value)
    return len(digits) == 12


def is_valid_aadhaar(value: str) -> bool:
    """Full validation: 12 digits, doesn't start with 0/1 (UIDAI never
    issues those), and a correct Verhoeff check digit (UIDAI's checksum
    algorithm for the 12th digit, computed over the preceding 11). Use this
    when actual validity matters (e.g. an audit confidence flag); use
    is_aadhaar_shaped() for the redaction decision itself — see that
    function's docstring for why they deliberately differ."""
    digits = normalize_aadhaar(value)
    if len(digits) != 12 or digits[0] in "01":
        return False
    checksum = 0
    for i, ch in enumerate(reversed(digits)):
        checksum = _VERHOEFF_D[checksum][_VERHOEFF_P[i % 8][int(ch)]]
    return checksum == 0


def is_valid_ipv4(value: str) -> bool:
    """Structural validation beyond "4 dot-separated numbers": each octet
    must be 0-255 with no leading zero on a multi-digit octet (RFC 1123 —
    "192.168.001.1" is not a canonical IPv4 form and real stacks reject it,
    so treating it as one risks false-positiving on version strings like
    "1.002.003.4"). This, not the regex alone, is what keeps
    pii_patterns.IP_ADDRESS_RE from matching arbitrary dotted-number
    sequences that merely have the right shape."""
    parts = value.split(".")
    if len(parts) != 4:
        return False
    for part in parts:
        if not part.isdigit() or not (1 <= len(part) <= 3):
            return False
        if len(part) > 1 and part[0] == "0":
            return False
        if int(part) > 255:
            return False
    return True


_DOB_CONTEXT_RE = re.compile(
    r"\b(date of birth|dob|born on|birth\s*date|birthday)\b", re.IGNORECASE
)
_DOB_CONTEXT_WINDOW = 30


def has_dob_context(full_text: str, match_start: int) -> bool:
    """DATE_OF_BIRTH, unlike every other recognizer here, requires context
    as a hard gate, not a confidence tier — a bare date ("03/14/1990",
    "1990-03-14") is indistinguishable from a deadline, a report date, an
    appointment, or any of dozens of other dates that appear constantly in
    ordinary business text, and there is no shape-based signal that
    separates "this date is someone's birthday" from "this date is
    anything else." Only an explicit nearby marker (dob/date of birth/born
    on/birthday) makes that distinction possible at all; without one, the
    date pattern is deliberately never redacted, matching pii_patterns.py's
    stated goal of avoiding extremely broad regexes that create false
    positives on ordinary content."""
    window = full_text[max(0, match_start - _DOB_CONTEXT_WINDOW) : match_start]
    return bool(_DOB_CONTEXT_RE.search(window))


def normalize_card(value: str) -> str:
    """Digits only — cards are written with spaces or hyphens in groups of
    four at least as often as they are written bare."""
    return re.sub(r"\D", "", value)


def is_valid_card(value: str) -> bool:
    """Luhn check digit plus a length gate.

    CREDIT_CARD_RE matches any 13-16 digit run, which is a very common shape:
    order references, batch numbers, and concatenated internal IDs all hit it.
    Without a checksum every one of those is redacted as a payment card, which
    both damages legitimate answers and trains users to distrust the
    redaction.

    Every real card number satisfies Luhn by construction, so this costs no
    true-positive coverage. The tradeoff it does make is a mistyped card
    number goes unredacted — acceptable, because a number that fails Luhn is
    not a usable card and therefore not the value being protected.

    Mirrors is_valid_aadhaar()'s Verhoeff approach: structural shape decides
    candidacy, an arithmetic check digit decides validity.
    """
    digits = normalize_card(value)
    if not 13 <= len(digits) <= 16 or not digits.isdigit():
        return False
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0
