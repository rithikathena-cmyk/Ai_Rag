import itertools
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Annotated, Literal, TypedDict

import anthropic
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from app.core.config import settings
from app.db.postgres import new_session
from app.gateway import availability, model_router, retry_handler
from app.gateway.claude_gateway import GenerationError, classify_anthropic_error, claude_gateway
from app.gateway.claude_gateway import _prompt_cache_enabled
from app.gateway.prompt_manager import load_prompt
from app.gateway.schemas import GenerationErrorReason, ModelTier, TokenUsage
from app.gateway.usage_tracker import record_usage
from app.services.agents.project_agent import list_my_projects
from app.services.agents.report_agent import ReportAgentError, generate_report
from app.services.agents.retrieval_agent import search_documents
from app.services.agents.sql_agent import SqlAgentError, run_analytics_query
from app.services.retrieval.query_rewrite import rewrite_query

PLANNER_PROMPT = load_prompt("planner_agent", "v5")
PLANNER_SYSTEM_PROMPT = PLANNER_PROMPT.text

INCOMPLETE_ANSWER = "I wasn't able to finish gathering everything needed to answer this within the allotted steps."
REFUSED_ANSWER = "I'm not able to help with that request."


@dataclass
class AgentRunResult:
    reply: str
    sources: list[dict] = field(default_factory=list)
    report: dict | None = None
    trace: list[dict] = field(default_factory=list)
    # Set only by run_retrieval_fallback() below — None on every real
    # run_agent() success. Mirrors the GenerationError.reason that triggered
    # the fallback (routers/chat.py's except GenerationError as exc: ...
    # reason=exc.reason) so ChatResponse.degraded_reason, and ultimately the
    # chat UI, can show *why* this is a degraded response instead of always
    # claiming "no AI model configured" regardless of the real cause.
    degraded_reason: str | None = None


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


def _maybe_rewrite_query(
    query: str, *, conversation_summary: str | None, request_id: str | None,
    user_id: uuid.UUID | None = None, role: str | None = None, department: str | None = None,
) -> tuple[str, dict | None]:
    """Phase 3B — query rewriting (docs/RAG_RETRIEVAL.md). Returns
    (effective_query, trace_entry_or_None): the query to actually search
    with, and an optional trace entry describing what happened. A no-op
    (returns `query` unchanged, no trace entry) when
    settings.query_rewriting_enabled is off — the default — so this adds
    nothing to the existing trace/behavior unless explicitly turned on.
    Extracted as a plain function (not inlined in the search_documents tool
    closure below) specifically so it's directly unit-testable without going
    through LangChain's tool-invocation machinery."""
    if not settings.query_rewriting_enabled:
        return query, None

    outcome = rewrite_query(
        query, context=conversation_summary, request_id=request_id,
        user_id=user_id, role=role, department=department,
    )
    trace_entry = {
        "agent": "Retrieval Agent",
        "tool": "query_rewrite",
        "input": query,
        "summary": (
            f"rewritten to: {outcome.query!r}" if outcome.rewritten
            else f"kept original query ({outcome.fallback_reason})"
        ),
    }
    return outcome.query, trace_entry


# The two projections of a search_documents()/retrieval_agent.py result dict
# (each item carries BOTH text/display_text and, when present,
# parent_context/parent_context_display — see
# services/guardrails/pii.py::DualText). This is the split point: everything
# built from _llm_source_view() only ever reaches the model inside this
# authorized tool-execution turn; everything built from _public_source_view()
# is the only thing that may reach all_sources -> AgentRunResult.sources ->
# ChatResponse.sources -> routers/chat.py's add_message() (persisted chat
# history) or run_retrieval_fallback()'s direct-to-user response. The two
# helpers are the only code in this module allowed to read a *_display key.
_RAW_ONLY_KEYS = ("display_text", "parent_context_display")
_DISPLAY_TO_PUBLIC_KEY = {"display_text": "text", "parent_context_display": "parent_context"}


def _llm_source_view(item: dict) -> dict:
    """What Claude actually reads (via the search_documents tool's return
    value) to formulate its answer or fill a report — the original,
    authorized content. Safe because this item already passed RBAC/
    department filtering in services/agents/retrieval_agent.py before it
    ever reached here — nothing new is exposed to the model that its role
    wasn't already cleared to retrieve. Never returned to an HTTP caller,
    never persisted — see _public_source_view for the representation that is."""
    return {k: v for k, v in item.items() if k not in _RAW_ONLY_KEYS}


