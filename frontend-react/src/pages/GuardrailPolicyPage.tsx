import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronRight, Plus, RotateCcw, ServerCrash, ShieldAlert } from 'lucide-react'
import {
  createGuardrailPolicy, listGuardrailPolicies, listGuardrailPolicyVersions, rollbackGuardrailPolicy,
  updateGuardrailPolicy,
} from '@/api/guardrailPolicies'
import { listCopilotPolicies } from '@/api/policyCopilot'
import { getApiError } from '@/lib/apiError'
import { toast } from '@/lib/toast'
import { PageHeader } from '@/components/layout/PageHeader'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { Card, CardBody } from '@/components/ui/Card'
import { SkeletonRows } from '@/components/ui/Skeleton'
import { StateMessage } from '@/components/ui/StateMessage'
import { Tabs } from '@/components/ui/Tabs'
import { Toggle } from '@/components/ui/Toggle'
import { PolicyCopilotTab } from '@/components/policy/PolicyCopilotTab'
import { cn } from '@/lib/cn'
import type { GuardrailPolicy, GuardrailPolicyCategory } from '@/types/guardrailPolicies'
import type { CopilotPolicyRow } from '@/types/policyCopilot'

const TAB_OPTIONS = [
  { value: 'copilot', label: 'Policy Copilot' },
  { value: 'policies', label: 'Policies' },
]

const CATEGORIES: GuardrailPolicyCategory[] = ['PII', 'REGEX', 'WORD_FILTER', 'SEMANTIC', 'PROMPT_INJECTION', 'MESSAGE_LIMIT']
// The filter/browse buttons exclude PII — every PII entity, custom or
// running on the built-in default, now has its own always-visible row in
// AllPiiEntitiesPanel below, so a PII filter button here would just show a
// partial, easy-to-mistake-for-complete subset (rows with a DB row only) of
// what that panel already covers completely. PII is still a valid choice in
// CreatePolicyPanel's own category dropdown (CATEGORIES, unchanged) — an
// admin can still hand-create a custom PII row, same as before.
const FILTER_CATEGORIES: GuardrailPolicyCategory[] = CATEGORIES.filter((c) => c !== 'PII')
const ACTIONS = ['ALLOW', 'FLAG', 'MASK', 'REDACT', 'BLOCK', 'ESCALATE']
const DETECTION_SOURCES = ['regex', 'presidio', 'gliner'] as const

const ACTION_TONE: Record<string, 'green' | 'red' | 'amber' | 'neutral' | 'blue'> = {
  ALLOW: 'green',
  FLAG: 'amber',
  MASK: 'blue',
  REDACT: 'blue',
  BLOCK: 'red',
  ESCALATE: 'red',
}

