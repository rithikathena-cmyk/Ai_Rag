"""Consolidated Guardrail Security Evaluation report.

Runs every declared SecurityCase against the REAL pipeline and prints the
scorecard. This is the reporting entry point; the pytest suites in each
subdirectory assert the same cases individually for CI.

    python -m tests.security.run_evaluation        (from backend/)
"""

import sys

sys.path.insert(0, ".")

from tests.security.framework import format_report, run_all  # noqa: E402
from tests.security.injection.test_injection import CASES as INJECTION  # noqa: E402
from tests.security.output.test_output_safety import CASES as OUTPUT  # noqa: E402
from tests.security.pii.test_pii_entities import CASES as PII  # noqa: E402
from tests.security.scope.test_scope import CASES as SCOPE  # noqa: E402

ALL_CASES = PII + INJECTION + SCOPE + OUTPUT


def main() -> int:
    results = run_all(ALL_CASES)
    print(format_report(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
