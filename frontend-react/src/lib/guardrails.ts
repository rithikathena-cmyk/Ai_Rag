import type { ChatTraceStep } from '@/types/chat'

const GUARDRAILS_AGENT = 'Guardrails'

export type GuardrailStepAction = 'pass' | 'redact' | 'block'

export function parseTraceStep(step: ChatTraceStep): { action: GuardrailStepAction | null; detail: string } {
  const match = /^(pass|redact|block):\s*([\s\S]*)$/.exec(step.summary)
  if (!match) return { action: null, detail: step.summary }
  return { action: match[1] as GuardrailStepAction, detail: match[2] }
}

export function isBlockedResponse(trace: ChatTraceStep[]): boolean {
  return trace.some((s) => s.agent === GUARDRAILS_AGENT && parseTraceStep(s).action === 'block')
}

// ---------------------------------------------------------------------------
// Security & Activity panel — a SAFE SUMMARY of the real backend trace, never
// a debugging console. Every row below comes from a real ChatTraceStep the
// backend actually recorded; nothing here is fabricated. See
// GuardrailsStatus.tsx and the approved "User-Facing Guardrail Activity /
// Security Trace Panel" plan for the full rationale.
// ---------------------------------------------------------------------------

export type ActivitySection = 'input' | 'rag' | 'llm' | 'output'
export type ActivityStatus = 'PASSED' | 'DETECTED' | 'FLAGGED' | 'REDACTED' | 'BLOCKED' | 'SKIPPED'

interface StageInfo {
  section: ActivitySection
  label: string
}

// Maps every known backend check/tool name to where it belongs in the panel
// and what to call it. Checks that run on BOTH input and output (PII, in
// particular) share one catalog entry — direction is resolved positionally
// in buildActivityTimeline(), not by name, since the backend itself doesn't
// tag direction on the step.
const STAGE_CATALOG: Record<string, StageInfo> = {
  authorization: { section: 'rag', label: 'Access authorization' },
  length_check: { section: 'input', label: 'Input length check' },
  secret_detected_check: { section: 'input', label: 'Credential & secret check' },
  prompt_injection_check: { section: 'input', label: 'Prompt injection check' },
  deberta_injection_check: { section: 'input', label: 'Advanced injection check' },
  destructive_intent_check: { section: 'input', label: 'Destructive intent check' },
  custom_word_check: { section: 'input', label: 'Word policy check' },
  custom_regex_check: { section: 'input', label: 'Pattern policy check' },
  scope_check: { section: 'input', label: 'Scope validation' },
  scope_semantic_check: { section: 'input', label: 'Scope validation' },
  // Deliberately its OWN label, not 'Scope validation' — buildActivityTimeline
  // merges same-labeled steps to their worst outcome, so reusing the label
  // would collapse a BLOCKED first pass and its PASSED re-check into one
  // BLOCKED row on a request that was actually allowed through.
  scope_semantic_recheck: { section: 'input', label: 'Scope re-validation' },
  // SF-03 fix: emitted instead of scope_semantic_check's own name when the
  // message decomposes into multiple request clauses and only SOME are in
  // scope — same section/label as scope_semantic_check since it's the same
  // check, just a distinguishable verdict for SANITIZED_MESSAGE below.
  scope_semantic_mixed: { section: 'input', label: 'Scope validation' },
  scope_unclear_pii: { section: 'input', label: 'Scope validation' },
  scope_unclear_document: { section: 'input', label: 'Scope validation' },
  scope_unclear_context: { section: 'input', label: 'Scope validation' },
  semantic_risk_check: { section: 'input', label: 'Semantic risk check' },
  toxicity_check: { section: 'input', label: 'Content safety check' },
  presidio_check: { section: 'input', label: 'PII detection' },
  gliner_check: { section: 'input', label: 'PII detection' },
  pii_redact: { section: 'input', label: 'PII detection' },
  query_rewrite: { section: 'rag', label: 'Query understanding' },
  search_documents: { section: 'rag', label: 'RAG search' },
  query_analytics: { section: 'rag', label: 'Analytics query' },
  generate_report: { section: 'rag', label: 'Report generation' },
  list_my_projects: { section: 'rag', label: 'Project lookup' },
  route: { section: 'llm', label: 'Request routing' },
  select_agent: { section: 'rag', label: 'Agent routing' },
  generate: { section: 'llm', label: 'Model response' },
  synthesize: { section: 'llm', label: 'Response synthesis' },
  system_prompt_leak_check: { section: 'output', label: 'System-prompt leak check' },
  output_citation_check: { section: 'output', label: 'Citation validation' },
  groundedness_check: { section: 'output', label: 'Groundedness validation' },
}

