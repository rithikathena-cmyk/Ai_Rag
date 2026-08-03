import itertools
import json
from dataclasses import dataclass, field
from typing import Annotated, Literal, TypedDict

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from app.core.config import settings
from app.db.postgres import new_session
from app.services.agents.report_agent import ReportAgentError, generate_report
from app.services.agents.retrieval_agent import search_documents
from app.services.agents.sql_agent import SqlAgentError, run_analytics_query
from app.services.generation.client import GenerationError
from app.services.monitoring.metrics import record_token_usage

PLANNER_SYSTEM_PROMPT = """You are the planning and synthesis layer for a multi-agent document intelligence system.

You have three tools, each backed by a specialized agent:
- search_documents (Retrieval Agent): hybrid semantic + keyword search over uploaded documents, with metadata filtering.
- query_analytics (SQL Agent): read-only analytics over the system's document/upload/entity metadata.
- generate_report (Report Agent): produces a downloadable CSV, Excel, Word, or PDF file from tabular data you provide.

Understand the user's intent and decide which tool(s), if any, are needed. If a request has independent parts, call the relevant tools in the same turn so they run in parallel. To generate a report, first gather the data with search_documents and/or query_analytics, then call generate_report with the assembled rows — this may take more than one turn.

Once you have what you need, write a final answer for the user. Cite document sources inline using bracketed numbers matching the source list returned by search_documents (e.g. [1], [2]). If you generated a report, mention it plainly; do not fabricate a download link yourself. Only use information returned by your tools — do not rely on outside knowledge, and say so plainly if the tools didn't return enough information."""

INCOMPLETE_ANSWER = "I wasn't able to finish gathering everything needed to answer this within the allotted steps."
REFUSED_ANSWER = "I'm not able to help with that request."


@dataclass
class AgentRunResult:
    reply: str
    sources: list[dict] = field(default_factory=list)
    report: dict | None = None
    trace: list[dict] = field(default_factory=list)


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]


def _build_system_prompt(conversation_summary: str | None, preferences: dict | None) -> str:
    system_prompt = PLANNER_SYSTEM_PROMPT
    if conversation_summary:
        system_prompt += f"\n\nSummary of earlier parts of this conversation:\n{conversation_summary}"
    if preferences:
        prefs_text = "; ".join(f"{k}: {v}" for k, v in preferences.items())
        system_prompt += f"\n\nUser preferences to keep in mind: {prefs_text}"
    return system_prompt


def _build_tools(
    all_sources: list[dict], citation_counter: itertools.count, report_holder: dict, trace: list[dict]
):
    """Builds the three tools fresh per run_agent() call.

    `all_sources`/`citation_counter`/`report_holder`/`trace` are captured by
    closure so tool calls can accumulate structured metadata (citation
    numbers, report info, an agent-routing trace for the UI) that never goes
    into the LLM-visible ToolMessage content. This has to be closure state,
    not LangGraph state updated via Command: LangGraph's InjectedState
    pattern hands a tool a *snapshot* taken before its parallel batch runs,
    so two concurrent search_documents calls would both see the same
    starting citation count and collide. Closure-captured plain Python
    objects (itertools.count(), lists) are shared by reference across
    ToolNode's thread pool — count().__next__() and list.append/extend are
    atomic under the GIL, so this stays correct under concurrent tool calls
    without needing a lock.
    """

    @tool("search_documents")
    def search_documents_tool(
        query: str,
        top_k: int | None = None,
        document_type: str | None = None,
        classification: str | None = None,
    ) -> str:
        """Search the uploaded document corpus using hybrid (semantic + keyword) retrieval with
        optional metadata filters. Use this to find information contained in documents to
        answer the user's question, or to gather source content for a report."""
        db = new_session()
        try:
            raw_results = search_documents(
                db, query=query, top_k=top_k, document_type=document_type, classification=classification
            )
        finally:
            db.close()
        numbered = [{"index": next(citation_counter), **r} for r in raw_results]
        all_sources.extend(numbered)
        trace.append({
            "agent": "Retrieval Agent",
            "tool": "search_documents",
            "input": query,
            "summary": f"{len(numbered)} chunk(s) matched",
        })
        return json.dumps(numbered, default=str)

    @tool("query_analytics")
    def query_analytics_tool(sql: str) -> str:
        """Run a read-only analytics SQL query against the system's PostgreSQL metadata to answer
        questions about the document corpus (counts, breakdowns, trends, dashboard metrics).
        Only a single SELECT statement is allowed, only against these tables:

        documents(id, filename, document_type, classification, classification_confidence,
        language, chunk_count, table_count, image_count, version_number, is_latest_version, created_at)
        chunks(id, document_id, chunk_index, strategy, token_count, created_at)
        entities(id, document_id, entity_text, entity_label, mention_count)
        terms(id, term)
        chunk_term_frequencies(chunk_id, term_id, term_frequency)
        upload_logs(id, document_id, filename, outcome, error_code, created_at)
        permissions(id, user_id, document_id, permission_level, created_at)
        reports(id, title, format, row_count, created_at)

        Results are automatically capped at 500 rows."""
        db = new_session()
        try:
            try:
                columns, rows = run_analytics_query(db, sql)
            except SqlAgentError as exc:
                trace.append({
                    "agent": "SQL Agent", "tool": "query_analytics", "input": sql, "summary": f"error: {exc}"
                })
                return json.dumps({"error": str(exc)})
            trace.append({
                "agent": "SQL Agent",
                "tool": "query_analytics",
                "input": sql,
                "summary": f"{len(rows)} row(s) returned",
            })
            return json.dumps({"columns": columns, "rows": rows}, default=str)
        finally:
            db.close()

    @tool("generate_report")
    def generate_report_tool(
        title: str, format: Literal["csv", "xlsx", "docx", "pdf"], columns: list[str], rows: list[list[str]]
    ) -> str:
        """Generate a downloadable report (CSV, Excel, Word, or PDF) from tabular data. Call this
        only after you have gathered the data (via search_documents and/or query_analytics) and
        assembled it into rows — provide the data directly, do not reference prior tool results
        by name."""
        db = new_session()
        try:
            try:
                report = generate_report(db, title=title, fmt=format, columns=columns, rows=rows)
            except ReportAgentError as exc:
                trace.append({
                    "agent": "Report Agent", "tool": "generate_report", "input": title, "summary": f"error: {exc}"
                })
                return json.dumps({"error": str(exc)})
        finally:
            db.close()
        info = {
            "id": str(report.id),
            "title": report.title,
            "format": report.format,
            "row_count": report.row_count,
            "download_url": f"/reports/{report.id}/download",
        }
        report_holder["value"] = info
        trace.append({
            "agent": "Report Agent",
            "tool": "generate_report",
            "input": title,
            "summary": f"{report.format.upper()} · {report.row_count} rows",
        })
        return json.dumps(info)

    return [search_documents_tool, query_analytics_tool, generate_report_tool]


