"""ReDoS defense for admin-authored regex patterns (Guardrail Policy Center
§6/§26) — no precedent for this exists anywhere else in this codebase
(every hard-coded pattern in injection.py/destructive.py/pii_patterns.py was
written and reviewed by a developer, never taken from request input). An
admin-supplied pattern is a new trust boundary: it must be rejected at
CREATE/UPDATE time if it can't be proven cheap to run, and guarded again at
actual match time as defense in depth.

Two layers, in order:

1. A STATIC heuristic (_has_nested_quantifier) that rejects the classic
   `(x+)+`/`(x*)*`/`(x+)*`/`(x*)+` catastrophic-backtracking shape without
   ever executing the pattern at all. This is the PRIMARY gate — see
   run_with_timeout()'s own docstring for why execution-time detection
   alone cannot be trusted in CPython on this platform.
2. Actual execution against a small battery of adversarial-shaped sample
   strings, measuring real elapsed wall-clock time. Catches shapes the
   static heuristic misses (e.g. alternation-based blowups like
   `(a|ab)*c`), but — important limitation, not silently glossed over — a
   pattern that slips past layer 1 and IS genuinely catastrophic will still
   take roughly as long to REJECT as the malicious computation itself
   takes to run, not a clean sub-second bound. See run_with_timeout().

Neither layer is a full static ReDoS proof (that's undecidable in general);
together they clear a practical bar every one of this app's own existing
hard-coded patterns passes easily.
"""

import queue
import re
import threading
import time

from app.core.errors import AppError

MAX_PATTERN_LENGTH = 200
_MATCH_TIMEOUT_SECONDS = 0.5

# Long repeated-character runs (with and without a non-matching suffix) are
# the classic trigger for catastrophic backtracking in a pattern with nested
# or ambiguous quantifiers — a pattern that can't search these quickly
# against ordinary text can't be trusted against real chat messages either,
# which routinely contain long runs (IDs, whitespace, repeated punctuation).
#
# Length 26 specifically, not longer: run_with_timeout() below can only
# ABANDON a thread that blows the deadline (Python has no safe way to kill
# one — see that function's docstring on why CPython's GIL makes even
# "abandon and move on" imprecise for a pattern this layer is meant to
# catch), so an adversarial sample must be long enough to reliably exceed
# _MATCH_TIMEOUT_SECONDS against a truly pathological pattern (measured:
# `(a+)+$` against 26 a's + a non-matching suffix takes ~5.5s wall-clock)
# but SHORT enough that an abandoned worker still finishes within a few
# seconds rather than pegging a CPU core indefinitely — at length 300 the
# same pattern's backtracking is effectively unbounded (2^300 steps), which
# was measured live to leave a runaway thread spinning for minutes.
_ADVERSARIAL_SAMPLES = (
    "a" * 26,
    ("a" * 26) + "!",
    ("a " * 20).strip(),
    "1" * 26,
)


