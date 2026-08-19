export type PolicyAction = 'ALLOW' | 'FLAG' | 'MASK' | 'REDACT' | 'BLOCK' | 'ESCALATE'
export type PolicyLocation = 'INPUT' | 'OUTPUT'
export type RiskLevel = 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL'

export interface CopilotChange {
  entity: string
  location: PolicyLocation
  action: PolicyAction
  /** Trailing characters a MASK leaves visible; null = the entity's default shape. */
  reveal_last: number | null
}

export interface CopilotRoleException {
  role: string
  location: PolicyLocation
  action: PolicyAction
}

/** What one role sees after the change. Sent for every role, not only the
 *  exempted ones — an approver granting HR an exception has to see, in the
 *  same table, that nobody else got one. */
export interface CopilotRoleEffect {
  role: string
  label: string
  action: PolicyAction
  sample: string | null
  is_exception: boolean
}

export interface CopilotImpact {
  entity: string
  location: PolicyLocation
  current_action: PolicyAction
  proposed_action: PolicyAction
  /** WEAKENS is the one an approver must not miss. */
  direction: 'WEAKENS' | 'STRENGTHENS' | 'UNCHANGED'
  risk: RiskLevel
  exposure: 'HIGH' | 'MEDIUM' | 'LOW' | 'NONE'
  affected_roles: string[]
  affected_flows: string[]
  blast_radius: string
  notes: string[]
  /** Rendered from synthetic values only — never real data. */
  current_sample: string | null
  proposed_sample: string | null
  reveal_last: number | null
  role_effects: CopilotRoleEffect[]
}

export interface CopilotTraceStage {
  stage: string
  status: string
  detail: string
}

export interface CopilotChatResponse {
  reply: string
  intent: string
  method: 'deterministic' | 'llm' | 'refused'
  changes: CopilotChange[]
  role_exceptions: CopilotRoleException[]
  valid: boolean
  errors: string[]
  warnings: string[]
  risk: RiskLevel
  impacts: CopilotImpact[]
  proposal_id: string | null
  requires_approval: boolean
  trace: CopilotTraceStage[]
}

export interface CopilotPolicyRow {
  entity: string
  input_action: PolicyAction
  output_action: PolicyAction
  /** "custom" = an enabled row is in force; "default" = built-in safe default. */
  source: 'custom' | 'default'
  /** A row exists but is off — the entity is still protected, by the default. */
  disabled_row_present: boolean
  dry_run: boolean
  /** Trailing characters a MASK leaves visible; null = the entity's default shape. */
  reveal_last: number | null
  /** Roles that resolve to something other than the base actions above.
   *  Only the slots that actually differ are present. */
  role_overrides: Record<string, Partial<{ input_action: PolicyAction; output_action: PolicyAction; reveal_last: number }>>
  detection: 'DETERMINISTIC' | 'SHAPE' | 'CONTEXTUAL' | 'NONE'
  detector: string
  enforceable: boolean
  reliable: boolean
  critical: boolean
  warning: string | null
  /** Detector capability state: UNSUPPORTED | DISABLED | PENDING_APPROVAL | ENABLED */
  capability_state?: 'UNSUPPORTED' | 'DISABLED' | 'PENDING_APPROVAL' | 'ENABLED'
  /** Where the detector comes from: 'built-in' | 'configured' | 'configurable' | 'none' */
  capability_source?: 'built-in' | 'configured' | 'configurable' | 'none'
  /** Human-readable explanation of the detector capability */
  capability_explanation?: string
  /** Whether this entity can be configured with a regex pattern */
  configurable?: boolean
}

export interface CopilotProposal {
  id: string
  action: string
  status: string
  role: string | null
  payload: Record<string, unknown> | null
  created_at: string | null
  decided_at: string | null
}
