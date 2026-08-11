# RAG Retrieval

Phase 3 of `docs/ARCHITECTURE_ENHANCEMENT_PLAN.md` — retrieval quality improvements, implemented as
three independent, individually-flagged sub-phases. **Phase 3A (parent-child retrieval)** is
approved and complete. **Phase 3B (query rewriting)** is implemented, experimental, and disabled by
default. **Phase 3C (multi-query retrieval)** is documented as planned but not started, pending
separate approval.

> **Evaluation Architecture Correction (see the section of that name below, after "Phase 3 Evaluation
> Results").** The retrieval-metric comparisons in the Phase 3A/3B sections and the original "Phase 3
> Evaluation Results" section were computed by an evaluation runner that bypassed the production
> retrieval path entirely — it could never have detected either feature's effect, regardless of
> whether the feature worked, was broken, or never ran. That comparison is kept below, unedited, and
> explicitly labeled **Historical / non-representative retrieval comparison**. The evaluation runner
> has since been corrected to call the real production retrieval boundary
> (`retrieval_agent.search_documents()` / `planner._maybe_rewrite_query()`), and re-run for real against
> a live Qdrant instance and both curated eval queries. The dataset was then expanded to 30 verified
> questions across 4 real documents (see "Evaluation Dataset Expansion" below) and the full gate re-run
> again. Verdict for both features is still **INSUFFICIENT EVIDENCE** — dataset size is no longer the
> blocker, but generation quality (Anthropic 401, unchanged) still can't be measured, and no
> reproducible retrieval-ranking effect was found (the small MRR/NDCG deltas that do appear are
> duplicate-document tie noise, traced and explained in that section, not a feature effect).

## Baseline architecture (unchanged)

```
User query → metadata/permission filtering → BGE-M3 embedding → dense + BM25 retrieval
→ RRF fusion → permission/department filtering → BGE reranker → citations
```

Nothing above changed. `services/retrieval/search.py::hybrid_search()` and
`services/reranking/pipeline.py::search_with_reranking()` are untouched.

## Phase 3A — Parent-child retrieval

### The gap

`services/chunking/parent_child.py` has always produced both a "parent" chunk (a full section, up
to `chunk_size_tokens_parent` tokens) and "child" chunks split from it (down to `chunk_size_tokens`
tokens) when a section is too large — both independently embedded and stored. Each child row's
`parent_chunk_id` (`ChunkModel`) was stored end-to-end (Postgres, the Qdrant payload, `SearchHit`,
the `/search` API response) but never *dereferenced*: retrieval always returned only the matched
chunk's own `text`. For a child chunk whose own text is short/terse (e.g. a heading fragment split
off because the parent overflowed the token budget), the LLM would see too little context to answer
well — even though the fuller context existed one hop away, in Postgres, the whole time.

### What changed

**`services/retrieval/search.py::fetch_parent_context()`** (new) — given a list of already-
retrieved, already-reranked `SearchHit`s, returns `{chunk_id: parent_text}` for hits that have a
`parent_chunk_id`, reading straight from `ChunkModel` (no new data model, no second parent-child
representation). Three rules, all directly reusable/testable in isolation:

- **Dedup**: a parent is fetched once and attached only to the first (highest-scoring) hit that
  references it — two hits sharing a parent never duplicate that parent's text in the result set.
- **Bounded**: `max_expansions` caps how many distinct hits get a parent lookup per query;
  `max_chars` truncates an individual parent's text. Both configurable (see below).
- **Missing parent**: if a referenced parent row doesn't exist (e.g. deleted), that hit is silently
  skipped — its own `text` still stands on its own; nothing errors.

**`services/agents/retrieval_agent.py::search_documents()`** — the chat/planner tool path only
(see "Why not `/search` too" below) — calls `fetch_parent_context()` when
`settings.parent_child_retrieval_enabled` is on, and attaches the result as a new, optional
`parent_context` key alongside each hit's existing `text`. **`text` never changes** — the citation a
hit gets numbered with in `services/agents/planner.py` still points at exactly the precisely-matched
child text; `parent_context` is additional background the LLM may use to answer more completely, not
a second citable source. `backend/prompts/planner_agent_v2.yaml` tells Claude exactly that.

**Why not `/search` too**: the standalone `POST /search` REST endpoint (and the frontend Search
page) returns raw ranked hits for a human to read directly — there's no LLM answer being generated
from it, so parent-context enrichment wouldn't serve this phase's stated goal ("improve answer
quality"). Scoped to the path that actually generates an answer.

### Authorization

No new permission check was added — none is needed. `resolve_document_ids()` (unchanged) computes
the caller's permitted document-ID allowlist and pushes it into the Qdrant query as a pre-filter
*before* any hit exists; every `SearchHit` `fetch_parent_context()` ever sees already passed that
gate. A parent chunk is always a row in the **same document** as its child (`parent_child.py` builds
both from one document in one pass — a child's `parent_index` can only point at a parent chunk
produced in that same call). Fetching a parent by ID from that same document therefore reads more
text from a document the caller was already authorized to retrieve from; it cannot expand access to
anything new. See `fetch_parent_context()`'s docstring for the full argument.

### Configuration

```
parent_child_retrieval_enabled: bool = False   # off by default — baseline stays available for A/B
parent_context_max_expansions: int = 5         # distinct hits per query that get a parent lookup
parent_context_max_chars: int = 2000           # per-parent truncation
```

Standard `backend/app/core/config.py` `Settings` fields — same pattern as every other feature flag
in this codebase (`guardrails_enabled`, `rate_limit_enabled`, etc.).

### Tests

22 new tests (`backend/tests/retrieval/test_parent_context.py`,
`backend/tests/test_retrieval_agent.py`): child→parent resolution, missing parent, duplicate-parent
dedup, `max_expansions` limiting, `max_chars` truncation (including the not-truncated case),
zero-expansions short-circuit (asserts no DB query happens at all), parent_context attached only
when the flag is on and a parent exists, citation `text` unchanged when `parent_context` is present,
and disabled-mode output-shape parity with the pre-Phase-3A contract. Full suite:
**290/290 passing**, zero regressions.

### Evaluation — real results, with an honest gap

Ran against this environment's actual data: a real Qdrant instance (`storage/collections/document_chunks`,
16 real points) and real Postgres rows, not a mock. A new eval query was curated (the one pre-existing
`eval_queries` row had a stale `expected_chunk_ids` reference to a chunk that no longer exists, so it
couldn't be used):

> *"How is in-home service handled for this washing machine, and what does it cost?"*
> `expected_chunk_ids`: the two `WM_1.pdf` child chunks under the "In-Home Service" heading — one of
> which is a 13-token heading-only fragment, deliberately chosen as a strong test case for whether
> parent context helps.

Two real runs, same query, only `parent_child_retrieval_enabled` toggled:

| Metric | Baseline (off) | Phase 3A (on) |
|---|---|---|
| Recall@5 | 1.0 | 1.0 |
| Precision@5 | 0.4 | 0.4 |
| MRR | 0.333 | 0.333 |
| NDCG@5 | 0.544 | 0.544 |
| Retrieved chunk IDs | identical set, identical order | identical set, identical order |

**Retrieval-ranking metrics are unaffected** — measured, but see the important correction below before
treating this as confirmation of anything about Phase 3A's code.

> **Correction (added during the Phase 3 Evaluation Gate, see "Phase 3 Evaluation Results" below):**
> the claim above is real but weaker than it originally read. `services/evaluation/runner.py::run_evaluation()`
> computes Recall/Precision/MRR/NDCG from a **direct call to `search_with_reranking()`**, which never
> passes through `retrieval_agent.search_documents()` — the only place `fetch_parent_context()` (Phase
> 3A's actual code) executes. That call path is bypassed **unconditionally**, regardless of
> `parent_child_retrieval_enabled`. So "identical retrieval metrics baseline vs. Phase 3A" is true, but
> it was never capable of being anything *other* than identical — the metric computation doesn't
> exercise Phase 3A's code at all. It does **not** demonstrate that parent-context enrichment leaves
> ranking unaffected; it demonstrates that this particular evaluation path can't see Phase 3A either
> way. The code-level guarantee (parent context is attached as a separate `parent_context` key without
> ever modifying a hit's `text`, so it structurally cannot change what was matched or its score) still
> holds and is what the 22 unit/integration tests actually verify directly — that guarantee just isn't
> what these two eval runs were measuring.

**Generation-quality and cost metrics could not be measured**: this environment's `ANTHROPIC_API_KEY`
is rejected by Anthropic's API (`401 authentication_error`, confirmed with a direct minimal SDK call,
not a bug in this phase's code — see the implementation report for detail). Both runs' `generated_answer`,
`groundedness`, `faithfulness`, `hallucination_rate`, `citation_accuracy`, `answer_relevance`,
`tokens_input/output`, and `cost_usd` are `null`/failure placeholders as a result. **No number for
any of these is fabricated or estimated here** — they are reported as unmeasured, per instruction.

**What would be expected, stated as a hypothesis, not a result**: attaching parent context increases
`tokens_input` (more context sent to Claude) and should, if the underlying hypothesis is right,
increase `groundedness`/`faithfulness`/`answer_relevance` for terse-child-chunk cases like the one
above — but this is exactly the claim the evaluation framework exists to verify objectively, and it
remains unverified pending a working API key.

## Phase 3B — Query rewriting

Experimental, disabled by default (`query_rewriting_enabled: bool = False`).

### The idea

A conversational follow-up like *"What happened to Line 3?"* right after discussing March
production is a weak retrieval query on its own — the actual topic ("production issues") is implicit
in the earlier turn, not in the message itself. Query rewriting asks Claude, once, to turn the
message plus recent conversation context into a better-formed search query, used only for retrieval.

### What changed

**`services/retrieval/query_rewrite.py::rewrite_query(query, *, context=None, request_id=None) ->
RewriteOutcome`** (new) — a single `claude_gateway.generate()` call (never a second Anthropic
client) at a configurable tier (`query_rewrite_tier`, default `fast`), returning a `RewriteOutcome`
dataclass: the query to actually use (`query`), whether a rewrite took effect (`rewritten`), both the
original and raw-model-output query for observability, and latency/tokens/cost.

**Reliability — every one of these falls back to the original query, unchanged, never raising**:
gateway error, an unexpected exception, a model refusal, malformed/non-JSON output, an empty
rewrite, and a rewrite exceeding `query_rewrite_max_chars`. **Timeout** is enforced independently of
`llm.yaml`'s global request timeout (the Anthropic client's timeout is fixed once at construction,
shared by every caller — not overridable per call without modifying `claude_gateway.py`, which this
phase does not do): the gateway call runs on a small reused `ThreadPoolExecutor`, bounded by
`future.result(timeout=query_rewrite_timeout_seconds)`. A timed-out call isn't truly cancelled (the
underlying HTTP request isn't preemptible) — the caller simply stops waiting and falls back; the
abandoned call finishes in the background without blocking the request that gave up on it.

**`services/agents/planner.py::_maybe_rewrite_query()`** (new, plain function — not inlined in the
tool closure, specifically so it's unit-testable without going through LangChain's tool-invocation
machinery) — a no-op returning the query unchanged when `query_rewriting_enabled` is off (the
default). When on, calls `rewrite_query()` with the conversation's running summary as context (the
same `conversation_summary` already computed for the system prompt — reused, not a second
context-selection mechanism) and the run's shared `request_id` (see Phase 2's evaluation
`request_id` threading — reused here too), and returns a trace entry describing what happened.

