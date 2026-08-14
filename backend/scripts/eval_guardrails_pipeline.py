"""Runs the SAME 600-case dataset from eval_guardrails.py through the real,
wired-up pipeline (services/guardrails/pipeline.py::run_input_guardrails()),
not individual check functions in isolation — so short-circuiting, step
ordering, and the pii-block-on-redact special case all behave exactly as
they do in production.

Per-guardrail attribution in a short-circuiting pipeline: for each case,
every check that actually executed left a GuardrailStep in result.steps
(with its own action), independent of which one ultimately caused
result.blocked. This script reads each guardrail's OWN step out of the
trace wherever it ran, rather than only crediting whichever check happened
to be last — length_check and prompt_injection_check run early enough
(1st, 2nd in pipeline.py's order) that they see nearly every case
regardless of category; destructive/scope/pii see progressively fewer as
earlier checks short-circuit more cases. Each guardrail's reported N
reflects exactly how many cases it actually got a chance to run on.

semantic_risk_check is disabled for this sweep (it makes a real BGE-M3
embedding call per case) — matching this repo's own established test-suite
convention (tests/guardrails/conftest.py disables it by default) rather than
making 600 live model calls in what's meant to be a fast, reproducible
regression suite. Its existing dedicated unit tests (test_semantic_check.py)
already exercise it directly with fakes/mocks.

presidio_check (formerly llm_advanced_check, an LLM-judge — see
services/guardrails/presidio_check.py's module docstring for why it was
replaced) is NOT disabled here: unlike the LLM-judge it replaced, it's local
and deterministic (spaCy NER via the already-installed en_core_web_sm model,
no network call, no per-call API cost), so this sweep gets real numbers for
it — the first spaCy model load adds a one-time few-second delay, not a
per-case cost. Its dedicated unit tests (test_presidio_check.py) still cover
it directly with fakes for the model-loading boundary specifically.

Usage (from backend/): python -m scripts.eval_guardrails_pipeline
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: E402
from app.services.guardrails import pipeline  # noqa: E402
from scripts.eval_guardrails import (  # noqa: E402
    _benign_requests, _benign_security_requests, _destructive_requests, _injection_attempts,
    _malformed_pii_edge_cases, _pii_requests,
)

_CHECK_ORDER = (
    "length_check", "prompt_injection_check", "destructive_intent_check",
    "semantic_risk_check", "scope_check", "presidio_check", "pii_redact",
)


def _run_and_extract(text: str) -> dict[str, str]:
    """Runs the real input pipeline once; returns {check_name: action} for
    every check that actually executed (checks short-circuited by an
    earlier block are simply absent from the dict)."""
    result = pipeline.run_input_guardrails(text)
    return {step.name: step.action for step in result.steps}


def _confusion(records: list[tuple[str, bool]]) -> tuple[int, int, int, int]:
    """records: list of (action, expected_positive). Returns (tp, fp, tn, fn).
    A check 'firing' means action in ('block', 'redact') — redact counts as
    a positive signal for pii_redact, matching eval_guardrails.py's
    convention (the input pipeline treats a pii redact as a block via the
    separate guardrail_pii_block_input flag, but the check's OWN verdict —
    what we're measuring here — is 'redact', not 'block')."""
    tp = fp = tn = fn = 0
    for action, expected in records:
        fired = action in ("block", "redact")
        if fired and expected:
            tp += 1
        elif fired and not expected:
            fp += 1
        elif not fired and expected:
            fn += 1
        else:
            tn += 1
    return tp, fp, tn, fn


def _metrics(tp: int, fp: int, tn: int, fn: int) -> dict:
    precision = tp / (tp + fp) if (tp + fp) else float("nan")
    recall = tp / (tp + fn) if (tp + fn) else float("nan")
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) and precision == precision and recall == recall and (precision + recall) > 0 else float("nan")
    return {"tp": tp, "fp": fp, "tn": tn, "fn": fn, "precision": precision, "recall": recall, "f1": f1}


