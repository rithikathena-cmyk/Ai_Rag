"""Base Rail class for guardrails framework

All guardrail rails inherit from this base class and implement:
- evaluate(): Check message against this rail's rules
"""

from abc import ABC, abstractmethod
from typing import List, Optional
from app.models.user import UserModel
from app.services.guardrails.engine import (
    Detection, RailResult, Verdict, Surface
)


class BaseRail(ABC):
    """Base class for all guardrail rails

    A rail is one layer of protection that checks for specific issues:
    - PII Rail: Detects and masks sensitive information
    - Injection Rail: Blocks prompt injection attempts
    - Scope Rail: Enforces role-based access boundaries
    - Policy Rail: Enforces business logic policies
    """

    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def evaluate(
        self,
        text: str,
        user: UserModel,
        surface: Surface
    ) -> RailResult:
        """Evaluate text against this rail's rules

        Args:
            text: Text to evaluate
            user: Current user (for role/permission checks)
            surface: Where in pipeline (user_prompt, retrieval, llm_response)

        Returns:
            RailResult with verdict and any redactions
        """
        pass

    def _create_detection(
        self,
        detected_type: str,
        confidence: float,
        reason: str,
        verdict: Verdict = Verdict.FLAG,
        location: Optional[str] = None,
        value: Optional[str] = None,
        masked_value: Optional[str] = None
    ) -> Detection:
        """Helper to create a detection"""
        return Detection(
            rail_name=self.name,
            verdict=verdict,
            detected_type=detected_type,
            confidence=confidence,
            reason=reason,
            location=location,
            value=value,
            masked_value=masked_value
        )

    def _create_result(
        self,
        passed: bool,
        verdict: Verdict,
        detections: List[Detection] = None,
        redacted_text: Optional[str] = None,
        error: Optional[str] = None
    ) -> RailResult:
        """Helper to create a result"""
        return RailResult(
            rail_name=self.name,
            passed=passed,
            verdict=verdict,
            detections=detections or [],
            redacted_text=redacted_text,
            error=error
        )