def _public_source_view(item: dict) -> dict:
    """What becomes AgentRunResult.sources -> ChatResponse.sources ->
    add_message()'s persisted chat history, and run_retrieval_fallback()'s
    direct-to-user response when there's no LLM in the loop at all.
    `text`/`parent_context` are overwritten with their `redact_pii()`'d
    *_display counterparts, and the raw fields are popped, not shadowed —
    the returned dict structurally cannot leak the raw value through any
    key, even if a future caller iterates every field."""
    view = dict(item)
    for display_key, public_key in _DISPLAY_TO_PUBLIC_KEY.items():
        if display_key in view:
            view[public_key] = view.pop(display_key)
    return view


def _build_tools(
    all_sources: list[dict], citation_counter: itertools.count, report_holder: dict, trace: list[dict],
    default_top_k: int | None = None, user_id: uuid.UUID | None = None,
    role: str | None = None, department: str | None = None, knowledge_departments: tuple[str, ...] | None = None,
    sql_allowed_tables: frozenset[str] | None = None, allowed_tools: frozenset[str] | None = None,
    tool_call_names: list[str] | None = None, retrieved_doc_ids: list[str] | None = None,
    report_row_filter: dict | None = None, conversation_summary: str | None = None,
    request_id: str | None = None,
):
    """Builds the tools fresh per run_agent() call, filtered to `allowed_tools`
    (services/llm_rbac/tools.py — None means "no filtering", the pre-LLM-RBAC
    default so existing direct callers of run_agent()/this function keep
    working unchanged).

    `all_sources`/`citation_counter`/`report_holder`/`trace`/`tool_call_names`/
    `retrieved_doc_ids` are captured by closure so tool calls can accumulate
    structured metadata (citation numbers, report info, an agent-routing
    trace for the UI, and the audit-log tool/document lists — see
    gateway/usage_tracker.py::record_usage()) that never goes into the
    LLM-visible ToolMessage content. This has to be closure state, not
    LangGraph state updated via Command: LangGraph's InjectedState pattern
    hands a tool a *snapshot* taken before its parallel batch runs, so two
    concurrent search_documents calls would both see the same starting
    citation count and collide. Closure-captured plain Python objects
    (itertools.count(), lists) are shared by reference across ToolNode's
    thread pool — count().__next__() and list.append/extend are atomic under
    the GIL, so this stays correct under concurrent tool calls without
    needing a lock.
    """
    tool_call_names = tool_call_names if tool_call_names is not None else []
    retrieved_doc_ids = retrieved_doc_ids if retrieved_doc_ids is not None else []

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
        tool_call_names.append("search_documents")
        effective_query, rewrite_trace = _maybe_rewrite_query(
            query, conversation_summary=conversation_summary, request_id=request_id,
            user_id=user_id, role=role, department=department,
        )
        if rewrite_trace:
            trace.append(rewrite_trace)
        db = new_session()
        try:
            raw_results = search_documents(
                db, query=effective_query, top_k=top_k or default_top_k, document_type=document_type,
                classification=classification, user_id=user_id,
                role=role, knowledge_departments=knowledge_departments,
            )
        finally:
            db.close()
        # Citation index assigned once, shared by both views below — the
        # number Claude cites in its answer must match the number the user
        # sees next to the (redacted) source it corresponds to.
        numbered = [{"index": next(citation_counter), **r} for r in raw_results]
        all_sources.extend(_public_source_view(item) for item in numbered)
        retrieved_doc_ids.extend(r["document_id"] for r in raw_results)
        trace.append({
            "agent": "Retrieval Agent",
            "tool": "search_documents",
            "input": effective_query,
            "summary": f"{len(numbered)} chunk(s) matched",
        })
        # The model reads the raw/authorized view — see _llm_source_view's
        # docstring for why that's safe. all_sources above already got the
        # redacted view; this return value is never itself returned to an
        # HTTP caller or persisted.
        return json.dumps([_llm_source_view(item) for item in numbered], default=str)

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
        tool_call_names.append("query_analytics")
        db = new_session()
        try:
            try:
                columns, rows = run_analytics_query(db, sql, allowed_tables=sql_allowed_tables)
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
        tool_call_names.append("generate_report")
        db = new_session()
        try:
            try:
                report = generate_report(
                    db, title=title, fmt=format, columns=columns, rows=rows,
                    owner_id=user_id, department=department,
                )
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

    @tool("list_my_projects")
    def list_my_projects_tool(limit: int = 50) -> str:
        """List projects visible to the current user — a Project Manager's own managed/member
        projects, or every project for CEO/Admin. Use this to gather project data before writing a
        project-status/progress/summary/risk/resource-allocation report. Read-only; never call this
        to try to change a project's status, priority, or manager — those require the dedicated
        /projects REST endpoints, not this tool."""
        tool_call_names.append("list_my_projects")
        db = new_session()
        try:
            scope = (report_row_filter or {}).get("scope", "own")
            rows = list_my_projects(db, user_id=user_id, scope=scope, limit=limit)
        finally:
            db.close()
        trace.append({
            "agent": "Project Agent", "tool": "list_my_projects", "input": None,
            "summary": f"{len(rows)} project(s) found",
        })
        return json.dumps(rows, default=str)

    all_tools = {
        "search_documents": search_documents_tool,
        "query_analytics": query_analytics_tool,
        "generate_report": generate_report_tool,
        "list_my_projects": list_my_projects_tool,
    }
    names = all_tools.keys() if allowed_tools is None else [n for n in all_tools if n in allowed_tools]
    return [all_tools[n] for n in names]