def _fmt(x: float) -> str:
    return "n/a" if x != x else f"{x*100:.1f}%"


def main() -> None:
    settings.guardrail_redact_pii = True
    settings.guardrail_pii_mode = "placeholder"
    settings.guardrail_pii_block_input = True
    settings.guardrail_block_prompt_injection = True
    settings.guardrail_block_destructive_intent = True
    settings.guardrails_enabled = True

    # Disable the one remaining model-backed rail for this sweep — see
    # module docstring. presidio_check is deliberately left enabled (it's
    # local/deterministic, not model-API-backed the way llm_advanced_check
    # was).
    import app.services.guardrails.semantic_check as semantic_check_mod
    semantic_check_mod.load_yaml_config = lambda name: {"semantic_check": {"enabled": False}}

    dataset = {
        "benign": [(t, {}) for t in _benign_requests(100)],
        "pii": [(t, {"pii_redact": True}) for t, _label in _pii_requests(100)],
        "injection": [(t, {"prompt_injection_check": True}) for t in _injection_attempts(100)],
        "destructive": [(t, {"destructive_intent_check": True}) for t in _destructive_requests(100)],
        "benign_security": [(t, {}) for t in _benign_security_requests(100)],
        "malformed_pii": [(t, {}) for t in _malformed_pii_edge_cases(100)],
    }
    total_cases = sum(len(v) for v in dataset.values())
    print(f"Running {total_cases} cases through the real input pipeline (run_input_guardrails)...\n")

    # {check_name: [(action_or_None, expected_bool), ...]}
    per_check: dict[str, list[tuple[str | None, bool]]] = {c: [] for c in _CHECK_ORDER}
    blocked_by_count: dict[str, int] = {}
    total_blocked = 0

    for category, cases in dataset.items():
        for text, expectations in cases:
            steps = _run_and_extract(text)
            for check_name in _CHECK_ORDER:
                if check_name in steps:
                    per_check[check_name].append((steps[check_name], expectations.get(check_name, False)))
            blocked = any(a == "block" for a in steps.values()) or (
                steps.get("pii_redact") == "redact" and settings.guardrail_pii_block_input
            )
            if blocked:
                total_blocked += 1
                blocking_check = next((n for n in reversed(_CHECK_ORDER) if steps.get(n) == "block"), None)
                if blocking_check is None and steps.get("pii_redact") == "redact":
                    blocking_check = "pii_redact"
                blocked_by_count[blocking_check] = blocked_by_count.get(blocking_check, 0) + 1

    print(f"{total_blocked}/{total_cases} cases blocked overall. Blocked-by breakdown: {blocked_by_count}\n")

    header = f"{'Guardrail':26}{'N':>6}{'Precision':>11}{'Recall':>10}{'F1':>8}{'FP':>6}{'FN':>6}"
    print(header)
    print("-" * len(header))
    for check_name in _CHECK_ORDER:
        records = per_check[check_name]
        n = len(records)
        actions_expected = [(a, exp) for a, exp in records]
        tp, fp, tn, fn = _confusion(actions_expected)
        m = _metrics(tp, fp, tn, fn)
        print(f"{check_name:26}{n:>6}{_fmt(m['precision']):>11}{_fmt(m['recall']):>10}{_fmt(m['f1']):>8}{m['fp']:>6}{m['fn']:>6}")

    print()
    print("semantic_risk_check      : disabled for this sweep (real BGE-M3 call) — see test_semantic_check.py")
    print("presidio_check           : enabled for real (local/deterministic, no API cost) — see test_presidio_check.py.")
    print("                            NOTE: only the 'pii' category has ground truth for pii_redact, not for this")
    print("                            check specifically — its precision/recall above reflects real Presidio")
    print("                            behavior across ALL categories, not a curated PII-only dataset.")
    print("system_prompt_leak_check : output-side (operates on replies, not queries) — not part of this input-side")
    print("                            600-case suite; see test_system_prompt_leak.py")
    print("output_citation_check    : output-side, same as above — see test_output_validation.py")


if __name__ == "__main__":
    main()