// Agents whose steps are never a Guardrails pass/redact/block decision — get
// their section straight from the catalog rather than positional splitting.
const FIXED_SECTION_AGENTS = new Set([
  'Access', 'Supervisor', 'Planner Agent', 'Retrieval Agent', 'SQL Agent', 'Report Agent', 'Project Agent', 'Model',
  'Response Synthesizer',
])
const LLM_AGENTS = new Set(['Planner Agent', 'Model', 'Response Synthesizer'])
// The SUBSET of FIXED_SECTION_AGENTS that marks "real RAG/LLM processing has
// started" for the purposes of the input-vs-output Guardrails split below.
// Deliberately excludes 'Access' — the authorization step always runs
// first, before any input guardrail, so its presence must not flip every
// following Guardrails-agent step (which are the actual input checks) into
// being misread as output.
const PROCESSING_TRANSITION_AGENTS = new Set([
  'Planner Agent', 'Retrieval Agent', 'SQL Agent', 'Report Agent', 'Project Agent', 'Model', 'Response Synthesizer',
])

// Some checks' raw detail text embeds internals this panel must never show
// verbatim — a classifier confidence score, a matched internal example
// phrase, an admin-configured topic list entry, a contradiction score. Every
// backend check module (services/guardrails/*.py) was read directly to
// confirm this: pii.py, presidio_check.py, gliner_check.py,
// custom_word_check.py, custom_regex_check.py, scope.py, output.py,
// citation_rail.py, destructive.py, toxicity_check.py, secrets.py, length.py,
// injection.py, and groundedness_check.py's own non-PASSED-override detail
// all confirmed SAFE — no score/threshold ever enters their detail string,
// on either PASS or BLOCK. Exactly two modules embed a raw score even on
// their PASS side — semantic_check.py ("best score=0.43") and
// deberta_injection_check.py ("score=1.00") — plus scope_semantic_check.py,
// already covered below. A fixed, generic message is used instead, per
// status, for those three.
const SANITIZED_MESSAGE: Partial<Record<string, Partial<Record<ActivityStatus, string>>>> = {
  deberta_injection_check: { PASSED: 'Passed', BLOCKED: 'Classified as a possible prompt injection' },
  toxicity_check: { BLOCKED: 'Flagged for potentially harmful content' },
  semantic_risk_check: { PASSED: 'Passed', BLOCKED: 'Elevated semantic risk detected' },
  // scope_check (keyword-based) blocks immediately, never deferred like
  // scope_semantic_check's four names — its raw detail otherwise echoes the
  // literal configured deny-keyword phrase (e.g. "Matched denied topic:
  // 'weather forecast'"), an internal-config leak the same as a score would
  // be. Same wording as scope_semantic_check's BLOCKED override below: both
  // reasons already collapse to the identical backend reply text
  // (response_generator.py's scope_keyword/semantic_scope), so the trace
  // detail should read the same regardless of which of the two caught it.
  scope_check: { BLOCKED: 'This question is outside the supported enterprise knowledge scope' },
  scope_semantic_check: { PASSED: 'Passed', BLOCKED: 'This question is outside the supported enterprise knowledge scope' },
  // Same treatment as scope_semantic_check above, and for the same reason:
  // its PASS detail is scope_semantic_check.py's "Closest configured topic:
  // <topic> (score=0.62)", which leaks both an admin-configured topic string
  // and a raw similarity score.
  scope_semantic_recheck: {
    PASSED: 'Re-checked after redaction — in scope',
    BLOCKED: 'This question is outside the supported enterprise knowledge scope',
  },
  // scope_semantic_mixed's raw detail is a per-clause breakdown with real
  // clause text (see scope_semantic_check.py's docstring on why that's safe
  // for /traces but not for this sanitized panel) — collapsed to a generic
  // line here. The specific, friendlier explanation ("I can help with the
  // part about X...") is what actually reaches the user, as the chat
  // reply itself, not this trace label.
  scope_semantic_mixed: { BLOCKED: 'Part of this question is outside the supported enterprise knowledge scope' },
  scope_unclear_pii: { BLOCKED: 'Request unclear and may involve personal information' },
  scope_unclear_document: { BLOCKED: 'Request unclear — resembles a document reference' },
  scope_unclear_context: { BLOCKED: 'Request unclear — more context needed' },
  groundedness_check: {
    PASSED: 'Passed',
    BLOCKED: 'Response could not be verified against retrieved sources',
  },
}

