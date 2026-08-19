"""Policy Enforcement Rail

Enforces business logic and security policies:
- Rate limiting checks
- Destructive intent detection
- High-risk operation blocking
"""

import re
from app.models.user import UserModel
from app.services.guardrails.rail import BaseRail
from app.services.guardrails.engine import (
    RailResult, Verdict, Surface
)


class PolicyRail(BaseRail):
    """Business Policy Enforcement"""

    # Patterns indicating destructive intent
    DESTRUCTIVE_PATTERNS = [
        r"delete.*all",
        r"drop.*database",
        r"remove.*user",
        r"ban.*user",
        r"suspend.*account",
        r"wipe.*data",
        r"destroy.*system",
    ]

    def __init__(self):
        super().__init__("policy")
        self.compiled_patterns = [
            re.compile(p, re.IGNORECASE) for p in self.DESTRUCTIVE_PATTERNS
        ]

    def evaluate(
        self,
        text: str,
        user: UserModel,
        surface: Surface
    ) -> RailResult:
        """Check against business policies"""

        detections = []

        # Check for destructive intent
        for pattern in self.compiled_patterns:
            if pattern.search(text):
                detection = self._create_detection(
                    detected_type="destructive_intent",
                    confidence=0.80,
                    reason="Detected potentially destructive operation",
                    verdict=Verdict.FLAG
                )
                detections.append(detection)

        # Check for high-risk operations by non-admin
        if user.role != "admin":
            risk_patterns = [
                r"delete.*document",
                r"modify.*policy",
                r"change.*permission",
            ]
            for pattern_str in risk_patterns:
                pattern = re.compile(pattern_str, re.IGNORECASE)
                if pattern.search(text):
                    detection = self._create_detection(
                        detected_type="high_risk_operation",
                        confidence=0.70,
                        reason=f"Non-admin cannot perform this operation",
                        verdict=Verdict.BLOCK
                    )
                    detections.append(detection)
                    break

        # Determine verdict
        if any(d.verdict == Verdict.BLOCK for d in detections):
            return self._create_result(
                passed=False,
                verdict=Verdict.BLOCK,
                detections=detections
            )

        if detections:
            return self._create_result(
                passed=True,
                verdict=Verdict.FLAG,
                detections=detections
            )

        return self._create_result(
            passed=True,
            verdict=Verdict.PASS,
            detections=[]
        )
