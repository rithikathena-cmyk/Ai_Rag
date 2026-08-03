from pathlib import Path

from pydantic_settings import BaseSettings

# Resolve the repo-root .env by absolute path so settings load the same way
# regardless of the process's cwd (matters when running natively instead of
# via Docker). In the Docker image this path simply doesn't exist, so
# pydantic-settings falls back to the OS env vars docker-compose injects.
_ROOT_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    qdrant_host: str = "qdrant"
    qdrant_port: int = 6333
    backend_host: str = "0.0.0.0"
    backend_port: int = 8000
    environment: str = "development"

    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "ragchat"
    postgres_user: str = "ragchat"
    postgres_password: str = "ragchat"

    upload_dir: str = "./document_storage"
    max_upload_size_mb: int = 100

    embedding_model_name: str = "BAAI/bge-m3"
    embedding_dimension: int = 1024
    embedding_batch_size: int = 32
    qdrant_collection_name: str = "document_chunks"

    zero_shot_model_name: str = "MoritzLaurer/deberta-v3-xsmall-zeroshot-v1.1-all-33"
    classification_rule_confidence_threshold: float = 0.6
    classification_zero_shot_confidence_threshold: float = 0.5

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
    reranker_candidate_pool: int = 50

    password_hash_iterations: int = 390000

    anthropic_api_key: str = ""
    claude_model_name: str = "claude-opus-5"
    claude_max_tokens: int = 2048
    claude_effort: str = "medium"
    chat_context_top_k: int = 8

    agent_max_tokens: int = 4096
    agent_max_tool_iterations: int = 4
    sql_agent_row_limit: int = 500

    report_dir: str = "./report_storage"

    conversation_summary_trigger_turns: int = 12
    conversation_recent_turns_kept: int = 6
    memory_summary_max_tokens: int = 300
    memory_summary_effort: str = "low"

    class Config:
        env_file = str(_ROOT_ENV_FILE)
        extra = "ignore"


settings = Settings()
