export interface PiiOccurrenceSummary {
  entity_id: string
  direction: 'input' | 'output'
  entity_type: string
  detector: 'regex' | 'gliner'
  country: string | null
  /** The same value already visible in the ordinary chat/trace text — never
   *  the original. */
  sanitized_value: string
  policy_version: number | null
  created_at: string | null
}

export interface PiiOccurrenceReveal {
  entity_id: string
  entity_type: string
  detector: 'regex' | 'gliner'
  country: string | null
  raw_value: string
  sanitized_value: string
  policy_version: number | null
}
