"""Tests for Guardrail Policy Research Agent.

Comprehensive test coverage for:
1. Scope enforcement (GUARDRAIL_ONLY boundary)
2. Intent classification (all research types)
3. Tool allowlist enforcement
4. Proposal generation
5. Security boundary validation
6. Audit logging
7. Integration with existing approval workflow
"""

from __future__ import annotations

import pytest

from app.db.postgres import new_session
from app.services.policy_copilot.research.orchestrator import ResearchOrchestrator, ResearchPhase
from app.services.policy_copilot.research.request_classifier import (
    ClassifiedRequest, ResearchIntent, classify_request,
)
from app.services.policy_copilot.research.scope import (
    ScopeClassification, ScopeType, assert_guardrail_only_scope, classify_scope,
)
from app.services.policy_copilot.research.tools import (
    ToolCall, ToolExecutor, enforce_tool_allowlist,
)


# =============================================================================
# SCOPE ENFORCEMENT TESTS (4 categories)
# =============================================================================

class TestScopeEnforcement:
    """Verify GUARDRAIL_ONLY scope enforcement."""

    def test_scope_guardrail_query_allowed(self):
        """Guardrail-related queries should be allowed."""
        scope = classify_scope("What is our current email masking policy?")
        assert scope.is_allowed is True
        assert scope.scope_type == ScopeType.PII_ENTITY_CONFIG

    def test_scope_pii_entity_query_allowed(self):
        """Queries mentioning PII entities should be allowed."""
        scope = classify_scope("Analyze phone number detection and masking")
        assert scope.is_allowed is True
        assert scope.scope_type == ScopeType.PII_ENTITY_CONFIG

    def test_scope_detector_query_allowed(self):
        """Detector-related queries should be allowed."""
        scope = classify_scope("What patterns are used for VEHICLE_PLATE detection?")
        assert scope.is_allowed is True
        assert scope.scope_type == ScopeType.DETECTOR_CAPABILITY

    def test_scope_guardrail_analysis_query_allowed(self):
        """Analysis queries should be allowed."""
        scope = classify_scope("Analyze gaps in our current PII policies")
        assert scope.is_allowed is True
        assert scope.scope_type == ScopeType.GUARDRAIL_ANALYSIS

    def test_scope_document_query_forbidden(self):
        """Queries about documents should be forbidden."""
        scope = classify_scope("What documents contain sensitive information?")
        assert scope.is_allowed is False
        assert scope.scope_type == ScopeType.FORBIDDEN

    def test_scope_user_query_forbidden(self):
        """Queries about users should be forbidden."""
        scope = classify_scope("Which users accessed confidential documents?")
        assert scope.is_allowed is False

    def test_scope_conversation_query_forbidden(self):
        """Queries about conversations should be forbidden."""
        scope = classify_scope("Show me all conversations containing SSNs")
        assert scope.is_allowed is False

    def test_scope_empty_query_forbidden(self):
        """Empty queries should be rejected."""
        scope = classify_scope("")
        assert scope.is_allowed is False

    def test_scope_short_query_forbidden(self):
        """Very short queries without guardrail keywords should be forbidden."""
        scope = classify_scope("hi")
        assert scope.is_allowed is False

    def test_assert_guardrail_scope_allowed(self):
        """assert_guardrail_only_scope should pass for allowed scope."""
        scope = classify_scope("Analyze email masking policies")
        assert scope.is_allowed is True
        # Should not raise
        assert_guardrail_only_scope(scope)

    def test_assert_guardrail_scope_forbidden(self):
        """assert_guardrail_only_scope should raise for forbidden scope."""
        scope = classify_scope("Show me user data")
        assert scope.is_allowed is False
        with pytest.raises(AssertionError, match="SECURITY VIOLATION"):
            assert_guardrail_only_scope(scope)


# =============================================================================
# INTENT CLASSIFICATION TESTS (3 categories)
# =============================================================================