def _run_floor_search(
    query: str, all_sources: list[dict], citation_counter: itertools.count, trace: list[dict],
    retrieved_doc_ids: list[str], tool_call_names: list[str], *, top_k: int | None, user_id: uuid.UUID | None,
    role: str | None, knowledge_departments: tuple[str, ...] | None,
) -> list:
    """settings.deterministic_floor_search_enabled's implementation: runs one
    search_documents call using `query` verbatim (the user's own message,
    never an LLM-chosen paraphrase) and returns it as a synthetic
    (AIMessage tool_call, ToolMessage result) pair — LangGraph/Anthropic's
    standard shape for "the model already made this tool call and here's
    what came back". Placed in the conversation before the agent's own first
    real turn, so call_model()'s first LLM call already has this baseline
    context available whether or not the agent decides to search again
    itself. Mutates all_sources/trace/retrieved_doc_ids/tool_call_names
    exactly like search_documents_tool (in _build_tools above) does, so
    citations/audit logging/the UI trace read identically regardless of
    whether a source came from this floor search or a real agent-issued
    tool call."""
    db = new_session()
    try:
        raw_results = search_documents(
            db, query=query, top_k=top_k, user_id=user_id, role=role, knowledge_departments=knowledge_departments,
        )
    finally:
        db.close()
    tool_call_names.append("search_documents")
    numbered = [{"index": next(citation_counter), **r} for r in raw_results]
    all_sources.extend(_public_source_view(item) for item in numbered)
    retrieved_doc_ids.extend(r["document_id"] for r in raw_results)
    trace.append({
        "agent": "Retrieval Agent",
        "tool": "search_documents",
        "input": query,
        "summary": f"{len(numbered)} chunk(s) matched (deterministic floor search on your literal message)",
    })
    call_id = f"floor-search-{uuid.uuid4().hex[:8]}"
    payload = json.dumps([_llm_source_view(item) for item in numbered], default=str)
    return [
        AIMessage(content="", tool_calls=[{"name": "search_documents", "args": {"query": query}, "id": call_id}]),
        ToolMessage(content=payload, tool_call_id=call_id, name="search_documents"),
    ]


def _to_lc_message(m: dict):
    return HumanMessage(m["content"]) if m["role"] == "user" else AIMessage(m["content"])


def _extract_text(message: AIMessage) -> str:
    if isinstance(message.content, str):
        return message.content
    return "".join(block.get("text", "") for block in message.content if isinstance(block, dict) and block.get("type") == "text")


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit].rstrip() + "..."


# Safe, user-facing phrase per GenerationErrorReason — deliberately generic
# (no exception text, no status codes, no provider internals) since this
# reaches the HTTP response and the chat UI directly. The real exception
# detail is only ever logged server-side (see claude_gateway.py's/
# retry_handler.py's own logger calls) — never threaded into this string.
_DEGRADED_REASON_DETAIL: dict[GenerationErrorReason, str] = {
    GenerationErrorReason.NO_API_KEY: "no AI model is configured for this deployment",
    GenerationErrorReason.MODEL_DISABLED: "the AI model has been temporarily disabled by an administrator",
    GenerationErrorReason.AUTH_FAILED: "the AI provider rejected our credentials",
    GenerationErrorReason.PROVIDER_UNAVAILABLE: "the AI provider is temporarily unavailable",
    GenerationErrorReason.PROVIDER_ERROR: "the AI provider could not process this request",
    GenerationErrorReason.CAPACITY: "the AI model is at capacity right now",
    GenerationErrorReason.INTERNAL: "the AI model was unavailable for this reply",
}


