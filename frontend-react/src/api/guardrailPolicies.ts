import { api } from '@/api/client'
import type {
  GuardrailPolicy,
  GuardrailPolicyCreateInput,
  GuardrailPolicyListResponse,
  GuardrailPolicyTestInput,
  GuardrailPolicyTestResult,
  GuardrailPolicyUpdateInput,
  GuardrailPolicyUpdateResponse,
  GuardrailPolicyVersion,
} from '@/types/guardrailPolicies'

export async function listGuardrailPolicies(category?: string): Promise<GuardrailPolicyListResponse> {
  const { data } = await api.get<GuardrailPolicyListResponse>('/guardrail-policies', { params: { category, limit: 200 } })
  return data
}

export async function createGuardrailPolicy(input: GuardrailPolicyCreateInput): Promise<GuardrailPolicyUpdateResponse> {
  // Creating a row that weakens a critical PII entity's protection is queued
  // for approval rather than applied (same gate update_policy() enforces) —
  // the backend signals that by raising a 202, which axios resolves as
  // success rather than rejecting, so the only way to tell the two apart is
  // the status code, not a thrown error. See routers/guardrail_policies.py's
  // create_policy for the 201-vs-202 contract this mirrors.
  const response = await api.post<GuardrailPolicy>('/guardrail-policies', input)
  if (response.status === 202) {
    return { status: 'pending_approval', policy: null, approval_id: null }
  }
  return { status: 'applied', policy: response.data, approval_id: null }
}

export async function updateGuardrailPolicy(
  id: string,
  input: GuardrailPolicyUpdateInput,
): Promise<GuardrailPolicyUpdateResponse> {
  const { data } = await api.patch<GuardrailPolicyUpdateResponse>(`/guardrail-policies/${id}`, input)
  return data
}

export async function rollbackGuardrailPolicy(
  id: string,
  expectedVersion: number,
  targetVersion: number,
): Promise<GuardrailPolicy> {
  const { data } = await api.post<GuardrailPolicy>(`/guardrail-policies/${id}/rollback`, {
    expected_version: expectedVersion,
    target_version: targetVersion,
  })
  return data
}

export async function listGuardrailPolicyVersions(id: string): Promise<GuardrailPolicyVersion[]> {
  const { data } = await api.get<GuardrailPolicyVersion[]>(`/guardrail-policies/${id}/versions`)
  return data
}

export async function testGuardrailPolicy(input: GuardrailPolicyTestInput): Promise<GuardrailPolicyTestResult> {
  const { data } = await api.post<GuardrailPolicyTestResult>('/guardrail-policies/test', input)
  return data
}
