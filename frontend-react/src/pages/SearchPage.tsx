import { type FormEvent, useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { FileSearch, Search as SearchIcon, ServerCrash } from 'lucide-react'
import { runSearch } from '@/api/search'
import { getApiError } from '@/lib/apiError'
import { PageHeader } from '@/components/layout/PageHeader'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Card, CardBody } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { StateMessage } from '@/components/ui/StateMessage'
import type { SearchMode } from '@/types/search'

const MODES: SearchMode[] = ['hybrid', 'semantic', 'keyword']

export function SearchPage() {
  const [query, setQuery] = useState('')
  const [mode, setMode] = useState<SearchMode>('hybrid')

  const searchMutation = useMutation({ mutationFn: runSearch })
  const searchError = searchMutation.isError ? getApiError(searchMutation.error, 'Search failed') : null

  function handleSubmit(event: FormEvent) {
    event.preventDefault()
    if (!query.trim()) return
    searchMutation.mutate({ query: query.trim(), mode, top_k: 10, rerank: true })
  }

  return (
    <div>
      <PageHeader title="Search" description="Query the corpus directly, outside the chat agent" />

      <div className="p-6">
        <form onSubmit={handleSubmit} className="flex gap-2">
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search documents..."
            className="flex-1"
            autoFocus
          />
          <select
            value={mode}
            onChange={(e) => setMode(e.target.value as SearchMode)}
            className="rounded-lg border border-neutral-300 bg-surface px-3 text-sm text-neutral-900 transition-colors focus:outline-none focus:ring-2 focus:ring-accent-400"
          >
            {MODES.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
          <Button type="submit" loading={searchMutation.isPending}>
            <SearchIcon className="h-4 w-4" /> Search
          </Button>
        </form>

        {searchMutation.data && (
          <p className="mt-4 animate-fade-slide-up text-sm text-neutral-500">
            {searchMutation.data.total} result{searchMutation.data.total === 1 ? '' : 's'}
            {searchMutation.data.reranked && ' · reranked'}
          </p>
        )}

        <div className="mt-4 space-y-3">
          {searchMutation.data?.results.map((r, i) => (
            <Card
              key={r.chunk_id}
              className="animate-fade-slide-up transition-all duration-150 hover:-translate-y-0.5 hover:border-accent-300 hover:shadow-md"
              style={{ animationDelay: `${Math.min(i, 10) * 30}ms` }}
            >
              <CardBody>
                <div className="mb-1.5 flex items-center justify-between gap-3">
                  <span className="truncate text-sm font-medium text-ink">
                    {r.document_filename ?? r.document_id}
                  </span>
                  <div className="flex shrink-0 items-center gap-2">
                    <Badge tone="blue">{r.strategy}</Badge>
                    <ScoreBar score={r.score} />
                  </div>
                </div>
                <p className="line-clamp-3 text-sm text-neutral-600">{r.text}</p>
              </CardBody>
            </Card>
          ))}
        </div>

        {searchError && (
          <StateMessage
            icon={ServerCrash}
            tone="error"
            title={searchError.isNetworkError ? "Can't reach the server" : 'Search failed'}
            description={searchError.message}
          />
        )}

        {searchMutation.isSuccess && searchMutation.data.results.length === 0 && (
          <StateMessage
            icon={FileSearch}
            title="No results"
            description="No documents in your accessible knowledge base matched this query."
          />
        )}
      </div>
    </div>
  )
}

function ScoreBar({ score }: { score: number }) {
  const pct = Math.max(0, Math.min(1, score)) * 100
  return (
    <div className="flex items-center gap-1.5" title={score.toFixed(3)}>
      <div className="h-1.5 w-10 overflow-hidden rounded-full bg-neutral-200">
        <div
          className="h-full rounded-full bg-accent-500 transition-all duration-500 ease-out"
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-xs tabular-nums text-neutral-400">{score.toFixed(2)}</span>
    </div>
  )
}