**`search_documents` tool** (`planner.py`) calls `_maybe_rewrite_query()` first, then passes
whichever query it returned into `retrieval_agent.search_documents()` — **unchanged** otherwise.
Claude's own conversation state (`state["messages"]`, and therefore the question it ultimately
answers from) is never touched by the rewrite; only the internal string handed to retrieval changes.
`backend/prompts/query_rewrite_agent_v1.yaml` — a new, dedicated prompt (not a reuse/edit of the
planner or judge prompts) — instructs Claude explicitly: preserve intent/entities/dates/identifiers,
never invent facts, never mention or alter roles/departments/permissions/access levels, return only
a JSON `{"rewritten_query": ...}`.

### Security

`rewrite_query()`'s signature is `(query, context, request_id) -> RewriteOutcome` — structurally
incapable of seeing or returning a role, department, or permission value, so it cannot expand,
narrow, or otherwise touch authorization even in principle. Every RBAC/department/metadata parameter
`retrieval_agent.search_documents()` receives (`role`, `knowledge_departments`, `user_id`,
`document_type`, `classification`) is threaded through from `planner.py` completely independently of
the rewrite step, before and after it — verified directly:
`tests/test_planner_query_rewrite.py::test_rbac_and_department_filters_unaffected_by_rewriting` and
`::test_metadata_filters_preserved_when_rewrite_falls_back` assert every one of those parameters
reaches retrieval unchanged whether the rewrite succeeds, fails, or never runs.

### Observability

No new tracking table. `claude_gateway.generate()` already auto-records every call it makes to
`gateway_usage_logs` (`agent_name="query_rewrite"`, tokens, cost, latency, model) — visible in the
existing Admin → Gateway & Cost page grouped by agent, same as every other gateway caller. Original
query, rewritten query (or fallback reason), and rewrite latency are recorded via the existing
planner `trace` mechanism — the same list already used for every other tool's activity, surfaced to
the frontend chat trace — rather than a new logging path. A rewrite failure only ever produces a
short reason string (`"gateway error: ..."`, `"timed out after Ns"`, `"malformed JSON response"`,
etc.) in the trace, not raw model output on failure paths, keeping logged content minimal.

### Configuration

```
query_rewriting_enabled: bool = False       # experimental — off by default
query_rewrite_max_chars: int = 300          # rewrite rejected (falls back) if longer than this
query_rewrite_timeout_seconds: float = 5.0
query_rewrite_tier: str = "fast"            # any ModelTier value; invalid values fall back to "fast"
```

### Tests

44 new tests: `tests/retrieval/test_query_rewrite.py` (22 — successful rewrite, original-query
preservation, gateway-error/unexpected-exception/timeout/malformed-JSON/empty-rewrite/refusal/
over-length fallback, conversation-context inclusion, request_id threading, tier selection and
invalid-tier fallback) and `tests/test_planner_query_rewrite.py` (22 — `_maybe_rewrite_query()`
disabled/enabled/fallback behavior and context/request_id threading, plus full tool-wiring tests:
disabled mode reaches retrieval with the original query, enabled mode reaches retrieval with the
rewritten query, RBAC/department/metadata filters unaffected by rewriting in both the success and
fallback cases). Full suite: **312/312 passing**, zero regressions.

### Evaluation — retrieval-only comparison completed for real; generation comparison still blocked

Query rewriting itself requires a working Claude call — unlike Phase 3A, there is no meaningful
"rewriting on vs. off" retrieval-only comparison to run in an environment where every rewrite attempt
fails, because both conditions collapse to "search with the original query." What *was* run for
real, against this environment's live Qdrant/Postgres and the same curated eval query from Phase 3A:

| Condition | Recall@5 | Precision@5 | MRR | NDCG@5 | Retrieved chunks |
|---|---|---|---|---|---|
| Phase 3A baseline (rewriting off) | 1.0 | 0.4 | 0.333 | 0.544 | identical set/order |
| `query_rewriting_enabled=True`, real (invalid) API key | 1.0 | 0.4 | 0.333 | 0.544 | identical set/order |

This shows retrieval did not fail and produced results identical to baseline with the flag on. However
see the correction below — this does **not** establish what it was originally written to establish.

