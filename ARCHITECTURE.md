# RAG Platform — Architecture & Flow

This document explains how the system is built and how a request actually moves through it, for anyone who didn't write the code.

**Stack**: FastAPI backend + Streamlit frontend + Qdrant (vectors) + Postgres (everything else) + Claude Opus 5 as the only LLM, orchestrated with LangGraph.

---

## 1. Two pipelines, not one

The system has two independent flows that only meet at the vector store:

1. **Ingestion** (runs once per uploaded document) — a fixed, deterministic pipeline. No LLM involved.
2. **Query time** (runs once per chat message) — an **agentic loop**: one LLM repeatedly decides which tool to call until it's ready to answer.

That split matters for the question "is this RAG, is it agentic, is it multi-model?" — the answer is **all three, at different layers**:

| Question | Answer |
|---|---|
| Is it RAG? | Yes — the `search_documents` tool is classic retrieve → augment → generate. |
| Is it agentic? | Yes — retrieval is a tool the planner LLM *chooses* to call (or not), inside a loop, not a fixed pre-step. |
| Is it multi-model? | Yes — 5 specialist models (embedding, classifier, NER, reranker, LLM) share the work. |
| Is it multi-LLM? | **No** — there is exactly one LLM (Claude Opus 5), reused for planning, SQL writing, report copy, and memory summaries. |

---

## 2. Ingestion pipeline

```mermaid
flowchart TD
    A["Upload file"] --> B["Detect format"]
    B --> C["Parse<br/>Docling / tree-sitter / tabular parsers"]
    C --> D["Classify<br/>rules → zero-shot fallback"]
    D --> E["Chunk<br/>strategy set by classification"]
    E --> F["Embed<br/>BGE-M3 · 1024d dense"]
    E --> G["Index sparse terms<br/>BM25 weights"]
    E --> H["Extract entities<br/>spaCy NER"]
    E --> I["Summarize<br/>extractive"]
    F --> J[("Qdrant<br/>dense + sparse vectors")]
    G --> J
    H --> K[("Postgres<br/>docs · chunks · entities")]
    I --> K
    D --> K
```

**Step by step:**

1. **Detect format** — PDF, DOCX, PPTX, HTML, image, Markdown, TXT, XLSX, CSV, JSON, XML, code, SQL.
2. **Parse** — PDF/DOCX/PPTX/HTML/images go through **Docling**; source code through tree-sitter grammars; tabular/structured formats through dedicated parsers.
3. **Classify** — a fast rule-based scorer runs first; if its confidence is below `0.6` it falls back to a zero-shot HF classifier (`deberta-v3-xsmall-zeroshot`). Labels: Research Paper, Company Policy, Manual, SOP, Legal, FAQ, Chat Log, Email, Other.
4. **Chunk** — the strategy depends on the classification: parent/child chunking for Manuals and SOPs, semantic (embedding-similarity) chunking for Research Papers, recursive for Policy/default text, turn-based for chat logs, thread-aware for email. Base chunk size is 400 tokens with 50-token overlap.
5. **Embed** — `BAAI/bge-m3`, 1024-dimensional, normalized.
6. **Sparse index** — BM25 term weights precomputed per chunk (IDF is handled natively by Qdrant at query time).
7. **Entity extraction** — spaCy NER.
8. **Summarize** — extractive summary of each document.
9. **Store** — Postgres gets the document/chunk/entity rows; Qdrant gets the dense + sparse vectors, upserted into a single collection with named vectors (`dense`, `bm25_sparse`).

No LLM call happens anywhere in this pipeline — it's pure ML-model inference plus deterministic logic, so the same document always produces the same result.

---

## 3. Query-time pipeline — the agentic loop

