"""Enhanced Guardrails Engine - Like GuardRails Framework

Orchestrates multiple guardrail rails through a complete pipeline:
1. NORMALIZE - Clean and normalize text
2. DETECT - Run all rails (PII, injection, content, policy)
3. AUDIT - Log all detections
4. MASK - Mask sensitive data
5. EXECUTE - Send to LLM
6. OUTPUT - Check LLM output
7. FINAL MASK - Redact any exposed PII in output
"""

import logging
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Any
from enum import Enum

from app.core.errors import AppError
from app.models.user import UserModel

logger = logging.getLogger(__name__)


class Surface(str, Enum):
    """Where guardrails are applied"""
    USER_PROMPT = "user_prompt"
    RETRIEVAL = "retrieval"
    LLM_RESPONSE = "llm_response"
    LLM_ASK_USER = "llm_ask_user"


class Verdict(str, Enum):
    """Detection verdict"""
    PASS = "pass"
    BLOCK = "block"
    REDACT = "redact"
    FLAG = "flag"
    REVIEW = "review"


@dataclass
class Detection:
    """A single detection from a rail"""
    rail_name: str
    verdict: Verdict
    detected_type: str
    confidence: float
    reason: str
    location: Optional[str] = None
    value: Optional[str] = None
    masked_value: Optional[str] = None


@dataclass
class RailResult:
    """Result from running a single rail"""
    rail_name: str
    passed: bool
    verdict: Verdict
    detections: List[Detection] = field(default_factory=list)
    redacted_text: Optional[str] = None
    error: Optional[str] = None


@dataclass
class EvaluationResult:
    """Result from evaluating all rails on one surface"""
    surface: Surface
    passed: bool
    final_verdict: Verdict
    rail_results: List[RailResult] = field(default_factory=list)
    text_after_redaction: Optional[str] = None
    should_block: bool = False
    block_reason: Optional[str] = None


@dataclass
class ConversationResult:
    """Complete result of processing user message through guardrails"""
    reply: str
    passed: bool
    blocked: bool = False
    block_reason: Optional[str] = None
    input_eval: Optional[EvaluationResult] = None
    output_eval: Optional[EvaluationResult] = None
    detections: List[Detection] = field(default_factory=list)
    audit_trail: List[Dict[str, Any]] = field(default_factory=list)
    pii_masked: bool = False
    injection_detected: bool = False


