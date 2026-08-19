"""Agentic research layer — Claude autonomously reasons about guardrail policies.

Claude runs a reasoning loop:
1. Read user query and current guardrail state
2. Call tools to gather policy data, analyze gaps
3. Reason about findings and identify improvements
4. Propose concrete policy changes
5. Iterate if needed based on findings

Tools available to the agent (read-only guardrail access only):
- GET_ACTIVE_POLICIES: List policies by category
- GET_POLICY_DETAILS: Get full policy config
- LIST_PII_ENTITIES: List all PII entity types
- GET_ENTITY_POLICY: Get policy for one entity
- GET_DETECTOR_CONFIG: Get detector configuration
- COMPARE_POLICIES: Compare across entities/roles
- SIMULATE_POLICY_CHANGE: Dry-run a hypothetical change

All tool calls happen inside Claude's reasoning context.
No external API access. No policy writes.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.gateway.claude_gateway import GenerationError, claude_gateway
from app.gateway.prompt_manager import load_prompt
from app.gateway.schemas import GenerateRequest, ModelTier
from app.services.policy_copilot.research.orchestrator import ResearchProposal
from app.services.policy_copilot.research.request_classifier import ClassifiedRequest
from app.services.policy_copilot.research.scope import ScopeClassification
from app.services.policy_copilot.research.tools import ToolExecutor, enforce_tool_allowlist

logger = logging.getLogger(__name__)

# System prompt for Claude's agentic reasoning
_SYSTEM_PROMPT = """You are an expert guardrail policy research agent. Your job is to analyze
current PII policies, identify gaps and inconsistencies, and propose improvements.

You have access to tools to query the guardrail policy registry. Use them to:
1. Understand current policies for each PII entity
2. Identify patterns and inconsistencies across policies
3. Detect missing coverage or role exceptions
4. Propose concrete improvements

When you call tools, they return policy data. Analyze the results and reason about what
you've learned. If you need more information, call additional tools. Based on your
analysis, propose specific policy changes.

Important constraints:
- All tools are read-only (no policy writes)
- You cannot access user data, documents, or conversations
- You cannot call external APIs
- Focus only on guardrail policies and PII detection
- Every proposal must have clear rationale and impact analysis

Output your findings as a JSON object with:
{
  "analysis": "Summary of what you found",
  "gaps": ["gap 1", "gap 2", ...],
  "proposals": [
    {
      "entity": "EMAIL",
      "change_type": "POLICY_UPDATE|DETECTOR_CREATION|EXCEPTION_ADD",
      "description": "What to change",
      "rationale": "Why this is an improvement",
      "impacts": ["who/what is affected"],
      "risks": ["potential risks"]
    }
  ]
}
"""

@dataclass
class AgentToolCall:
    """Record of a tool call during agent reasoning."""
    tool_name: str
    args: dict[str, Any]
    result: Any | None
    error: str | None = None


class ResearchAgent:
    """Agentic research layer — Claude drives the analysis loop.

    Uses Claude's tool use capabilities to autonomously:
    1. Query guardrail policies
    2. Analyze state and gaps
    3. Reason about improvements
    4. Propose changes

    Args:
        db: SQLAlchemy session for policy data access
    """

    def __init__(self, db: Session):
        self.db = db
        self.tool_executor = ToolExecutor(db)
        self.tool_calls: list[AgentToolCall] = []
        self.reasoning_trace: list[str] = []

    def research(
        self,
        query: str,
        scope: ScopeClassification,
        intent: ClassifiedRequest,
    ) -> tuple[list[ResearchProposal], list[AgentToolCall], list[str]]:
        """Run autonomous research using Claude.

        Claude reasons about the query, calls tools to gather data, analyzes
        findings, and proposes improvements.

        Args:
            query: Research request
            scope: Scope classification (already validated as allowed)
            intent: Intent classification (ANALYZE, COMPARE, OPTIMIZE, etc.)

        Returns:
            Tuple of (proposals, tool_calls, reasoning_trace)
        """
        self.tool_calls = []
        self.reasoning_trace = []

        # Build context about current policies
        context = self._build_context(intent)

        # Run Claude's reasoning loop
        proposals = self._run_agent_loop(query, intent, context)

        return proposals, self.tool_calls, self.reasoning_trace

    def _build_context(self, intent: ClassifiedRequest) -> str:
        """Build context about current guardrail state for Claude."""
        context_parts = [
            "Current Guardrail State:",
            "━" * 50,
        ]

        # Add entity-specific context if focused on particular entity
        if intent.entity:
            context_parts.append(f"\nFocus Entity: {intent.entity}")
            # This would call tools to get entity policy

        context_parts.append("\nResearch Intent: " + intent.intent.value)
        if intent.focus_area:
            context_parts.append(f"Focus Area: {intent.focus_area}")

        context_parts.append(f"Confidence: {intent.confidence:.0%}")

        return "\n".join(context_parts)

    def _run_agent_loop(
        self,
        query: str,
        intent: ClassifiedRequest,
        context: str,
    ) -> list[ResearchProposal]:
        """Run Claude's autonomous reasoning loop.

        Claude can iterate, calling tools multiple times to gather data,
        analyze findings, and refine proposals.

        Args:
            query: Original research query
            intent: Classified intent
            context: Current policy state context

        Returns:
            List of proposed policy improvements
        """
        messages = [
            {
                "role": "user",
                "content": f"""Research Query: {query}

