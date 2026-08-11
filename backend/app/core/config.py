from pathlib import Path

from pydantic_settings import BaseSettings

# Resolve the repo-root .env by absolute path so settings load the same way
# regardless of the process's cwd (matters when running natively instead of
# via Docker). In the Docker image this path simply doesn't exist, so
# pydantic-settings falls back to the OS env vars docker-compose injects.
_ROOT_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"

# Same cwd-independence problem applies to storage dirs: anchor them to the
# backend app root (parents[2] = backend/) so uploads/reports always land in
# backend/document_storage and backend/report_storage regardless of where
# the process was started from.
_BACKEND_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    qdrant_host: str = "127.0.0.1"
    qdrant_port: int = 6333
    # Qdrant Cloud mode: set qdrant_url (e.g. "https://xyz.cloud.qdrant.io:6333")
    # and qdrant_api_key instead of qdrant_host/qdrant_port — db/qdrant.py
    # prefers qdrant_url when set, falling back to host/port for local/Docker
    # Qdrant (which has no auth). Both point at the same collection API, so
    # nothing above db/qdrant.py needs to know which mode is active.
    qdrant_url: str = ""
    qdrant_api_key: str = ""
    backend_host: str = "0.0.0.0"
    backend_port: int = 8000
    environment: str = "development"

    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "ragchat"
    postgres_user: str = "ragchat"
    postgres_password: str = "ragchat"
    # Managed Postgres (Supabase, Neon, RDS, ...) requires SSL on external
    # connections; local/Docker Postgres has none configured, so this is
    # opt-in via .env rather than a hardcoded "require" that would break the
    # existing local dev setup. Passed straight through to libpq's sslmode —
    # "require" is Supabase's documented value.
    postgres_sslmode: str = ""

    # Infra resilience (docs/RAG_RETRIEVAL.md's /search hardening) — timeouts,
    # pool sizing, and retry policy for the two backing stores the retrieval
    # path depends on. Live here rather than a YAML file, matching the
    # existing precedent of *_timeout_seconds fields above.
    qdrant_timeout_seconds: float = 5.0
    qdrant_retry_max_attempts: int = 2
    qdrant_retry_base_delay_seconds: float = 0.25
    qdrant_retry_max_delay_seconds: float = 2.0

    postgres_pool_size: int = 10
    postgres_max_overflow: int = 5
    postgres_pool_timeout_seconds: int = 10
    postgres_connect_timeout_seconds: int = 5
    postgres_statement_timeout_ms: int = 10000
    postgres_retry_max_attempts: int = 1
    postgres_retry_base_delay_seconds: float = 0.2
    postgres_retry_max_delay_seconds: float = 1.0

    upload_dir: str = str(_BACKEND_ROOT / "document_storage")
    max_upload_size_mb: int = 100

    # Comma-separated allowed origins for the Streamlit frontend (browser-side
    # requests only — api_client.py's own calls are server-to-server Python
    # `requests` and aren't subject to CORS at all, so this matters mainly for
    # Streamlit's websocket/asset traffic and any future client-side fetches).
    # Local dev default covers `streamlit run` on its default port; add the
    # deployed Streamlit Community Cloud URL (https://<app>.streamlit.app) in
    # production.
    cors_allowed_origins: str = "http://localhost:8501"

    embedding_model_name: str = "BAAI/bge-m3"
    embedding_dimension: int = 1024
    embedding_batch_size: int = 32
    qdrant_collection_name: str = "document_chunks"

    zero_shot_model_name: str = "MoritzLaurer/deberta-v3-xsmall-zeroshot-v1.1-all-33"
    classification_rule_confidence_threshold: float = 0.6
    classification_zero_shot_confidence_threshold: float = 0.5

    parse_timeout_seconds: float = 120
    chunk_timeout_seconds: float = 60
    embed_timeout_seconds: float = 300

    chunk_size_tokens: int = 400
    chunk_overlap_tokens: int = 50
    chunk_size_tokens_parent: int = 1500
    default_overlap_ratio: float = 0.10
    manual_overlap_ratio: float = 0.25
    semantic_similarity_threshold: float = 0.5
    row_chunk_batch_size: int = 20

    qdrant_dense_vector_name: str = "dense"
    qdrant_sparse_vector_name: str = "bm25_sparse"
    sparse_bm25_k1: float = 1.2
    sparse_bm25_b: float = 0.75
    sparse_bm25_avg_doc_length: float = 256.0
    sparse_max_keywords_per_chunk: int = 10

    summary_min_sentences: int = 3
    summary_max_sentences: int = 10
    summary_target_ratio: float = 0.15

    spacy_model_name: str = "en_core_web_sm"
    entity_extraction_max_chars: int = 50000

    search_max_top_k: int = 50
    hybrid_prefetch_limit: int = 50

    reranker_model_name: str = "BAAI/bge-reranker-base"
    reranker_candidate_pool: int = 20

    # Phase 3A — parent-child retrieval (docs/RAG_RETRIEVAL.md). Off by
    # default so baseline behavior stays available for A/B comparison.
    parent_child_retrieval_enabled: bool = False
    parent_context_max_expansions: int = 5
    parent_context_max_chars: int = 2000

    # Phase 3B — query rewriting (docs/RAG_RETRIEVAL.md). Experimental,
    # feature-flagged, off by default. Uses the existing Claude Gateway at a
    # deliberately cheap/fast tier — see services/retrieval/query_rewrite.py.
    query_rewriting_enabled: bool = False
    query_rewrite_max_chars: int = 300
    query_rewrite_timeout_seconds: float = 5.0
    # Any ModelTier value (fast/reasoning/sonnet/opus) — validated at call
    # time in query_rewrite.py, falls back to "fast" if misconfigured.
    query_rewrite_tier: str = "fast"

    password_hash_iterations: int = 390000

    anthropic_api_key: str = ""
    claude_model_name: str = "claude-opus-5"
    claude_max_tokens: int = 2048
    claude_effort: str = "medium"
    chat_context_top_k: int = 5

    agent_max_tokens: int = 4096
    # Raised from 4: a Haiku-tier (Employee-role) run chasing a named-but-absent
    # document through repeated near-duplicate search_documents calls hit this
    # limit (recursion_limit = iterations*2+1) before ever reaching a synthesis
    # turn, returning the canned incomplete-answer message with 20 already-
    # retrieved chunks unused. Paired with the v3 planner prompt's search-budget
    # guidance (prompts/planner_agent_v3.yaml), which should make hitting this
    # ceiling rare regardless of headroom.
    agent_max_tool_iterations: int = 6
    sql_agent_row_limit: int = 500

    report_dir: str = str(_BACKEND_ROOT / "report_storage")

    conversation_summary_trigger_turns: int = 12
    conversation_recent_turns_kept: int = 6
    memory_summary_max_tokens: int = 300
    memory_summary_effort: str = "low"

    guardrails_enabled: bool = True
    guardrail_max_input_chars: int = 4000
    guardrail_block_prompt_injection: bool = True
    guardrail_block_destructive_intent: bool = True
    guardrail_redact_pii: bool = True
    guardrail_scope_deny_keywords: str = ""
    guardrail_scope_allow_keywords: str = ""
    guardrail_block_system_prompt_leak: bool = True

    # PII replacement style (docs/GUARDRAILS_ARCHITECTURE.md §11) — "placeholder"
    # is the original fixed [REDACTED_EMAIL]-style token; "hash" replaces the
    # match with a salted, deterministic short hash ([REDACTED_EMAIL_a1b2c3d4])
    # instead, so the same value always redacts to the same token within one
    # deployment (useful for correlating "same person mentioned twice" without
    # ever storing/exposing the real value) while staying non-reversible.
    guardrail_pii_mode: str = "placeholder"  # "placeholder" | "hash"
    # Dev-safe default so the app still boots without a .env, exactly like
    # jwt_secret_key above — override with a long random value in production.
    # Salting matters even for a "hash": an *unsalted* hash of a low-entropy
    # value like an SSN or phone number is crackable via a rainbow table
    # (only ~1 billion possible SSNs) — the salt is what makes it actually
    # non-reversible, not the hash function alone.
    guardrail_pii_hash_salt: str = "dev-insecure-pii-salt-change-me"
    # When True (the default), a message containing PII is blocked outright
    # — it never reaches Claude, redacted or not. When False, restores the
    # original behavior: PII is redacted in place and the redacted message
    # still proceeds. Output-side PII (Claude's reply) is never blocked
    # either way, only ever redacted — the model already generated it by
    # that point; redaction is what's left to do.
    guardrail_pii_block_input: bool = True

    # LLM-based advanced guardrail check (docs/GUARDRAILS_ARCHITECTURE.md §10) —
    # secrets only; enablement/model/timeout live in backend/config/guardrails.yaml
    # (this repo's convention for genuinely-new rails, matching the retrieval
    # and citation rails). Deliberately not the Claude Gateway: every call
    # here would cost real Anthropic tokens on top of the planner/judge/
    # rewrite calls that already use it. Gemini's free tier has a real $0
    # quota for small models, so it's the default provider for this rail.
    gemini_api_key: str = ""

    fallback_retrieval_top_k: int = 3
    fallback_chunk_char_limit: int = 500

    # Auth (JWT)
    # Dev-safe default so the app still boots without a .env; override with a
    # long random value in production (e.g. `python -c "import secrets;
    # print(secrets.token_hex(32))"`).
    jwt_secret_key: str = "dev-insecure-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 30
    jwt_refresh_token_expire_days: int = 7

    # First-admin bootstrap: role escalation (PATCH /users/{id}) already
    # requires an existing admin, so without this there's no way to create
    # the very first one. Empty by default (no-op); set both to seed one
    # idempotently on startup — see db/postgres.py::_bootstrap_admin_user.
    bootstrap_admin_email: str = ""
    bootstrap_admin_password: str = ""

    # Upload security
    upload_mime_check_enabled: bool = True

    class Config:
        env_file = str(_ROOT_ENV_FILE)
        extra = "ignore"


settings = Settings()