export const STATUS_RANK: Record<ActivityStatus, number> = {
  BLOCKED: 5, REDACTED: 4, DETECTED: 3, FLAGGED: 2, PASSED: 1, SKIPPED: 0,
}

function describeOutcome(step: ChatTraceStep, sanitize: boolean): { status: ActivityStatus; message: string } {
  const parsed = parseTraceStep(step)
  let status: ActivityStatus
  let message: string

  if (step.tool === 'search_documents' && /contain PII|flagged for suspicious/.test(parsed.detail)) {
    // Audit-only visibility scan on retrieved chunks — detected but
    // non-blocking, so it's neither a bare PASSED nor a REDACTED/BLOCKED.
    // Checked first: search_documents' summary has no pass:/block: prefix
    // at all (parsed.action is always null for it), so this must not be
    // shadowed by the null-action branch below.
    status = 'FLAGGED'
    message = parsed.detail
  } else if (parsed.action === null) {
    // Informational step with no pass/redact/block verdict (e.g. the
    // deterministic tool-call trace entries) — always safe to show as-is,
    // these never carry a score, threshold, or raw PII.
    status = 'PASSED'
    message = parsed.detail
  } else if (parsed.action === 'block') {
    status = 'BLOCKED'
    message = parsed.detail
  } else if (parsed.action === 'redact') {
    status = 'REDACTED'
    message = parsed.detail
  } else {
    // A "pass: <detail>" check — surface the real detail rather than a bare
    // "Passed": every check name reachable here was already reviewed (see
    // this file's header comment) and confirmed not to embed a score/
    // threshold/raw value in its PASS-side detail, EXCEPT
    // scope_semantic_check, groundedness_check, semantic_risk_check, and
    // deberta_injection_check, whose PASSED entries in SANITIZED_MESSAGE
    // below force them back to a bare "Passed" — that override, not this line, is
    // what keeps their score out of the UI.
    status = 'PASSED'
    message = parsed.detail
  }

  // sanitize=false is used ONLY by the admin-only Traces page
  // (routers/traces.py::list_traces() / pages/TracesPage.tsx) — gated
  // behind the same VIEW_AUDIT_LOGS permission the rest of the audit trail
  // requires, so this is the one place a classifier's real confidence
  // score is deliberately shown, to the same trusted roles who already see
  // it in the raw API response, not a new leak surface.
  if (!sanitize) return { status, message }

  const override = SANITIZED_MESSAGE[step.tool]?.[status]
  return { status, message: override ?? message }
}

export interface ActivityItem {
  key: string
  label: string
  status: ActivityStatus
  message: string
}

export interface ActivitySectionGroup {
  key: ActivitySection
  title: string
  items: ActivityItem[]
}

export interface ActivityTimeline {
  sections: ActivitySectionGroup[]
  finalStatus: 'ALLOWED' | 'BLOCKED'
}

const SECTION_TITLES: Record<ActivitySection, string> = {
  input: 'Input Security',
  rag: 'RAG & Access Security',
  llm: 'LLM',
  output: 'Output Security',
}
const SECTION_ORDER: ActivitySection[] = ['input', 'rag', 'llm', 'output']

/**
 * Groups the real, ordered backend trace into the 4 labeled sections of the
 * Security & Activity panel. A check that runs on both input and output
 * (PII/toxicity) is bucketed by POSITION — before the first
 * RAG/LLM-processing step is input, after is output — matching how
 * pipeline.py itself actually sequences the two passes. Same-labeled steps
 * within a section are merged into one row, keeping the highest-severity
 * outcome (BLOCKED > REDACTED > DETECTED/FLAGGED > PASSED) — every
 * executed check still contributes, nothing is silently dropped, but a
 * request with all-passing PII checks shows one clean "PII detection:
 * Passed" row instead of three near-identical ones.
 */
