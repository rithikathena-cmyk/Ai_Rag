export interface EvalQuery {
  id: string
  query: string
  description: string | null
  expected_chunk_ids: string[]
  categories: string[]
  created_at: string
}

export interface EvalRun {
  id: string
  eval_query_id: string
  k: number
  retrieved_chunk_ids: string[]
  recall_at_k: number | null
  precision_at_k: number | null
  mrr: number | null
  ndcg_at_k: number | null
  retrieval_latency_ms: number | null
  generated_answer: string | null
  groundedness: number | null
  faithfulness: number | null
  hallucination_rate: number | null
  citation_accuracy: number | null
  answer_relevance: number | null
  judge_notes: string | null
  generation_latency_ms: number | null
  total_latency_ms: number | null
  tokens_input: number | null
  tokens_output: number | null
  total_tokens: number | null
  cost_usd: number | null
  model: string | null
  experiment_label: string | null
  created_at: string
}

export interface EvalSummary {
  run_count: number
  avg_recall_at_k: number | null
  avg_precision_at_k: number | null
  avg_mrr: number | null
  avg_ndcg_at_k: number | null
  avg_groundedness: number | null
  avg_faithfulness: number | null
  avg_hallucination_rate: number | null
  avg_retrieval_latency_ms: number | null
  avg_generation_latency_ms: number | null
  avg_citation_accuracy: number | null
  avg_answer_relevance: number | null
  avg_total_latency_ms: number | null
  avg_tokens_input: number | null
  avg_tokens_output: number | null
  avg_cost_usd: number | null
  total_cost_usd: number | null
}