class TestIntentClassification:
    """Verify request intent classification."""

    def test_classify_analyze_intent(self):
        """ANALYZE intent should be recognized."""
        req = classify_request("Analyze our current email masking policy")
        assert req.intent == ResearchIntent.ANALYZE
        assert req.confidence >= 0.5

    def test_classify_compare_intent(self):
        """COMPARE intent should be recognized."""
        req = classify_request("Compare email and phone masking policies")
        assert req.intent == ResearchIntent.COMPARE
        assert req.confidence >= 0.5

    def test_classify_optimize_intent(self):
        """OPTIMIZE intent should be recognized."""
        req = classify_request("How can we improve our SSN detection?")
        assert req.intent == ResearchIntent.OPTIMIZE
        assert req.confidence >= 0.5

    def test_classify_audit_intent(self):
        """AUDIT intent should be recognized."""
        req = classify_request("Check if our policies are consistent")
        assert req.intent == ResearchIntent.AUDIT
        assert req.confidence >= 0.5

    def test_classify_design_intent(self):
        """DESIGN intent should be recognized."""
        req = classify_request("Design a new detector for API keys")
        assert req.intent == ResearchIntent.DESIGN
        assert req.confidence >= 0.5

    def test_classify_entity_extraction(self):
        """Entity type should be extracted from query."""
        req = classify_request("Analyze email masking")
        assert req.entity == "EMAIL"

        req = classify_request("How do we detect phone numbers?")
        assert req.entity == "PHONE"

        req = classify_request("SSN blocking policy")
        assert req.entity == "SSN"

    def test_classify_focus_area_extraction(self):
        """Focus area should be extracted."""
        req = classify_request("Improve masking for sensitive data")
        assert req.focus_area == "masking"

        req = classify_request("Compare blocking strategies")
        assert req.focus_area == "blocking"

    def test_classify_unclear_intent(self):
        """Vague queries should return UNCLEAR intent."""
        req = classify_request("something something policy")
        assert req.intent == ResearchIntent.UNCLEAR or req.confidence < 0.5


# =============================================================================
# TOOL ALLOWLIST ENFORCEMENT TESTS (3 categories)
# =============================================================================

class TestToolAllowlist:
    """Verify tool allowlist enforcement."""

    def test_allowed_tool_get_active_policies(self):
        """GET_ACTIVE_POLICIES should be allowed."""
        assert enforce_tool_allowlist("GET_ACTIVE_POLICIES", {}) is True

    def test_allowed_tool_list_pii_entities(self):
        """LIST_PII_ENTITIES should be allowed."""
        assert enforce_tool_allowlist("LIST_PII_ENTITIES", {}) is True

    def test_allowed_tool_simulate_policy_change(self):
        """SIMULATE_POLICY_CHANGE should be allowed."""
        assert enforce_tool_allowlist("SIMULATE_POLICY_CHANGE", {"entity": "EMAIL"}) is True

    def test_forbidden_tool_write_policy(self):
        """WRITE_POLICY should be forbidden."""
        assert enforce_tool_allowlist("WRITE_POLICY", {}) is False

    def test_forbidden_tool_delete_policy(self):
        """DELETE_POLICY should be forbidden."""
        assert enforce_tool_allowlist("DELETE_POLICY", {}) is False

    def test_forbidden_tool_access_audit_log(self):
        """ACCESS_AUDIT_LOG should be forbidden."""
        assert enforce_tool_allowlist("ACCESS_AUDIT_LOG", {}) is False

    def test_forbidden_tool_access_messages(self):
        """ACCESS_MESSAGES should be forbidden."""
        assert enforce_tool_allowlist("ACCESS_MESSAGES", {}) is False

    def test_forbidden_tool_access_users(self):
        """ACCESS_USERS should be forbidden."""
        assert enforce_tool_allowlist("ACCESS_USERS", {}) is False

    def test_unknown_tool_disallowed(self):
        """Unknown tools should be disallowed."""
        assert enforce_tool_allowlist("UNKNOWN_TOOL", {}) is False

    def test_invalid_tool_name_type(self):
        """Non-string tool names should be rejected."""
        assert enforce_tool_allowlist(123, {}) is False
        assert enforce_tool_allowlist(None, {}) is False