export function buildActivityTimeline(trace: ChatTraceStep[], opts: { sanitize?: boolean } = {}): ActivityTimeline {
  const sanitize = opts.sanitize ?? true
  let seenProcessing = false
  const grouped = new Map<ActivitySection, Map<string, ActivityItem>>()
  for (const key of SECTION_ORDER) grouped.set(key, new Map())

  for (const step of trace) {
    const info = STAGE_CATALOG[step.tool]
    const label = info?.label ?? step.tool
    let section: ActivitySection
    if (info && FIXED_SECTION_AGENTS.has(step.agent)) {
      section = LLM_AGENTS.has(step.agent) ? 'llm' : 'rag'
    } else {
      section = seenProcessing ? 'output' : 'input'
    }
    if (PROCESSING_TRANSITION_AGENTS.has(step.agent)) seenProcessing = true

    const { status, message } = describeOutcome(step, sanitize)
    const bucket = grouped.get(section)!
    const existing = bucket.get(label)
    if (!existing || STATUS_RANK[status] > STATUS_RANK[existing.status]) {
      bucket.set(label, { key: label, label, status, message })
    }
  }

  const sections = SECTION_ORDER.map((key) => ({
    key, title: SECTION_TITLES[key], items: Array.from(grouped.get(key)!.values()),
  })).filter((s) => s.items.length > 0)

  return { sections, finalStatus: isBlockedResponse(trace) ? 'BLOCKED' : 'ALLOWED' }
}

// Parses the raw numeric confidence/similarity score a handful of checks
// embed in their unsanitized detail text — "best score=0.43",
// "score=0.95", "best=0.30 vs ...", "contradiction score=0.12",
// "confidence=0.62" — into a 0-1 number for the Traces page's per-check
// score scale. Only semantic_risk_check, deberta_injection_check,
// scope_semantic_check (and its scope_unclear_* block variants),
// groundedness_check, and the Supervisor's select_agent routing step ever
// embed one (confirmed by grepping every services/guardrails/*.py module
// for "score="/"best=", and routers/chat.py for "confidence="); every
// other check's message returns null here, same as a check whose message
// happens to carry no such substring. Callers must only ever pass an
// UNSANITIZED message (sanitize: false) — the Security & Activity panel's
// sanitized text has already had the guardrail checks' PASSED/BLOCKED
// wording replaced via SANITIZED_MESSAGE above, so this simply finds
// nothing there for those; select_agent's confidence is never sanitized
// either way (chat.py deliberately shows it in full to every caller), so
// this stays scoped to Traces-page use by convention, not by necessity.
export function extractCheckScore(message: string): number | null {
  const match = /(?:score|best|confidence)=(\d+(?:\.\d+)?)/i.exec(message)
  if (!match) return null
  const value = Number(match[1])
  return Number.isFinite(value) ? Math.min(1, Math.max(0, value)) : null
}

export interface ChecksSummary {
  total: number
  passed: number
  percent: number
  worstStatus: ActivityStatus
}

// A single "how many checks actually passed" score for the Security &
// Activity panel — every ActivityItem across every rendered section counts
// once (sections already dedupe same-labeled steps to their worst outcome,
// so this never double-counts a check that ran more than once). SKIPPED
// items don't count toward the denominator — they never ran, so they're
// neither a pass nor a finding.
export function summarizeChecks(timeline: ActivityTimeline): ChecksSummary {
  const items = timeline.sections.flatMap((s) => s.items).filter((i) => i.status !== 'SKIPPED')
  const total = items.length
  const passed = items.filter((i) => i.status === 'PASSED').length
  const percent = total === 0 ? 100 : Math.round((passed / total) * 100)
  const worstStatus = items.reduce<ActivityStatus>(
    (worst, item) => (STATUS_RANK[item.status] > STATUS_RANK[worst] ? item.status : worst),
    'PASSED',
  )
  return { total, passed, percent, worstStatus }
}

// How far a request got through the 4-section pipeline (Input Security ->
// RAG & Access Security -> LLM -> Output Security) before either completing
// or being blocked — a coarse, deterministic measure for the Traces page's
// per-row progress bar. A fully ALLOWED request always has all 4 sections
// present (Access authorization alone guarantees the 'rag' section even on
// a general_conversation turn with no tool calls), so this naturally lands
// at 100% for every non-blocked trace; a request blocked at, say,
// prompt_injection_check never reaches 'rag'/'llm'/'output' at all, so its
// trace only ever has the 'input' section — 25%.
export function pipelineCompletionPercent(trace: ChatTraceStep[]): number {
  const { sections } = buildActivityTimeline(trace)
  return Math.round((sections.length / SECTION_ORDER.length) * 100)
}