> **Correction (added during the Phase 3 Evaluation Gate, see "Phase 3 Evaluation Results" below):**
> the claim that this "confirms the fallback path works under a real failure" overstated what actually
> ran. `run_evaluation()`'s retrieval metrics come from a direct `search_with_reranking()` call that
> never reaches `planner.py`'s `search_documents` tool — the only place `_maybe_rewrite_query()`/
> `rewrite_query()` execute — so, as with Phase 3A, this comparison could never have detected a
> difference regardless of whether rewriting worked, failed, or was never attempted.
>
> More importantly: in this environment, `run_agent()`'s **first** LLM call (`call_model`, the
> planner's own routing decision) fails immediately on the invalid API key — before Claude ever
> decides whether to call `search_documents` at all. That means the `search_documents` tool, and
> therefore `_maybe_rewrite_query()`/`rewrite_query()`, **never actually executed** during any real
> evaluation run performed this session, with either flag value. The "fallback under a real failure"
> that *was* genuinely exercised is `run_agent()`'s own top-level `except Exception` → `GenerationError`
> handling (pre-existing, unrelated to Phase 3B) — not `rewrite_query()`'s internal fallback logic
> (gateway-error/timeout/malformed-JSON/refusal/over-length → original query), which was never reached.
> That internal fallback logic remains verified only at the unit-test level (22 tests in
> `test_query_rewrite.py` directly call `rewrite_query()` with mocked gateway failures) — which is real
> and valid evidence, just not live-environment evidence.

**Not measured, and not claimed**: whether a *successful* rewrite improves retrieval or generation
quality, and — per the correction above — whether `rewrite_query()`'s own fallback logic behaves
correctly when actually invoked in this live environment (as opposed to under mocks). Both require a
working API key to observe for real. Per instruction, no number for either is estimated or invented —
both are reported as unavailable/unverified-in-this-environment.

### Recommendation

Keep `query_rewriting_enabled=False` in any shared/deployed environment. The implementation is
tested at the unit and integration level (fallback safety confirmed under mocked failures — see the
correction above for why this hasn't yet been confirmed live), but its actual value (does a successful
rewrite retrieve better results?) is completely unverified, and it adds latency/cost to every
`search_documents` call when on. Re-evaluate once a working API key is available, using the same
before/after methodology as Phase 3A.

## Phase 3 Evaluation Results

> **Historical / non-representative retrieval comparison.** Every retrieval-ranking number in this
> section (Recall/Precision/MRR/NDCG, "identical across conditions") was computed by
> `run_evaluation()` calling `search_with_reranking()` directly — a call path that structurally never
> reaches `retrieval_agent.search_documents()` (Phase 3A's `fetch_parent_context()`) or
> `planner.py`'s `search_documents` tool (Phase 3B's `_maybe_rewrite_query()`). It could not have shown
> a difference between conditions no matter what Phase 3A/3B's code actually did. Kept below verbatim,
> for the record — **do not use it to judge Phase 3A/3B's retrieval effect.** The corrected
> methodology and current status are in "Evaluation Architecture Correction" further down.

A controlled experiment gate (`services/evaluation/experiments.py`, `POST /eval/experiments/run`,
Evaluation page → Experiments tab) was built to compare Baseline (both flags off) against Parent-Child
(`parent_child_retrieval_enabled=True`) and Query Rewrite (`query_rewriting_enabled=True`) under
otherwise identical conditions, before deciding whether Phase 3C should start. This section reports
what that gate actually found, running for real against this environment's live Qdrant/Postgres data —
including an important limitation discovered while building it, which changes how the Phase 3A/3B
sections above should be read.

### Method

Each condition runs the exact same eval dataset through the exact same `run_evaluation()` function
Phase 2 built, with only `parent_child_retrieval_enabled`/`query_rewriting_enabled` toggled via a
scoped context manager (`_temporary_flags()`) that restores the prior value afterward — never a
permanent settings change. Held constant across every condition: dataset/questions/expected chunk
IDs, model tier (`fast`), judge prompt version, retrieval top-K (10), reranker, permission context
(unrestricted — `run_evaluation()` never passes a role/department), and database state (same live
Postgres/Qdrant, no writes between conditions other than the tagged `eval_runs` rows themselves).

**Unavoidable difference, disclosed rather than hidden**: `retrieval_latency_ms`/`generation_latency_ms`
are wall-clock measurements taken in a shared process with a cold BGE-M3/reranker model cache — the
condition that happens to run first in a fresh process pays a one-time model-load cost the later
conditions don't. Three full gate runs were performed while building this gate; the numbers below are
from the final run (`run_gate()` called directly, `k=10`, all three of baseline/parent-child/query-
rewrite plus combined). Baseline ran first in that process with no warm-up query beforehand, so its
latency figures include a one-time model-load cost the later conditions in the same run did not pay —
this is called out explicitly wherever it affects a number below, rather than presented as a real
effect of either feature.

### Dataset

2 curated eval queries (`eval_queries` table) — both `WM_1.pdf` (`parent_child` chunking strategy),
one deliberately chosen because its top-ranked expected chunk is a short, heading-only fragment (the
intended test case for parent-context enrichment):

| Query | Expected chunks |
|---|---|
| "How is in-home service handled for this washing machine, and what does it cost?" | 2 child chunks under "In-Home Service" |
| "What damages are excluded from the in-home service coverage?" | 1 child chunk |

Per instruction, a dataset this small cannot support a claim of statistical significance — every
number below is directional, from real data, not a simulated or larger dataset.

### Results — retrieval metrics (measured, but see the limitation below)

Averaged across both queries, `k=10`, from `run_gate()`'s actual output:

| Metric | Baseline | Parent-Child | Query Rewrite | Combined |
|---|---|---|---|---|
| Recall@10 | 0.500 | 0.500 | 0.500 | 0.500 |
| Precision@10 | 0.100 | 0.100 | 0.100 | 0.100 |
| MRR | 0.1667 | 0.1667 | 0.1667 | 0.1667 |
| NDCG@10 | 0.2719 | 0.2719 | 0.2719 | 0.2719 |
| Retrieved chunk IDs (per query) | — | identical set/order to baseline | identical set/order to baseline | identical set/order to baseline |