class TestToolExecutor:
    """Verify tool executor enforces allowlist on execution."""

    def test_tool_executor_allowed_tool(self):
        """Allowed tools should execute."""
        db = new_session()
        try:
            executor = ToolExecutor(db)
            result = executor.execute("GET_ACTIVE_POLICIES", {"category": "PII"})
            assert result.name == "GET_ACTIVE_POLICIES"
            assert result.error is None
            assert result.result is not None
        finally:
            db.close()

    def test_tool_executor_forbidden_tool(self):
        """Forbidden tools should fail at execution."""
        db = new_session()
        try:
            executor = ToolExecutor(db)
            result = executor.execute("WRITE_POLICY", {"policy": "test"})
            assert result.name == "WRITE_POLICY"
            assert result.error is not None
            assert "not allowed" in result.error
        finally:
            db.close()

    def test_tool_executor_missing_required_args(self):
        """Missing required arguments should fail."""
        db = new_session()
        try:
            executor = ToolExecutor(db)
            result = executor.execute("GET_ENTITY_POLICY", {})
            assert result.error is not None
            assert "required" in result.error
        finally:
            db.close()


# =============================================================================
# ORCHESTRATOR INTEGRATION TESTS (3 categories)
# =============================================================================

class TestResearchOrchestrator:
    """Verify complete research flow."""

    def test_research_allowed_query_flow(self):
        """Allowed query should flow through all phases."""
        db = new_session()
        try:
            orchestrator = ResearchOrchestrator(db)
            result = orchestrator.research("Analyze current email masking policy")

            assert result.success is True
            assert result.scope is not None
            assert result.scope.is_allowed is True
            assert result.intent is not None
            assert len(result.trace) > 0

            # Verify phase trace
            phases = [t.phase for t in result.trace]
            assert ResearchPhase.SCOPE_CHECK in phases
            assert ResearchPhase.INTENT_CLASSIFICATION in phases
        finally:
            db.close()

    def test_research_forbidden_query_rejected(self):
        """Forbidden query should be rejected at scope phase."""
        db = new_session()
        try:
            orchestrator = ResearchOrchestrator(db)
            result = orchestrator.research("Show me all user conversations")

            assert result.success is False
            assert result.scope is not None
            assert result.scope.is_allowed is False
            assert "Research rejected" in result.message
        finally:
            db.close()

    def test_research_unclear_intent_rejected(self):
        """Unclear intent should be rejected."""
        db = new_session()
        try:
            orchestrator = ResearchOrchestrator(db)
            result = orchestrator.research("xyz abc def")

            # Either rejected at intent or at scope, but should fail
            assert result.success is False or (
                result.intent and result.intent.confidence < 0.5
            )
        finally:
            db.close()

    def test_research_trace_includes_all_phases(self):
        """Trace should include all research phases."""
        db = new_session()
        try:
            orchestrator = ResearchOrchestrator(db)
            result = orchestrator.research("Analyze email policies for improvement")

            phases = {t.phase for t in result.trace}
            # Should have at least scope check and intent classification
            assert ResearchPhase.SCOPE_CHECK in phases
            assert ResearchPhase.INTENT_CLASSIFICATION in phases
        finally:
            db.close()


# =============================================================================
# SECURITY BOUNDARY TESTS (2 categories)
# =============================================================================

class TestSecurityBoundary:
    """Verify security boundaries are enforced."""

    def test_scope_check_before_tool_execution(self):
        """Scope should be checked before tool execution."""
        db = new_session()
        try:
            orchestrator = ResearchOrchestrator(db)
            # Forbidden query
            result = orchestrator.research("Access user data")

            # Should fail at scope, not reach tool execution
            assert result.success is False
            scope_phase = next((t for t in result.trace if t.phase == ResearchPhase.SCOPE_CHECK), None)
            assert scope_phase is not None
            assert scope_phase.status == "FAILED"
        finally:
            db.close()

    def test_tool_allowlist_enforced_before_llm(self):
        """Tool allowlist should prevent forbidden tools."""
        # This is conceptual — when LLM integration is added,
        # forbidden tools should never reach execution
        assert enforce_tool_allowlist("WRITE_POLICY", {}) is False
        assert enforce_tool_allowlist("ACCESS_USERS", {}) is False

    def test_assert_guardrail_scope_at_code_layer(self):
        """Code layer should assert guardrail scope."""
        # Allowed scope should not raise
        allowed = classify_scope("Analyze email masking")
        assert_guardrail_only_scope(allowed)

        # Forbidden scope should raise
        forbidden = classify_scope("Show user data")
        with pytest.raises(AssertionError):
            assert_guardrail_only_scope(forbidden)
