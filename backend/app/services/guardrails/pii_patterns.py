"""Centralized, compiled, boundary-aware PII regex patterns.

These are deliberately "candidate" patterns for the multi-word/high-format-
variance types (PHONE) — they narrow down plausible spans structurally, but
the real accept/reject decision for those types happens in pii_validators.py
via normalization + length/checksum validation, not the regex alone. For the
tightly-structured types (EMAIL, PAN, SSN) the regex itself is the complete
structural definition.
"""

import re

from app.services.guardrails.secrets import CREDENTIAL_PATTERNS as _CREDENTIAL_PATTERNS

# EMAIL — RFC 5322 is far looser than real-world addresses need; this covers
# local-part (letters/digits/._+-, no leading/trailing/doubled separator
# requirement enforced — kept simple since over-validating the local part
# risks false negatives on real addresses), an '@', one or more dot-separated
# domain labels, and a final TLD of 2+ letters. The repeated-label group is
# what makes multi-level TLDs (.co.in, .co.uk) and subdomains
# (subdomain.company.com) fall out naturally: "company.co.in" is label
# "company", one repetition of ".co", then the required final ".in".
# Lookaround boundaries (not \b) because \b doesn't fire between two
# non-word characters (e.g. a '(' immediately before the address) — see
# pii.py's PHONE pattern comment for the same issue in a different pattern.
EMAIL_RE = re.compile(
    r"(?<![\w.+-])"
    r"[A-Za-z0-9](?:[A-Za-z0-9._+-]*[A-Za-z0-9])?"
    r"@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)*"
    r"\.[A-Za-z]{2,}"
    # Not a bare `(?![\w.+-])`: that also rejects a sentence-ending period
    # right after the address ("...@acme.com.") the same way it rejects a
    # genuinely continuing domain, since both are "followed by a dot" —
    # found live via the reply-shaped eval dataset (a name-then-email
    # sentence ending in a period is an extremely common real shape this
    # app's earlier email test cases never happened to include). A trailing
    # dot is only disqualifying if ANOTHER alphanumeric follows it — i.e.
    # the domain actually continues — not when it's simply followed by
    # nothing (end of string) or other punctuation/whitespace.
    r"(?![\w+-]|\.[A-Za-z0-9])",
    re.IGNORECASE,
)

# PHONE — a *candidate* matcher only (see module docstring): an optional
# leading country code, an optional parenthesized group, then one or more
# digit runs separated by spaces/hyphens. Deliberately permissive on shape
# (it will match "2026" or "12345" as candidates) because the actual
# accept/reject gate is pii_validators.is_valid_phone()'s digit-count and
# prefix validation after normalization — trying to make the regex alone
# precise enough to both accept every format in the spec and reject every
# short number would require a regex per country; two-stage is deliberate.
PHONE_CANDIDATE_RE = re.compile(
    r"(?<!\w)"
    r"(?:\+\d{1,3}[\s-]?)?"
    r"(?:\(\d{2,5}\)[\s-]?)?"
    r"\d{2,15}(?:[\s-]\d{2,15}){0,4}"
    r"(?!\w)"
)

# PAN (India) — 5 letters, 4 digits, 1 letter, e.g. ABCDE1234F. This is a
# structural check only (case-insensitive, normalized to uppercase before
# storage) — real PAN issuance encodes holder-type/surname info in specific
# character positions, but that's not a checksum and isn't validated here;
# the structural shape is what distinguishes a PAN from an arbitrary
# 10-character alphanumeric string, which is the false-positive this guards
# against.
PAN_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{5}[0-9]{4}[A-Za-z](?![A-Za-z0-9])")

# AADHAAR (India) — 12 digits, either space-grouped in 4s ("1234 5678 9012")
# or bare ("123456789012"). The left boundary excludes both a preceding
# digit AND a preceding '+' — the latter specifically because an Indian
# phone number written with its country code and no separator
# ("+919876543210") is *also* exactly 12 digits once the '+' is stripped,
# and a '+' immediately before is a strong, unambiguous "this is a
# country-coded phone number, not an Aadhaar number" signal (Aadhaar numbers
# are never written with a leading '+'). Beyond that, this only narrows to
# "12-digit-shaped" — full validation (leading-digit rule, Verhoeff
# checksum) lives in pii_validators.py; see is_aadhaar_shaped()'s docstring
# for why redaction deliberately does NOT gate on that stricter check.
# The spaced alternative gets boundary checks on BOTH sides (not just a
# shared trailing one) to reject a 16-digit card number formatted in groups
# of 4 ("4111 1111 1111 1111"): any 3 consecutive groups out of its 4 are
# themselves a perfectly-shaped spaced Aadhaar — the first 3 ("4111 1111
# 1111") are ruled out by the right-side check (a 4th " 1111" group
# follows), but the LAST 3 ("1111 1111 1111", starting after "4111 ") have
# nothing objectionable *following* them (just " expires..."), so a
# right-only check misses that case. `(?<!\d{4} )` mirrors the right-side
# `(?![ ]?\d)` on the left: reject if a "NNNN " group immediately precedes
# the match too. (Python's lookbehind requires fixed width, which `\d{4} `
# — always exactly 5 characters — satisfies.)
AADHAAR_RE = re.compile(
    r"(?<![\d+])(?:(?<!\d{4} )\d{4}[ ]\d{4}[ ]\d{4}(?![ ]?\d)|\d{12}(?!\d))"
)

