export function PendingIndicator() {
  return (
    <div className="flex items-center gap-2 py-0.5 text-sm text-neutral-500">
      <span className="inline-flex items-center gap-1">
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className="h-1.5 w-1.5 animate-bounce-dot rounded-full bg-neutral-400"
            style={{ animationDelay: `${i * 0.15}s` }}
          />
        ))}
      </span>
      ATHENA is thinking...
    </div>
  )
}
