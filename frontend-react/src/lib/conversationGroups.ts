import type { ConversationSummary } from '@/types/chat'

export interface ConversationGroup {
  label: string
  items: ConversationSummary[]
}

export function groupByDate(items: ConversationSummary[]): ConversationGroup[] {
  const now = new Date()
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const startOfYesterday = startOfToday - 86_400_000
  const startOfWeek = startOfToday - 7 * 86_400_000

  const buckets: ConversationGroup[] = [
    { label: 'Today', items: [] },
    { label: 'Yesterday', items: [] },
    { label: 'Previous 7 days', items: [] },
    { label: 'Older', items: [] },
  ]
  for (const c of items) {
    const t = new Date(c.updated_at ?? c.created_at).getTime()
    if (t >= startOfToday) buckets[0].items.push(c)
    else if (t >= startOfYesterday) buckets[1].items.push(c)
    else if (t >= startOfWeek) buckets[2].items.push(c)
    else buckets[3].items.push(c)
  }
  return buckets.filter((b) => b.items.length > 0)
}

export function splitPinned(items: ConversationSummary[]): {
  pinned: ConversationSummary[]
  rest: ConversationSummary[]
} {
  const pinned = items
    .filter((c) => c.pinned_at)
    .sort((a, b) => new Date(b.pinned_at as string).getTime() - new Date(a.pinned_at as string).getTime())
  const rest = items.filter((c) => !c.pinned_at)
  return { pinned, rest }
}