def run_retrieval_fallback(
    query: str, db, *, top_k: int | None = None, user_id: uuid.UUID | None = None,
    role: str | None = None, knowledge_departments: tuple[str, ...] | None = None,
    reason: GenerationErrorReason = GenerationErrorReason.INTERNAL,
) -> AgentRunResult:
    """Used when the configured Anthropic model is unavailable — skips
    planning/synthesis entirely and hands back matching search results as
    structured sources (no LLM synthesis, so results are intentionally kept
    small: capped count, truncated chunk text) instead of a hard failure.
    Still governed by LLM RBAC's retrieval filters (`role`/
    `knowledge_departments`) — a degraded response path is not an exemption
    from document access control.

    `reason` is the GenerationErrorReason the caller's GenerationError
    carried (routers/chat.py passes `exc.reason`) — used only to pick an
    accurate, safe phrase from _DEGRADED_REASON_DETAIL above and to set
    AgentRunResult.degraded_reason; every reason still gets the exact same
    degraded behavior (raw search results, no synthesis)."""
    raw_results = search_documents(
        db, query=query, top_k=top_k or settings.fallback_retrieval_top_k, user_id=user_id,
        role=role, knowledge_departments=knowledge_departments,
    )
    char_limit = settings.fallback_chunk_char_limit
    # No LLM synthesis happens on this path at all (that's the whole point
    # of the fallback) — every source below goes straight to the user, so
    # only the public/redacted view is ever appropriate here, same as
    # routers/search.py's direct API response.
    sources = []
    for i, r in enumerate(raw_results, start=1):
        public = _public_source_view(r)
        public["index"] = i
        public["text"] = _truncate(public["text"], char_limit)
        sources.append(public)
    detail = _DEGRADED_REASON_DETAIL.get(reason, _DEGRADED_REASON_DETAIL[GenerationErrorReason.INTERNAL])
    trace = [{
        "agent": "Retrieval Agent",
        "tool": "search_documents",
        "input": query,
        "summary": f"{len(sources)} chunk(s) matched ({detail} — raw results only)",
    }]

    reply = (
        f"Found {len(sources)} matching result(s) for {query!r} ({detail}, so results are unsummarized — see sources)."
        if sources
        else f"No matching content found for {query!r} ({detail})."
    )
    return AgentRunResult(reply=reply, sources=sources, trace=trace, degraded_reason=reason.value)