def _to_lc_message(m: dict):
    return HumanMessage(m["content"]) if m["role"] == "user" else AIMessage(m["content"])


def _extract_text(message: AIMessage) -> str:
    if isinstance(message.content, str):
        return message.content
    return "".join(block.get("text", "") for block in message.content if isinstance(block, dict) and block.get("type") == "text")


def run_agent(
    query: str,
    *,
    history: list[dict] | None = None,
    conversation_summary: str | None = None,
    preferences: dict | None = None,
) -> AgentRunResult:
    if not settings.anthropic_api_key:
        raise GenerationError("ANTHROPIC_API_KEY is not configured")

    all_sources: list[dict] = []
    report_holder: dict = {"value": None}
    citation_counter = itertools.count(1)
    trace: list[dict] = [{
        "agent": "Planner Agent",
        "tool": "route",
        "input": query,
        "summary": "Understood intent and routed the request",
    }]
    tools = _build_tools(all_sources, citation_counter, report_holder, trace)
    system_prompt = _build_system_prompt(conversation_summary, preferences)

    model = ChatAnthropic(
        model=settings.claude_model_name,
        max_tokens=settings.agent_max_tokens,
        thinking={"type": "adaptive"},
        output_config={"effort": settings.claude_effort},
        api_key=settings.anthropic_api_key,
    ).bind_tools(tools)

    def call_model(state: AgentState) -> dict:
        response = model.invoke([SystemMessage(system_prompt)] + state["messages"])
        usage = getattr(response, "usage_metadata", None)
        if usage:
            record_token_usage(
                "chat_agent", settings.claude_model_name,
                usage.get("input_tokens", 0), usage.get("output_tokens", 0),
            )
        return {"messages": [response]}

    def should_continue(state: AgentState) -> str:
        last = state["messages"][-1]
        return "tools" if getattr(last, "tool_calls", None) else END

    workflow = StateGraph(AgentState)
    workflow.add_node("agent", call_model)
    workflow.add_node("tools", ToolNode(tools))
    workflow.add_edge(START, "agent")
    workflow.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    workflow.add_edge("tools", "agent")
    graph = workflow.compile()

    initial_state: AgentState = {"messages": [_to_lc_message(m) for m in (history or [])] + [HumanMessage(query)]}

    try:
        final_state = graph.invoke(
            initial_state, config={"recursion_limit": settings.agent_max_tool_iterations * 2 + 1}
        )
    except GraphRecursionError:
        return AgentRunResult(reply=INCOMPLETE_ANSWER, sources=all_sources, report=report_holder["value"], trace=trace)
    except Exception as exc:
        raise GenerationError(str(exc)) from exc

    last_message = final_state["messages"][-1]
    if last_message.response_metadata.get("stop_reason") == "refusal":
        return AgentRunResult(reply=REFUSED_ANSWER, trace=trace)

    trace.append({
        "agent": "Response Synthesizer",
        "tool": "synthesize",
        "input": None,
        "summary": "Combined agent output(s) into a grounded, cited reply",
    })
    return AgentRunResult(
        reply=_extract_text(last_message), sources=all_sources, report=report_holder["value"], trace=trace
    )
