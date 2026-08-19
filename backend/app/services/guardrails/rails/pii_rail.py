"""PII Detection and Masking Rail

Detects and masks sensitive information like:
- Email addresses
- Phone numbers
- SSN/ID numbers
- Credit card numbers
- Names in sensitive contexts
"""

import re
from typing import List, Optional
from app.models.user import UserModel
from app.services.guardrails.rail import BaseRail
from app.services.guardrails.engine import (
    Detection, RailResult, Verdict, Surface
)


class PIIRail(BaseRail):
    """PII Detection and Masking"""

    # Regex patterns for PII detection
    PATTERNS = {
        "email": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
        "phone": r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b",
        "ssn": r"\b\d{3}-\d{2}-\d{4}\b",
        "credit_card": r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b",
        "employee_id": r"\b(EMP|STF|ENG|MFG)-\d+\b",
    }

    def __init__(self):
        super().__init__("pii")
        self.compiled_patterns = {
            k: re.compile(v) for k, v in self.PATTERNS.items()
        }

    def evaluate(
        self,
        text: str,
        user: UserModel,
        surface: Surface
    ) -> RailResult:
        """Detect and mask PII in text"""

        detections = []
        text_to_redact = text

        # Scan for each PII type
        for pii_type, pattern in self.compiled_patterns.items():
            for match in pattern.finditer(text):
                value = match.group()

                # Determine verdict based on role and surface
                verdict = self._determine_verdict(
                    pii_type, user, surface, value
                )

                detection = self._create_detection(
                    detected_type=pii_type,
                    confidence=0.95,  # Regex is high confidence
                    reason=f"Detected {pii_type} in message",
                    verdict=verdict,
                    location=f"position {match.start()}-{match.end()}",
                    value=value,
                    masked_value=self._mask_pii(pii_type, value)
                )

                detections.append(detection)

                # If should redact, replace in text
                if verdict in [Verdict.REDACT, Verdict.BLOCK]:
                    text_to_redact = text_to_redact.replace(
                        value,
                        self._mask_pii(pii_type, value)
                    )

        # Determine overall result
        has_blocking = any(d.verdict == Verdict.BLOCK for d in detections)
        has_redacting = any(
            d.verdict == Verdict.REDACT for d in detections
        )

        if has_blocking:
            return self._create_result(
                passed=False,
                verdict=Verdict.BLOCK,
                detections=detections,
                redacted_text=text_to_redact
            )

        if has_redacting:
            return self._create_result(
                passed=True,
                verdict=Verdict.REDACT,
                detections=detections,
                redacted_text=text_to_redact
            )

        return self._create_result(
            passed=True,
            verdict=Verdict.PASS,
            detections=detections
        )

    def _determine_verdict(
        self,
        pii_type: str,
        user: UserModel,
        surface: Surface,
        value: str
    ) -> Verdict:
        """Determine verdict based on context"""

        # SSN always blocks (high risk)
        if pii_type == "ssn":
            return Verdict.BLOCK

        # Credit card always blocks
        if pii_type == "credit_card":
            return Verdict.BLOCK

        # For HR accessing employee data in correct context
        if user.role == "hr" and surface == Surface.USER_PROMPT:
            # Allow with redaction in input (will be masked)
            return Verdict.REDACT

        # CEO can access but redact in output
        if user.role == "ceo" and surface == Surface.LLM_RESPONSE:
            return Verdict.REDACT

        # For output to non-HR roles, always redact
        if surface == Surface.LLM_RESPONSE and user.role != "hr":
            return Verdict.REDACT

        # For retrieval, redact by default
        if surface == Surface.RETRIEVAL:
            return Verdict.REDACT

        # Email: redact by default (not high-risk)
        if pii_type == "email":
            return Verdict.REDACT

        # Default: redact
        return Verdict.REDACT

    def _mask_pii(self, pii_type: str, value: str) -> str:
        """Mask PII value"""

        if pii_type == "email":
            # john.doe@company.com → jo####@company.com
            parts = value.split("@")
            if len(parts) == 2:
                name, domain = parts
                masked_name = name[:2] + "#" * (len(name) - 2)
                return f"{masked_name}@{domain}"
            return value

        if pii_type == "phone":
            # 555-123-4567 → 555-###-####
            return re.sub(r"\d{3}$", "####", re.sub(r"-\d{3}-", "-###-", value))

        if pii_type == "ssn":
            # 123-45-6789 → ###-##-####
            return "###-##-####"

        if pii_type == "credit_card":
            # 1234-5678-9012-3456 → ####-####-####-3456
            return re.sub(r"(\d{4})[\s-]?(\d{4})[\s-]?(\d{4})[\s-]?", "####-####-####-", value)

        if pii_type == "employee_id":
            # EMP-12345 → ###-#####
            return re.sub(r"\d", "#", value)

        return value