def _has_nested_quantifier(pattern: str) -> bool:
    """True if `pattern` contains a parenthesized group whose own contents
    include a quantifier (+, *, {n,}), immediately followed by another
    quantifier outside the group — e.g. `(a+)+`, `(\\d*)*`, `([a-z]+)*`. This
    is THE single most common real-world ReDoS shape and, unlike execution-
    time measurement, is detected without ever running the pattern — no
    thread/GIL/timeout reliability concerns at all. Deliberately a simple
    balanced-paren scan (handles escaped characters, not full regex-AST
    parsing) — good enough to catch the common shape, not a general
    static ReDoS prover."""
    group_starts: list[int] = []
    i, n = 0, len(pattern)
    while i < n:
        ch = pattern[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "(":
            group_starts.append(i + 1)
        elif ch == ")":
            if group_starts:
                start = group_starts.pop()
                inner = pattern[start:i]
                inner_has_quantifier = re.search(r"(?<!\\)[+*]|(?<!\\)\{\d*,?\d*\}", inner) is not None
                next_char = pattern[i + 1] if i + 1 < n else ""
                if inner_has_quantifier and next_char in "+*{":
                    return True
        i += 1
    return False


def safe_compile(pattern: str) -> re.Pattern:
    """Syntax + length + static-shape validation. Does not itself prove the
    pattern is cheap to run against arbitrary input (the static heuristic
    only catches ONE common shape) — call test_pattern_safety() for the
    full gate before persisting or executing a pattern against real input."""
    if not pattern:
        raise AppError(422, "invalid_regex", "Pattern must not be empty")
    if len(pattern) > MAX_PATTERN_LENGTH:
        raise AppError(422, "invalid_regex", f"Pattern exceeds the maximum length of {MAX_PATTERN_LENGTH} characters")
    if _has_nested_quantifier(pattern):
        raise AppError(
            422, "unsafe_regex_pattern",
            "Pattern contains a nested/ambiguous quantifier shape that can cause catastrophic backtracking",
        )
    try:
        return re.compile(pattern)
    except re.error as exc:
        raise AppError(422, "invalid_regex", f"Pattern does not compile: {exc}")


def run_with_timeout(compiled: re.Pattern, text: str) -> re.Match | None:
    """Runs compiled.search(text) against a deadline, on a best-effort basis.

    IMPORTANT LIMITATION: this is NOT a reliable preemptive timeout.
    CPython's `_sre` matching loop, once entered, does not return control to
    the bytecode interpreter (and therefore does not release the GIL) during
    a long backtracking run — measured live: a genuinely catastrophic
    pattern monopolizes the GIL for its ENTIRE run, so the watchdog thread
    below doesn't actually wake up at _MATCH_TIMEOUT_SECONDS, it wakes up
    only once the worker finishes (however long that actually took). This
    function therefore checks REAL ELAPSED TIME after the fact and raises if
    it exceeds the deadline, rather than trusting thread.is_alive() alone
    (which would incorrectly read False — "it finished in time" — for a
    worker that in fact ran far past the deadline before finally returning).
    A signal-based (SIGALRM) or subprocess-based hard-kill would be truly
    preemptive, but SIGALRM doesn't exist on Windows and a subprocess per
    match is too expensive for the hot per-message runtime path this
    function is also used on — see module docstring: the static heuristic
    in safe_compile() is the layer this app actually depends on for
    genuinely catastrophic patterns; this function is defense in depth for
    milder cases and a correctness backstop, not a hard latency bound.

    A timeout is treated as a match-attempt FAILURE (raises), not as "no
    match" — same fail-closed convention this app's PII/injection detectors
    already use for a classifier failure: an unknown result must never be
    silently treated as safe.

    Deliberately a bare daemon `threading.Thread`, NOT
    concurrent.futures.ThreadPoolExecutor: ThreadPoolExecutor registers a
    NON-daemon atexit hook that JOINS every worker thread before the process
    is allowed to exit — meaning one truly-pathological pattern would hang
    the entire application's shutdown, not just this one call. A daemon
    thread is abandoned cleanly instead (Python does not wait for daemon
    threads on exit)."""
    result: queue.Queue = queue.Queue(maxsize=1)
    started_at = time.monotonic()

    def _worker() -> None:
        try:
            result.put(("ok", compiled.search(text)))
        except Exception as exc:  # pragma: no cover - re.Pattern.search on a str practically never raises
            result.put(("error", exc))

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join(timeout=_MATCH_TIMEOUT_SECONDS)
    elapsed = time.monotonic() - started_at

    if thread.is_alive() or elapsed > _MATCH_TIMEOUT_SECONDS:
        raise AppError(422, "regex_execution_timeout", "Pattern took too long to execute and was rejected")

    status, payload = result.get_nowait()
    if status == "error":
        raise AppError(422, "invalid_regex", f"Pattern failed during execution: {payload}")
    return payload


def test_pattern_safety(pattern: str) -> re.Pattern:
    """Compiles the pattern (including the static nested-quantifier check —
    see safe_compile()) and exercises it against a battery of adversarial-
    shaped sample strings under a timeout — the full ReDoS gate. Call this
    from every write path that persists a regex (policy create/update, the
    /test playground) before the pattern is ever saved or matched against
    real input."""
    compiled = safe_compile(pattern)
    for sample in _ADVERSARIAL_SAMPLES:
        run_with_timeout(compiled, sample)
    return compiled
