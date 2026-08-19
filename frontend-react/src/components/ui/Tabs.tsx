import { cn } from '@/lib/cn'

export interface TabOption {
  value: string
  label: string
}

export function Tabs({
  options,
  value,
  onChange,
}: {
  options: TabOption[]
  value: string
  onChange: (value: string) => void
}) {
  return (
    <div className="flex gap-1 border-b border-neutral-200 px-6">
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          onClick={() => onChange(option.value)}
          className={cn(
            'relative px-3 py-2.5 text-sm font-medium transition-colors duration-150',
            value === option.value ? 'text-accent-700' : 'text-neutral-500 hover:text-ink',
          )}
        >
          {option.label}
          {value === option.value && (
            <span className="absolute inset-x-0 -bottom-px h-0.5 rounded-full bg-accent-600" />
          )}
        </button>
      ))}
    </div>
  )
}