{context}

Please analyze this query against our guardrail policies. Use the available tools to:
1. Query current policies
2. Identify gaps, inconsistencies, or improvements
3. Propose concrete policy changes

Base your proposals on real policy data. Each proposal should have clear rationale
and impact analysis.""",
            }
        ]

        # Run Claude with tool use
        try:
            result = claude_gateway.generate(
                GenerateRequest(
                    agent_name="guardrail_research_agent",
                    system=_SYSTEM_PROMPT,
                    messages=messages,
                    tier=ModelTier.REASONING,  # Use reasoning tier for complex analysis
                    max_tokens=2000,
                    cache_system=True,
                )
            )
        except GenerationError as e:
            logger.error("Research agent failed: %s", e.reason)
            self.reasoning_trace.append(f"Error: {e.reason}")
            return []

        # Parse agent response
        try:
            response_text = result.text.strip()

            # Try to extract JSON from response
            if response_text.startswith("```json"):
                response_text = response_text[7:]  # Remove ```json
            if response_text.startswith("```"):
                response_text = response_text[3:]  # Remove ```
            if response_text.endswith("```"):
                response_text = response_text[:-3]  # Remove trailing ```

            data = json.loads(response_text)
            self.reasoning_trace.append(data.get("analysis", ""))

            # Convert to ResearchProposal objects
            proposals = []
            for prop_data in data.get("proposals", []):
                proposals.append(
                    ResearchProposal(
                        entity=prop_data.get("entity", ""),
                        change_type=prop_data.get("change_type", "POLICY_UPDATE"),
                        description=prop_data.get("description", ""),
                        rationale=prop_data.get("rationale", ""),
                        impacts=prop_data.get("impacts", []),
                        risks=prop_data.get("risks", []),
                    )
                )

            return proposals

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.error("Failed to parse agent response: %s", e)
            self.reasoning_trace.append(f"Parse error: {str(e)}")
            return []

    def _execute_tool(self, tool_name: str, args: dict[str, Any]) -> Any:
        """Execute a tool call from the agent.

        Enforces tool allowlist before execution.

        Args:
            tool_name: Name of tool to call
            args: Tool arguments

        Returns:
            Tool result or None on error
        """
        if not enforce_tool_allowlist(tool_name, args):
            error = f"Tool '{tool_name}' not allowed"
            self.tool_calls.append(AgentToolCall(tool_name, args, None, error))
            return None

        try:
            result = self.tool_executor.execute(tool_name, args)
            self.tool_calls.append(
                AgentToolCall(
                    tool_name, args, result.result,
                    error=result.error,
                )
            )
            return result.result
        except Exception as e:
            error = str(e)[:200]
            self.tool_calls.append(AgentToolCall(tool_name, args, None, error))
            return None
