import { useEffect, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Plus, Trash2 } from 'lucide-react'
import { getMyPreferences, updateMyPreferences } from '@/api/users'
import { useAuth } from '@/context/AuthContext'
import { getApiError } from '@/lib/apiError'
import { toast } from '@/lib/toast'
import { PageHeader } from '@/components/layout/PageHeader'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { FullPageSpinner } from '@/components/ui/Spinner'

interface Row {
  id: string
  key: string
  value: string
}

let rowSeq = 0
function newRow(key = '', value = ''): Row {
  rowSeq += 1
  return { id: `row-${rowSeq}`, key, value }
}

export function SettingsPage() {
  const { user, capabilities } = useAuth()
  const [rows, setRows] = useState<Row[]>([])

  const preferencesQuery = useQuery({
    queryKey: ['preferences', user?.id],
    queryFn: () => getMyPreferences(user!.id),
    enabled: Boolean(user),
  })

  useEffect(() => {
    if (preferencesQuery.data) {
      const entries = Object.entries(preferencesQuery.data)
      setRows(entries.length > 0 ? entries.map(([k, v]) => newRow(k, v)) : [newRow()])
    }
  }, [preferencesQuery.data])

  const saveMutation = useMutation({
    mutationFn: (updates: Record<string, string>) => updateMyPreferences(user!.id, updates),
    onSuccess: () => toast.success('Preferences saved'),
    onError: (err) => toast.error(getApiError(err, "Couldn't save preferences.").message),
  })

  function updateRow(id: string, field: 'key' | 'value', value: string) {
    setRows((prev) => prev.map((r) => (r.id === id ? { ...r, [field]: value } : r)))
  }

  function removeRow(id: string) {
    setRows((prev) => prev.filter((r) => r.id !== id))
  }

  function addRow() {
    setRows((prev) => [...prev, newRow()])
  }

  function handleSave() {
    const updates: Record<string, string> = {}
    for (const row of rows) {
      const key = row.key.trim()
      if (key) updates[key] = row.value
    }
    saveMutation.mutate(updates)
  }

  return (
    <div>
      <PageHeader title="Settings" description="Your account and assistant preferences" />

      <div className="max-w-2xl space-y-6 p-6">
        <Card>
          <CardHeader>
            <h2 className="text-sm font-semibold text-ink">Account</h2>
          </CardHeader>
          <CardBody className="space-y-2 text-sm">
            <div className="flex justify-between">
              <span className="text-neutral-500">Name</span>
              <span className="font-medium text-ink">{user?.display_name ?? '—'}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-neutral-500">Email</span>
              <span className="font-medium text-ink">{user?.email}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-neutral-500">Role</span>
              <span className="font-medium text-ink">{capabilities?.display_name ?? user?.role}</span>
            </div>
          </CardBody>
        </Card>

        <Card>
          <CardHeader className="flex items-center justify-between">
            <div>
              <h2 className="text-sm font-semibold text-ink">Assistant preferences</h2>
              <p className="mt-0.5 text-xs text-neutral-500">
                Free-form notes the assistant keeps in mind for your conversations, e.g. "response_style: concise".
              </p>
            </div>
          </CardHeader>
          <CardBody>
            {preferencesQuery.isLoading ? (
              <FullPageSpinner />
            ) : (
              <div className="space-y-2">
                {rows.map((row) => (
                  <div key={row.id} className="flex items-center gap-2">
                    <Input
                      value={row.key}
                      onChange={(e) => updateRow(row.id, 'key', e.target.value)}
                      placeholder="key"
                      className="flex-1"
                    />
                    <Input
                      value={row.value}
                      onChange={(e) => updateRow(row.id, 'value', e.target.value)}
                      placeholder="value"
                      className="flex-1"
                    />
                    <button
                      type="button"
                      onClick={() => removeRow(row.id)}
                      aria-label="Remove preference"
                      className="shrink-0 rounded-md p-2 text-neutral-400 transition-colors hover:bg-red-50 hover:text-red-600"
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </div>
                ))}

                <div className="flex items-center justify-between pt-2">
                  <Button variant="ghost" size="sm" onClick={addRow}>
                    <Plus className="h-4 w-4" /> Add preference
                  </Button>
                  <Button size="sm" onClick={handleSave} loading={saveMutation.isPending}>
                    Save changes
                  </Button>
                </div>
              </div>
            )}
          </CardBody>
        </Card>
      </div>
    </div>
  )
}
