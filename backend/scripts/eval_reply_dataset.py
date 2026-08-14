"""Evaluates tests/data/reply_guardrail_cases.jsonl against the REAL
output-side guardrails: services/guardrails/pipeline.py::run_output_guardrails()
(system-prompt-leak + output PII redaction) and
services/guardrails/citation_rail.py::check_citations(). Three of the
dataset's nine categories (OUTPUT_PII, SYSTEM_PROMPT_LEAK, CITATION) map
onto these; SAFE_RESPONSE is the negative/control set for all three. The
remaining five categories (GROUNDEDNESS, UNSUPPORTED_CLAIM, CONFIDENTIAL_DATA,
DOCUMENT_INJECTION, HALLUCINATION) have no corresponding guardrail in this
codebase — reported as "not evaluated", not fabricated.

Usage (from backend/): python -m scripts.eval_reply_dataset
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: E402
from app.services.guardrails.citation_rail import check_citations  # noqa: E402
from app.services.guardrails.pipeline import run_output_guardrails  # noqa: E402

_DATA_PATH = Path(__file__).resolve().parent.parent / "tests" / "data" / "reply_guardrail_cases.jsonl"

_NOT_EVALUATED = {"GROUNDEDNESS", "UNSUPPORTED_CLAIM", "CONFIDENTIAL_DATA", "DOCUMENT_INJECTION", "HALLUCINATION"}


def _load_cases() -> list[dict]:
    with _DATA_PATH.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _fmt(x: float) -> str:
    return "n/a" if x != x else f"{x*100:.1f}%"


def _confusion(records: list[tuple[bool, bool]]) -> tuple[int, int, int, int]:
    tp = fp = tn = fn = 0
    for fired, expected in records:
        if fired and expected:
            tp += 1
        elif fired and not expected:
            fp += 1
        elif not fired and expected:
            fn += 1
        else:
            tn += 1
    return tp, fp, tn, fn


def _metrics(tp, fp, tn, fn) -> dict:
    precision = tp / (tp + fp) if (tp + fp) else float("nan")
    recall = tp / (tp + fn) if (tp + fn) else float("nan")
    f1 = (2 * precision * recall / (precision + recall)) if precision == precision and recall == recall and (precision + recall) > 0 else float("nan")
    return {"tp": tp, "fp": fp, "tn": tn, "fn": fn, "precision": precision, "recall": recall, "f1": f1}


def main() -> None:
    settings.guardrail_redact_pii = True
    settings.guardrail_pii_mode = "placeholder"
    settings.guardrail_block_system_prompt_leak = True

    cases = _load_cases()
    print(f"Loaded {len(cases)} reply-shaped cases from {_DATA_PATH.name}\n")

    by_cat: dict[str, int] = {}
    for c in cases:
        by_cat[c["category"]] = by_cat.get(c["category"], 0) + 1
    print("Category distribution:")
    for cat, n in sorted(by_cat.items()):
        tag = " (not evaluated — no guardrail exists)" if cat in _NOT_EVALUATED else ""
        print(f"  {cat:20}{n:>4}{tag}")
    print()

    # ---- SYSTEM_PROMPT_LEAK + SAFE_RESPONSE (negative set) via run_output_guardrails ----
    spl_records = []
    spl_examples = {"blocked_correctly": [], "false_positive": [], "false_negative": []}
    for c in cases:
        if c["category"] not in ("SYSTEM_PROMPT_LEAK", "SAFE_RESPONSE"):
            continue
        result = run_output_guardrails(c["response"])
        expected_block = c["category"] == "SYSTEM_PROMPT_LEAK" and c["expected"] == "BLOCK"
        spl_records.append((result.blocked, expected_block))
        if result.blocked and expected_block and len(spl_examples["blocked_correctly"]) < 2:
            spl_examples["blocked_correctly"].append(c["response"][:80])
        if result.blocked and not expected_block:
            spl_examples["false_positive"].append(c["response"][:80])
        if not result.blocked and expected_block:
            spl_examples["false_negative"].append(c["response"][:80])
    spl_tp, spl_fp, spl_tn, spl_fn = _confusion(spl_records)
    spl_m = _metrics(spl_tp, spl_fp, spl_tn, spl_fn)

    # ---- OUTPUT_PII + SAFE_RESPONSE (negative set) via run_output_guardrails' redaction ----
    pii_records = []
    pii_examples = {"redacted_correctly": [], "false_positive": [], "false_negative": []}
    for c in cases:
        if c["category"] not in ("OUTPUT_PII", "SAFE_RESPONSE"):
            continue
        result = run_output_guardrails(c["response"])
        redacted = result.text != c["response"]
        expected_redact = c["category"] == "OUTPUT_PII" and c["expected"] == "REDACT"
        pii_records.append((redacted, expected_redact))
        if redacted and expected_redact and len(pii_examples["redacted_correctly"]) < 2:
            pii_examples["redacted_correctly"].append((c["response"][:70], result.text[:70]))
        if redacted and not expected_redact:
            pii_examples["false_positive"].append(c["response"][:80])
        if not redacted and expected_redact:
            pii_examples["false_negative"].append(c["response"][:80])
    pii_tp, pii_fp, pii_tn, pii_fn = _confusion(pii_records)
    pii_m = _metrics(pii_tp, pii_fp, pii_tn, pii_fn)

    # ---- CITATION via check_citations ----
    cit_records = []
    for c in cases:
        if c["category"] != "CITATION":
            continue
        step = check_citations(c["response"], c["sources"])
        passed = "fabricated" not in step.detail.lower() and "no citation" not in step.detail.lower()
        expected_pass = c["expected"] == "PASS"
        cit_records.append((not passed, not expected_pass))  # "fired" = flagged/FAIL
    cit_tp, cit_fp, cit_tn, cit_fn = _confusion(cit_records)
    cit_m = _metrics(cit_tp, cit_fp, cit_tn, cit_fn)

    header = f"{'Category':22}{'N':>6}{'Precision':>11}{'Recall':>10}{'F1':>8}{'FP':>6}{'FN':>6}"
    print(header)
    print("-" * len(header))
    print(f"{'SYSTEM_PROMPT_LEAK':22}{len(spl_records):>6}{_fmt(spl_m['precision']):>11}{_fmt(spl_m['recall']):>10}{_fmt(spl_m['f1']):>8}{spl_m['fp']:>6}{spl_m['fn']:>6}")
    print(f"{'OUTPUT_PII':22}{len(pii_records):>6}{_fmt(pii_m['precision']):>11}{_fmt(pii_m['recall']):>10}{_fmt(pii_m['f1']):>8}{pii_m['fp']:>6}{pii_m['fn']:>6}")
    print(f"{'CITATION':22}{len(cit_records):>6}{_fmt(cit_m['precision']):>11}{_fmt(cit_m['recall']):>10}{_fmt(cit_m['f1']):>8}{cit_m['fp']:>6}{cit_m['fn']:>6}")
    for cat in sorted(_NOT_EVALUATED):
        print(f"{cat:22}{by_cat.get(cat, 0):>6}{'not evaluated':>11} (no corresponding guardrail implemented)")

    print("\nSYSTEM_PROMPT_LEAK — correctly blocked examples:")
    for ex in spl_examples["blocked_correctly"]:
        print(f"  BLOCK: {ex!r}")
    if spl_examples["false_positive"]:
        print("  FALSE POSITIVES:", spl_examples["false_positive"])
    if spl_examples["false_negative"]:
        print("  FALSE NEGATIVES:", spl_examples["false_negative"])

    print("\nOUTPUT_PII — correctly redacted examples:")
    for before, after in pii_examples["redacted_correctly"]:
        print(f"  {before!r} -> {after!r}")
    if pii_examples["false_positive"]:
        print("  FALSE POSITIVES (over-redacted):", pii_examples["false_positive"])
    if pii_examples["false_negative"]:
        print("  FALSE NEGATIVES (missed PII):", pii_examples["false_negative"])


if __name__ == "__main__":
    main()
