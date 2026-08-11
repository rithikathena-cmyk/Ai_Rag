# RAG Evaluation

How answer/retrieval quality is measured — what's curated by hand, what's computed
deterministically, and what's LLM-judged. This is Phase 2 of
`docs/ARCHITECTURE_ENHANCEMENT_PLAN.md` (evaluation completeness); the retrieval/generation
pipeline itself is unchanged.

## Data model

- **`eval_queries`** (`EvalQueryModel`) — a curated question plus, optionally, `expected_chunk_ids`
  (ground-truth relevant chunks). Retrieval-ranking metrics are only computable for a query once
  it has curated ground truth; until then they're `null`, not zero.
- **`eval_runs`** (`EvalRunModel`) — one row per `POST /eval/queries/{id}/run` execution, storing
  every metric below plus the generated answer and judge notes.

## Running an evaluation

`services/evaluation/runner.py::run_evaluation()` does four things in order, against the **real**
production pipeline — not a stub:

1. **Retrieve** — `search_with_reranking()` (the same function `/search` and the chat planner's
   `search_documents` tool use), timed.
2. **Generate** — `run_agent()` (the same LangGraph planner chat uses), timed.
3. **Judge** — `generation_judge.judge_answer()`, one Claude call (tier `FAST`) that scores the
   answer against its sources, timed.
4. **Persist** — one `EvalRunModel` row with every metric below.

## Metrics

### Retrieval-ranking (rule-based, require curated `expected_chunk_ids`)

Recall@K, Precision@K, MRR, NDCG@K — binary relevance, `services/evaluation/retrieval_metrics.py`.
Unchanged since before Phase 2.

### Generation quality (LLM-judged, one call scores all five)

`generation_judge.judge_answer()` sends the question, numbered sources, and generated answer to
Claude once and parses a single JSON verdict:

- **Groundedness**, **faithfulness**, **hallucination rate** — existing (v1 prompt).
- **Citation accuracy** *(Phase 2)* — of the answer's `[n]` citation markers (the same numbering
  `services/agents/planner.py`'s `search_documents` tool already assigns via its
  `citation_counter`, surfaced to the frontend chat trace), what fraction point to a source that
  actually supports the claim it's attached to. This reuses the existing citation
  numbering/representation end to end — there is no second citation system.
- **Answer relevance** *(Phase 2)* — how directly the answer addresses the question asked,
  independent of correctness.

Both new fields come from `backend/prompts/judge_agent_v2.yaml` — a new prompt *version*, not an
edit to `v1` in place, per `prompt_manager.py`'s existing "bump the version, don't edit in place"
convention (old eval runs stay reproducible against the prompt that actually produced them).

**Why not two more Claude calls?** The v1 judge call already receives everything needed to score
citation accuracy and answer relevance (the question, the numbered sources, the answer). Scoring
two more dimensions in the same call is zero additional latency and zero additional cost, versus
standing up a second citation-scoring system with its own Claude call. This was a deliberate
deviation from `docs/ARCHITECTURE_ENHANCEMENT_PLAN.md`'s original sketch (which proposed separate
`citation_accuracy.py`/`answer_relevance.py` modules) made during Phase 2 implementation once the
existing judge call's inputs were inspected directly.

### Latency, tokens, cost, model *(Phase 2)*

- `retrieval_latency_ms`, `generation_latency_ms` — existing, wall-clock around steps 1–2 above.
- `total_latency_ms` *(new)* — retrieval + generation + judge wall-clock, summed once at the end;
  no second timing mechanism, reuses the same `time.perf_counter()` pattern the existing two fields
  already use.
- `tokens_input`, `tokens_output`, `cost_usd`, `model` *(new)* — **not** re-derived. Every real
  Claude call an evaluation makes (however many planner tool-loop turns `run_agent()` takes, plus
  the judge call) already writes its own row to `gateway_usage_logs` via
  `claude_gateway.generate()`/`usage_tracker.record_usage()` — the same audit trail
  `docs/AUDIT_LOGGING.md` and the Admin "Gateway & Cost" page already read. `run_evaluation()`
  generates one `request_id` and passes it into both `run_agent(..., request_id=...)` and
  `judge_answer(..., request_id=...)` (both previously always minted their own fresh id per call);
  after both finish, it sums `tokens_input`/`tokens_output`/`cost_usd` from every
  `gateway_usage_logs` row sharing that `request_id`. Cost uses `usage_tracker.estimate_cost_usd()`,
  which reads pricing from `backend/config/models.yaml`'s `pricing` block — the one place pricing
  is defined; this phase did not add a second pricing table. Passing `request_id` is opt-in and
  backward compatible: production `chat.py` never supplies one, so every `run_agent()` call outside
  evaluation keeps generating a fresh id per turn exactly as before.
- These four fields are `null` (not zero) if generation/judge fails entirely, or if no gateway rows
  matched — never a fabricated zero.

## API

`GET /eval/summary` returns `avg_*` for every metric above, plus `total_cost_usd` (a sum, since
total spend on an eval suite is usually more actionable than a per-run average). Averages skip
`null` rows rather than treating them as zero — a run made before this migration has every Phase 2
field `null` and doesn't skew the averages down.

## Frontend

`frontend/app/views/evaluation.py`'s Summary tab shows all sixteen metrics (four existing rows,
plus new citation accuracy/answer relevance/total latency/tokens/cost cards) using the same
`st.metric` + `metric_cards()` pattern already used for recall/precision/MRR/nDCG — no new UI
pattern introduced. The Runs tab's `explorable_table()` picks up the new `EvalRunResponse` columns
automatically since it just renders whatever the API returns.

## Known gaps (not in this phase)

- No per-citation breakdown is stored — `citation_accuracy` is one score for the whole answer, not
  per-`[n]`-marker. Would need the judge to return a list, not a scalar; deferred until there's a
  concrete need to drill into *which* citation was wrong.
- `total_latency_ms` includes the judge's LLM call time, which chat users in production never wait
  on — it measures "how long does a full evaluation take," not "how long does a chat turn take."
- Evaluation still requires hand-curated `expected_chunk_ids` for retrieval-ranking metrics; there's
  no automatic ground-truth generation.
