interface Suggestion {
  label: string
  prompt: string
}

function buildSuggestions(departments: string[]): Suggestion[] {
  const primaryDepartment = departments[0]
  return [
    { label: 'What documents can I access?', prompt: 'What documents can I access?' },
    {
      label: primaryDepartment ? `Summarize available ${primaryDepartment} documents` : 'Summarize the available documents',
      prompt: primaryDepartment
        ? `Summarize the available ${primaryDepartment} documents`
        : 'Summarize the documents available to me',
    },
    {
      label: 'Show recent relevant information',
      prompt: 'Show me recent relevant information from the knowledge base',
    },
    { label: 'What can you help me with?', prompt: 'What can you help me with?' },
  ]
}

export function SuggestedPrompts({
  departments,
  onSelect,
}: {
  departments: string[]
  onSelect: (prompt: string) => void
}) {
  const suggestions = buildSuggestions(departments)

  return (
    <div className="flex w-full flex-col gap-1.5">
      {suggestions.map((s, i) => (
        <button
          key={s.label}
          type="button"
          onClick={() => onSelect(s.prompt)}
          style={{ animationDelay: `${i * 40}ms` }}
          className="animate-fade-slide-up rounded-lg border border-neutral-200 px-3.5 py-2.5 text-left text-sm text-neutral-600 transition-colors duration-150 hover:border-accent-300 hover:bg-accent-50/40 hover:text-ink"
        >
          {s.label}
        </button>
      ))}
    </div>
  )
}