function formatDate(value: string): string {
  return new Date(value).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

export function GuardrailPolicyPage() {
  const [tab, setTab] = useState('copilot')

  return (
    <div>
      <PageHeader
        title="Guardrail Policy Center"
        description="Manage PII, regex, word, and threshold policies for the guardrail runtime — Admin and CEO only"
      />
      <Tabs options={TAB_OPTIONS} value={tab} onChange={setTab} />
      <div key={tab} className="animate-fade-slide-up">
        {tab === 'copilot' && <PolicyCopilotTab />}
        {tab === 'policies' && <PoliciesTab />}
      </div>
    </div>
  )
}

// ---- Configuration fields (shared by the create form and the playground) --

function defaultConfigFor(category: string): Record<string, unknown> {
  switch (category) {
    case 'PII':
      return {
        // Seeded to match the backend's uniform safe default for personal
        // data (guardrail_policy/pii_policy.py::_PERSONAL_DATA), so creating
        // a row and saving it unchanged reproduces current behaviour rather
        // than silently weakening it to the old FLAG tier.
        entity: '', input_action: 'MASK', output_action: 'REDACT', severity: 'MEDIUM',
        detection_sources: ['regex', 'presidio', 'gliner'], redaction_format: '',
      }
    case 'REGEX':
      return { pattern: '', entity: '' }
    case 'WORD_FILTER':
      return { word: '', match_mode: 'WORD', case_sensitive: false }
    case 'SEMANTIC':
    case 'PROMPT_INJECTION':
      return { threshold: 0.8 }
    case 'MESSAGE_LIMIT':
      return { max_input_chars: 4000, max_output_chars: 4000 }
    default:
      return {}
  }
}

function ConfigurationFields({
  category,
  value,
  onChange,
}: {
  category: string
  value: Record<string, unknown>
  onChange: (next: Record<string, unknown>) => void
}) {
  const set = (key: string, v: unknown) => onChange({ ...value, [key]: v })
  const inputClass =
    'w-full rounded-lg border border-neutral-300 bg-surface px-3 py-1.5 text-sm transition-colors duration-150 focus:outline-none focus:ring-2 focus:ring-accent-400'

  if (category === 'PII') {
    const sources = Array.isArray(value.detection_sources) ? (value.detection_sources as string[]) : []
    return (
      <div className="space-y-3">
        <div className="grid grid-cols-2 gap-3">
          <label className="text-xs font-medium text-neutral-500">
            Entity
            <input
              className={cn(inputClass, 'mt-1')}
              value={String(value.entity ?? '')}
              onChange={(e) => set('entity', e.target.value.toUpperCase())}
              placeholder="SSN, PASSWORD, API_KEY..."
            />
          </label>
          <label className="text-xs font-medium text-neutral-500">
            Severity
            <select className={cn(inputClass, 'mt-1')} value={String(value.severity ?? 'MEDIUM')} onChange={(e) => set('severity', e.target.value)}>
              {['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'].map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          </label>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <label className="text-xs font-medium text-neutral-500">
            Input action
            <select
              className={cn(inputClass, 'mt-1')}
              value={String(value.input_action ?? 'MASK')}
              onChange={(e) => set('input_action', e.target.value)}
            >
              {ACTIONS.map((a) => (
                <option key={a} value={a}>{a}</option>
              ))}
            </select>
          </label>
          <label className="text-xs font-medium text-neutral-500">
            Output action
            <select
              className={cn(inputClass, 'mt-1')}
              value={String(value.output_action ?? 'REDACT')}
              onChange={(e) => set('output_action', e.target.value)}
            >
              {ACTIONS.map((a) => (
                <option key={a} value={a}>{a}</option>
              ))}
            </select>
          </label>
        </div>
        <div>
          <p className="text-xs font-medium text-neutral-500">Detection sources</p>
          <div className="mt-1 flex gap-4">
            {DETECTION_SOURCES.map((source) => (
              <label key={source} className="flex items-center gap-1.5 text-xs text-neutral-600">
                <input
                  type="checkbox"
                  checked={sources.includes(source)}
                  onChange={(e) =>
                    set('detection_sources', e.target.checked ? [...sources, source] : sources.filter((s) => s !== source))
                  }
                />
                {source}
              </label>
            ))}
          </div>
        </div>
        <label className="block text-xs font-medium text-neutral-500">
          Redaction format (optional — overrides the default placeholder token)
          <input
            className={cn(inputClass, 'mt-1 font-mono')}
            value={String(value.redaction_format ?? '')}
            onChange={(e) => set('redaction_format', e.target.value)}
            placeholder="[REDACTED_SSN]"
          />
        </label>
      </div>
    )
  }

  if (category === 'REGEX') {
    return (
      <div className="grid grid-cols-2 gap-3">
        <label className="col-span-2 text-xs font-medium text-neutral-500">
          Pattern
          <input
            className={cn(inputClass, 'mt-1 font-mono')}
            value={String(value.pattern ?? '')}
            onChange={(e) => set('pattern', e.target.value)}
            placeholder="CONFIDENTIAL-[0-9]+"
          />
        </label>
        <label className="text-xs font-medium text-neutral-500">
          Entity label
          <input className={cn(inputClass, 'mt-1')} value={String(value.entity ?? '')} onChange={(e) => set('entity', e.target.value.toUpperCase())} />
        </label>
      </div>
    )
  }

  if (category === 'WORD_FILTER') {
    return (
      <div className="grid grid-cols-2 gap-3">
        <label className="text-xs font-medium text-neutral-500">
          Word / phrase
          <input className={cn(inputClass, 'mt-1')} value={String(value.word ?? '')} onChange={(e) => set('word', e.target.value)} />
        </label>
        <label className="text-xs font-medium text-neutral-500">
          Match mode
          <select className={cn(inputClass, 'mt-1')} value={String(value.match_mode ?? 'WORD')} onChange={(e) => set('match_mode', e.target.value)}>
            {['EXACT', 'WORD', 'PHRASE', 'REGEX'].map((m) => (
              <option key={m} value={m}>{m}</option>
            ))}
          </select>
        </label>
        <label className="col-span-2 flex items-center gap-2 text-xs font-medium text-neutral-500">
          <Toggle checked={Boolean(value.case_sensitive)} onChange={(v) => set('case_sensitive', v)} label="Case sensitive" />
          Case sensitive
        </label>
      </div>
    )
  }

  if (category === 'SEMANTIC' || category === 'PROMPT_INJECTION') {
    return (
      <label className="block text-xs font-medium text-neutral-500">
        Risk threshold (0.00 – 1.00)
        <input
          type="number"
          min={0}
          max={1}
          step={0.01}
          className={cn(inputClass, 'mt-1')}
          value={String(value.threshold ?? 0.8)}
          onChange={(e) => set('threshold', Number(e.target.value))}
        />
      </label>
    )
  }

  if (category === 'MESSAGE_LIMIT') {
    return (
      <div className="grid grid-cols-2 gap-3">
        <label className="text-xs font-medium text-neutral-500">
          Max input characters
          <input
            type="number" min={100} max={100000} className={cn(inputClass, 'mt-1')}
            value={String(value.max_input_chars ?? 4000)} onChange={(e) => set('max_input_chars', Number(e.target.value))}
          />
        </label>
        <label className="text-xs font-medium text-neutral-500">
          Max output characters
          <input
            type="number" min={100} max={100000} className={cn(inputClass, 'mt-1')}
            value={String(value.max_output_chars ?? 4000)} onChange={(e) => set('max_output_chars', Number(e.target.value))}
          />
        </label>
      </div>
    )
  }

  return null
}

// ---- Policies -------------------------------------------------------

function PoliciesTab() {
  const queryClient = useQueryClient()
  const [category, setCategory] = useState<string | undefined>(undefined)
  const [showCreate, setShowCreate] = useState(false)
  const [expandedId, setExpandedId] = useState<string | null>(null)

  const query = useQuery({
    queryKey: ['guardrail-policies', category],
    // PII rows live entirely in AllPiiEntitiesPanel above — filtered out
    // here so a custom PII rule an admin created doesn't appear twice under
    // two different presentations on the same tab.
    queryFn: () => listGuardrailPolicies(category),
    select: (data) => ({ ...data, items: data.items.filter((p) => p.category !== 'PII') }),
  })

  return (
    <div className="p-6">
      <AllPiiEntitiesPanel />

      <div className="mb-4 flex items-center justify-between">
        <div className="flex flex-wrap gap-1.5">
          <button
            type="button"
            onClick={() => setCategory(undefined)}
            className={cn(
              'rounded-full px-3 py-1 text-xs font-medium transition-colors duration-150',
              category === undefined ? 'bg-accent-600 text-white' : 'bg-neutral-100 text-neutral-600 hover:bg-neutral-200',
            )}
          >
            All
          </button>
          {FILTER_CATEGORIES.map((c) => (
            <button
              key={c}
              type="button"
              onClick={() => setCategory(c)}
              className={cn(
                'rounded-full px-3 py-1 text-xs font-medium transition-colors duration-150',
                category === c ? 'bg-accent-600 text-white' : 'bg-neutral-100 text-neutral-600 hover:bg-neutral-200',
              )}
            >
              {c}
            </button>
          ))}
        </div>
        <Button size="sm" onClick={() => setShowCreate((v) => !v)}>
          <Plus className="h-3.5 w-3.5" /> New policy
        </Button>
      </div>

      {showCreate && (
        <CreatePolicyPanel
          onCreated={() => {
            setShowCreate(false)
            void queryClient.invalidateQueries({ queryKey: ['guardrail-policies'] })
            void queryClient.invalidateQueries({ queryKey: ['copilot', 'policies'] })
          }}
          onCancel={() => setShowCreate(false)}
        />
      )}

      {query.isLoading ? (
        <SkeletonRows rows={6} cols={6} />
      ) : query.isError ? (
        <StateMessage
          icon={ServerCrash}
          tone="error"
          title="Couldn't load policies"
          description={getApiError(query.error, 'Something went wrong.').message}
          action={
            <Button size="sm" variant="secondary" onClick={() => void query.refetch()}>
              Try again
            </Button>
          }
        />
      ) : query.data!.items.length === 0 ? (
        <StateMessage icon={ShieldAlert} title="No policies yet" description="Create one to get started." />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-neutral-200 text-neutral-500">
                <th className="pb-2 pr-4 font-medium" />
                <th className="pb-2 pr-4 font-medium">Name</th>
                <th className="pb-2 pr-4 font-medium">Category</th>
                <th className="pb-2 pr-4 font-medium">Action</th>
                <th className="pb-2 pr-4 font-medium">Priority</th>
                <th className="pb-2 pr-4 font-medium">Version</th>
                <th className="pb-2 pr-4 font-medium">Enabled</th>
              </tr>
            </thead>
            <tbody>
              {query.data!.items.map((p, i) => (
                <PolicyRow
                  key={p.id}
                  policy={p}
                  index={i}
                  expanded={expandedId === p.id}
                  onToggleExpand={() => setExpandedId((cur) => (cur === p.id ? null : p.id))}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

/** Every known PII entity — custom row or built-in default — with its
 * resolved action, directly editable whether or not a row exists yet
 * (PiiActionControls creates the row on first change). Sourced from the
 * same live resolver the Copilot's own sidebar and simulation use
 * (services/policy_copilot/entities_view.py via GET /policy-copilot/policies),
 * so this can never show a different action than what actually runs at
 * request time. */
function AllPiiEntitiesPanel() {
  const entitiesQuery = useQuery({ queryKey: ['copilot', 'policies'], queryFn: listCopilotPolicies })
  const rowsQuery = useQuery({ queryKey: ['guardrail-policies', 'PII'], queryFn: () => listGuardrailPolicies('PII') })

  if (entitiesQuery.isLoading || rowsQuery.isLoading) {
    return (
      <div className="mb-6">
        <SkeletonRows rows={8} cols={5} />
      </div>
    )
  }
  if (entitiesQuery.isError) {
    return (
      <div className="mb-6">
        <StateMessage
          icon={ServerCrash}
          tone="error"
          title="Couldn't load PII policies"
          description={getApiError(entitiesQuery.error, 'Something went wrong.').message}
          action={
            <Button size="sm" variant="secondary" onClick={() => void entitiesQuery.refetch()}>
              Try again
            </Button>
          }
        />
      </div>
    )
  }

  const rowByEntity = new Map<string, GuardrailPolicy>(
    (rowsQuery.data?.items ?? [])
      .map((r) => [String((r.configuration as Record<string, unknown> | undefined)?.entity ?? '').toUpperCase(), r]),
  )
  const entities = entitiesQuery.data?.policies ?? []

  return (
    <div className="mb-6">
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-ink">PII entity policies</h3>
        <p className="text-xs text-neutral-500">
          Every recognised PII type, whether or not it has a custom rule yet — {entities.length} total.
        </p>
      </div>
      <div className="overflow-x-auto rounded-lg border border-neutral-200">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-neutral-200 bg-neutral-50 text-neutral-500">
              <th className="px-3 py-2 font-medium">Entity</th>
              <th className="px-3 py-2 font-medium">Action</th>
              <th className="px-3 py-2 font-medium">Source</th>
              <th className="px-3 py-2 font-medium">Role exceptions</th>
            </tr>
          </thead>
          <tbody>
            {entities.map((entity, i) => (
              <PiiEntityRow key={entity.entity} entity={entity} row={rowByEntity.get(entity.entity)} index={i} />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function PiiEntityRow({
  entity, row, index,
}: {
  entity: CopilotPolicyRow
  row: GuardrailPolicy | undefined
  index: number
}) {
  const overrideEntries = Object.entries(entity.role_overrides ?? {})
  const capabilityTone = {
    ENABLED: 'green',
    DISABLED: 'amber',
    PENDING_APPROVAL: 'blue',
    UNSUPPORTED: 'red',
  } as const

  const capabilityLabel = {
    ENABLED: 'detector available',
    DISABLED: 'detector disabled',
    PENDING_APPROVAL: 'approval pending',
    UNSUPPORTED: 'no detector',
  } as const

  return (
    <tr
      className="animate-fade-slide-up border-b border-neutral-100 last:border-0"
      style={{ animationDelay: `${Math.min(index, 20) * 20}ms` }}
    >
      <td className="px-3 py-2 align-top">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="font-mono text-xs font-semibold text-ink">{entity.entity}</span>
          {entity.critical && <Badge tone="red">critical</Badge>}
          {entity.capability_state && (
            <Badge
              tone={capabilityTone[entity.capability_state] ?? 'neutral'}
              title={entity.capability_explanation}
            >
              {capabilityLabel[entity.capability_state] ?? entity.capability_state}
            </Badge>
          )}
          {entity.enforceable && !entity.reliable && <Badge tone="amber">phrasing-sensitive</Badge>}
          {entity.disabled_row_present && !entity.capability_state && <Badge tone="amber">disabled rule — default in force</Badge>}
        </div>
      </td>
      <td className="px-3 py-2 align-top">
        <PiiActionControls entity={entity} row={row} />
      </td>
      <td className="px-3 py-2 align-top text-xs text-neutral-500">
        <div>
          <p>{entity.source === 'custom' ? 'custom rule' : 'built-in default'}</p>
          {entity.capability_source && (
            <p className="text-[11px] text-neutral-400 mt-0.5">
              {entity.capability_source === 'built-in' && 'Built-in detector'}
              {entity.capability_source === 'configured' && 'Configured pattern'}
              {entity.capability_source === 'configurable' && 'Can be configured'}
              {entity.capability_source === 'none' && 'No detector'}
            </p>
          )}
        </div>
      </td>
      <td className="px-3 py-2 align-top text-[11px]">
        {overrideEntries.length === 0 ? (
          <span className="text-neutral-400">none</span>
        ) : (
          <div className="space-y-0.5">
            {overrideEntries.map(([role, actions]) => (
              <p key={role} className="text-amber-700">
                <span className="font-medium">{role}</span>:{' '}
                {[
                  actions.input_action && `in ${actions.input_action}`,
                  actions.output_action && `out ${actions.output_action}`,
                  actions.reveal_last != null && `last ${actions.reveal_last} visible`,
                ]
                  .filter(Boolean)
                  .join(' · ')}
              </p>
            ))}
          </div>
        )}
      </td>
    </tr>
  )
}

function CreatePolicyPanel({
  onCreated, onCancel,
}: {
  onCreated: () => void
  onCancel: () => void
}) {
  const [category, setCategory] = useState<GuardrailPolicyCategory>('REGEX')
  const [policyKey, setPolicyKey] = useState('')
  const [name, setName] = useState('')
  const [action, setAction] = useState('BLOCK')
  const [priority, setPriority] = useState(100)
  const [config, setConfig] = useState<Record<string, unknown>>(defaultConfigFor('REGEX'))

  const createMutation = useMutation({
    mutationFn: () =>
      createGuardrailPolicy({
        policy_key: policyKey, name, category, action, priority, configuration: config,
      }),
    onSuccess: (result) => {
      if (result.status === 'pending_approval') {
        toast.success('Queued for approval — the current protection stays in force until approved')
      } else {
        toast.success('Policy created')
      }
      onCreated()
    },
    onError: (err) => toast.error(getApiError(err, "Couldn't create that policy.").message),
  })

  const inputClass =
    'w-full rounded-lg border border-neutral-300 bg-surface px-3 py-1.5 text-sm transition-colors duration-150 focus:outline-none focus:ring-2 focus:ring-accent-400'

  return (
    <Card className="mb-4 animate-fade-slide-up">
      <CardBody className="space-y-3">
        <div className="grid grid-cols-3 gap-3">
          <label className="text-xs font-medium text-neutral-500">
            Category
            <select
              className={cn(inputClass, 'mt-1')}
              value={category}
              onChange={(e) => {
                const next = e.target.value as GuardrailPolicyCategory
                setCategory(next)
                setConfig(defaultConfigFor(next))
              }}
            >
              {CATEGORIES.map((c) => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
          </label>
          <label className="text-xs font-medium text-neutral-500">
            Policy key
            <input className={cn(inputClass, 'mt-1')} value={policyKey} onChange={(e) => setPolicyKey(e.target.value)} placeholder="pii.ssn" />
          </label>
          <label className="text-xs font-medium text-neutral-500">
            Name
            <input className={cn(inputClass, 'mt-1')} value={name} onChange={(e) => setName(e.target.value)} placeholder="SSN detection" />
          </label>
        </div>

        <ConfigurationFields category={category} value={config} onChange={setConfig} />

        <div className="grid grid-cols-3 gap-3">
          {category !== 'PII' && (
            // PII's action is independent per direction (Input/Output
            // action dropdowns above, inside ConfigurationFields) — the
            // server derives the single top-level action from those and
            // ignores whatever's sent here for this category.
            <label className="text-xs font-medium text-neutral-500">
              Action
              <select className={cn(inputClass, 'mt-1')} value={action} onChange={(e) => setAction(e.target.value)}>
                {ACTIONS.map((a) => (
                  <option key={a} value={a}>{a}</option>
                ))}
              </select>
            </label>
          )}
          <label className="text-xs font-medium text-neutral-500">
            Priority
            <input
              type="number" min={1} max={1000} className={cn(inputClass, 'mt-1')}
              value={priority} onChange={(e) => setPriority(Number(e.target.value))}
            />
          </label>
        </div>

        <div className="flex justify-end gap-2 pt-1">
          <Button size="sm" variant="ghost" onClick={onCancel}>Cancel</Button>
          <Button
            size="sm"
            loading={createMutation.isPending}
            disabled={!policyKey || !name}
            onClick={() => createMutation.mutate()}
          >
            Create policy
          </Button>
        </div>
      </CardBody>
    </Card>
  )
}

/** Per-direction action pickers, editable straight from the list — for every
 * entity, whether or not it has a custom row yet.
 *
 * PII rows carry input_action and output_action independently, so one combined
 * "action" cell cannot express what the row actually does — it shows the worst
 * of the two and hides the rest. Two selects say it plainly and let it be
 * changed without opening a form.
 *
 * When `row` is undefined (the entity is still running on the built-in
 * default), the first change creates a custom row seeded from the entity's
 * current resolved action plus the one field just changed — the same shape
 * "Configure" used to build by hand, just applied on first edit instead of
 * behind a separate form.
 *
 * A weakening change may come back as `pending_approval` rather than applying
 * — true for both the update path (existing critical row) and the create
 * path (a brand-new row weaker than the critical entity's safe default,
 * gated identically by create_policy()); the toast says so instead of
 * implying the change took effect. */
function PiiActionControls({ entity, row }: { entity: CopilotPolicyRow; row: GuardrailPolicy | undefined }) {
  const queryClient = useQueryClient()
  const config: Record<string, unknown> = row
    ? ((row.configuration ?? {}) as Record<string, unknown>)
    : {
        entity: entity.entity,
        input_action: entity.input_action,
        output_action: entity.output_action,
        severity: entity.critical ? 'CRITICAL' : 'MEDIUM',
        ...(entity.reveal_last != null ? { reveal_last: entity.reveal_last } : {}),
      }

  const mutation = useMutation({
    mutationFn: (next: Record<string, unknown>) =>
      row
        ? updateGuardrailPolicy(row.id, {
            expected_version: row.version,
            configuration: { ...config, ...next },
          })
        : createGuardrailPolicy({
            policy_key: `pii.${entity.entity.toLowerCase()}`,
            name: `${entity.entity} PII policy`,
            category: 'PII',
            action: String(config.input_action ?? 'MASK'),
            configuration: { ...config, ...next },
          }),
    onSuccess: (result) => {
      if (result.status === 'pending_approval') {
        toast.success('Queued for approval — the current protection stays in force until approved')
      } else {
        toast.success(row ? 'Policy updated' : 'Policy created')
      }
      void queryClient.invalidateQueries({ queryKey: ['guardrail-policies'] })
      // Only strictly needed on the create path (an entity's row flips from
      // "default" to "custom" in this view) — invalidated unconditionally
      // since it's a cheap no-op refetch on the update path.
      void queryClient.invalidateQueries({ queryKey: ['copilot', 'policies'] })
    },
    onError: (err) =>
      toast.error(getApiError(err, "Couldn't update that policy — it may have changed. Reload and try again.").message),
  })

  const selectClass =
    'rounded border border-neutral-300 bg-surface px-1.5 py-0.5 text-[11px] font-mono transition-colors focus:border-accent-500 focus:outline-none disabled:opacity-50'

  // Only a MASK has trailing characters to reveal — REDACT, BLOCK and
  // ESCALATE replace the value entirely. Showing the control against those
  // would offer a setting that quietly does nothing.
  const masks = config.input_action === 'MASK' || config.output_action === 'MASK'

  return (
    <div className="flex items-center gap-1.5">
      {(['input_action', 'output_action'] as const).map((slot) => (
        <label key={slot} className="flex items-center gap-1">
          <span className="text-[10px] uppercase tracking-wide text-neutral-400">
            {slot === 'input_action' ? 'in' : 'out'}
          </span>
          <select
            className={selectClass}
            disabled={mutation.isPending}
            value={String(config[slot] ?? '')}
            onChange={(e) => mutation.mutate({ [slot]: e.target.value })}
          >
            {ACTIONS.map((a) => (
              <option key={a} value={a}>{a}</option>
            ))}
          </select>
        </label>
      ))}
      {masks && (
        <label className="flex items-center gap-1" title="Trailing characters a MASK leaves visible">
          <span className="text-[10px] uppercase tracking-wide text-neutral-400">reveal</span>
          <select
            className={selectClass}
            disabled={mutation.isPending}
            value={String(config.reveal_last ?? '')}
            onChange={(e) =>
              mutation.mutate({ reveal_last: e.target.value ? Number(e.target.value) : null })
            }
          >
            <option value="">default</option>
            {[1, 2, 3, 4, 5, 6, 7, 8].map((n) => (
              <option key={n} value={n}>last {n}</option>
            ))}
          </select>
        </label>
      )}
    </div>
  )
}

function PolicyRow({
  policy, index, expanded, onToggleExpand,
}: {
  policy: GuardrailPolicy
  index: number
  expanded: boolean
  onToggleExpand: () => void
}) {
  const queryClient = useQueryClient()

  const versionsQuery = useQuery({
    queryKey: ['guardrail-policies', policy.id, 'versions'],
    queryFn: () => listGuardrailPolicyVersions(policy.id),
    enabled: expanded,
  })

  const toggleMutation = useMutation({
    mutationFn: (enabled: boolean) => updateGuardrailPolicy(policy.id, { expected_version: policy.version, enabled }),
    onSuccess: (result) => {
      if (result.status === 'pending_approval') {
        toast.success('Change queued for CEO/Admin approval — this protection stays active until approved')
      } else {
        toast.success('Policy updated')
      }
      void queryClient.invalidateQueries({ queryKey: ['guardrail-policies'] })
    },
    onError: (err) => toast.error(getApiError(err, "Couldn't update that policy — it may have changed. Reload and try again.").message),
  })

  const rollbackMutation = useMutation({
    mutationFn: (targetVersion: number) => rollbackGuardrailPolicy(policy.id, policy.version, targetVersion),
    onSuccess: () => {
      toast.success('Rolled back')
      void queryClient.invalidateQueries({ queryKey: ['guardrail-policies'] })
    },
    onError: (err) => toast.error(getApiError(err, "Couldn't roll back.").message),
  })

  return (
    <>
      <tr
        onClick={onToggleExpand}
        className={cn(
          'animate-fade-slide-up cursor-pointer border-b border-neutral-100 transition-colors duration-150 hover:bg-neutral-50',
          expanded && 'bg-accent-50/60',
        )}
        style={{ animationDelay: `${Math.min(index, 20) * 25}ms` }}
      >
        <td className="py-2.5 pl-1">
          <ChevronRight className={cn('h-3.5 w-3.5 text-neutral-400 transition-transform duration-150', expanded && 'rotate-90')} />
        </td>
        <td className="py-2.5 pr-4 font-medium text-ink">{policy.name}</td>
        <td className="py-2.5 pr-4 text-neutral-600">{policy.category}</td>
        {/* PII rows never reach this table — they're filtered out of the
            query above and shown exclusively in AllPiiEntitiesPanel, which
            renders PiiActionControls itself. */}
        <td className="py-2.5 pr-4" onClick={(e) => e.stopPropagation()}>
          <Badge tone={ACTION_TONE[policy.action] ?? 'neutral'}>{policy.action}</Badge>
        </td>
        <td className="py-2.5 pr-4 tabular-nums text-neutral-600">{policy.priority}</td>
        <td className="py-2.5 pr-4 tabular-nums text-neutral-500">v{policy.version}</td>
        <td className="py-2.5 pr-4" onClick={(e) => e.stopPropagation()}>
          <Toggle
            checked={policy.enabled}
            disabled={toggleMutation.isPending}
            label={`${policy.name} enabled`}
            onChange={(checked) => toggleMutation.mutate(checked)}
          />
        </td>
      </tr>
      {expanded && (
        <tr className="animate-fade-slide-up border-b border-neutral-100 bg-neutral-50/60">
          <td colSpan={7} className="px-4 py-3 text-xs">
            <dl className="mb-3 space-y-1.5">
              <div className="flex gap-2">
                <dt className="shrink-0 font-medium text-neutral-500">Policy key</dt>
                <dd className="text-neutral-600">{policy.policy_key}</dd>
              </div>
              <div className="flex gap-2">
                <dt className="shrink-0 font-medium text-neutral-500">Configuration</dt>
                <dd className="text-neutral-600">{JSON.stringify(policy.configuration)}</dd>
              </div>
              <div className="flex gap-2">
                <dt className="shrink-0 font-medium text-neutral-500">Mode</dt>
                <dd className="text-neutral-600">{policy.mode}</dd>
              </div>
            </dl>
            <p className="mb-1.5 font-medium text-neutral-500">Version history</p>
            {versionsQuery.isLoading ? (
              <p className="text-neutral-400">Loading…</p>
            ) : (
              <div className="space-y-1.5">
                {versionsQuery.data?.map((v) => (
                  <div key={v.version} className="flex items-center gap-2">
                    <Badge tone="neutral">v{v.version}</Badge>
                    <span className="text-neutral-500">
                      {formatDate(v.changed_at)}{v.reason ? ` · ${v.reason}` : ''}
                    </span>
                    {v.version !== policy.version && (
                      <Button
                        size="sm" variant="ghost"
                        loading={rollbackMutation.isPending && rollbackMutation.variables === v.version}
                        onClick={() => rollbackMutation.mutate(v.version)}
                      >
                        <RotateCcw className="h-3 w-3" /> Roll back to v{v.version}
                      </Button>
                    )}
                  </div>
                ))}
              </div>
            )}
          </td>
        </tr>
      )}
    </>
  )
}

// ---- Test playground ---------------------------------------------------

// ---- Approvals (guardrail_policy scoped) --------------------------------

