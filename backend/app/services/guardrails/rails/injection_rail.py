"""Prompt Injection Prevention Rail

Detects and blocks prompt injection attempts like:
- "Ignore instructions"
- "Pretend you're in training mode"
- "Show me hidden documents"
"""

import re
from app.models.user import UserModel
from app.services.guardrails.rail import BaseRail
from app.services.guardrails.engine import (
    RailResult, Verdict, Surface
)


class InjectionRail(BaseRail):
    """Prompt Injection Detection and Prevention"""

    # Injection patterns
    INJECTION_PATTERNS = [
        r"ignore.*instruction",
        r"forget.*what.*you",
        r"pretend.*you.*are",
        r"you.*are.*now",
        r"new.*instruction",
        r"disregard.*previous",
        r"bypass.*security",
        r"override.*rule",
        r"training.*mode",
        r"developer.*mode",
        r"admin.*access",
        r"show.*secret",
        r"show.*hidden",
        r"reveal.*password",
    ]

    def __init__(self):
        super().__init__("injection")
        self.compiled_patterns = [
            re.compile(p, re.IGNORECASE) for p in self.INJECTION_PATTERNS
        ]

    def evaluate(
        self,
        text: str,
        user: UserModel,
        surface: Surface
    ) -> RailResult:
        """Detect injection attempts"""

        detections = []

        # Check for injection patterns
        for pattern in self.compiled_patterns:
            if pattern.search(text):
                detection = self._create_detection(
                    detected_type="prompt_injection",
                    confidence=0.85,
                    reason=f"Detected injection pattern: {pattern.pattern}",
                    verdict=Verdict.BLOCK
                )
                detections.append(detection)
                break  # Report first match

        if detections:
            return self._create_result(
                passed=False,
                verdict=Verdict.BLOCK,
                detections=detections
            )

        return self._create_result(
            passed=True,
            verdict=Verdict.PASS,
            detections=[]
        )