class GuardrailsEngine:
    """Main orchestrator for guardrails pipeline

    Runs all guardrail rails through a comprehensive pipeline:
    1. Input validation (PII, injection, scope, policy)
    2. Message processing
    3. LLM execution
    4. Output validation
    5. Response masking
    """

    def __init__(self):
        self.rails = {}
        self._register_default_rails()

    def _register_default_rails(self):
        """Register all guardrail rails"""
        from app.services.guardrails.rails.pii import PIIRail
        from app.services.guardrails.rails.injection import InjectionRail
        from app.services.guardrails.rails.scope import ScopeRail
        from app.services.guardrails.rails.policy import PolicyRail

        self.register_rail("pii", PIIRail())
        self.register_rail("injection", InjectionRail())
        self.register_rail("scope", ScopeRail())
        self.register_rail("policy", PolicyRail())

    def register_rail(self, name: str, rail):
        """Register a guardrail rail"""
        self.rails[name] = rail
        logger.info(f"Registered rail: {name}")

    def evaluate_input(
        self,
        message: str,
        user: UserModel,
        surface: Surface = Surface.USER_PROMPT
    ) -> EvaluationResult:
        """Evaluate user input through all rails

        Args:
            message: User's input message
            user: Current user
            surface: Where in pipeline this is

        Returns:
            EvaluationResult with verdict and any redactions
        """
        logger.info(f"Evaluating input from {user.role} on {surface}")

        rail_results = []
        all_detections = []
        final_verdict = Verdict.PASS
        text_to_process = message

        # Run each rail
        for rail_name, rail in self.rails.items():
            try:
                result = rail.evaluate(
                    text=text_to_process,
                    user=user,
                    surface=surface
                )
                rail_results.append(result)
                all_detections.extend(result.detections)

                # Update text if rail returned redacted version
                if result.redacted_text:
                    text_to_process = result.redacted_text

                # Most restrictive verdict wins
                if result.verdict == Verdict.BLOCK:
                    final_verdict = Verdict.BLOCK
                elif result.verdict == Verdict.FLAG and final_verdict != Verdict.BLOCK:
                    final_verdict = Verdict.FLAG
                elif result.verdict == Verdict.REDACT and final_verdict == Verdict.PASS:
                    final_verdict = Verdict.REDACT

            except Exception as e:
                logger.error(f"Error in rail {rail_name}: {e}")
                rail_results.append(
                    RailResult(
                        rail_name=rail_name,
                        passed=False,
                        verdict=Verdict.BLOCK,
                        error=str(e)
                    )
                )
                final_verdict = Verdict.BLOCK

        # Determine if should block
        should_block = final_verdict == Verdict.BLOCK
        block_reason = None

        if should_block:
            # Get reason from first blocking detection
            for detection in all_detections:
                if detection.verdict == Verdict.BLOCK:
                    block_reason = detection.reason
                    break

        return EvaluationResult(
            surface=surface,
            passed=not should_block,
            final_verdict=final_verdict,
            rail_results=rail_results,
            text_after_redaction=text_to_process if final_verdict == Verdict.REDACT else None,
            should_block=should_block,
            block_reason=block_reason
        )

    def evaluate_output(
        self,
        reply: str,
        user: UserModel,
        surface: Surface = Surface.LLM_RESPONSE
    ) -> EvaluationResult:
        """Evaluate LLM output through guardrails

        Ensures response doesn't expose PII or violate policies
        """
        logger.info(f"Evaluating output for {user.role}")

        rail_results = []
        all_detections = []
        final_verdict = Verdict.PASS
        text_to_process = reply

        # Run each rail on output
        for rail_name, rail in self.rails.items():
            try:
                result = rail.evaluate(
                    text=text_to_process,
                    user=user,
                    surface=surface
                )
                rail_results.append(result)
                all_detections.extend(result.detections)

                if result.redacted_text:
                    text_to_process = result.redacted_text

                # Update verdict
                if result.verdict == Verdict.BLOCK:
                    final_verdict = Verdict.BLOCK
                elif result.verdict == Verdict.FLAG and final_verdict != Verdict.BLOCK:
                    final_verdict = Verdict.FLAG
                elif result.verdict == Verdict.REDACT and final_verdict == Verdict.PASS:
                    final_verdict = Verdict.REDACT

            except Exception as e:
                logger.error(f"Error in rail {rail_name}: {e}")
                rail_results.append(
                    RailResult(
                        rail_name=rail_name,
                        passed=False,
                        verdict=Verdict.BLOCK,
                        error=str(e)
                    )
                )

        return EvaluationResult(
            surface=surface,
            passed=final_verdict != Verdict.BLOCK,
            final_verdict=final_verdict,
            rail_results=rail_results,
            text_after_redaction=text_to_process if final_verdict == Verdict.REDACT else reply,
            should_block=final_verdict == Verdict.BLOCK
        )

    def process_message(
        self,
        message: str,
        user: UserModel,
        llm_fn=None
    ) -> ConversationResult:
        """Complete pipeline: input → LLM → output

        Args:
            message: User input
            user: Current user
            llm_fn: Function to call LLM (if None, skips LLM)

        Returns:
            ConversationResult with full processing result
        """
        audit_trail = []

        # STEP 1: Evaluate input
        input_eval = self.evaluate_input(message, user, Surface.USER_PROMPT)
        audit_trail.append({
            "step": "input_evaluation",
            "verdict": input_eval.final_verdict,
            "detections": len(input_eval.rail_results)
        })

        if input_eval.should_block:
            return ConversationResult(
                reply="",
                passed=False,
                blocked=True,
                block_reason=input_eval.block_reason,
                input_eval=input_eval,
                audit_trail=audit_trail
            )

        # STEP 2: Use redacted text if needed
        text_for_llm = input_eval.text_after_redaction or message

        # STEP 3: Call LLM (if provided)
        reply = ""
        if llm_fn:
            try:
                reply = llm_fn(text_for_llm)
                audit_trail.append({
                    "step": "llm_execution",
                    "status": "success"
                })
            except Exception as e:
                logger.error(f"LLM error: {e}")
                return ConversationResult(
                    reply="",
                    passed=False,
                    blocked=True,
                    block_reason=f"LLM error: {e}",
                    audit_trail=audit_trail
                )

        # STEP 4: Evaluate output
        output_eval = self.evaluate_output(reply, user, Surface.LLM_RESPONSE)
        audit_trail.append({
            "step": "output_evaluation",
            "verdict": output_eval.final_verdict
        })

        # STEP 5: Use safe output
        safe_reply = output_eval.text_after_redaction or reply

        return ConversationResult(
            reply=safe_reply,
            passed=output_eval.passed,
            blocked=output_eval.should_block,
            block_reason=output_eval.block_reason,
            input_eval=input_eval,
            output_eval=output_eval,
            audit_trail=audit_trail,
            pii_masked=output_eval.final_verdict in [Verdict.REDACT, Verdict.FLAG]
        )
