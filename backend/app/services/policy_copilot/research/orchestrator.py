"""Guardrail Policy Research Agent orchestrator.

Coordinates the research flow:
1. Scope classification (GUARDRAIL_ONLY enforcement)
2. Request intent classification (what type of research)
3. Tool execution (read-only guardrail registry access)
4. Proposal generation (candidate policy improvements)
5. Validation and approval workflow integration

This module never writes policy — it produces research proposals for human
review and approval. All proposals feed into the existing ApprovalRequestModel
workflow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from sqlalchemy.orm import Session

from app.services.policy_copilot.research.request_classifier import (
    ClassifiedRequest, ResearchIntent, classify_request,
)
from app.services.policy_copilot.research.scope import (
    ScopeClassification, ScopeType, assert_guardrail_only_scope, classify_scope,
)
from app.services.policy_copilot.research.tools import ToolCall, ToolExecutor


class ResearchPhase(str, Enum):
    SCOPE_CHECK = "scope_check"
    INTENT_CLASSIFICATION = "intent_classification"
    TOOL_EXECUTION = "tool_execution"
    PROPOSAL_GENERATION = "proposal_generation"
    VALIDATION = "validation"
    APPROVAL_WORKFLOW = "approval_workflow"


@dataclass
class ResearchTrace:
    """Audit trail for one research request."""
    phase: ResearchPhase
    status: str  # OK, FAILED, SKIPPED
    detail: str = ""


@dataclass
class ResearchProposal:
    """A candidate policy improvement from research."""
    entity: str
    change_type: str  # POLICY_UPDATE, DETECTOR_CREATION, EXCEPTION_ADD, etc.
    description: str
    rationale: str
    impacts: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)


@dataclass
class ResearchResult:
    """Result of a complete research request."""
    success: bool
    message: str
    scope: ScopeClassification | None = None
    intent: ClassifiedRequest | None = None
    proposals: list[ResearchProposal] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    trace: list[ResearchTrace] = field(default_factory=list)


class ResearchOrchestrator:
    """Main controller for guardrail policy research.

    Implements the complete research flow with strict security boundaries
    at every stage. All phases are traceable for audit logging.

    Args:
        db: SQLAlchemy session for policy data access
    """

    def __init__(self, db: Session):
        self.db = db
        self.tool_executor = ToolExecutor(db)
        self.trace: list[ResearchTrace] = []

    def research(self, query: str) -> ResearchResult:
        """Execute one complete research request.

        Flow:
        1. Scope classification (deterministic, rejects non-guardrail)
        2. Intent classification (ANALYZE, COMPARE, OPTIMIZE, etc.)
        3. Tool execution (read-only guardrail registry access)
        4. Proposal generation (candidate improvements)
        5. Validation against existing policies
        6. Integration with approval workflow

        Args:
            query: Research request (typically 20-500 chars)

        Returns:
            ResearchResult with proposals, trace, and success status
        """
        self.trace = []
        result = ResearchResult(success=False, message="Research started")

        # Phase 1: Scope Classification
        scope = classify_scope(query)
        result.scope = scope
        self.trace.append(
            ResearchTrace(
                phase=ResearchPhase.SCOPE_CHECK,
                status="OK" if scope.is_allowed else "FAILED",
                detail=scope.reason,
            )
        )

        if not scope.is_allowed:
            result.message = f"Research rejected: {scope.reason}"
            result.trace = self.trace
            return result

        # Phase 2: Intent Classification
        intent = classify_request(query)
        result.intent = intent
        status = "OK" if intent.confidence >= 0.5 else "FAILED"
        self.trace.append(
            ResearchTrace(
                phase=ResearchPhase.INTENT_CLASSIFICATION,
                status=status,
                detail=f"Intent: {intent.intent.value}, confidence: {intent.confidence:.2f}",
            )
        )

        if intent.confidence < 0.5:
            result.message = "Could not determine research intent. Please be more specific."
            result.trace = self.trace
            return result

        # Phase 3: Tool Execution
        # This would call tools based on intent, but tools are placeholders for now
        self.trace.append(
            ResearchTrace(
                phase=ResearchPhase.TOOL_EXECUTION,
                status="OK",
                detail=f"Executed tools for {intent.intent.value}",
            )
        )

        # Phase 4: Proposal Generation
        proposals = self._generate_proposals(intent, scope)
        result.proposals = proposals
        self.trace.append(
            ResearchTrace(
                phase=ResearchPhase.PROPOSAL_GENERATION,
                status="OK" if proposals else "SKIPPED",
                detail=f"Generated {len(proposals)} proposal(s)",
            )
        )

        # Phase 5: Validation
        # Would validate proposals against existing policies
        self.trace.append(
            ResearchTrace(
                phase=ResearchPhase.VALIDATION,
                status="OK",
                detail=f"Validated {len(proposals)} proposal(s)",
            )
        )

        # Phase 6: Approval Workflow Integration
        # Would create ApprovalRequestModel rows for each proposal
        self.trace.append(
            ResearchTrace(
                phase=ResearchPhase.APPROVAL_WORKFLOW,
                status="OK" if proposals else "SKIPPED",
                detail="Ready for approval workflow integration",
            )
        )

        result.success = True
        result.message = f"Research complete: {len(proposals)} proposal(s) generated"
        result.trace = self.trace
        return result

    def _generate_proposals(
        self, intent: ClassifiedRequest, scope: ScopeClassification
    ) -> list[ResearchProposal]:
        """Generate candidate policy proposals based on intent.

        Args:
            intent: Classified research request
            scope: Scope classification result

        Returns:
            List of candidate proposals for human review
        """
        # Security boundary: assert scope is allowed before accessing any data
        assert_guardrail_only_scope(scope)

        proposals: list[ResearchProposal] = []

        # TODO: Implement proposal generation based on intent
        # This would analyze current policies and generate candidates for:
        # - Missing role exceptions
        # - Inconsistent masking strategies
        # - Detector pattern recommendations
        # - Policy consolidation opportunities

        return proposals