# SSN / CREDIT_CARD — kept here so every PII pattern lives in one module
# instead of being split across pii.py and this file.
#
# SSN_RE accepts a space OR a hyphen between groups (not mixed — [- ] means
# "one of these", each group's own separator can differ from the other
# group's, matching how CREDIT_CARD_RE's own [ -]? already treats the two
# separators as interchangeable) — "123-45-6789" and "123 45 6789" both
# match, found missing live: a space-separated SSN passed through pii_redact
# completely undetected (tests/security/pii's PII-SSN-03 case).
SSN_RE = re.compile(r"\b\d{3}[- ]\d{2}[- ]\d{4}\b")
CREDIT_CARD_RE = re.compile(r"\b(?:\d[ -]?){13,16}\b")

# PHONE (obfuscated, single-digit-per-token spacing) — a SEPARATE candidate
# pattern from PHONE_CANDIDATE_RE above, not a loosening of it: that pattern
# requires each digit RUN to be 2-15 digits, deliberately, and is already
# heavily exercised (test_pii_patterns.py, test_pii_validators.py,
# test_search_pii_redaction.py) — lowering its per-run minimum to 1 would
# balloon its candidate-match volume across ordinary text (page numbers,
# short digit lists, list markers, ...) system-wide just to cover one narrow
# shape. This pattern instead only fires on that shape specifically: 7+
# consecutive SINGLE digits, each separated by exactly one space/hyphen —
# essentially impossible to produce by accident in ordinary prose. Live-
# verified redaction bypass: asking the model to "put a space between every
# digit" of a real phone number reproduced it completely unredacted, because
# neither this shape nor the spelled-out one below matched anything.
# Downstream validation (pii_validators.is_valid_phone/phone_confidence,
# wired in pii.py) is identical to the ordinary PHONE recognizer's — only
# how the candidate span gets found differs.
# The optional parenthesized-area-code prefix is matched as its own group,
# separate from the main run below — not folded into one continuous
# digit-with-separators pattern — because ')' itself isn't a valid
# inter-digit separator, so "( 2 0 6 )   5 5 5 - 0 1 3 8" would otherwise
# only match its second half, leaving the area code exposed (found exactly
# this way live, testing this pattern against the real bypass phrase).
PHONE_CHAR_SPACED_RE = re.compile(
    r"(?<!\w)(?:\(\s*\d(?:\s+\d){1,4}\s*\)[\s-]*)?\d(?:[\s-]+\d){6,14}(?!\w)"
)

# PHONE (obfuscated, spelled out as English digit words) — same rationale as
# PHONE_CHAR_SPACED_RE above, same live-verified bypass ("spell out the
# phone number digit by digit" / "write each digit as a word" both
# reproduced the real number in plain English with zero redaction).
# normalize_phone()'s \D-strip can't help on its own here — there are no
# digit characters in the matched text at all; see pii_validators.py's
# digit_words_to_digits() for the word->digit conversion this pattern's
# match gets run through before the same downstream validation applies.
# The separator class includes '-' alongside whitespace/comma: live-tested
# again after the initial fix, the model separated the words with hyphens
# ("two - zero - six - ...") rather than spaces/commas, and a hyphen-free
# separator class let that specific run through completely unredacted.
_DIGIT_WORD = r"(?:zero|oh|one|two|three|four|five|six|seven|eight|nine)"
PHONE_SPELLED_OUT_RE = re.compile(rf"(?<!\w){_DIGIT_WORD}(?:[\s,-]+{_DIGIT_WORD}){{6,14}}(?!\w)", re.IGNORECASE)