```mermaid
flowchart TD
    U["User message"] --> P{{"Planner agent\nClaude Opus 5 · LangGraph"}}
    P -- "tool: search_documents" --> T1["Retrieval tool"]
    P -- "tool: query_analytics" --> T2["SQL tool"]
    P -- "tool: generate_report" --> T3["Report tool"]
    P -- "no more tool calls" --> ANS["Final answer\n+ inline citations"]

    T1 --> R1["Hybrid search\ndense + BM25, RRF fusion"]
    R1 --> R2["Rerank\nbge-reranker-base"]
    R2 -. "tool result" .-> P

    T2 --> S1["Claude writes SQL"]
    S1 --> S2["Validate + sandbox\nSELECT-only · LIMIT 500"]
    S2 -. "tool result" .-> P

    T3 --> RP1["Claude assembles content"]
    RP1 --> RP2["Write CSV / XLSX / DOCX / PDF"]
    RP2 -. "tool result" .-> P

    ANS --> MEM["Update memory\nsummarize after 12 turns"]
```

**Why this is "agentic" and not just "RAG with extra steps":** the dotted edges loop back into the planner. Every tool result is handed back to the same LLM, which re-reads the conversation and decides — again — whether to call another tool or answer. This can repeat up to 4 times per turn before the graph forces a stop.

**The three tools:**

| Tool | What runs | LLM involved? |
|---|---|---|
| `search_documents` | Metadata filter (Postgres) → embed query (BGE-M3) → hybrid dense+BM25 search with Qdrant-native RRF fusion → rerank with `bge-reranker-base` cross-encoder → numbered citations | No — deterministic retrieval, this is the actual "RAG" part |
| `query_analytics` | Claude writes read-only SQL against an allow-listed set of metadata tables → validated (`sqlparse`, single `SELECT` only, blocklisted DDL/DML) → wrapped in `LIMIT 500` → always rolled back | Yes — to generate the SQL |
| `generate_report` | Claude assembles the content → written to CSV/XLSX/DOCX/PDF via `openpyxl`/`python-docx`/`reportlab` | Yes — to generate the content |

**After the loop ends:** the final answer carries bracketed inline citations `[1]` tied to chunk/document IDs. Conversation memory keeps the last 6 turns verbatim and folds anything older into a running LLM-generated summary once the conversation passes 12 turns.

---

## 4. Where it lives in the code

```
backend/app/services/agents/planner.py       ← LangGraph StateGraph, the loop itself
backend/app/services/agents/retrieval_agent.py ← search_documents tool
backend/app/services/agents/sql_agent.py       ← query_analytics tool
backend/app/services/agents/report_agent.py    ← generate_report tool
backend/app/routers/chat.py                    ← POST /chat, invokes the compiled graph
```

The graph wiring (`planner.py`):

```python
model = ChatAnthropic(...)                     # Claude Opus 5
workflow = StateGraph(AgentState)
workflow.add_node("agent", call_model)         # planner node
workflow.add_node("tools", ToolNode(tools))    # tool-execution node
workflow.add_edge(START, "agent")
workflow.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
workflow.add_edge("tools", "agent")            # the loop-back edge
graph = workflow.compile()
```

---

## 5. Key configuration (`backend/app/core/config.py`)

| Setting | Value |
|---|---|
| Embedding model | `BAAI/bge-m3`, 1024d |
| Reranker | `BAAI/bge-reranker-base` |
| Zero-shot classifier | `deberta-v3-xsmall-zeroshot` |
| LLM | `claude-opus-5` |
| Chunk size / overlap | 400 tokens / 50 tokens |
| Chat retrieval top-k | 8 |
| Search top-k cap | 50 |
| Agent max tool iterations | 4 (recursion limit 9) |
| SQL row limit | 500 |
| Memory: recent turns kept / summary trigger | 6 / 12 turns |

---

## 6. One-paragraph summary for a non-technical audience

Documents get parsed, tagged by type, split into chunks, and turned into two kinds of index (a meaning-based vector index and a keyword index). When someone asks a question, an AI planner decides for itself whether it needs to search the documents, run a data query, or generate a report file — it can do more than one, in any order, and checks its own results before answering. It always answers with citations pointing back to the source, and it remembers the conversation by summarizing older turns instead of forgetting them.