def run_agent(
    query: str,
    *,
    history: list[dict] | None = None,
    conversation_summary: str | None = None,
    preferences: dict | None = None,
    top_k: int | None = None,
    user_id: uuid.UUID | None = None,
    role: str | None = None,
    department: str | None = None,
    knowledge_departments: tuple[str, ...] | None = None,
    sql_allowed_tables: frozenset[str] | None = None,
    allowed_tools: frozenset[str] | None = None,
    model_tier: ModelTier | None = None,
    action: str | None = None,
    report_row_filter: dict | None = None,
    request_id: str | None = None,
) -> AgentRunResult:
    if not settings.anthropic_api_key:
        raise GenerationError("ANTHROPIC_API_KEY is not configured", reason=GenerationErrorReason.NO_API_KEY)
    if availability.is_disabled():
        raise GenerationError(
            "Model manually disabled (admin testing toggle)", reason=GenerationErrorReason.MODEL_DISABLED
        )

    all_sources: list[dict] = []
    report_holder: dict = {"value": None}
    citation_counter = itertools.count(1)
    tool_call_names: list[str] = []
    retrieved_doc_ids: list[str] = []
    trace: list[dict] = [{
        "agent": "Planner Agent",
        "tool": "route",
        "input": query,
        "summary": "Understood intent and routed the request",
    }]
    tools = _build_tools(
        all_sources, citation_counter, report_holder, trace, default_top_k=top_k, user_id=user_id,
        role=role, department=department, knowledge_departments=knowledge_departments,
        sql_allowed_tables=sql_allowed_tables, allowed_tools=allowed_tools,
        tool_call_names=tool_call_names, retrieved_doc_ids=retrieved_doc_ids,
        report_row_filter=report_row_filter, conversation_summary=conversation_summary,
        request_id=request_id,
    )
    system_prompt = _build_system_prompt(conversation_summary, preferences)
    # Cached per docs/CLAUDE_GATEWAY_MODEL_ROUTING.md's prompt-caching notes:
    # this is the dominant cost path (the system prompt + tools is resent on
    # every tool-loop turn), unlike claude_gateway.generate()'s one-shot
    # callers. Built once per run_agent() call (system_prompt is fixed across
    # the whole tool loop) rather than per call_model() invocation.
    system_message = (
        SystemMessage(content=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}])
        if _prompt_cache_enabled()
        else SystemMessage(system_prompt)
    )

    tier = model_tier or ModelTier.FAST
    tier_config = model_router.resolve(tier)
    model = claude_gateway.get_langchain_model(
        tier=tier, max_tokens=settings.agent_max_tokens, temperature=settings.agent_temperature
    ).bind_tools(tools)

    def call_model(state: AgentState) -> dict:
        call_start = time.perf_counter()
        # LangGraph/LangChain's .invoke() isn't routed through
        # claude_gateway.generate(), so it doesn't get that method's retry
        # wrapping (or process-wide concurrency cap) for free — apply both
        # explicitly here instead, at the one place this graph actually
        # calls the model.
        with claude_gateway.capacity_guard():
            response = retry_handler.call_with_retry(
                lambda: model.invoke([system_message] + state["messages"]), agent_name="planner_agent"
            )
        usage = getattr(response, "usage_metadata", None)
        if usage:
            record_usage(
                # A caller-supplied request_id (currently only
                # services/evaluation/runner.py) makes every gateway call
                # this run_agent() invocation makes — across however many
                # tool-loop turns it takes — share one id in
                # gateway_usage_logs, so token/cost/model can be read back
                # for the whole call. Default (every production chat.py
                # call) is unchanged: a fresh id per turn, exactly as before.
                request_id=request_id or str(uuid.uuid4()),
                agent_name="planner_agent",
                model=tier_config.model,
                tier=tier.value,
                usage=TokenUsage(usage.get("input_tokens", 0), usage.get("output_tokens", 0)),
                latency_ms=(time.perf_counter() - call_start) * 1000,
                user_id=user_id,
                role=role,
                department=department,
                prompt_version=PLANNER_PROMPT.version,
                tool_calls=list(tool_call_names),
                documents_retrieved=list(dict.fromkeys(retrieved_doc_ids)),
                requested_capability=action,
                output_format=report_holder["value"]["format"] if report_holder["value"] else None,
                resource_scope=report_row_filter,
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

    floor_search_messages = []
    if settings.deterministic_floor_search_enabled and (allowed_tools is None or "search_documents" in allowed_tools):
        floor_search_messages = _run_floor_search(
            query, all_sources, citation_counter, trace, retrieved_doc_ids, tool_call_names,
            top_k=top_k, user_id=user_id, role=role, knowledge_departments=knowledge_departments,
        )

    initial_state: AgentState = {
        "messages": [_to_lc_message(m) for m in (history or [])] + [HumanMessage(query)] + floor_search_messages
    }

    try:
        final_state = graph.invoke(
            initial_state, config={"recursion_limit": settings.agent_max_tool_iterations * 2 + 1}
        )
    except GraphRecursionError:
        return AgentRunResult(reply=INCOMPLETE_ANSWER, sources=all_sources, report=report_holder["value"], trace=trace)
    except GenerationError:
        raise  # already classified (e.g. capacity_guard) — don't re-wrap and lose the reason
    except anthropic.APIError as exc:
        # Raised by model.invoke() inside call_model() above, which calls the
        # LangChain client directly (not claude_gateway.generate()) so it
        # doesn't get that method's own classify_anthropic_error() call for
        # free — classify here instead of collapsing into the generic
        # INTERNAL reason below.
        raise GenerationError(str(exc), reason=classify_anthropic_error(exc)) from exc
    except Exception as exc:
        # Anything else — most commonly a bug in one of the tool functions
        # above (e.g. a DB error inside search_documents_tool) — is a real
        # failure but not actually "the model is unavailable"; INTERNAL keeps
        # run_retrieval_fallback()'s message honest instead of claiming a
        # model/provider problem that isn't what happened.
        raise GenerationError(str(exc), reason=GenerationErrorReason.INTERNAL) from exc

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