Every retrieval-ranking metric is bit-for-bit identical across all four conditions, for both queries,
in every gate run performed this session (delta = 0.0 exactly, per `compare()`'s output).

**Limitation — this result is not meaningful evidence about Phase 3A or 3B, and should not be read as
"confirmed safe."** `run_evaluation()` computes these four metrics from a direct call to
`services/reranking/pipeline.py::search_with_reranking()`, which calls `hybrid_search()` directly and
never passes through `services/agents/retrieval_agent.py::search_documents()` — the only place
`fetch_parent_context()` (Phase 3A) executes — nor through `services/agents/planner.py`'s
`search_documents` tool — the only place `_maybe_rewrite_query()` (Phase 3B) executes. This is
unconditional: `search_with_reranking()` never reads `parent_child_retrieval_enabled` or
`query_rewriting_enabled` at all. So these four metrics were **structurally guaranteed** to be
identical across every condition regardless of whether Phase 3A/3B's code had any effect, a bug, or
even ran at all. This is a real, pre-existing characteristic of the Phase 2 evaluation runner
(unrelated to and not introduced by this gate), discovered while building this comparison — reported
here rather than silently left implicit, and **not fixed as part of this task**, since changing
`run_evaluation()`'s retrieval-metrics computation is outside this task's scope ("an evaluation gate,
not another architecture expansion").

### Results — generation-quality, cost, and token metrics: unavailable

**Generation evaluation unavailable: Anthropic authentication failed.** All 16 tagged evaluation runs
across both gate executions hit the same pre-existing, environment-level condition documented in
Phase 3A/3B above (`ANTHROPIC_API_KEY` rejected with HTTP 401 `authentication_error`, confirmed with a
direct SDK call outside this task's code — not modified, worked around, or bypassed here, per
instruction). `groundedness`, `faithfulness`, `hallucination_rate`, `citation_accuracy`,
`answer_relevance`, `tokens_input`, `tokens_output`, `cost_usd`, and `model` are `null` on every run —
reported as unavailable, never coerced to 0 or estimated (verified directly by
`services/evaluation/experiments.py::compare()`'s handling and its dedicated tests).

**A second, deeper reason generation metrics couldn't be meaningful here even with a valid key**:
`run_agent()`'s LangGraph loop starts with the planner's own top-level Claude call (`call_model`) —
Claude's *first* decision, before any tool has been invoked. Because that first call itself fails on
the invalid key, the graph never reaches a point where Claude could decide to call the
`search_documents` tool. That means `_maybe_rewrite_query()` and, downstream of it,
`retrieval_agent.search_documents()`/`fetch_parent_context()` **never executed at all** during any of
this session's real evaluation runs, under any flag combination. The "fallback confirmed under a real
failure" language in the Phase 3B section above referred to `run_agent()`'s own outer
`GenerationError` handling (pre-existing, not part of Phase 3B), not to `rewrite_query()`'s internal
fallback logic — that internal logic remains verified only by its 22 mocked unit tests, not by any
live run performed this session. Had the key been valid, generation-stage metrics (unlike the
retrieval-ranking metrics above) *would* have been meaningful, since `run_agent()`'s tool loop is the
one path that actually exercises both features — this is specifically what's blocked, and specifically
what should be re-run once a working key is available.

### Latency and cost differences

Not attributable to either feature, and this run's raw numbers make that especially visible:

| Metric (avg) | Baseline | Parent-Child | Query Rewrite |
|---|---|---|---|
| Retrieval latency | 34,653.8 ms | 20,337.3 ms (−41.3%) | 20,964.5 ms (−39.5%) |
| Generation latency | 896.1 ms | 623.1 ms (−30.5%) | 648.5 ms (−27.6%) |
| Total latency | 35,549.9 ms | 20,960.4 ms (−41.0%) | 21,613.0 ms (−39.2%) |

Both non-baseline conditions look ~40% "faster" — this is the cold-start artifact described in
**Method** above, not a real effect: baseline ran first in this process and paid a one-time
BGE-M3/reranker model-load cost the later conditions, running in the same warm process, did not.
`retrieval_latency_ms` comes from the same bypassing `search_with_reranking()` call for every
condition regardless of flags; `generation_latency_ms` measures only how long the failed top-level
Claude call plus retry policy took, not real generation work, since the tool loop was never reached.
No token or dollar-cost figure could be computed for any condition. **No latency or cost number in
this table should be read as caused by Phase 3A or 3B.**

### Per-question paired comparison

With only 2 queries, `paired_comparison()` (paired by `eval_query_id`; "improved"/"degraded" here mean
only "numerically higher/lower than baseline for that question" — direction-agnostic, not a
value judgment, per the function's own docstring) is reported for completeness but explicitly **not**
treated as evidence: every retrieval metric was `unchanged` for both features (0 improved, 0 degraded,
2 unchanged — exact equality per question, not just on average), and every generation-quality metric
was `skipped_unavailable` (2/2) for both Parent-Child and Query Rewrite. The latency metrics show
`degraded` (numerically lower, i.e. "faster") on both questions for Parent-Child, and a mixed 1/1 split
for Query Rewrite — consistent with cold-start noise, not a signal.

### Recommendation

| Feature | Verdict | Reasoning |
|---|---|---|
| Parent-child retrieval (`parent_child_retrieval_enabled`) | **INSUFFICIENT EVIDENCE** | Dataset (2 queries) is below the 5-query minimum this gate requires for a verdict, and no generation-quality metric was measurable (Anthropic auth failure) — and even if it were, the code path this specific feature touches was never exercised live this session. |
| Query rewriting (`query_rewriting_enabled`) | **INSUFFICIENT EVIDENCE** | Same two blockers as above, plus the feature's own code never executed during any real run this session (see the second reason above) — there is no live signal of any kind for or against it, only mocked unit-test coverage. |
| Combined (both on) | No independent recommendation computed, by design — a combined result cannot attribute effect to either feature individually. Combined runs completed without error and are recorded for reference. |

Neither feature has evidence to justify enabling by default. Neither has evidence of harm either — the
honest state is "unmeasured," not "safe" or "unsafe." **Recommendation: keep both
`parent_child_retrieval_enabled=False` and `query_rewriting_enabled=False`, and do not proceed to Phase
3C** until at minimum (a) a valid `ANTHROPIC_API_KEY` allows `run_agent()`'s tool loop to actually run,
and, ideally, (b) `run_evaluation()`'s retrieval-metrics computation is changed to go through
`retrieval_agent.search_documents()` (the real chat/planner retrieval path) instead of a direct
`search_with_reranking()` call, so that Recall/Precision/MRR/NDCG can actually reflect what Phase 3A
does. (b) is a suggested follow-up, not performed here — it would mean modifying Phase 2's evaluation
runner, out of scope for an evaluation gate.

### What this gate did validate

Despite the above, this work was not wasted: the gate infrastructure itself — flag isolation with
guaranteed restoration (including on exception), zero permanent mutation of application configuration,
per-run tagging (`experiment_label`), average and per-question paired comparison math, unavailable-vs-zero
handling, 401 detection, and dataset-size/evidence-gated recommendation logic — is implemented, covered
by 29 dedicated tests, and now confirmed to run correctly end-to-end against real data (two full gate
runs, 16 tagged rows persisted, zero crashes). The gate is ready to produce a meaningful verdict the
moment its two current blockers (API key, dataset size) are resolved — nothing about *this* result is
inconclusive due to a bug in the gate itself.

## Evaluation Architecture Correction

Corrects the limitation documented (but deliberately not fixed) in the "Phase 3 Evaluation Results"
section above. Scope: fix what the evaluation runner measures, so it reflects the real production
retrieval path — not another retrieval feature, not Phase 3C, not a change to Phase 3A/3B's own logic.

### The bug

`services/evaluation/runner.py::run_evaluation()` computed Recall@K/Precision@K/MRR/NDCG@K from:

```python
hits, _reranked = search_with_reranking(db, query=eval_query.query, mode="hybrid", top_k=k)
```

— a direct call to `services/reranking/pipeline.py::search_with_reranking()`, which calls
`hybrid_search()` and returns. This path:

- **Never reaches `retrieval_agent.search_documents()`**, the only place `fetch_parent_context()`
  (Phase 3A) executes.
- **Never reaches `planner.py`'s `search_documents` tool**, the only place `_maybe_rewrite_query()`
  (Phase 3B) executes.
- Reads `eval_query.query` directly — even with `query_rewriting_enabled=True`, the string handed to
  retrieval was always the original, unrewritten query.

This was unconditional: `search_with_reranking()` never reads `parent_child_retrieval_enabled` or
`query_rewriting_enabled` at all. Retrieval metrics were therefore **structurally guaranteed** to be
identical across every flag combination, regardless of what Phase 3A/3B's code did, had a bug, or
never ran — which is exactly what the historical comparison above shows, and exactly why it can't be
read as evidence either way.

Separately, `run_evaluation()` also called `run_agent()` for the generated answer — and *that* call
already went through the real planner tool loop (rewrite + parent context both reachable from inside
it). But nothing about the generated answer's retrieval calls fed back into the Recall/Precision/MRR/
NDCG numbers; those two halves of one evaluation run were measuring two different retrieval paths
without anyone being told so.

### The fix

`run_evaluation()`'s retrieval stage now calls the same two functions
`services/agents/planner.py`'s `search_documents` tool calls, in the same order, with no
reimplementation of either:

```python
effective_query, rewrite_trace = _maybe_rewrite_query(
    eval_query.query, conversation_summary=None, request_id=eval_request_id
)
raw_results = search_documents(db, query=effective_query, top_k=k)
```

`_maybe_rewrite_query` is imported from `services/agents/planner.py`; `search_documents` from
`services/agents/retrieval_agent.py` — the exact objects, not copies (`runner.search_documents is
retrieval_agent.search_documents` and `runner._maybe_rewrite_query is planner._maybe_rewrite_query`
are both asserted directly by a dedicated regression test,
`tests/evaluation/test_evaluation_integration.py::test_runner_calls_the_exact_production_functions_not_copies`).
`search_documents()` internally calls `fetch_parent_context()` when `parent_child_retrieval_enabled`
is on — nothing about Phase 3A's or Phase 3B's own code changed. `retrieved_chunk_ids` (and therefore
Recall/Precision/MRR/NDCG) now come from `search_documents()`'s actual output, so a real change to
ranking, filtering, or the query string reaches the metrics that grade it.

Corrected call graph:

```
Evaluation question
        ↓
_maybe_rewrite_query()          (Phase 3B — planner.py, real function, real trace)
        ↓
search_documents()              (retrieval_agent.py)
        ↓
search_with_reranking()          → hybrid_search() → dense+BM25 → RRF → permission/dept filter → rerank
        ↓
fetch_parent_context()          (Phase 3A — search.py, only when parent_child_retrieval_enabled)
        ↓
Citation-ready results → Recall/Precision/MRR/NDCG computed from these ids, not a separate call
```

Generation metrics (citation accuracy, answer relevance, faithfulness, hallucination, latency,
tokens, cost) are untouched — still `run_agent()` → `judge_answer()`, exactly as Phase 2 built them.
Retrieval and generation questions stay answered independently, per instruction: a retrieval-metric
change reflects retrieval quality only; a generation-metric change reflects answer quality only. They
are deliberately not cross-derived from each other — including the fact that `run_agent()`'s own tool
loop may call `search_documents` with its own independently-rewritten query if the agent chooses to;
the evaluation's retrieval metrics measure "what would a single `search_documents` call return for
this question right now," not "what did this specific generated answer's tool calls happen to use."

### Evaluation trace — proof of what actually ran

Every `EvalRunModel` row now carries a `retrieval_trace` JSONB column (additive, nullable — old rows
are simply `null`), built entirely from real values returned by the real calls above, never
reconstructed after the fact:

```json
{
  "original_query": "...",
  "effective_query": "...",
  "query_rewriting_enabled": true,
  "rewrite_trace": {"agent": "Retrieval Agent", "tool": "query_rewrite", "input": "...", "summary": "rewritten to: '...'"},
  "parent_child_retrieval_enabled": true,
  "parent_context_chunk_ids": ["<chunk-id-that-actually-got-a-parent-expansion>"],
  "retrieved_chunk_ids": ["...", "..."],
  "retrieval_latency_ms": 42.1,
  "generation_available": true
}
```

`rewrite_trace` is exactly the dict `_maybe_rewrite_query()` itself builds — `null` when query
rewriting is off, otherwise the real trace entry (rewritten text or fallback reason). A non-empty
`parent_context_chunk_ids` is only possible when `fetch_parent_context()` actually attached parent
text to a hit. This answers, per run, "did this evaluation actually execute Phase 3A or Phase 3B?"
directly from evidence, without re-deriving it from settings alone (settings could be on while the
code path is unreachable for other reasons — this trace catches that).

A new `EvaluationRetrievalError` (raised by `run_evaluation()`) is used when the retrieval stage
itself cannot run at all (Qdrant or PostgreSQL unreachable) — deliberately distinct from a legitimate
zero-hit retrieval, which still produces measurable (zero-valued) metrics. `routers/evaluation.py`
turns this into a `503 retrieval_unavailable` response instead of an opaque 500, for both
`POST /eval/queries/{id}/run` and `POST /eval/experiments/run`.

### Proof Phase 3A and 3B actually execute during evaluation

`tests/evaluation/test_evaluation_integration.py` (13 tests) leaves the real orchestration in place
and mocks only the true external I/O boundaries underneath it:

```
runner.run_evaluation()
  → planner._maybe_rewrite_query()          [REAL]
       → query_rewrite.rewrite_query()       [REAL]
            → claude_gateway.generate()       ← mocked (Anthropic HTTP)
  → retrieval_agent.search_documents()       [REAL]
       → search_with_reranking()              ← mocked (Qdrant / BGE-M3)
       → fetch_parent_context()               [REAL, against a fake DB session]
```

Covers, each executing the real function (not a stand-in for it): baseline (original query, no
expansion), parent-child (real `fetch_parent_context()` attaches parent text, proven by
`parent_context_chunk_ids` being non-empty), query-rewrite (real `rewrite_query()` JSON parse and
`RewriteOutcome` construction; the rewritten string is proven to be what actually reaches
`search_with_reranking()`), combined (both at once), and all four via `experiments.run_experiment()`
directly (proving `_temporary_flags()` actually changes what the real chain does, not just what
`settings` says). Failure paths, run through the real fallback logic rather than re-stubbed: a real
`GenerationError` from the mocked Anthropic boundary falls back to the original query
(`rewrite_query()`'s own except-branch, unmocked); a real `ThreadPoolExecutor` timeout (shortened
`query_rewrite_timeout_seconds`, a genuinely slow mocked call) falls back the same way; a missing
parent row lets retrieval succeed with the child's own text; a simulated Qdrant/PostgreSQL outage
raises `EvaluationRetrievalError`. One test (`test_claude_unavailable_real_anthropic_call_...`) makes
a real, unmocked call through `run_agent()`/`claude_gateway` using this environment's actual
`ANTHROPIC_API_KEY` and asserts on its real outcome — see "Live-environment verification" below for
what that call actually returned this session.

### Runtime prerequisites

The repository already has a supported way to run every dependency; this section documents what
existed, it does not introduce a second infrastructure configuration.

| Service | How it runs | Port | Env vars (`.env`) | Health check |
|---|---|---|---|---|
| PostgreSQL | `docker-compose.yml` service `postgres` (image `postgres:16-alpine`) | 5432 | `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | `pg_isready -U $POSTGRES_USER -d $POSTGRES_DB` (compose healthcheck) |
| Redis | `docker-compose.yml` service `redis` (image `redis:7-alpine`) | 6379 | `REDIS_HOST`, `REDIS_PORT`, `REDIS_DB`, `REDIS_PASSWORD` | `redis-cli ping` (compose healthcheck) |
| **Qdrant** | **Native binary, not a compose service** — `docker-compose.yml`'s `backend` service has a standing comment: *"Qdrant runs natively on the host ..., not in a container, so reach it via Docker Desktop's host gateway rather than a compose service name."* The repo already contains a native Qdrant install at `qdrant-bin/qdrant.exe` (v1.18.3) with its own on-disk storage at `qdrant-bin/storage/` — no second install or config was created. | 6333 (HTTP), 6334 (gRPC) | `QDRANT_HOST`, `QDRANT_PORT` | `GET /collections` / `GET /healthz` |
| Claude Gateway / Anthropic | `backend/app/gateway/claude_gateway.py`, reads `ANTHROPIC_API_KEY` via `app/core/config.py`'s `Settings` (pydantic-settings, loads `.env`) | — (HTTPS to Anthropic) | `ANTHROPIC_API_KEY`, `CLAUDE_MODEL_NAME` | none built-in; verified here with one minimal `claude_gateway.generate()` call |

**Qdrant startup command used this session** (from the repository's own existing binary — no new
infrastructure introduced):

```
cd qdrant-bin
./qdrant.exe
```

Run from `qdrant-bin/` specifically, so Qdrant's default relative `./storage` path resolves to
`qdrant-bin/storage/` — the directory that already held this environment's real, previously-ingested
vector data (`qdrant-run.log`/`qdrant.log` in that same directory show prior real sessions against
this exact storage). No config file, flag, or environment variable was changed; this is the same
invocation implied by `docker-compose.yml`'s own comment.

### Verified this session, in order

1. **PostgreSQL** — already running (a pre-existing local install, `postgresql-x64-18`, independent of
   `docker-compose.yml`'s own containerized Postgres); reachable at `127.0.0.1:5432` with the
   configured credentials; confirmed the two curated `eval_queries` rows from the earlier Phase 3
   Evaluation Gate were still present.
2. **Qdrant** — was not running at session start (no Docker daemon available in this sandbox, and
   Qdrant is never a compose service in this repo — see the table above). Started via the command
   above. Verified:
   - `GET /collections` → `{"collections":[{"name":"document_chunks"}]}`
   - `GET /collections/document_chunks` → `points_count: 16`, dense vector `"dense"` (size 1024,
     cosine — matches `settings.embedding_dimension`/`qdrant_dense_vector_name`), sparse vector
     `"bm25_sparse"` (matches `qdrant_sparse_vector_name`) — the exact configuration the app expects.
   - All three curated `expected_chunk_ids` referenced by the two eval queries were confirmed present
     as real points in this collection (`19107c3a-...`, `d0d47fa9-...`, `7371fad6-...`), by scrolling
     the collection directly and checking each id.
3. **Anthropic credential** — `ANTHROPIC_API_KEY` is present and non-empty in the resolved `Settings`
   (checked by presence/length only, never printed). A direct, minimal `claude_gateway.generate()`
   call (system prompt "reply with OK", 10 max tokens) returned:
   ```
   Anthropic authentication unavailable: HTTP 401 authentication_error
   ```
   — unchanged from before. Not modified, not hardcoded, not bypassed, no provider switch.

### Controlled single-question validation

Before running the full dataset, one eval query — *"How is in-home service handled for this washing
machine, and what does it cost?"* — was run through `run_evaluation()` under all four configurations,
reading each real `retrieval_trace` back to confirm the expected execution shape:

| Config | `rewrite_trace` | `parent_context_chunk_ids` | Retrieved set vs. baseline |
|---|---|---|---|
| Baseline | `null` (rewriting off — `claude_gateway.generate` asserted unreachable) | `[]` | — |
| Parent-child | `null` | `["8c37989a...", "d0d47fa9..."]` — 2 of 10 hits enriched | identical set/order to baseline |
| Query rewrite | real trace entry: `"kept original query (gateway error: ... 401 ... API key is invalid.)"` | `[]` | identical (rewrite fell back to the original query) |
| Combined | same real fallback trace entry as above | same 2 hits enriched | identical set/order to baseline |

This confirms, from real execution rather than source inspection alone: Phase 3A's
`fetch_parent_context()` actually runs and attaches parent text only when the flag is on; Phase 3B's
`_maybe_rewrite_query()`/`rewrite_query()` actually attempts a real Anthropic call and, on the real 401
it receives, correctly falls back to the original query (its documented, tested behavior) rather than
failing the evaluation. Because Claude is unavailable in this environment, **no run this session
observed a *successful* rewrite** — only the fallback path, which is nonetheless real, live evidence
(not a mock) that the fallback logic behaves correctly under a genuine failure.

### Real evaluation — both curated eval queries, all four configurations

Executed via `experiments.run_gate(db, eval_queries, k=10, include_combined=True)` — the same function
`POST /eval/experiments/run` calls — against the now-live Qdrant and the two curated eval queries.
Total wall time for all 8 runs (2 queries × 4 configs): **121.1s**.

**Retrieval metrics** (averaged across both queries):

| Metric | Baseline | Parent-Child | Query Rewrite | Combined |
|---|---:|---:|---:|---:|
| Recall@10 | 0.500 | 0.500 | 0.500 | 0.500 |
| Precision@10 | 0.100 | 0.100 | 0.100 | 0.100 |
| MRR | 0.125 | 0.125 | 0.125 | 0.125 |
| NDCG@10 | 0.2412 | 0.2412 | 0.2412 | 0.2412 |
| Citation Accuracy | unavailable (401) | unavailable | unavailable | unavailable |
| Answer Relevance | unavailable | unavailable | unavailable | unavailable |
| Faithfulness | unavailable | unavailable | unavailable | unavailable |
| Hallucination | unavailable | unavailable | unavailable | unavailable |
| Retrieval latency (avg) | 23,653.8 ms | 10,078.5 ms | 10,623.3 ms | 12,430.2 ms |
| Generation latency (avg) | 916.5 ms | 766.3 ms | 440.7 ms | 574.0 ms |
| Total latency (avg) | 24,570.2 ms | 10,844.7 ms | 11,063.9 ms | 12,004.2 ms |
| Input/output/total tokens | unavailable | unavailable | unavailable | unavailable |
| Cost | unavailable | unavailable | unavailable | unavailable |

Every "unavailable" cell is genuinely unmeasured — `judge_answer()` and `run_agent()` both fail on the
same real 401 before any token usage is ever recorded to `gateway_usage_logs`, so `tokens_input`/
`tokens_output`/`cost_usd`/`model`/every judge score are `None` on every one of the 8 runs, not zero.
Retrieval **is** real and complete — Recall/Precision/MRR/NDCG above come from real Qdrant queries
against real vector data, through the corrected call path, for the first time.

**Why every retrieval number is identical across all four configurations, honestly explained (this is
not a repeat of the historical bug)**: unlike the historical comparison, this result is not structural
— the corrected runner *could* have shown a difference. It didn't, for two different, real, and
individually verifiable reasons:
1. **Parent-child retrieval is designed to never change ranking** — `fetch_parent_context()` only adds
   a supplementary `parent_context` field to already-selected hits; it never alters which chunks match
   or their order (see `fetch_parent_context()`'s own docstring and the dedicated unit tests). The
   `retrieval_trace` above proves it ran and enriched 2 of 10 hits per query — it simply isn't the kind
   of feature that would move Recall/Precision/MRR/NDCG by design.
2. **Query rewriting never got to attempt a real rewrite** — every rewrite attempt this session hit the
   same real Anthropic 401 and fell back to the original query (proven, not assumed, via the real
   `rewrite_trace` on every run). With the effective query always equal to the original query, retrieval
   was always going to be identical to baseline. This is a live-environment credential limitation, not a
   finding about query rewriting's potential effect.

### Cold-start vs. warm latency (do not read the baseline latency numbers above as a feature effect)

Two separate real runs this session, each in a fresh process, showed the same qualitative pattern:

| Run | First retrieval call (cold) | Subsequent calls (warm) |
|---|---:|---:|
| Controlled single-question run (4 configs, same query, one process) | 78,674 ms | 22,729 – 29,008 ms |
| Full 2-query × 4-config run (separate process, started right after Qdrant itself) | 39,210 ms | 8,098 – 11,927 ms |

The first retrieval call in a fresh process is always substantially slower than every call after it —
model-weight loading (BGE-M3 dense embedder, the BGE reranker) happens once per process and is folded
into that first call's latency. The **warm plateau itself differed between the two runs** (≈23–29 s vs.
≈8–12 s): the second run benefited from Qdrant's own process already having served several requests
(OS/file-cache warm-up on Qdrant's side, independent of the Python process). Both effects are reported
here rather than collapsed into one number. **Practical conclusion: only compare warm-to-warm latency
within the same run** — which is exactly what the "Retrieval latency (avg)" row above does (baseline's
first, cold call inflates its average by design of this run's ordering; parent-child/query-rewrite/
combined ran second-through-fourth in the same process and are warm-to-warm comparable with each
other). An apples-to-apples baseline number, from this run's own warm baseline data point (query 2
only, 8,097.5 ms), is closer to parent-child/query-rewrite/combined's ~10-12s than the 23,653.8 ms
average above suggests — the average is disclosed as-is, not silently corrected, per instruction not to
hide the cold-start effect.

**Approximate rewrite latency**: not separately instrumented as its own field (the persisted
`rewrite_trace` records what happened, not a standalone timing). Estimated from the one clean
warm-to-warm pair available (query 2, both fully warm): baseline retrieval 8,097.5 ms vs.
query-rewrite retrieval 10,465.5 ms → **≈2.4 s attributable to one failed (non-retried — HTTP 401 is
not in the gateway's retryable-error list) Anthropic round trip.** Reported as an approximation from
real data, not a controlled micro-benchmark.

### Per-question analysis

Only 2 eval queries — too few for an "improved/degraded/unchanged" count to mean anything beyond
stating what happened directly:

| Query | Expected chunk(s) | Recall (all 4 configs) | Retrieved set/order | Parent context attached |
|---|---|---|---|---|
| "How is in-home service handled for this washing machine, and what does it cost?" | 2 chunks, both found | **1.0**, identical across baseline/parent-child/query-rewrite/combined | identical across all 4 configs | same 2 chunks (`8c37989a...`, `d0d47fa9...`) enriched under parent-child/combined |
| "What damages are excluded from the in-home service coverage?" | 1 chunk, not found | **0.0**, identical across all 4 configs | identical across all 4 configs | same 2 chunks enriched under parent-child/combined (neither is the expected one) |

**Zero questions improved, zero degraded, two unchanged** — real and honestly reported, not the
structural "always unchanged" of the historical bug: this run had a real chance to show a difference
(rewrite genuinely attempted, real Qdrant queried) and didn't, for the reasons explained above.

One real, pre-existing data observation surfaced by actually running this: query 2's expected chunk
(`7371fad6-b2e1-4d1d-9e50-cdf0b46e0cf4`) was not retrieved in the top 10 of 16 total points under any
condition. This is a retrieval-ranking/eval-dataset characteristic of this small (16-point) corpus, not
something either Phase 3A or Phase 3B — by design — could have fixed (neither feature changes *which*
chunks are retrieved beyond what the base hybrid+rerank pipeline already selects). Flagged here as an
observation; investigating or fixing it is out of this task's scope (would mean touching chunking,
embedding, or the base retrieval pipeline — none of which this task is authorized to modify).

### Production decision

| Feature | Verdict | Reasoning |
|---|---|---|
| Parent-child retrieval (`parent_child_retrieval_enabled`) | **INSUFFICIENT EVIDENCE** | The evaluation architecture is now correct and was run for real against live vector data — `fetch_parent_context()` is proven to execute (2 of 10 hits enriched per query, in the real `retrieval_trace`). But the dataset (2 queries) is below this gate's own 5-query minimum for a verdict, and — the more fundamental limitation — the feature's real design goal (does richer context improve the *generated answer*?) cannot be assessed at all while Claude is unavailable; parent-context enrichment cannot move retrieval-ranking metrics by construction, so retrieval metrics alone can never approve or reject this feature. |
| Query rewriting (`query_rewriting_enabled`) | **INSUFFICIENT EVIDENCE** | Same dataset-size blocker. The fallback path is now proven correct under a real failure (real 401, real fallback, real retrieval on the original query) — genuinely stronger evidence than before. What remains completely unmeasured is the feature's actual purpose: whether a *successful* rewrite retrieves better evidence. That requires a working Anthropic key, which this environment does not have. |
| Combined | No independent recommendation, by design (unchanged) — cannot attribute effect to either feature individually. |

Both verdicts remain **INSUFFICIENT EVIDENCE** — real numbers now exist (this is new), but they are not
sufficient to approve either feature: the dataset is too small, and, independently, generation quality
— the dimension both features actually aim to improve — could not be measured at all this session.
`parent_child_retrieval_enabled` and `query_rewriting_enabled` remain `False` by default. **Do not
proceed to Phase 3C.** Next step: re-run `POST /eval/experiments/run` (`include_combined=true`) once a
valid `ANTHROPIC_API_KEY` is available in an environment with Qdrant reachable (both together — this
session only had one of the two blockers resolved) and once the curated eval dataset has grown past 5
queries.

### Files changed / created / tests

See `docs/ARCHITECTURE_ENHANCEMENT_PLAN.md` §16 for the full file list, test count, and report.

## Evaluation Dataset Expansion

Goal: with the evaluation architecture corrected and runnable (previous two sections), the last
blocker to a meaningful production decision was the dataset itself — 2 curated queries against one
document, below this gate's own 5-query minimum for any verdict. This section expands it using the
real documents already in this environment, re-runs all four configurations, and reports the result.
No retrieval architecture was modified.

### Dataset construction methodology

Every real document already ingested in this environment was inventoried first. Postgres had chunk
rows for 7 document uploads, but only `WM_1.pdf` (uploaded twice — see "A data-quality finding"
below) was actually indexed in Qdrant; `sop_m102.txt`, `hr_leave_policy.txt`,
`eng_design_guidelines.txt`, and two `note.txt` uploads had been parsed and chunked but never
embedded (Qdrant's storage evidently predates those uploads). These are real, previously-uploaded
files, not new test fixtures — completing their indexing uses the repository's own existing, unmodified
reindex logic (`routers/documents.py::reindex_document()` — embed, compute term frequencies, build the
sparse index, upsert to Qdrant; no re-parse, no chunk-boundary change), the same maintenance path the
product already exposes at `POST /documents/{id}/reindex`. `note.txt`'s content ("hello world") wasn't
usable for any meaningful question and was excluded.

Every one of the 30 resulting cases was built by first reading the real chunk text directly (not
guessed or templated), then writing a question whose answer is verifiably in that text, then resolving
its `expected_chunk_ids` by live document-id + chunk-index lookup against Postgres and asserting each
one is actually present in Qdrant — a build-time `assert` that would halt dataset construction if any
id were wrong, so nothing unverifiable could be committed. Each case also records `description`
(expected-answer criteria and, where relevant, a citation-evidence pointer) and `categories` (a new
`eval_queries.categories` JSONB column, additive migration, not forced onto cases that don't fit one).

### A data-quality finding, investigated and corrected in place

The original, pre-existing query 2 ("What damages are excluded from the in-home service coverage?")
expected chunk `7371fad6-...` — verified by reading that chunk's actual text: it's the *trailing*
portion of the "In-Home Service" section (legal boilerplate + contact phone/address), which does not
list excluded damages at all. The real exclusions list ("THIS LIMITED WARRANTY DOES NOT COVER" — water
pipe damage, misuse, unauthorized modification, cosmetic damage after 7 days, etc.) is a *different*
chunk entirely (`idx0` of the same document). This was a real, pre-existing ground-truth labeling
error — not a retrieval failure, and not something Phase 3A/3B could ever have fixed. Corrected in
place (same `eval_queries` row, same question text, `expected_chunk_ids` updated to the real evidence
chunk), documented here rather than silently fixed, per instruction not to silently drop or alter a
difficult case without explanation. This explains why the historical 2-query gate showed a 0.0-recall
question that looked like a retrieval miss: it was a mislabeled answer key, confirmed once the real
chunk text was actually read.

### Categories

| Category | Count | Category | Count |
|---|---:|---|---:|
| `direct_fact` | 20 | `identifier` | 7 |
| `date_based` | 7 | `context_required` | 4 |
| `parent_context` | 2 | `ambiguous_query` | 2 |
| `conversational` | 2 | `terminology` | 2 |
| `rewrite_candidate` | 1 | `rewrite_not_needed` | 1 |

(A case can carry more than one tag — e.g. `direct_fact` + `identifier` — so columns don't sum to 30.)
30 verified questions total, drawn from the LG washing machine warranty document, `sop_m102.txt`
(machine-starting SOP, identifiers + a genuine cross-chunk step split), `hr_leave_policy.txt` (7
distinct numeric/date facts from one self-contained chunk), and `eng_design_guidelines.txt` (Line 3
Retrofit — percentages, terminology, and the deliberately vague *"What happened to Line 3?"* as the
`ambiguous_query`/`conversational`/`rewrite_candidate` case).

### Anthropic authentication status (re-verified this session)

Unchanged: `Anthropic authentication unavailable: HTTP 401 authentication_error`, confirmed live
immediately before this run, key never printed, not modified.

### Real evaluation — all 30 questions, all four configurations

Run via `experiments.run_experiment()` (the same production function `run_gate()`/`POST
/eval/experiments/run` calls) against the now-fully-indexed live Qdrant. Each configuration ran to
completion in its own process — 4 runs × 30 questions = 120 real retrieval calls.

| Metric | Baseline | Parent-Child | Query Rewrite | Combined |
|---|---:|---:|---:|---:|
| Recall@10 | 0.9833 | 0.9833 | 0.9833 | 0.9833 |
| Precision@10 | 0.1067 | 0.1067 | 0.1067 | 0.1067 |
| MRR | 0.8892 | 0.8559 | 0.8725 | 0.9059 |
| NDCG@10 | 0.9040 | 0.8794 | 0.8917 | 0.9163 |
| Citation Accuracy / Answer Relevance / Faithfulness / Hallucination | unavailable (0/30 — 401) | unavailable | unavailable | unavailable |
| Retrieval latency (avg) | 18,090 ms | 11,164 ms | 8,702 ms | 11,003 ms |
| Total latency (avg) | 18,968 ms | 11,903 ms | 9,494 ms | 13,739 ms |
| Input/output/total tokens | unavailable (0/30) | unavailable | unavailable | unavailable |
| Cost | unavailable | unavailable | unavailable | unavailable |

**Successful rewrites: 0/30. Fallback-to-original: 30/30**, for both `query_rewrite` and `combined` (60
real attempts total) — every real Anthropic call this session hit the same 401 and correctly fell back
to the original query, per `rewrite_query()`'s own tested contract. This is strong, repeated live
evidence the *fallback* is reliable; it is **not** evidence about whether a successful rewrite would
help, which remains unverified live (per instruction: fallback-to-original is never evidence of rewrite
effectiveness).

**Parent context attached: 30/30 runs**, both `parent_child` and `combined` — `fetch_parent_context()`
executed and enriched results on every single run this dataset produced, confirmed via each run's real
`retrieval_trace.parent_context_chunk_ids`.

**Recall/Precision are bit-for-bit identical across all four configurations** — a real result, not the
old structural bug: since query rewriting always fell back to the identical query this session (above),
and parent-context enrichment never alters which chunks match by design, retrieval *set* membership
had no mechanism to differ this run. **MRR/NDCG are not identical**, and that difference is fully
explained below — it is not a Phase 3A/3B effect either.

### Why MRR/NDCG differ slightly — duplicate-document tie noise, not a feature effect

`WM_1.pdf` is indexed twice in this environment (two separate uploads of the same file, evidently
accidental — a pre-existing environmental condition, not introduced by this task). Several questions'
correct answer chunk has a byte-identical twin in the duplicate document, at an equal or near-equal
relevance score. Each of the four configurations ran as a **separate process**; near-tied scores
between two identical-content chunks can resolve in either order run-to-run (floating-point/thread
non-determinism), which moves rank-sensitive metrics (MRR, NDCG) without moving *set*-based ones
(Recall, Precision) at all. Directly verified: every question whose `retrieved_chunk_ids` differed
between configurations involved exactly this duplicate-pair pattern (9 of 30 questions), and recall was
identical for all 9. This was flagged mid-run and, per instruction, deliberately **left as a documented
limitation rather than fixed** (the alternative — broadening `expected_chunk_ids` to accept either
duplicate — was offered and declined in favor of transparency about the raw signal).

One of the two purpose-built `parent_context` cases ("How long is the warranty coverage for the
washing machine's stainless steel drum?" — the case designed to test whether Phase 3A's terse-heading
fix also covers a heading with *no* `parent_chunk_id` link) shows this tie noise directly: MRR 1.0 →
0.5 under `parent_child`. This is **not** parent-context enrichment causing a regression —
`retrieval_trace` confirms no parent context was attached to either the correct chunk or its duplicate
for this question (neither has a parent link, exactly as designed to test) — it's the same duplicate-
document tie landing differently in that particular process run. **`combined`'s nominally-highest
MRR/NDCG in the table above is this same noise, not a synergistic effect** — it is not reproducible
evidence that combining the two features helps; a different run's tie-breaks could easily favor a
different configuration.

### Category-level analysis

Paired against baseline by MRR, per question:

| Category | Baseline MRR | Parent-Child | Query Rewrite | Combined | Improved / Degraded / Unchanged (vs. baseline, any experiment) |
|---|---:|---:|---:|---:|---|
| `direct_fact` (20) | 0.867 | 0.817 | 0.842 | 0.892 | 1 improved (combined), 3 degraded (2 parent-child, 1 query-rewrite) — all duplicate-tie noise, confirmed by cross-referencing `retrieval_trace` |
| `date_based` (7) | 1.000 | 1.000 | 1.000 | 1.000 | 0 / 0 / 7 — perfectly stable (single self-contained chunk, no duplicate) |
| `identifier` (7) | 0.878 | 0.878 | 0.878 | 0.878 | 0 / 0 / 7 |
| `context_required` (4) | 0.633 | 0.633 | 0.633 | 0.633 | 0 / 0 / 4 — includes the SOP cross-chunk-split case; recall was 1.0 for all 4 in every condition |
| `parent_context` (2) | 0.667 | 0.417 | 0.667 | 0.667 | 0 / 1 / 1 — the tie-noise case above; recall unaffected (still 1.0) |
| `ambiguous_query` / `conversational` (2 each, overlapping) | 1.000 | 1.000 | 1.000 | 1.000 | 0 / 0 / 2 — includes "What happened to Line 3?"; rewrite never got a successful attempt to test |
| `terminology` (2) | 1.000 | 1.000 | 1.000 | 1.000 | 0 / 0 / 2 |
| `rewrite_candidate` / `rewrite_not_needed` (1 each) | 1.000 / 1.000 | unchanged | unchanged | unchanged | 0 / 0 / 2 — rewrite fell back to original for both, so neither could move |

**No category shows a real, reproducible improvement or regression from either feature.** Every
non-zero delta traces directly to the duplicate-document tie noise described above, confirmed by
cross-referencing each affected question's `retrieval_trace`.

### Production decision

| Feature | Verdict | Reasoning |
|---|---|---|
| Parent-child retrieval | **INSUFFICIENT EVIDENCE** | Dataset is now large enough (30 ≥ 5-query minimum) and `fetch_parent_context()` is proven to execute on every applicable run — but retrieval-ranking metrics show no real effect (by design: enrichment never changes ranking), and the dimension this feature actually targets — does richer context improve the *generated answer* — remains completely unmeasured while Claude is unavailable. |
| Query rewriting | **INSUFFICIENT EVIDENCE** | Same dataset-size point resolved. Fallback reliability now has strong, repeated live evidence (60/60 real attempts this session). Zero successful rewrites were observed, so the feature's actual value proposition — does a rewritten query retrieve better evidence — remains completely unverified live. |
| Combined | No independent recommendation, by design (unchanged) — and see the tie-noise section above for why its nominally-best numbers specifically must not be read as a synergy signal. |

Both verdicts remain **INSUFFICIENT EVIDENCE**. `parent_child_retrieval_enabled` and
`query_rewriting_enabled` remain `False` by default — not because either measured badly, but because
neither has been measured on the dimension that would justify enabling it (generation quality), which
requires a valid `ANTHROPIC_API_KEY`. **Do not proceed to Phase 3C.**

### Files changed / created

See `docs/ARCHITECTURE_ENHANCEMENT_PLAN.md` §17 for the full file list and report.

## Phase 3C — Multi-query retrieval (not started)

Planned: generate a configurable number of query variants, run each through the existing
`hybrid_search()` unchanged, fuse with the same RRF approach already used for dense+sparse fusion
(extracted into a reusable helper rather than duplicated), every variant subject to the identical
RBAC/department/permission filtering as the original query. Awaiting Phase 3B sign-off and separate
approval per the request's explicit phase-gating instruction.

## Observability

Not yet centralized (that's Phase 7, out of scope here) — `services/monitoring/metrics.py`'s
existing `record_retrieval_metrics()` call in `search_with_reranking()` is unchanged by Phase 3A/3B.
Phase 3B records rewrite activity via the existing `gateway_usage_logs` (auto-recorded by
`claude_gateway.generate()`) and the planner's `trace` mechanism — see Phase 3B's own Observability
section above. Phase 3C, when implemented, should record variant count/fan-out latency the same way
rather than inventing a parallel mechanism, per this document's own precedent.