# EMAIL (obfuscated, "dot"/"at"/"hyphen"/"dash"/"underscore" spelled out in
# place of "."/"@"/"-"/"-"/"_") — live-verified bypass: asking for an email
# address "with spaces around the @ sign, like: name at domain dot com"
# reproduced the real address with zero redaction, since EMAIL_RE requires a
# literal '@'. Two further real-world variants of the same bypass were
# found live in the same session: (1) a model spelling out a domain that
# itself contains a hyphen wrote it as a separate word ("harborline hyphen
# test" instead of "harborline-test"), and (2) asking it to spell the
# address out "letter by letter" produced single characters joined by a
# literal '-' used as plain punctuation ("d - i - e - g - o - dot - m ..."),
# not the word "hyphen" — a bare '-' token is now an accepted connector too,
# for exactly that shape. Both broke the original rigid "word dot word... at
# word dot word" structure (each segment a single contiguous token). This is
# deliberately loose about the *shape* of the word/connector chain — any run
# of 4+ words alternately joined by dot/at/hyphen/dash/underscore/'-'
# matches the regex itself — and instead relies on
# pii_validators.is_valid_spelled_out_email() as the real structural gate
# (>=1 "at" AND >=2 "dot", checked as whole words on the matched span) to
# keep an ordinary sentence merely containing "at" or "dot" in isolation
# ("meet me at noon", "version 2 dot 0"), or a plain hyphenated/dashed list
# ("well - known - fact - pattern"), from matching; the combined "at" +
# 2x"dot" shape essentially never occurs by accident. A literal '-'/'_'
# character embedded directly in a token (e.g. "harborline-test" typed as
# one word, no spelled-out "hyphen") is still handled too, since the word
# class itself allows '-'/'_'.
_EMAIL_SPELLED_OUT_WORD = r"[A-Za-z0-9_-]+"
_EMAIL_SPELLED_OUT_CONNECTOR = r"(?:dot|at|hyphen|dash|underscore|-)"
EMAIL_SPELLED_OUT_RE = re.compile(
    rf"(?<!\w){_EMAIL_SPELLED_OUT_WORD}"
    rf"(?:\s+{_EMAIL_SPELLED_OUT_CONNECTOR}\s+{_EMAIL_SPELLED_OUT_WORD}){{3,}}(?!\w)",
    re.IGNORECASE,
)

# BANK ACCOUNT — deliberately NOT a bare 8-18 digit regex (the spec this was
# built against explicitly calls that out as a false-positive trap: an
# unlabeled 8-18 digit run is indistinguishable from an order id, a phone
# number, a reference code, ...). Only matches when an explicit account-type
# label appears in the same short span immediately before the digits, e.g.
# "account number: 123456789012" or "IBAN GB29NWBK60161331926819".
BANK_ACCOUNT_RE = re.compile(
    r"(?:account\s*(?:no\.?|number)|a/c\s*no\.?|iban)\s*[:#]?\s*"
    r"([A-Z]{0,2}\d{8,18})",
    re.IGNORECASE,
)


# IP ADDRESS (IPv4) — shape only; pii_validators.is_valid_ipv4() enforces
# 0-255 per octet and rejects a leading zero on a multi-digit octet, which
# is what actually stops this from matching an arbitrary dotted-number
# string (a version number, a decimal-ish id) that merely has 4 groups of
# digits separated by dots. IPv6 is a documented gap, not silently
# unsupported — see the evaluation report's remaining-risks section.
IP_ADDRESS_RE = re.compile(r"(?<![\w.])\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?![\w.])")

# DATE OF BIRTH — a *candidate* date shape only (day 1-31, month 1-12,
# either numeric-numeric-numeric or "Month DD, YYYY"); pii_validators.
# has_dob_context() is the actual gate, not this regex — see that
# function's docstring for why a bare date is never enough on its own.
_MONTH_NAMES = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|"
    r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)
DATE_OF_BIRTH_RE = re.compile(
    rf"(?<!\w)(?:"
    rf"(?:0?[1-9]|1[0-2])[/-](?:0?[1-9]|[12]\d|3[01])[/-](?:19|20)\d{{2}}"  # MM/DD/YYYY or MM-DD-YYYY
    rf"|(?:19|20)\d{{2}}-(?:0?[1-9]|1[0-2])-(?:0?[1-9]|[12]\d|3[01])"  # YYYY-MM-DD
    rf"|(?:{_MONTH_NAMES})\.?\s+\d{{1,2}},?\s+(?:19|20)\d{{2}}"  # Month DD, YYYY
    rf")(?!\w)",
    re.IGNORECASE,
)


# JWT — the SAME compiled pattern secrets.py's CREDENTIAL_PATTERNS uses for
# its own JWT entry (header.payload.signature, base64url segments), imported
# rather than duplicated so the two can never drift apart. secrets.py's
# check_secrets() always BLOCKs a JWT outright (it's a credential, no
# legitimate reason to appear in a chat message); this recognizer instead
# lets pii.py's own PII Policy Center additionally offer JWT as a normal
# per-entity MASK/REDACT/BLOCK/ALLOW-tunable PII type (see guardrail_policy/
# entities.py's JWT row) — genuinely different call sites sharing one shape.
JWT_RE = next(pattern for label, pattern in _CREDENTIAL_PATTERNS if label == "JWT")


def compile_employee_id_pattern(pattern: str) -> re.Pattern:
    """Compiles a configuration-supplied employee-ID pattern. Only ever
    called when settings.guardrail_employee_id_pattern is set — this
    recognizer has no built-in default shape (EMP-12345 vs EMP12345 vs a
    completely different org-specific scheme all differ per deployment),
    per the explicit requirement that it stay config-driven rather than a
    hard-coded guess at one company's convention."""
    return re.compile(pattern, re.IGNORECASE)
