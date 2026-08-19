"""Scope Enforcement Rail

Ensures users only access documents within their role's scope:
- Employee: Manufacturing only
- HR: HR documents only
- Project Manager: Engineering only
- CEO: All documents
"""

from app.models.user import UserModel
from app.services.guardrails.rail import BaseRail
from app.services.guardrails.engine import (
    RailResult, Verdict, Surface
)


class ScopeRail(BaseRail):
    """Scope and Department Boundary Enforcement"""

    # Department keywords by role
    ALLOWED_KEYWORDS = {
        "employee": ["manufacturing", "production", "line7", "quality", "shift"],
        "hr": ["hr", "recruitment", "benefits", "attendance", "policy", "onboarding"],
        "project_manager": ["engineering", "equipment", "maintenance", "specs", "conveyor", "fx2200"],
        "ceo": ["executive", "strategy", "performance", "kpi", "quarterly", "annual"],
        "admin": [".*"],  # All
    }

    # Restricted keywords for specific roles
    DENIED_KEYWORDS = {
        "employee": ["employee.*contact", "email", "phone", "salary", "hr"],
        "project_manager": ["hr", "email", "contact", "salary"],
    }

    def __init__(self):
        super().__init__("scope")

    def evaluate(
        self,
        text: str,
        user: UserModel,
        surface: Surface
    ) -> RailResult:
        """Check if user is accessing content within scope"""

        # Admins have access to everything
        if user.role == "admin":
            return self._create_result(
                passed=True,
                verdict=Verdict.PASS,
                detections=[]
            )

        text_lower = text.lower()

        # Check denied keywords
        denied_list = self.DENIED_KEYWORDS.get(user.role, [])
        for denied_keyword in denied_list:
            if denied_keyword in text_lower:
                detection = self._create_detection(
                    detected_type="scope_violation",
                    confidence=0.90,
                    reason=f"User {user.role} cannot access '{denied_keyword}'",
                    verdict=Verdict.BLOCK
                )
                return self._create_result(
                    passed=False,
                    verdict=Verdict.BLOCK,
                    detections=[detection]
                )

        # Check allowed keywords (positive list)
        allowed_list = self.ALLOWED_KEYWORDS.get(user.role, [])
        if allowed_list and allowed_list != [".*"]:
            found_match = any(
                keyword in text_lower for keyword in allowed_list
            )
            if not found_match and surface == Surface.USER_PROMPT:
                # Only block if user is explicitly asking about specific data
                if any(word in text_lower for word in ["show", "display", "list", "what", "who"]):
                    detection = self._create_detection(
                        detected_type="scope_limitation",
                        confidence=0.75,
                        reason=f"Content outside {user.role} scope",
                        verdict=Verdict.FLAG
                    )
                    return self._create_result(
                        passed=True,
                        verdict=Verdict.FLAG,
                        detections=[detection]
                    )

        return self._create_result(
            passed=True,
            verdict=Verdict.PASS,
            detections=[]
        )
